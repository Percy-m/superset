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
"""Synchronous, atomic Dashboard Tab XLSX export orchestration."""

from __future__ import annotations

import copy
import logging
import math
import tempfile
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast, Mapping, TypedDict

import pandas as pd
from flask import current_app

from superset.commands.base import BaseCommand
from superset.commands.dashboard.exceptions import (
    DashboardXlsxChartFailedError,
    DashboardXlsxInvalidTabError,
    DashboardXlsxNoTableError,
    DashboardXlsxTableLimitExceededError,
)
from superset.common.chart_data import ChartDataResultFormat, ChartDataResultType
from superset.common.db_query_status import QueryStatus
from superset.common.query_context import QueryContext
from superset.common.query_context_factory import QueryContextFactory
from superset.common.table_alerts import TableRuleResolver
from superset.exceptions import SupersetSecurityException
from superset.models.dashboard import Dashboard
from superset.models.slice import Slice
from superset.utils import json
from superset.utils.core import (
    DatasourceDict,
    extract_dataframe_dtypes,
    FilterOperator,
    GenericDataType,
)
from superset.utils.styled_excel import (
    resolve_sheet_name,
    StyledWorkbookWriter,
)

logger = logging.getLogger(__name__)

MAX_EXPORTED_TABLES = 10
MAX_STATE_FILTERS = 100
MAX_FILTER_VALUES = 1000
MAX_FILTER_STRING_LENGTH = 10_000


class DashboardXlsxNote(TypedDict):
    """Non-sensitive diagnostic included in the workbook notes sheet."""

    chart_id: int | str
    title: str
    reason: str


@dataclass(frozen=True)
class DashboardXlsxWorkItem:
    """One trusted saved Table chart and its deterministic sheet name."""

    chart: Slice
    sheet_name: str


@dataclass(frozen=True)
class DashboardXlsxExportResult:
    """Completed temporary workbook awaiting response lifecycle cleanup."""

    path: Path
    filename: str


_INVALID_FILTER_VALUE = object()
_FILTER_OPERATORS = {
    operator.value.casefold(): operator.value for operator in FilterOperator
}


def _safe_filter_value(value: object, depth: int = 0) -> object:
    if depth > 3:
        return _INVALID_FILTER_VALUE
    if value is None or isinstance(value, (bool, int)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else _INVALID_FILTER_VALUE
    if isinstance(value, str):
        return (
            value if len(value) <= MAX_FILTER_STRING_LENGTH else _INVALID_FILTER_VALUE
        )
    if isinstance(value, list) and len(value) <= MAX_FILTER_VALUES:
        values = [_safe_filter_value(item, depth + 1) for item in value]
        return (
            _INVALID_FILTER_VALUE
            if any(item is _INVALID_FILTER_VALUE for item in values)
            else values
        )
    return _INVALID_FILTER_VALUE


def _sanitize_filters(value: object) -> tuple[list[dict[str, object]], int]:
    if not isinstance(value, list):
        return [], int(value is not None)
    filters: list[dict[str, object]] = []
    dropped = 0
    for candidate in value[:MAX_STATE_FILTERS]:
        if not isinstance(candidate, dict):
            dropped += 1
            continue
        column = candidate.get("col")
        operator = candidate.get("op")
        canonical_operator = (
            _FILTER_OPERATORS.get(operator.casefold())
            if isinstance(operator, str)
            else None
        )
        filter_value = _safe_filter_value(candidate.get("val"))
        if (
            not isinstance(column, str)
            or not column
            or len(column) > 512
            or canonical_operator is None
            or filter_value is _INVALID_FILTER_VALUE
        ):
            dropped += 1
            continue
        sanitized: dict[str, object] = {
            "col": column,
            "op": canonical_operator,
            "val": filter_value,
            "isExtra": True,
        }
        if isinstance(candidate.get("grain"), str):
            sanitized["grain"] = candidate["grain"]
        filters.append(sanitized)
    dropped += max(0, len(value) - MAX_STATE_FILTERS)
    return filters, dropped


class DashboardFilterStateResolver:
    """Resolve untrusted Dashboard data masks against saved layout and scopes."""

    def __init__(self, dashboard: Dashboard, data_mask: object) -> None:
        self._layout = dashboard.position
        try:
            metadata = json.loads(dashboard.json_metadata or "{}")
        except (TypeError, json.JSONDecodeError):
            metadata = {}
        self._metadata = metadata if isinstance(metadata, dict) else {}
        self._data_mask = data_mask if isinstance(data_mask, dict) else {}
        self._chart_ids = {
            int(node["meta"]["chartId"])
            for node in self._layout.values()
            if isinstance(node, dict)
            and node.get("type") == "CHART"
            and isinstance(node.get("meta"), dict)
            and isinstance(node["meta"].get("chartId"), int)
        }
        self.dropped_state_count = 0

    def _mask(self, state_id: str | int) -> dict[str, object]:
        candidate = self._data_mask.get(str(state_id))
        return candidate if isinstance(candidate, dict) else {}

    def _targets(
        self,
        configuration: Mapping[str, object],
        *,
        source_chart_id: int | None = None,
    ) -> set[int]:
        saved_targets = configuration.get("chartsInScope")
        if isinstance(saved_targets, list):
            return {
                chart_id
                for candidate in saved_targets
                if isinstance(candidate, int)
                and (chart_id := candidate) in self._chart_ids
                and chart_id != source_chart_id
            }

        scope = configuration.get("scope")
        if scope == "global":
            return self._chart_ids - ({source_chart_id} if source_chart_id else set())
        if not isinstance(scope, dict):
            return set()
        root_path = {
            item for item in scope.get("rootPath", []) if isinstance(item, str)
        }
        excluded = {item for item in scope.get("excluded", []) if isinstance(item, int)}
        targets: set[int] = set()
        for node in self._layout.values():
            if not isinstance(node, dict) or node.get("type") != "CHART":
                continue
            meta = node.get("meta")
            node_chart_id = meta.get("chartId") if isinstance(meta, dict) else None
            parents = node.get("parents")
            if (
                isinstance(node_chart_id, int)
                and node_chart_id not in excluded
                and node_chart_id != source_chart_id
                and isinstance(parents, list)
                and any(parent in root_path for parent in parents)
            ):
                targets.add(node_chart_id)
        return targets

    def _extra_form_data(
        self, mask: Mapping[str, object], *, filters_only: bool = False
    ) -> dict[str, object]:
        raw_extra = mask.get("extraFormData")
        if not isinstance(raw_extra, dict):
            return {}
        filters, dropped = _sanitize_filters(raw_extra.get("filters"))
        self.dropped_state_count += dropped
        extra: dict[str, object] = {"filters": filters} if filters else {}
        if filters_only:
            return extra
        for key in ("time_range", "time_grain_sqla", "granularity_sqla"):
            value = raw_extra.get(key)
            if isinstance(value, str) and len(value) <= MAX_FILTER_STRING_LENGTH:
                extra[key] = value
        return extra

    def resolve(  # noqa: C901
        self, chart_id: int
    ) -> tuple[dict[str, object], list[dict[str, str]]]:
        """Return effective native/cross filters and target-owned alert references."""
        filters: list[dict[str, object]] = []
        scalar_extra: dict[str, object] = {}
        native_filters = self._metadata.get("native_filter_configuration")
        if isinstance(native_filters, list):
            for configuration in native_filters:
                if not isinstance(configuration, dict):
                    continue
                filter_id = configuration.get("id")
                if not isinstance(filter_id, str) or chart_id not in self._targets(
                    configuration
                ):
                    continue
                extra = self._extra_form_data(self._mask(filter_id))
                filters.extend(cast(list[dict[str, object]], extra.pop("filters", [])))
                scalar_extra.update(extra)

        if self._metadata.get("cross_filters_enabled", True) is not False:
            chart_configuration = self._metadata.get("chart_configuration")
            if isinstance(chart_configuration, dict):
                for source_id, configuration in chart_configuration.items():
                    try:
                        source_chart_id = int(source_id)
                    except (TypeError, ValueError):
                        continue
                    if not isinstance(configuration, dict):
                        continue
                    cross_filters = configuration.get("crossFilters")
                    if not isinstance(
                        cross_filters, dict
                    ) or chart_id not in self._targets(
                        cross_filters, source_chart_id=source_chart_id
                    ):
                        continue
                    extra = self._extra_form_data(
                        self._mask(source_chart_id), filters_only=True
                    )
                    filters.extend(
                        cast(list[dict[str, object]], extra.get("filters", []))
                    )

        alert_filters: list[dict[str, str]] = []
        own_state = self._mask(chart_id).get("ownState")
        references = (
            own_state.get("alertFilters") if isinstance(own_state, dict) else None
        )
        if isinstance(references, list):
            for reference in references[:50]:
                if (
                    isinstance(reference, dict)
                    and isinstance(reference.get("ruleId"), str)
                    and reference.get("level") in {"RED", "YELLOW", "GREEN"}
                ):
                    alert_filters.append(
                        {
                            "rule_id": reference["ruleId"],
                            "level": cast(str, reference["level"]),
                        }
                    )
                else:
                    self.dropped_state_count += 1
            self.dropped_state_count += max(0, len(references) - 50)

        return (
            {**scalar_extra, **({"filters": filters} if filters else {})},
            alert_filters,
        )


class ExportDashboardXlsxCommand(BaseCommand):
    """Validate a Dashboard and atomically export selected layout subtrees."""

    def __init__(
        self,
        dashboard: Dashboard,
        tab_ids: list[str],
        data_mask: object | None = None,
    ) -> None:
        self._dashboard = dashboard
        self._tab_ids = tab_ids
        self._data_mask = data_mask or {}

    def _collect_work_items(  # noqa: C901
        self,
    ) -> tuple[list[DashboardXlsxWorkItem], list[DashboardXlsxNote]]:
        layout = self._dashboard.position
        charts_by_id = {chart.id: chart for chart in self._dashboard.slices}
        notes: list[DashboardXlsxNote] = []
        ordered_charts: list[Slice] = []
        seen_slice_ids: set[int] = set()
        has_tabs = any(
            isinstance(node, dict) and node.get("type") == "TAB"
            for node in layout.values()
        )

        for tab_id in self._tab_ids:
            tab = layout.get(tab_id)
            if not isinstance(tab, dict):
                raise DashboardXlsxInvalidTabError()
            is_tab = tab.get("type") == "TAB"
            is_untabbed_root = (
                tab_id == "ROOT_ID" and tab.get("type") == "ROOT" and not has_tabs
            )
            if not is_tab and not is_untabbed_root:
                raise DashboardXlsxInvalidTabError()
            visited_nodes: set[str] = set()
            pending_nodes = [
                child_id
                for child_id in reversed(tab.get("children", []))
                if isinstance(child_id, str)
            ]
            while pending_nodes:
                node_id = pending_nodes.pop()
                if node_id in visited_nodes:
                    continue
                visited_nodes.add(node_id)
                node = layout.get(node_id)
                if not isinstance(node, dict):
                    continue
                if node.get("type") == "CHART":
                    meta = node.get("meta")
                    chart_id = meta.get("chartId") if isinstance(meta, dict) else None
                    title = (
                        str(meta.get("sliceName") or "")
                        if isinstance(meta, dict)
                        else ""
                    )
                    if not isinstance(chart_id, int):
                        continue
                    if chart_id in seen_slice_ids:
                        notes.append(
                            {
                                "chart_id": chart_id,
                                "title": title,
                                "reason": "Duplicate chart skipped",
                            }
                        )
                        continue
                    seen_slice_ids.add(chart_id)
                    chart = charts_by_id.get(chart_id)
                    if chart is None:
                        notes.append(
                            {
                                "chart_id": chart_id,
                                "title": title,
                                "reason": "Chart is unavailable",
                            }
                        )
                    elif chart.viz_type != "table":
                        notes.append(
                            {
                                "chart_id": chart_id,
                                "title": chart.slice_name or title,
                                "reason": "Unsupported visualization skipped",
                            }
                        )
                    else:
                        ordered_charts.append(chart)
                    continue
                pending_nodes.extend(
                    child_id
                    for child_id in reversed(node.get("children", []))
                    if isinstance(child_id, str)
                )

        if len(ordered_charts) > MAX_EXPORTED_TABLES:
            raise DashboardXlsxTableLimitExceededError()
        if not ordered_charts:
            raise DashboardXlsxNoTableError()

        used_names = {"_导出说明"}
        work_items: list[DashboardXlsxWorkItem] = []
        for chart in ordered_charts:
            sheet_name, changed = resolve_sheet_name(
                chart.slice_name,
                chart.id,
                used_names,
            )
            used_names.add(sheet_name)
            work_items.append(DashboardXlsxWorkItem(chart, sheet_name))
            if changed:
                notes.append(
                    {
                        "chart_id": chart.id,
                        "title": chart.slice_name or "",
                        "reason": "Sheet name normalized",
                    }
                )
        return work_items, notes

    @staticmethod
    def _row_limit(form_data: Mapping[str, object]) -> int:
        configured_limit = int(current_app.config["ROW_LIMIT"])
        try:
            saved_limit = int(cast(Any, form_data.get("row_limit") or configured_limit))
        except (TypeError, ValueError):
            saved_limit = configured_limit
        if saved_limit <= 0:
            saved_limit = configured_limit
        if configured_limit <= 0:
            return saved_limit
        return min(saved_limit, configured_limit)

    def _query_context(
        self,
        chart: Slice,
        extra_form_data: Mapping[str, object],
        alert_filters: list[dict[str, str]],
    ) -> QueryContext:
        try:
            saved_context = json.loads(chart.query_context or "")
        except (TypeError, json.JSONDecodeError) as ex:
            raise DashboardXlsxChartFailedError() from ex
        if not isinstance(saved_context, dict):
            raise DashboardXlsxChartFailedError()
        saved_queries = saved_context.get("queries")
        datasource = saved_context.get("datasource")
        if (
            not isinstance(saved_queries, list)
            or not saved_queries
            or not isinstance(saved_queries[0], dict)
            or not isinstance(datasource, dict)
        ):
            raise DashboardXlsxChartFailedError()

        form_data = chart.form_data
        main_query = copy.deepcopy(saved_queries[0])
        main_query["row_limit"] = self._row_limit(form_data)
        main_query["row_offset"] = 0
        main_query.pop("is_rowcount", None)
        main_query.pop("is_table_alert_totals", None)
        filters = list(main_query.get("filters") or [])
        filters.extend(
            cast(list[dict[str, object]], extra_form_data.get("filters", []))
        )
        main_query["filters"] = filters
        for key in ("time_range", "granularity_sqla"):
            if key in extra_form_data:
                main_query[key] = extra_form_data[key]
        if "time_grain_sqla" in extra_form_data:
            extras = dict(main_query.get("extras") or {})
            extras["time_grain_sqla"] = extra_form_data["time_grain_sqla"]
            main_query["extras"] = extras
        if alert_filters:
            main_query["alert_filters"] = alert_filters

        queries = [main_query]
        metrics = main_query.get("metrics")
        if form_data.get("show_totals") and isinstance(metrics, list) and metrics:
            totals_query = copy.deepcopy(main_query)
            totals_query["columns"] = (
                copy.deepcopy(main_query.get("columns") or []) if alert_filters else []
            )
            totals_query["row_limit"] = 0
            totals_query["row_offset"] = 0
            totals_query["post_processing"] = []
            totals_query["orderby"] = []
            totals_query.pop("order_desc", None)
            if alert_filters:
                totals_query["is_table_alert_totals"] = True
            queries.append(totals_query)

        return QueryContextFactory().create(
            current_slice=chart,
            datasource=cast(DatasourceDict, datasource),
            queries=queries,
            form_data=form_data,
            result_type=ChartDataResultType.FULL,
            result_format=ChartDataResultFormat.JSON,
            force=True,
        )

    @staticmethod
    def _execute_chart(
        query_context: QueryContext,
    ) -> tuple[pd.DataFrame, list[GenericDataType], dict[str, object] | None]:
        query_context.raise_for_access()
        main_payload = query_context.get_df_payload(query_context.queries[0])
        if main_payload.get("status") == QueryStatus.FAILED:
            raise DashboardXlsxChartFailedError()
        dataframe = main_payload["df"]
        column_types = extract_dataframe_dtypes(dataframe, query_context.datasource)
        totals: dict[str, object] | None = None
        if len(query_context.queries) > 1:
            totals_payload = query_context.get_df_payload(query_context.queries[1])
            if totals_payload.get("status") == QueryStatus.FAILED:
                raise DashboardXlsxChartFailedError()
            totals_df = totals_payload["df"]
            if not totals_df.empty:
                totals = cast(dict[str, object], totals_df.iloc[0].to_dict())
        return dataframe, column_types, totals

    def run(self) -> DashboardXlsxExportResult:
        work_items, notes = self._collect_work_items()
        state_resolver = DashboardFilterStateResolver(
            self._dashboard,
            self._data_mask,
        )
        temporary = tempfile.NamedTemporaryFile(
            prefix=f"superset-dashboard-{self._dashboard.id}-",
            suffix=".xlsx",
            delete=False,
        )
        temporary_path = Path(temporary.name)
        temporary.close()
        writer: StyledWorkbookWriter | None = None
        completed = False
        active_chart_id: int | None = None
        try:
            writer = StyledWorkbookWriter(temporary_path)
            for work_item in work_items:
                chart_id = work_item.chart.id
                if chart_id is None:
                    raise DashboardXlsxChartFailedError()
                active_chart_id = chart_id
                extra_form_data, alert_filters = state_resolver.resolve(chart_id)
                query_context = self._query_context(
                    work_item.chart,
                    extra_form_data,
                    alert_filters,
                )
                dataframe, column_types, totals = self._execute_chart(query_context)
                rules = TableRuleResolver(
                    work_item.chart,
                    cast(Any, query_context.datasource),
                ).resolve_styles([str(column) for column in dataframe.columns])
                column_config = work_item.chart.form_data.get("column_config")
                writer.write_dataframe(
                    work_item.sheet_name,
                    dataframe,
                    rules,
                    column_types=column_types,
                    column_config=(
                        cast(Mapping[str, object], column_config)
                        if isinstance(column_config, dict)
                        else None
                    ),
                    totals=totals,
                )
                del dataframe
            if notes:
                writer.write_notes(notes)
            writer.close()
            completed = True
            logger.info(
                "Dashboard XLSX export completed dashboard_id=%s tables=%s notes=%s "
                "dropped_state=%s invalid_colors=%s",
                self._dashboard.id,
                len(work_items),
                len(notes),
                state_resolver.dropped_state_count,
                writer.invalid_color_count,
            )
            return DashboardXlsxExportResult(
                temporary_path,
                f"dashboard_{self._dashboard.id}.xlsx",
            )
        except SupersetSecurityException:
            raise
        except (
            DashboardXlsxChartFailedError,
            DashboardXlsxInvalidTabError,
            DashboardXlsxNoTableError,
            DashboardXlsxTableLimitExceededError,
        ):
            raise
        except Exception as ex:
            logger.warning(
                "Dashboard XLSX chart failed chart_id=%s error_type=%s",
                active_chart_id,
                type(ex).__name__,
            )
            raise DashboardXlsxChartFailedError() from ex
        finally:
            if not completed:
                if writer is not None:
                    with suppress(Exception):
                        writer.close()
                temporary_path.unlink(missing_ok=True)

    def validate(self) -> None:
        """Validation is performed while resolving trusted saved Dashboard state."""
