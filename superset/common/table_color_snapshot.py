# Licensed to the Apache Software Foundation (ASF) under one
# or more contributor license agreements.  See the NOTICE file
# distributed with this work for additional information
# regarding copyright ownership.  The ASF licenses this file
# to you under the Apache License, Version 2.0 (the
# "License"); you may not use this file except in compliance
# with the License.  You may obtain a copy of the License at
#
#   http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing,
# software distributed under the License is distributed on an
# "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY
# KIND, either express or implied.  See the License for the
# specific language governing permissions and limitations
# under the License.
"""Bounded shared storage for immutable, user-bound Table color results."""

from __future__ import annotations

import copy
import logging
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Iterable, Iterator, Sequence

from flask import current_app

from superset.common.table_color_context import access_denied
from superset.common.table_color_schema import (
    COLOR_ORDER,
    TableColorFilterError,
    TableColorSelection,
)
from superset.extensions import cache_manager
from superset.utils import json

PREFIX = "table-color-v2:"
logger = logging.getLogger(__name__)
_SHARED_CACHES = frozenset(
    {
        "RedisCache",
        "RedisSentinelCache",
        "RedisClusterCache",
        "MemcachedCache",
        "SASLMemcachedCache",
        "SpreadSASLMemcachedCache",
    }
)


def unavailable_cache() -> TableColorFilterError:
    """Describe a missing shared cache without exposing connection details."""
    return TableColorFilterError(
        "TABLE_COLOR_FILTER_CACHE_UNAVAILABLE",
        "Color filtering requires an available shared data cache.",
        503,
    )


@dataclass(frozen=True)
class ColorSnapshotBudget:
    """Result limits, independent of database scan and query row limits."""

    rows: int
    cells: int
    bytes: int
    cell_bytes: int
    timeout: float
    ttl: int

    @classmethod
    def from_config(cls) -> ColorSnapshotBudget:
        """Load positive deployment budgets without enlarging existing timeouts."""
        config = current_app.config
        return cls(
            rows=max(1, int(config["TABLE_ALERT_FILTER_MAX_ROWS"])),
            cells=max(1, int(config["TABLE_ALERT_FILTER_MAX_CELLS"])),
            bytes=max(1, int(config["TABLE_ALERT_FILTER_MAX_BYTES"])),
            cell_bytes=max(1, int(config["TABLE_ALERT_FILTER_MAX_CELL_BYTES"])),
            timeout=max(
                1,
                min(
                    float(config["TABLE_ALERT_FILTER_TIMEOUT"]),
                    float(config["SUPERSET_WEBSERVER_TIMEOUT"]),
                ),
            ),
            ttl=max(1, int(config["TABLE_ALERT_FILTER_SNAPSHOT_TTL"])),
        )

    def exceeded(self) -> TableColorFilterError:
        """Explain that the entire result is required; no silent truncation."""
        return TableColorFilterError(
            "TABLE_COLOR_FILTER_LIMIT_EXCEEDED",
            f"The complete color-filter result exceeds the {self.rows:,}-row "
            "or data-size limit. Narrow the query; ordinary Table browsing "
            "remains available.",
            422,
        )

    def check_rows(self, records: Sequence[dict[str, Any]]) -> int:
        """Check row/cell budgets and return their incremental UTF-8 value size."""
        if len(records) > self.rows or sum(len(row) for row in records) > self.cells:
            raise self.exceeded()
        size = 0
        for row in records:
            for value in row.values():
                cell_size = len(_encode(value))
                size += cell_size
                if cell_size > self.cell_bytes or size > self.bytes:
                    raise self.exceeded()
        return size

    def check_snapshot(self, snapshot: dict[str, Any]) -> None:
        """Bound both display values and the styles stored alongside them."""
        self.check_rows(snapshot["records"])
        self.check_rows(snapshot["display_records"])
        if len(_encode(snapshot)) > self.bytes:
            raise self.exceeded()


def _encode(value: object) -> bytes:
    return json.dumps(
        value, default=json.json_int_dttm_ser, ignore_nan=True, ensure_ascii=False
    ).encode("utf-8")


def count_color_rows(
    styles: Sequence[dict[str, Any]], columns: Iterable[str]
) -> dict[str, dict[str, int]]:
    """Count each final color once per baseline row in each visible column."""
    counts = {column: dict.fromkeys(COLOR_ORDER, 0) for column in columns}
    for row in styles:
        for column, paint in row.items():
            if column in counts:
                for color in set(paint.get("colors", [])):
                    if color in counts[column]:
                        counts[column][color] += 1
    return counts


def select_color_rows(
    styles: Sequence[dict[str, Any]],
    selections: Sequence[TableColorSelection],
    capabilities: dict[str, Any],
) -> list[int]:
    """Select final visible colors: OR within a column and AND across columns."""
    for selection in selections:
        capability = capabilities.get(selection["column"])
        if not capability or not capability.get("enabled"):
            raise TableColorFilterError(
                "TABLE_COLOR_FILTER_INVALID", "Color filter column is not available."
            )
        if not capability.get("supported"):
            reason = capability.get("reason") or {}
            raise TableColorFilterError(
                reason.get("code", "TABLE_COLOR_FILTER_GRADIENT_UNSUPPORTED"),
                reason.get(
                    "message",
                    "This column contains gradient formatting and cannot "
                    "be filtered by color.",
                ),
                422,
            )
    return [
        index
        for index, row in enumerate(styles)
        if all(
            set(row.get(selection["column"], {}).get("colors", []))
            & set(selection["colors"])
            for selection in selections
        )
    ]


class TableColorSnapshotStore:
    """Use a shared cache namespace; opaque IDs never grant access themselves."""

    def __init__(self, budget: ColorSnapshotBudget) -> None:
        self.budget = budget
        self.cache = cache_manager.data_cache
        backend = type(self.cache.cache).__name__
        if backend not in _SHARED_CACHES and not (
            backend == "SimpleCache"
            and current_app.config["TABLE_ALERT_FILTER_ALLOW_IN_MEMORY_CACHE"]
        ):
            raise unavailable_cache()

    def _get(self, key: str) -> Any:
        try:
            return self.cache.get(f"{PREFIX}{key}")
        except Exception as ex:  # pylint: disable=broad-except
            raise unavailable_cache() from ex

    def _set(self, key: str, value: object, ttl: int) -> None:
        try:
            if not self.cache.set(f"{PREFIX}{key}", value, timeout=ttl):
                raise unavailable_cache()
        except TableColorFilterError:
            raise
        except Exception as ex:  # pylint: disable=broad-except
            raise unavailable_cache() from ex

    def load(self, snapshot_id: str, owner: str) -> dict[str, Any]:
        """Validate identity and expiry on every read, including exports."""
        snapshot = self._get(snapshot_id)
        if (
            not isinstance(snapshot, dict)
            or snapshot.get("expires_at", 0) <= time.time()
        ):
            raise TableColorFilterError(
                "TABLE_COLOR_FILTER_SNAPSHOT_EXPIRED",
                "The color-filter result expired. Reload the chart to rebuild it.",
                410,
            )
        if snapshot.get("owner") != owner:
            raise access_denied()
        return copy.deepcopy(snapshot)

    def find(self, generation: str, owner: str) -> dict[str, Any] | None:
        """Reuse a completed build, never a partially prepared result."""
        snapshot_id = self._get(f"index:{generation}")
        if not isinstance(snapshot_id, str):
            return None
        try:
            return self.load(snapshot_id, owner)
        except TableColorFilterError as ex:
            if ex.status == 410:
                return None
            raise

    def failed_reason(self, generation: str) -> dict[str, Any] | None:
        """Avoid repeatedly probing the same known-oversized query on every page."""
        result = self._get(f"unavailable:{generation}")
        return result if isinstance(result, dict) else None

    def remember_failure(self, generation: str, reason: dict[str, str]) -> None:
        """Cache only a non-sensitive reason, never a partially filtered result."""
        self._set(f"unavailable:{generation}", reason, self.budget.ttl)

    def save(self, snapshot: dict[str, Any], ttl: int) -> dict[str, Any]:
        """Publish an immutable result only after all size and build checks pass."""
        snapshot = copy.deepcopy(snapshot)
        snapshot["snapshot_id"] = uuid.uuid4().hex
        snapshot["expires_at"] = time.time() + ttl
        self.budget.check_snapshot(snapshot)
        self._set(snapshot["snapshot_id"], snapshot, ttl)
        self._set(f"index:{snapshot['generation']}", snapshot["snapshot_id"], ttl)
        return snapshot

    @contextmanager
    def building(self, generation: str) -> Iterator[None]:
        """Prevent concurrent requests from repeating one expensive build."""
        key = f"{PREFIX}build:{generation}"
        token = uuid.uuid4().hex
        try:
            acquired = self.cache.add(key, token, timeout=int(self.budget.timeout) + 10)
        except Exception as ex:  # pylint: disable=broad-except
            raise unavailable_cache() from ex
        if not acquired:
            raise TableColorFilterError(
                "TABLE_COLOR_FILTER_BUILD_IN_PROGRESS",
                "This color-filter result is being prepared. Retry after it finishes.",
                503,
            )
        try:
            yield
        finally:
            try:
                if self.cache.get(key) == token:
                    self.cache.delete(key)
            except Exception:  # pylint: disable=broad-except
                # The lease expires even when the shared backend is unavailable.
                logger.warning("table_color_snapshot lease_release_failed")
