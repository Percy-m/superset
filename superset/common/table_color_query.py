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
"""Execute bounded Table queries once and filter their immutable final painting."""

from __future__ import annotations

import copy
import logging
import math
import time
from decimal import Decimal
from numbers import Integral, Real
from typing import Any, cast, TYPE_CHECKING

import pandas as pd
from flask import current_app, g

from superset import is_feature_enabled
from superset.common.chart_data import ChartDataResultFormat, ChartDataResultType
from superset.common.db_query_status import QueryStatus
from superset.common.query_actions import get_query_results
from superset.common.table_color_context import (
    dashboard_filter_fingerprint,
    TableColorContext,
)
from superset.common.table_color_schema import (
    COLOR_ORDER,
    TableColorFilterError,
    TableColorRequest,
)
from superset.common.table_color_snapshot import (
    ColorSnapshotBudget,
    count_color_rows,
    select_color_rows,
    TableColorSnapshotStore,
    unavailable_cache,
)
from superset.common.table_color_styles import resolve_table_color_styles
from superset.exceptions import CacheLoadError, QueryObjectValidationError
from superset.extensions import security_manager
from superset.utils.cache import generate_cache_key
from superset.utils.core import (
    extract_dataframe_dtypes,
    ExtraFiltersReasonType,
    GenericDataType,
    get_column_name,
    get_time_filter_status,
)

if TYPE_CHECKING:
    from superset.common.query_context import QueryContext
    from superset.common.query_object import QueryObject
    from superset.connectors.sqla.models import SqlaTable

logger = logging.getLogger(__name__)

# Invalidate persisted paint/index pairs when color-filter semantics change.
TABLE_COLOR_STYLE_REVISION = 2


class TableColorQueryProcessor:
    """A color-only branch before QueryContext's totals and SQL execution."""

    def __init__(self, query_context: QueryContext) -> None:
        self.context = query_context
        self.request = cast(TableColorRequest, query_context.table_color_filter)
        self.trusted = TableColorContext.resolve(query_context, self.request)
        self.budget = ColorSnapshotBudget.from_config()
        self.main = query_context.queries[0]
        self.server_pagination = bool(self.trusted.form_data.get("server_pagination"))
        self.is_export = (
            query_context.result_format in ChartDataResultFormat.table_like()
            or query_context.result_type == ChartDataResultType.RESULTS
        )
        if "view_rows" in self.request and (
            not self.is_export or not self.request.get("snapshot_id")
        ):
            raise TableColorFilterError(
                "TABLE_COLOR_FILTER_INVALID",
                "Current-view row references require an existing export snapshot.",
            )
        self.source_page_size = (
            int(self.main.row_limit or self.trusted.row_limit)
            if self.server_pagination and not self.is_export
            else int(
                self.trusted.form_data.get("server_page_length")
                or self.trusted.row_limit
            )
            if self.server_pagination
            else self.trusted.row_limit
        )
        self.started = time.monotonic()
        self.selections = self.request.get("selections", [])
        self._offset_cache: dict[str, tuple[pd.DataFrame, str]] = {}
        self._fallback_queries: list[dict[str, Any]] | None = None

    def _generation(self) -> str:
        query = copy.copy(self.main)
        query.row_limit = self.trusted.row_limit
        query.row_offset = 0
        query.result_type = None
        return generate_cache_key(
            {
                "owner": self.trusted.owner,
                "query": self.context.query_cache_key(query),
                "form_data": self.trusted.form_data,
                "source_page_size": self.source_page_size,
                "theme": self.trusted.theme,
                "comparison_main_label": self.trusted.comparison_main_label,
                "dashboard_id": self.trusted.dashboard_id,
                "version": 2,
                "style_revision": TABLE_COLOR_STYLE_REVISION,
            },
            "table-color-context-",
        )

    def _check_time(self) -> None:
        if time.monotonic() - self.started >= self.budget.timeout:
            raise TableColorFilterError(
                "TABLE_COLOR_FILTER_TIMEOUT",
                "The color-filter result could not be prepared within its time budget.",
                504,
            )

    def run(self, force_cached: bool = False) -> list[dict[str, Any]]:
        """Read a complete snapshot or prepare one without a per-page SQL loop."""
        try:
            if self.context.get_cache_timeout() == -1:
                raise unavailable_cache()
            return self._run_cached(TableColorSnapshotStore(self.budget), force_cached)
        except TableColorFilterError as ex:
            if ex.code != "TABLE_COLOR_FILTER_CACHE_UNAVAILABLE":
                raise
            if self.selections or self.request.get("snapshot_id") or self.is_export:
                raise
            if self._fallback_queries is not None:
                self._fallback_queries[0]["table_color_metadata"] = (
                    self._unavailable_metadata(ex, self._fallback_queries[0])
                )
                return self._fallback_queries
            return self._ordinary(ex, force_cached)

    def _run_cached(
        self,
        store: TableColorSnapshotStore,
        force_cached: bool,
    ) -> list[dict[str, Any]]:
        """Keep cache failure recovery outside the build and publication path."""
        snapshot_id = self.request.get("snapshot_id")
        if snapshot_id and (not self.context.force or self.is_export):
            snapshot = store.load(snapshot_id, self.trusted.owner)
            if self.is_export:
                self.source_page_size = snapshot["source_page_size"]
            if snapshot["generation"] != self._generation():
                raise TableColorFilterError(
                    "TABLE_COLOR_FILTER_CONTEXT_CHANGED",
                    "The query, formatting or access context changed. "
                    "Reload the chart.",
                    409,
                )
            return self._render(snapshot, cached=True)

        generation = self._generation()
        if not self.context.force:
            if cached_snapshot := store.find(generation, self.trusted.owner):
                return self._render(cached_snapshot, cached=True)
            if reason := store.failed_reason(generation):
                return self._ordinary(
                    TableColorFilterError(reason["code"], reason["message"], 422),
                    force_cached,
                )
        if force_cached:
            raise CacheLoadError("Color filter result is not cached")

        with store.building(generation):
            if not self.context.force and (
                existing_snapshot := store.find(generation, self.trusted.owner)
            ):
                return self._render(existing_snapshot, cached=True)
            snapshot = self._build(generation)
            if "unavailable" in snapshot:
                self._fallback_queries = snapshot["queries"]
                store.remember_failure(generation, snapshot["unavailable"])
                return snapshot["queries"]
            self._check_time()
            return self._publish_snapshot(store, snapshot)

    def _publish_snapshot(
        self, store: TableColorSnapshotStore, snapshot: dict[str, Any]
    ) -> list[dict[str, Any]]:
        """Publish under the build lease, retaining safe ordinary-data fallback."""
        try:
            snapshot = store.save(snapshot, self._ttl())
        except TableColorFilterError as error:
            if (
                error.code != "TABLE_COLOR_FILTER_LIMIT_EXCEEDED"
                or self.selections
                or self.request.get("snapshot_id")
                or self.is_export
                or self._fallback_queries is None
            ):
                raise
            # Publication adds identity/expiry metadata. A byte-boundary
            # rejection must retain the ordinary result already fetched.
            store.remember_failure(snapshot["generation"], error.reason())
            return self._fallback_queries
        logger.info(
            "table_color_snapshot prepared rows=%d columns=%d elapsed_ms=%d",
            len(snapshot["records"]),
            len(snapshot["colnames"]),
            int((time.monotonic() - self.started) * 1000),
        )
        return self._render(snapshot, cached=False)

    def _ttl(self) -> int:
        ttl = self.budget.ttl
        cache_timeout = self.context.get_cache_timeout()
        if cache_timeout is not None and cache_timeout > 0:
            ttl = min(ttl, cache_timeout)
        if security_manager.is_guest_user():
            ttl = min(ttl, max(1, int(g.user.guest_token["exp"] - time.time())))
        return ttl

    def _ordinary(
        self, error: TableColorFilterError, force_cached: bool = False
    ) -> list[dict[str, Any]]:
        """Keep baseline browsing when no selection was claimed to be applied."""
        if self.selections or self.request.get("snapshot_id") or self.is_export:
            raise error
        if not force_cached:
            self.context._processor.ensure_totals_available()  # pylint: disable=protected-access
        results = [
            get_query_results(
                query.result_type or self.context.result_type,
                self.context,
                query,
                force_cached,
            )
            for query in self.context.queries
        ]
        if results:
            results[0]["table_color_metadata"] = self._unavailable_metadata(
                error, results[0]
            )
        return results

    def _unavailable_metadata(
        self, error: TableColorFilterError, primary: dict[str, Any]
    ) -> dict[str, Any]:
        painted = resolve_table_color_styles(
            [],
            primary.get("colnames", []),
            primary.get("coltypes", []),
            self.trusted.form_data,
            self.trusted.theme,
            comparison_main_label=self.trusted.comparison_main_label,
        )
        return {
            "status": "unavailable",
            "capabilities": painted["capabilities"],
            "reason": error.reason(),
        }

    def _fetch(self, query: QueryObject) -> dict[str, Any]:
        payload = self.context.get_df_payload(query)
        self._check_time()
        if payload.get("status") == QueryStatus.FAILED:
            raise TableColorFilterError(
                "TABLE_COLOR_FILTER_QUERY_FAILED",
                "The Table query could not be completed.",
                422,
            )
        return payload

    def _auxiliary(
        self, complete_count: int | None
    ) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
        results: list[dict[str, Any]] = []
        totals = None
        needs_totals, totals_index = (
            self.context._processor._prepare_contribution_totals()  # pylint: disable=protected-access
        )
        loaded: dict[str, dict[str, Any]] = {}
        for index, query in enumerate(self.context.queries[1:], start=1):
            if query.is_rowcount and complete_count is not None:
                results.append(self._simple_payload({"rowcount": complete_count}))
                continue
            key = self.context.query_cache_key(query) or str(id(query))
            if key not in loaded:
                loaded[key] = self._as_payload(self._fetch(query), query)
            payload = copy.deepcopy(loaded[key])
            results.append(payload)
            if needs_totals and index == totals_index:
                frame = pd.DataFrame.from_records(payload["data"])
                totals = {
                    column: frame[column].sum()
                    for column in frame.columns
                    if frame[column].dtype.kind in "biufc"
                }
        return results, totals

    def _build(self, generation: str) -> dict[str, Any]:
        raw_query = copy.copy(self.main)
        raw_query.row_offset = 0
        raw_query.row_limit = (
            min(
                self.trusted.row_limit,
                max(
                    self.budget.rows + 1,
                    self.main.row_offset + self.source_page_size,
                ),
            )
            if self.server_pagination
            else self.trusted.row_limit
        )
        raw_query.time_offsets = []
        raw_query.post_processing = []
        raw_payload = self._fetch(raw_query)
        raw_df = raw_payload["df"]
        over_limit = len(raw_df) > self.budget.rows
        auxiliary, totals = self._auxiliary(None if over_limit else len(raw_df))
        if over_limit:
            if self.selections or self.is_export:
                raise self.budget.exceeded()
            return self._oversized(raw_payload, auxiliary, totals)

        try:
            processed, paints = self._process_pages(raw_df, totals)
        except TableColorFilterError as error:
            if (
                error.code != "TABLE_COLOR_FILTER_LIMIT_EXCEEDED"
                or self.selections
                or self.is_export
            ):
                raise
            return self._oversized(raw_payload, auxiliary, totals)
        primary = self._as_payload({**raw_payload, "df": processed}, self.main)
        snapshot = {
            "owner": self.trusted.owner,
            "generation": generation,
            "source_page_size": self.source_page_size,
            "source_form_data": self.trusted.form_data,
            "source_context_queries": copy.deepcopy(
                self.context.cache_values["queries"]
            ),
            "saved_chart_id": self.context.slice_.id if self.context.slice_ else None,
            "dashboard_id": self.trusted.dashboard_id,
            "dashboard_filters": dashboard_filter_fingerprint(
                (self.context.form_data or {}).get("extra_form_data") or {}
            ),
            "theme_mode": self.request.get("theme_mode", "default"),
            "records": primary["data"],
            "colnames": primary["colnames"],
            "coltypes": primary["coltypes"],
            "primary": {key: value for key, value in primary.items() if key != "data"},
            "auxiliary": auxiliary,
            **paints,
        }
        try:
            self.budget.check_snapshot(snapshot)
        except TableColorFilterError:
            if self.selections or self.is_export:
                raise
            return self._unavailable_result(primary, auxiliary)
        self._fallback_queries = self._unavailable_result(
            copy.deepcopy(primary), copy.deepcopy(auxiliary)
        )["queries"]
        return snapshot

    def _post_process_page(
        self,
        page: pd.DataFrame,
        start: int,
        size: int,
        totals: dict[str, Any] | None,
        offset_cache: dict[str, tuple[pd.DataFrame, str]],
    ) -> pd.DataFrame:
        """Run native post-processing with the original page and denominators."""
        query = copy.copy(self.main)
        query.row_offset = start
        query.row_limit = size
        query.post_processing = copy.deepcopy(self.main.post_processing)
        if totals is not None:
            for operation in query.post_processing:
                if operation.get("operation") == "contribution":
                    operation.setdefault("options", {})["contribution_totals"] = totals
        if not page.empty:
            if query.time_offsets:
                page = cast(
                    "SqlaTable", self.context.datasource
                ).processing_time_offsets(
                    page,
                    query,
                    local_offset_cache=offset_cache,
                )["df"]
            page = query.exec_post_processing(page)
        self._check_time()
        return page

    def _process_pages(
        self, frame: pd.DataFrame, totals: dict[str, Any] | None
    ) -> tuple[pd.DataFrame, dict[str, Any]]:
        processed_pages: list[pd.DataFrame] = []
        display_records: list[dict[str, Any]] = []
        styles: list[dict[str, Any]] = []
        catalog: dict[str, set[str]] = {}
        capabilities: dict[str, Any] = {}
        display_columns: list[str] = []
        display_coltypes: list[GenericDataType] = []
        row_count = 0
        cell_count = 0
        value_bytes = 0
        size = max(1, self.source_page_size if self.server_pagination else len(frame))
        for start in range(0, max(1, len(frame)), size):
            page = frame.iloc[start : start + size].copy(deep=True)
            page = self._post_process_page(
                page, start, size, totals, self._offset_cache
            )
            processed_pages.append(page)
            page_records = page.to_dict(orient="records")
            row_count += len(page_records)
            cell_count += sum(len(record) for record in page_records)
            value_bytes += self.budget.check_rows(page_records)
            if (
                row_count > self.budget.rows
                or cell_count > self.budget.cells
                or value_bytes > self.budget.bytes
            ):
                raise self.budget.exceeded()
            column_types = extract_dataframe_dtypes(page, self.context.datasource)
            painting = resolve_table_color_styles(
                page_records,
                list(page.columns),
                column_types,
                self.trusted.form_data,
                self.trusted.theme,
                comparison_main_label=self.trusted.comparison_main_label,
            )
            display_records.extend(painting["records"])
            styles.extend(painting["styles"])
            display_columns = painting["columns"]
            display_coltypes = [
                GenericDataType(value) for value in painting["coltypes"]
            ]
            for column, colors in painting["catalog"].items():
                catalog.setdefault(column, set()).update(colors)
            for column, capability in painting["capabilities"].items():
                if column not in capabilities or not capability["supported"]:
                    capabilities[column] = capability
        return pd.concat(processed_pages, ignore_index=True), {
            "display_records": display_records,
            "display_columns": display_columns,
            "display_coltypes": display_coltypes,
            "styles": styles,
            "catalog": {
                column: [color for color in COLOR_ORDER if color in colors]
                for column, colors in catalog.items()
            },
            "capabilities": capabilities,
        }

    def _oversized(
        self,
        raw_payload: dict[str, Any],
        auxiliary: list[dict[str, Any]],
        totals: dict[str, Any] | None,
    ) -> dict[str, Any]:
        """Reuse the fetched first page instead of running the slow query again."""
        raw_df = raw_payload["df"]
        if self.server_pagination:
            offset = self.main.row_offset
            size = int(self.main.row_limit or self.source_page_size)
            raw_df = raw_df.iloc[offset : offset + size]
        processed = self._post_process_page(
            raw_df.copy(deep=True),
            self.main.row_offset,
            len(raw_df),
            totals,
            self._offset_cache,
        )
        primary = self._as_payload({**raw_payload, "df": processed}, self.main)
        return self._unavailable_result(primary, auxiliary, already_paged=True)

    def _unavailable_result(
        self,
        primary: dict[str, Any],
        auxiliary: list[dict[str, Any]],
        *,
        already_paged: bool = False,
    ) -> dict[str, Any]:
        """Return ordinary rows already obtained, without retrying the base SQL."""
        if self.server_pagination and not already_paged:
            offset = self.main.row_offset
            limit = int(self.main.row_limit or self.source_page_size)
            primary["data"] = primary["data"][offset : offset + limit]
            primary["rowcount"] = len(primary["data"])
            primary["indexnames"] = list(range(primary["rowcount"]))
        primary["table_color_metadata"] = self._unavailable_metadata(
            self.budget.exceeded(), primary
        )
        return {
            "unavailable": self.budget.exceeded().reason(),
            "queries": [primary, *auxiliary],
        }

    def _as_payload(self, raw: dict[str, Any], query: QueryObject) -> dict[str, Any]:
        payload = dict(raw)
        frame = payload.pop("df")
        applied_time, rejected_time = get_time_filter_status(
            self.context.datasource, query.applied_time_extras
        )
        applied = payload.pop("applied_filter_columns", [])
        rejected = payload.pop("rejected_filter_columns", [])
        payload.update(
            data=frame.to_dict(orient="records"),
            colnames=list(frame.columns),
            coltypes=extract_dataframe_dtypes(frame, self.context.datasource),
            indexnames=list(range(len(frame))),
            rowcount=len(frame),
            result_format=self.context.result_format,
            applied_filters=[{"column": get_column_name(column)} for column in applied]
            + applied_time,
            rejected_filters=[
                {
                    "column": get_column_name(column),
                    "reason": ExtraFiltersReasonType.COL_NOT_IN_DATASOURCE,
                }
                for column in rejected
            ]
            + rejected_time,
        )
        return payload

    @staticmethod
    def _simple_payload(values: dict[str, Any]) -> dict[str, Any]:
        return {
            "data": [values],
            "colnames": list(values),
            "coltypes": [GenericDataType.NUMERIC] * len(values),
            "rowcount": 1,
            "sql_rowcount": 1,
            "status": QueryStatus.SUCCESS,
            "error": None,
            "query": "",
            "is_cached": True,
        }

    @staticmethod
    def _sum_display_rows(rows: list[dict[str, Any]]) -> dict[str, Any]:
        if not rows:
            return {}
        totals: dict[str, Any] = {}
        for column in rows[0]:
            values = [row.get(column) for row in rows]
            non_null = [value for value in values if value is not None]
            if non_null and all(
                isinstance(value, (Real, Decimal)) and not isinstance(value, bool)
                for value in non_null
            ):
                # Python integers do not overflow like numpy UInt64 aggregates.
                normalized = [
                    int(value) if isinstance(value, Integral) else value
                    for value in non_null
                    if not pd.isna(value)
                ]
                if any(isinstance(value, Decimal) for value in normalized):
                    totals[column] = sum(Decimal(str(value)) for value in normalized)
                else:
                    totals[column] = sum(normalized)
        return totals

    def _render(self, snapshot: dict[str, Any], cached: bool) -> list[dict[str, Any]]:
        selected = select_color_rows(
            snapshot["styles"], self.selections, snapshot["capabilities"]
        )
        if "view_rows" in self.request:
            view_rows = self.request["view_rows"]
            allowed = set(selected)
            if len(set(view_rows)) != len(view_rows) or any(
                type(index) is not int or index not in allowed for index in view_rows
            ):
                raise TableColorFilterError(
                    "TABLE_COLOR_FILTER_INVALID",
                    "Current-view row references do not match the filtered result.",
                )
            # References only project already authorized rows; they never supply data
            # or alter the immutable painting and percentage calculation contexts.
            selected = view_rows
        all_records = [snapshot["records"][index] for index in selected]
        display_records = [snapshot["display_records"][index] for index in selected]
        display_styles = [snapshot["styles"][index] for index in selected]
        totals = self._sum_display_rows(display_records) if self.selections else None
        if self.is_export:
            return self._export(
                snapshot, display_records, display_styles, totals, selected
            )
        offset = self.main.row_offset if self.server_pagination else 0
        limit = self.main.row_limit if self.server_pagination else None
        if offset >= len(all_records):
            offset = 0
        stop = offset + limit if limit is not None else None
        records = all_records[offset:stop]
        primary = {
            **copy.deepcopy(snapshot["primary"]),
            "data": records,
            "rowcount": len(records),
            "indexnames": list(range(len(records))),
            "is_cached": cached,
            "table_color_metadata": {
                "status": "ready",
                "snapshot_id": snapshot["snapshot_id"],
                "generation": snapshot["generation"],
                "baseline_rowcount": len(snapshot["records"]),
                "filtered_rowcount": len(selected),
                "source_page_size": snapshot["source_page_size"],
                "row_offset": offset,
                "row_indices": selected[offset:stop],
                "theme_mode": snapshot.get("theme_mode", "default"),
                "catalog": snapshot["catalog"],
                "color_counts": count_color_rows(
                    snapshot["styles"], snapshot["catalog"]
                ),
                "capabilities": snapshot["capabilities"],
                "styles": display_styles[offset:stop],
                "expires_in": max(0, math.ceil(snapshot["expires_at"] - time.time())),
                "selections": self.selections,
                **({"totals": totals} if totals is not None else {}),
            },
        }
        auxiliary = copy.deepcopy(snapshot["auxiliary"])
        for index, query in enumerate(self.context.queries[1:]):
            if query.is_rowcount:
                auxiliary[index] = self._simple_payload({"rowcount": len(selected)})
            elif self.selections:
                raw_totals = self._sum_display_rows(all_records)
                auxiliary[index] = self._simple_payload(raw_totals)
        return [primary, *auxiliary]

    def _export(
        self,
        snapshot: dict[str, Any],
        rows: list[dict[str, Any]],
        styles: list[dict[str, Any]],
        totals: dict[str, Any] | None,
        row_indices: list[int],
    ) -> list[dict[str, Any]]:
        export_limit = int(current_app.config["ROW_LIMIT"])
        if len(rows) > export_limit:
            raise self.budget.exceeded()
        # Mixed NULL + UInt64 columns must not be coerced to lossy float64.
        frame = pd.DataFrame(rows, columns=snapshot["display_columns"], dtype=object)
        frame.attrs["table_color_styles"] = styles
        if (
            self.context.result_format == ChartDataResultFormat.XLSX
            and self.context.result_format_options.get("styled")
            and is_feature_enabled("STYLED_XLSX_EXPORT")
        ):
            from superset.utils.styled_excel import (  # pylint: disable=import-outside-toplevel
                dataframe_to_styled_xlsx,
                resolve_sheet_name,
            )

            chart = self.context.slice_
            if chart is None:
                raise QueryObjectValidationError(
                    "Styled XLSX requires a saved Table chart."
                )
            sheet_name, _ = resolve_sheet_name(chart.slice_name, chart.id, set())
            data: object = dataframe_to_styled_xlsx(
                frame,
                [],
                sheet_name=sheet_name,
                column_types=snapshot["display_coltypes"],
                column_config=self.trusted.form_data.get("column_config"),
            )
        else:
            data = self.context.get_data(frame, snapshot["display_coltypes"])
        return [
            {
                "data": data,
                "colnames": snapshot["display_columns"],
                "coltypes": snapshot["display_coltypes"],
                "rowcount": len(rows),
                "sql_rowcount": len(snapshot["records"]),
                "status": QueryStatus.SUCCESS,
                "error": None,
                "query": "",
                "is_cached": True,
                "table_color_metadata": {
                    "status": "ready",
                    "snapshot_id": snapshot["snapshot_id"],
                    "generation": snapshot["generation"],
                    "styles": styles,
                    "baseline_rowcount": len(snapshot["records"]),
                    "filtered_rowcount": len(rows),
                    "source_page_size": snapshot["source_page_size"],
                    "row_offset": 0,
                    "row_indices": row_indices,
                    "theme_mode": snapshot.get("theme_mode", "default"),
                    "catalog": snapshot["catalog"],
                    "color_counts": count_color_rows(
                        snapshot["styles"], snapshot["catalog"]
                    ),
                    "capabilities": snapshot["capabilities"],
                    "selections": self.selections,
                    "expires_in": max(
                        0, math.ceil(snapshot["expires_at"] - time.time())
                    ),
                    **({"totals": totals} if totals is not None else {}),
                },
            }
        ]
