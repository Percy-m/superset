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
from __future__ import annotations

from typing import Any, cast, TYPE_CHECKING

from flask import current_app

from superset import is_feature_enabled, security_manager
from superset.common.chart_data import ChartDataResultFormat, ChartDataResultType
from superset.common.query_context import QueryContext
from superset.common.query_object import QueryObject
from superset.common.query_object_factory import QueryObjectFactory
from superset.common.table_alerts import (
    ALERT_FILTER_FINGERPRINT_EXTRA_KEY,
    ALERT_FILTERS_EXTRA_KEY,
    ALERT_TOTALS_EXTRA_KEY,
    INVALID_ALERT_RULE_MESSAGE,
    STYLED_XLSX_UNSUPPORTED_MESSAGE,
    TableRuleResolver,
)
from superset.daos.chart import ChartDAO
from superset.daos.dashboard import DashboardDAO
from superset.daos.datasource import DatasourceDAO
from superset.exceptions import QueryObjectValidationError
from superset.explorables.base import Explorable
from superset.models.slice import Slice
from superset.superset_typing import Column
from superset.utils.core import DatasourceDict, DatasourceType, is_adhoc_column

if TYPE_CHECKING:
    from superset.connectors.sqla.models import SqlaTable


def create_query_object_factory() -> QueryObjectFactory:
    return QueryObjectFactory(current_app.config, DatasourceDAO())


class QueryContextFactory:  # pylint: disable=too-few-public-methods
    _query_object_factory: QueryObjectFactory

    def __init__(self) -> None:
        self._query_object_factory = create_query_object_factory()

    def create(  # pylint: disable=too-many-arguments
        self,
        *,
        current_slice: Slice | None = None,
        datasource: DatasourceDict,
        queries: list[dict[str, Any]],
        form_data: dict[str, Any] | None = None,
        result_type: ChartDataResultType | None = None,
        result_format: ChartDataResultFormat | None = None,
        result_format_options: dict[str, Any] | None = None,
        force: bool = False,
        custom_cache_timeout: int | None = None,
    ) -> QueryContext:
        datasource_model_instance = None
        if datasource:
            datasource_model_instance = self._convert_to_model(datasource)

        slice_ = None
        if isinstance(current_slice, Slice):
            slice_ = current_slice
        elif form_data and form_data.get("slice_id") is not None:
            slice_ = self._get_slice(
                form_data.get("slice_id"),
                form_data,
                datasource_model_instance,
            )

        result_type = result_type or ChartDataResultType.FULL
        result_format = result_format or ChartDataResultFormat.JSON
        result_format_options = result_format_options or {}

        styled_xlsx_requested = (
            is_feature_enabled("STYLED_XLSX_EXPORT")
            and result_format_options.get("styled") is True
        )
        valid_styled_xlsx = styled_xlsx_requested and (
            result_format == ChartDataResultFormat.XLSX
            and isinstance(slice_, Slice)
            and slice_.viz_type == "table"
            and slice_.datasource_type == "table"
            and datasource_model_instance is not None
            and slice_.datasource_id == datasource_model_instance.id
        )
        if styled_xlsx_requested and not valid_styled_xlsx:
            raise QueryObjectValidationError(STYLED_XLSX_UNSUPPORTED_MESSAGE)
        if (
            result_format_options.get("xlsx_primary_query_only") is True
            and not valid_styled_xlsx
        ):
            raise QueryObjectValidationError(STYLED_XLSX_UNSUPPORTED_MESSAGE)

        # The server pagination var is extracted from form data as the
        # row limit for server pagination is more
        # This particular flag server_pagination only exists for table viz type
        server_pagination = (
            bool(form_data.get("server_pagination")) if form_data else False
        )

        resolved_queries = [
            self._resolve_table_alerts(query, slice_, datasource_model_instance)
            for query in queries
        ]
        queries_ = [
            self._process_query_object(
                datasource_model_instance,
                form_data,
                self._query_object_factory.create(
                    result_type,
                    datasource=datasource,
                    server_pagination=server_pagination,
                    **query_obj,
                ),
            )
            for query_obj in resolved_queries
        ]
        cache_values = {
            "datasource": datasource,
            "queries": [
                self._query_for_cache(query, resolved_query)
                for query, resolved_query in zip(
                    queries,
                    resolved_queries,
                    strict=True,
                )
            ],
            "result_type": result_type,
            "result_format": result_format,
            "result_format_options": result_format_options,
        }
        return QueryContext(
            datasource=datasource_model_instance,
            queries=queries_,
            slice_=slice_,
            form_data=form_data,
            result_type=result_type,
            result_format=result_format,
            result_format_options=result_format_options,
            force=force,
            custom_cache_timeout=custom_cache_timeout,
            cache_values=cache_values,
        )

    def _resolve_table_alerts(
        self,
        query: dict[str, Any],
        slice_: Slice | None,
        datasource: Explorable | None,
    ) -> dict[str, Any]:
        """Replace untrusted alert references with canonical server-owned rules."""
        resolved_query = dict(query)
        references = resolved_query.pop("alert_filters", None)
        wants_totals = resolved_query.pop("is_table_alert_totals", False)
        had_extras = "extras" in resolved_query
        extras = dict(resolved_query.get("extras") or {})
        for internal_key in (
            ALERT_FILTERS_EXTRA_KEY,
            ALERT_FILTER_FINGERPRINT_EXTRA_KEY,
            ALERT_TOTALS_EXTRA_KEY,
        ):
            extras.pop(internal_key, None)
        if extras or had_extras:
            resolved_query["extras"] = extras
        else:
            resolved_query.pop("extras", None)
        if not is_feature_enabled("TABLE_ALERT_FILTERS") or not references:
            return resolved_query
        if (
            not isinstance(slice_, Slice)
            or datasource is None
            or not hasattr(datasource, "columns")
            or not hasattr(datasource, "metrics")
        ):
            # TableRuleResolver intentionally returns one non-sensitive error shape.
            raise QueryObjectValidationError(INVALID_ALERT_RULE_MESSAGE)

        groups, fingerprint = TableRuleResolver(
            slice_, cast("SqlaTable", datasource)
        ).resolve(
            references=references,
            query=resolved_query,
        )
        canonical_query = dict(resolved_query)
        extras = dict(resolved_query.get("extras") or {})
        extras[ALERT_FILTERS_EXTRA_KEY] = groups
        extras[ALERT_FILTER_FINGERPRINT_EXTRA_KEY] = fingerprint
        if wants_totals:
            extras[ALERT_TOTALS_EXTRA_KEY] = True
        canonical_query["extras"] = extras
        return canonical_query

    @staticmethod
    def _query_for_cache(
        query: dict[str, Any],
        resolved_query: dict[str, Any],
    ) -> dict[str, Any]:
        """Keep canonical alert references so async cache reads can revalidate them."""
        cache_query = dict(resolved_query)
        extras = resolved_query.get("extras") or {}
        if ALERT_FILTERS_EXTRA_KEY not in extras:
            return cache_query

        references = {
            (str(reference["rule_id"]), reference["level"]): {
                "rule_id": str(reference["rule_id"]),
                "level": reference["level"],
            }
            for reference in query.get("alert_filters") or []
        }
        cache_query["alert_filters"] = [references[key] for key in sorted(references)]
        if query.get("is_table_alert_totals") is True:
            cache_query["is_table_alert_totals"] = True
        return cache_query

    def _convert_to_model(self, datasource: DatasourceDict) -> Explorable:
        return DatasourceDAO.get_datasource(
            datasource_type=DatasourceType(datasource["type"]),
            database_id_or_uuid=datasource["id"],
        )

    def _get_slice(
        self,
        slice_id: Any,
        form_data: dict[str, Any],
        datasource: Explorable | None,
    ) -> Slice | None:
        if (chart := ChartDAO.find_by_id(slice_id)) is not None:
            return chart

        # ChartFilter only recognizes direct datasource grants. Embedded guests and
        # Dashboard RBAC users may instead receive access through a Dashboard, so
        # fall back only to a chart inside an already authorized Dashboard. Native
        # Filter QueryContexts cannot use this path to borrow unrelated chart config.
        is_guest_user = security_manager.is_guest_user()
        dashboard_rbac_enabled = is_feature_enabled("DASHBOARD_RBAC")
        if (
            form_data.get("type") == "NATIVE_FILTER"
            or form_data.get("dashboardId") is None
            or datasource is None
            or not (is_guest_user or dashboard_rbac_enabled)
        ):
            return None

        dashboard = DashboardDAO.find_by_id(
            form_data["dashboardId"],
            skip_base_filter=True,
        )
        if (
            dashboard is None
            or (not is_guest_user and not dashboard.roles)
            or not security_manager.can_access_dashboard(dashboard)
        ):
            return None

        return next(
            (
                dashboard_chart
                for dashboard_chart in dashboard.slices
                if str(dashboard_chart.id) == str(slice_id)
                and dashboard_chart.datasource_id == datasource.id
            ),
            None,
        )

    def _process_query_object(
        self,
        datasource: Explorable,
        form_data: dict[str, Any] | None,
        query_object: QueryObject,
    ) -> QueryObject:
        self._apply_granularity(query_object, form_data, datasource)
        self._apply_filters(query_object)
        self._add_tooltip_columns(query_object, form_data)
        self._add_currency_column(query_object, form_data, datasource)
        return query_object

    def _add_tooltip_columns(
        self,
        query_object: QueryObject,
        form_data: dict[str, Any] | None,
    ) -> None:
        """Add tooltip columns to the query object."""
        if not form_data:
            return

        tooltip_columns = self._extract_tooltip_columns(form_data)
        if not tooltip_columns:
            return

        existing_columns = self._get_existing_column_names(query_object.columns)
        self._append_missing_tooltip_columns(
            query_object, tooltip_columns, existing_columns
        )

    def _get_existing_column_names(self, columns: list[Column]) -> set[str]:
        """Extract column names from existing columns."""
        column_names: set[str] = set()
        for col in columns:
            if isinstance(col, dict):
                column_name = col.get("column_name")
                if column_name and isinstance(column_name, str):
                    column_names.add(column_name)
            elif isinstance(col, str):
                column_names.add(col)
        return column_names

    def _append_missing_tooltip_columns(
        self,
        query_object: QueryObject,
        tooltip_columns: list[str],
        existing_columns: set[str],
    ) -> None:
        """Append missing tooltip columns to query object."""
        for col in tooltip_columns:
            if col not in existing_columns:
                column_def = self._find_column_definition(query_object, col)
                query_object.columns.append(column_def or col)

    def _find_column_definition(
        self, query_object: QueryObject, column_name: str
    ) -> Any | None:
        """Find column definition from datasource."""
        if not (
            query_object.datasource and hasattr(query_object.datasource, "columns")
        ):
            return None

        return next(
            (
                c
                for c in query_object.datasource.columns
                if c.column_name == column_name
            ),
            None,
        )

    def _extract_tooltip_columns(self, form_data: dict[str, Any]) -> list[str]:
        """Extract column names from tooltip_contents configuration."""
        tooltip_columns = []
        if tooltip_contents := form_data.get("tooltip_contents", []):
            for item in tooltip_contents:
                if isinstance(item, str):
                    tooltip_columns.append(item)
                elif isinstance(item, dict) and item.get("item_type") == "column":
                    column_name = item.get("column_name")
                    if column_name:
                        tooltip_columns.append(column_name)
        return tooltip_columns

    def _add_currency_column(
        self,
        query_object: QueryObject,
        form_data: dict[str, Any] | None,
        datasource: Explorable,
    ) -> None:
        """
        Add currency_code_column to the query for pivot_table_v2 cell-level formatting.

        When currency_format.symbol is 'AUTO', injects the datasource's
        currency_code_column into query columns for per-cell currency formatting.
        """
        if not form_data or not query_object.columns:
            return

        if form_data.get("viz_type") != "pivot_table_v2":
            return

        currency_format = form_data.get("currency_format", {})
        if not (
            isinstance(currency_format, dict)
            and currency_format.get("symbol") == "AUTO"
        ):
            return

        currency_column = getattr(datasource, "currency_code_column", None)
        if not currency_column:
            return

        existing_columns = self._get_existing_column_names(query_object.columns)
        if currency_column not in existing_columns:
            query_object.columns.append(currency_column)

    def _apply_granularity(  # noqa: C901
        self,
        query_object: QueryObject,
        form_data: dict[str, Any] | None,
        datasource: Explorable,
    ) -> None:
        temporal_columns = {
            column["column_name"] if isinstance(column, dict) else column.column_name
            for column in datasource.columns
            if (column["is_dttm"] if isinstance(column, dict) else column.is_dttm)
        }
        x_axis = form_data and form_data.get("x_axis")

        if granularity := query_object.granularity:
            filter_to_remove = None
            if is_adhoc_column(x_axis):  # type: ignore
                x_axis = x_axis.get("sqlExpression")
            if isinstance(x_axis, dict) and "sqlExpression" in x_axis:
                x_axis = x_axis.get("sqlExpression")
            if x_axis and x_axis in temporal_columns:
                filter_to_remove = x_axis
                x_axis_column = next(
                    (
                        column
                        for column in query_object.columns
                        if column == x_axis
                        or (
                            isinstance(column, dict)
                            and column["sqlExpression"] == x_axis
                        )
                    ),
                    None,
                )
                # Replaces x-axis column values with granularity
                if x_axis_column:
                    if isinstance(x_axis_column, dict):
                        x_axis_column["sqlExpression"] = granularity
                        x_axis_column["label"] = granularity
                    else:
                        query_object.columns = [
                            granularity if column == x_axis_column else column
                            for column in query_object.columns
                        ]
                    for post_processing in query_object.post_processing:
                        if post_processing.get("operation") == "pivot":
                            post_processing["options"]["index"] = [granularity]

            # If no temporal x-axis, then get the default temporal filter
            if not filter_to_remove:
                temporal_filters = [
                    filter["col"]
                    for filter in query_object.filter
                    if filter["op"] == "TEMPORAL_RANGE"
                ]
                if len(temporal_filters) > 0:
                    # Use granularity if it's already in the filters
                    if granularity in temporal_filters:
                        filter_to_remove = granularity
                    else:
                        # Use the first temporal filter
                        filter_to_remove = temporal_filters[0]

            # Removes the temporal filter which may be an x-axis or
            # another temporal filter. A new filter based on the value of
            # the granularity will be added later in the code.
            # In practice, this is replacing the previous default temporal filter.
            if is_adhoc_column(filter_to_remove):  # type: ignore
                filter_to_remove = filter_to_remove.get("sqlExpression")

            if filter_to_remove:
                query_object.filter = [
                    filter
                    for filter in query_object.filter
                    if filter["col"] != filter_to_remove
                ]

    def _apply_filters(self, query_object: QueryObject) -> None:
        if query_object.time_range:
            for filter_object in query_object.filter:
                if filter_object["op"] == "TEMPORAL_RANGE":
                    filter_object["val"] = query_object.time_range
