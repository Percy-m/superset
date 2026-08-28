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

import copy
from dataclasses import replace
from types import SimpleNamespace
from typing import Any, Iterator
from unittest.mock import patch

import pytest
from flask import Flask
from flask_caching import Cache

from superset.common import table_color_snapshot as snapshot_module
from superset.common.table_color_schema import (
    TableColorFilterError,
    TableColorSelection,
)
from superset.common.table_color_snapshot import (
    ColorSnapshotBudget,
    PREFIX,
    select_color_rows,
    TableColorSnapshotStore,
)

BUDGET = ColorSnapshotBudget(
    rows=1000,
    cells=50_000,
    bytes=8 * 1024 * 1024,
    cell_bytes=1024 * 1024,
    timeout=30,
    ttl=300,
)


@pytest.fixture
def snapshot_app() -> Iterator[Flask]:
    """Use a local cache only with the explicit non-production allowance."""
    app = Flask(__name__)
    app.config["TABLE_ALERT_FILTER_ALLOW_IN_MEMORY_CACHE"] = True
    with app.app_context():
        yield app


@pytest.fixture
def store(
    snapshot_app: Flask, monkeypatch: pytest.MonkeyPatch
) -> TableColorSnapshotStore:
    """Exercise Flask-Caching storage without touching the application's cache."""
    cache = Cache(snapshot_app, config={"CACHE_TYPE": "SimpleCache"})
    monkeypatch.setattr(
        snapshot_module, "cache_manager", SimpleNamespace(data_cache=cache)
    )
    return TableColorSnapshotStore(BUDGET)


def snapshot() -> dict[str, Any]:
    """Make a small complete result with value-independent semantic color tags."""
    return {
        "owner": "owner-a",
        "generation": "generation-a",
        "records": [{"value": -1}, {"value": 0}, {"value": 1}],
        "display_records": [{"value": -1}, {"value": 0}, {"value": 1}],
        "styles": [
            {"value": {"colors": ["GREEN"], "backgroundColor": "#5ac189"}},
            {"value": {"colors": ["YELLOW"], "backgroundColor": "#fcc700"}},
            {"value": {"colors": ["RED"], "backgroundColor": "#e04355"}},
        ],
        "capabilities": {"value": {"enabled": True, "supported": True}},
        "catalog": {"value": ["GREEN", "YELLOW", "RED"]},
    }


def test_snapshot_save_and_load_are_immutable(store: TableColorSnapshotStore) -> None:
    """Mutating any caller-owned copy cannot change a published snapshot."""
    source = snapshot()
    saved = store.save(source, ttl=60)
    source["records"][0]["value"] = 999
    saved["styles"][0]["value"]["colors"] = ["RED"]
    loaded = store.load(saved["snapshot_id"], "owner-a")
    assert loaded["records"][0]["value"] == -1
    assert loaded["styles"][0]["value"]["colors"] == ["GREEN"]
    loaded["records"].clear()
    assert len(store.load(saved["snapshot_id"], "owner-a")["records"]) == 3
    found = store.find("generation-a", "owner-a")
    assert found is not None
    assert found["snapshot_id"] == saved["snapshot_id"]


def test_snapshot_ids_do_not_authorize_other_owners(
    store: TableColorSnapshotStore,
) -> None:
    """A leaked snapshot ID or generation key is not a bearer token."""
    saved = store.save(snapshot(), ttl=60)
    for load in (
        lambda: store.load(saved["snapshot_id"], "owner-b"),
        lambda: store.find("generation-a", "owner-b"),
    ):
        with pytest.raises(TableColorFilterError) as error:
            load()
        assert error.value.status == 403
        assert error.value.code == "TABLE_COLOR_FILTER_ACCESS_DENIED"


def test_snapshot_expiry_and_generation_lookup_do_not_rebuild(
    store: TableColorSnapshotStore,
) -> None:
    """Expired immutable results fail explicitly, while an index miss is reusable."""
    with patch.object(snapshot_module.time, "time", return_value=1000) as clock:
        saved = store.save(snapshot(), ttl=10)
        assert saved["expires_at"] == 1010
        clock.return_value = 1009
        assert store.load(saved["snapshot_id"], "owner-a")
        clock.return_value = 1010
        with pytest.raises(TableColorFilterError) as error:
            store.load(saved["snapshot_id"], "owner-a")
        assert error.value.status == 410
        assert error.value.code == "TABLE_COLOR_FILTER_SNAPSHOT_EXPIRED"
        assert store.find("generation-a", "owner-a") is None


def test_snapshot_missing_id_has_explicit_expiry_error(
    store: TableColorSnapshotStore,
) -> None:
    """Missing results must never be interpreted as an empty successful filter."""
    with pytest.raises(TableColorFilterError, match="SNAPSHOT_EXPIRED"):
        store.load("missing", "owner-a")


@pytest.mark.parametrize("cache_type", ["NullCache", "SimpleCache"])
def test_unshared_cache_rejected_without_local_allowance(
    snapshot_app: Flask, monkeypatch: pytest.MonkeyPatch, cache_type: str
) -> None:
    """Production cannot publish per-worker snapshots that disappear on a hop."""
    snapshot_app.config["TABLE_ALERT_FILTER_ALLOW_IN_MEMORY_CACHE"] = False
    cache = Cache(snapshot_app, config={"CACHE_TYPE": cache_type})
    monkeypatch.setattr(
        snapshot_module, "cache_manager", SimpleNamespace(data_cache=cache)
    )
    with pytest.raises(TableColorFilterError, match="CACHE_UNAVAILABLE") as error:
        TableColorSnapshotStore(BUDGET)
    assert error.value.status == 503


@pytest.mark.parametrize(
    "backend",
    [
        "RedisCache",
        "RedisSentinelCache",
        "RedisClusterCache",
        "MemcachedCache",
        "SASLMemcachedCache",
        "SpreadSASLMemcachedCache",
    ],
)
def test_known_shared_cache_backends_are_accepted(
    snapshot_app: Flask, monkeypatch: pytest.MonkeyPatch, backend: str
) -> None:
    """Shared-backend admission is independent of the local-only override."""
    snapshot_app.config["TABLE_ALERT_FILTER_ALLOW_IN_MEMORY_CACHE"] = False
    cache = SimpleNamespace(cache=type(backend, (), {})())
    monkeypatch.setattr(
        snapshot_module, "cache_manager", SimpleNamespace(data_cache=cache)
    )
    assert TableColorSnapshotStore(BUDGET).cache is cache


def test_cache_failures_do_not_expose_connection_details(
    store: TableColorSnapshotStore,
) -> None:
    """Backend errors are normalized before they can reach an API response."""
    with patch.object(store.cache, "get", side_effect=RuntimeError("secret-db-url")):
        with pytest.raises(TableColorFilterError) as error:
            store.load("a", "owner-a")
    assert error.value.status == 503
    assert error.value.code == "TABLE_COLOR_FILTER_CACHE_UNAVAILABLE"
    assert "secret-db-url" not in str(error.value)


def test_failed_publication_does_not_publish_a_generation_index(
    store: TableColorSnapshotStore,
) -> None:
    """A failed cache write cannot advertise a result as available to clients."""
    with patch.object(store.cache, "set", return_value=False):
        with pytest.raises(TableColorFilterError, match="CACHE_UNAVAILABLE"):
            store.save(snapshot(), ttl=60)
    assert store.find("generation-a", "owner-a") is None


def test_single_flight_lease_blocks_duplicate_build_and_releases_on_error(
    store: TableColorSnapshotStore,
) -> None:
    """Concurrent same-context builds fail fast; failed builders release ownership."""

    def fail_build() -> None:
        """Raise after verifying a nested request cannot obtain the same lease."""
        with store.building("generation-a"):
            with pytest.raises(TableColorFilterError, match="BUILD_IN_PROGRESS"):
                with store.building("generation-a"):
                    pytest.fail("A duplicate builder must not acquire the lease")
            raise ValueError("build failure")

    with pytest.raises(ValueError, match="build failure"):
        fail_build()
    with store.building("generation-a"):
        assert store.cache.get(f"{PREFIX}build:generation-a")
    assert store.cache.get(f"{PREFIX}build:generation-a") is None


def test_builder_does_not_release_a_replaced_lease(
    store: TableColorSnapshotStore,
) -> None:
    """An expired owner's cleanup must not delete a different builder's lease."""
    key = f"{PREFIX}build:generation-a"
    with store.building("generation-a"):
        store.cache.set(key, "other-builder", timeout=60)
    assert store.cache.get(key) == "other-builder"


def test_known_capacity_failure_cache_contains_only_reason(
    store: TableColorSnapshotStore,
) -> None:
    """A rejected result is not kept as a partial or truncated snapshot."""
    reason = BUDGET.exceeded().reason()
    store.remember_failure("oversized-generation", reason)
    failure = store.failed_reason("oversized-generation")
    assert failure is not None
    assert failure == reason
    assert set(failure) == {"code", "message"}
    assert store.find("oversized-generation", "owner-a") is None


def test_default_result_row_budget_accepts_1000_and_rejects_1001() -> None:
    """The agreed default is the result-row count, not database scan count."""
    BUDGET.check_rows([{"value": index} for index in range(1000)])
    with pytest.raises(TableColorFilterError, match="LIMIT_EXCEEDED"):
        BUDGET.check_rows([{"value": index} for index in range(1001)])


def test_cell_count_budget_accepts_boundary_and_rejects_overflow() -> None:
    """A wide result is rejected even when its row count fits."""
    budget = replace(BUDGET, cells=4)
    budget.check_rows([{"a": 1, "b": 2}, {"a": 3, "b": 4}])
    with pytest.raises(TableColorFilterError, match="LIMIT_EXCEEDED"):
        budget.check_rows([{"a": 1, "b": 2}, {"a": 3, "b": 4, "c": 5}])


def test_single_cell_budget_counts_utf8_encoded_data() -> None:
    """CJK and emoji consume their encoded bytes, not just character counts."""
    budget = replace(BUDGET, cell_bytes=6)
    budget.check_rows([{"value": "😀"}])
    with pytest.raises(TableColorFilterError, match="LIMIT_EXCEEDED"):
        budget.check_rows([{"value": "😀a"}])


def test_rows_stop_at_total_byte_budget_before_snapshot_serialization() -> None:
    """Individually valid cells must not create an oversized intermediate result."""
    budget = replace(BUDGET, bytes=10, cell_bytes=10)
    assert budget.check_rows([{"a": "中"}, {"a": "中"}]) == 10
    with pytest.raises(TableColorFilterError, match="LIMIT_EXCEEDED"):
        budget.check_rows([{"a": "中"}, {"a": "中文"}])


def test_snapshot_budget_includes_display_values_and_style_metadata() -> None:
    """Derived comparison columns and frozen paints consume the same storage cap."""
    value = snapshot()
    BUDGET.check_snapshot(value)
    budget = replace(BUDGET, bytes=100)
    with pytest.raises(TableColorFilterError, match="LIMIT_EXCEEDED"):
        budget.check_snapshot(value)
    value["display_records"] = [{"a": 1, "b": 2, "c": 3}]
    with pytest.raises(TableColorFilterError, match="LIMIT_EXCEEDED"):
        replace(BUDGET, cells=2).check_snapshot(value)


def test_budget_configuration_caps_timeout_by_webserver_timeout(
    snapshot_app: Flask,
) -> None:
    """The feature cannot silently enlarge the deployment's request timeout."""
    snapshot_app.config.update(
        TABLE_ALERT_FILTER_MAX_ROWS=1000,
        TABLE_ALERT_FILTER_MAX_CELLS=50_000,
        TABLE_ALERT_FILTER_MAX_BYTES=8 * 1024 * 1024,
        TABLE_ALERT_FILTER_MAX_CELL_BYTES=1024 * 1024,
        TABLE_ALERT_FILTER_TIMEOUT=60,
        TABLE_ALERT_FILTER_SNAPSHOT_TTL=300,
        SUPERSET_WEBSERVER_TIMEOUT=20,
    )
    assert ColorSnapshotBudget.from_config().timeout == 20


def test_repeated_selections_include_third_color_without_repainting() -> None:
    """Selection changes are masks over one baseline, never new coloring input."""
    source = snapshot()
    before = copy.deepcopy(source)
    for colors, expected in (
        (["GREEN"], [0]),
        (["GREEN", "YELLOW"], [0, 1]),
        (["GREEN", "YELLOW", "RED"], [0, 1, 2]),
        (["RED"], [2]),
    ):
        assert (
            select_color_rows(
                source["styles"],
                [{"column": "value", "colors": colors}],
                source["capabilities"],
            )
            == expected
        )
    assert select_color_rows(source["styles"], [], source["capabilities"]) == [0, 1, 2]
    assert source == before


def test_color_selection_is_or_within_column_and_and_across_three_columns() -> None:
    """A third active column participates without replacing the previous two."""
    styles = [
        {
            "a": {"colors": ["GREEN"]},
            "b": {"colors": ["RED"]},
            "c": {"colors": ["YELLOW"]},
        },
        {
            "a": {"colors": ["YELLOW"]},
            "b": {"colors": ["RED"]},
            "c": {"colors": ["YELLOW"]},
        },
        {
            "a": {"colors": ["GREEN"]},
            "b": {"colors": ["GREEN"]},
            "c": {"colors": ["YELLOW"]},
        },
        {
            "a": {"colors": ["GREEN"]},
            "b": {"colors": ["RED"]},
            "c": {"colors": ["RED"]},
        },
    ]
    capabilities = {
        column: {"enabled": True, "supported": True} for column in ("a", "b", "c")
    }
    selections: list[TableColorSelection] = [
        {"column": "a", "colors": ["GREEN", "YELLOW"]},
        {"column": "b", "colors": ["RED"]},
        {"column": "c", "colors": ["YELLOW"]},
    ]
    assert select_color_rows(styles, selections, capabilities) == [0, 1]


@pytest.mark.parametrize(
    "capabilities,code,status",
    [
        ({}, "TABLE_COLOR_FILTER_INVALID", 400),
        (
            {"value": {"enabled": False, "supported": True}},
            "TABLE_COLOR_FILTER_INVALID",
            400,
        ),
        (
            {"value": {"enabled": True, "supported": False}},
            "TABLE_COLOR_FILTER_GRADIENT_UNSUPPORTED",
            422,
        ),
    ],
)
def test_unavailable_selection_is_not_reported_as_success(
    capabilities: dict[str, Any], code: str, status: int
) -> None:
    """Missing, disabled and gradient targets have explicit non-success outcomes."""
    with pytest.raises(TableColorFilterError) as error:
        select_color_rows(
            [{"value": {"colors": ["GREEN"]}}],
            [{"column": "value", "colors": ["GREEN"]}],
            capabilities,
        )
    assert error.value.code == code
    assert error.value.status == status
