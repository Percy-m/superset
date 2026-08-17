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
import logging
from typing import Any, Iterable, Literal, Optional

from flask import current_app as app

from superset.commands.dataset.exceptions import (
    DatasetSamplesFailedError,
    DatasetSamplesFeatureError,
)
from superset.common.chart_data import ChartDataResultType
from superset.common.query_context_factory import QueryContextFactory
from superset.common.utils.query_cache_manager import QueryCacheManager
from superset.constants import CacheRegion
from superset.daos.datasource import DatasourceDAO
from superset.utils import json
from superset.utils.core import FilterOperator, GenericDataType, QueryStatus

logger = logging.getLogger(__name__)

DetailMode = Literal["server", "bounded_client"]

CONFIGURABLE_DRILL_DETAIL_NULL_ORDER_KEY = "__configurable_drill_detail_null_ordering"
BOUNDED_CLIENT_MAX_ROWS = 1000
BOUNDED_CLIENT_QUERY_ROWS = BOUNDED_CLIENT_MAX_ROWS + 1
BOUNDED_CLIENT_MAX_CELLS = 50_000
BOUNDED_CLIENT_MAX_PAYLOAD_BYTES = 8 * 1024 * 1024
BOUNDED_CLIENT_MAX_CELL_BYTES = 1024 * 1024


def get_limit_clause(page: Optional[int], per_page: Optional[int]) -> dict[str, int]:
    samples_row_limit = app.config.get("SAMPLES_ROW_LIMIT", 1000)
    limit = samples_row_limit
    offset = 0

    if isinstance(page, int) and isinstance(per_page, int):
        limit = int(per_page)
        if limit < 0 or limit > samples_row_limit:
            # reset limit value if input is invalid
            limit = samples_row_limit

        offset = max((int(page) - 1) * limit, 0)

    return {"row_offset": offset, "row_limit": limit}


def replace_verbose_with_column(
    filters: list[dict[str, Any]],
    columns: Iterable[Any],
    verbose_attr: str = "verbose_name",
    column_attr: str = "column_name",
) -> None:
    """
    Replace filter 'col' values that match column verbose_name with the column_name.
    Operates in-place on the filters list

    Args:
        filters: List of filter dicts, each must have 'col' key.
        columns: Iterable of column objects with verbose_name and column_name.
        verbose_attr: Attribute name for verbose/label.
        column_attr: Attribute name for actual column name.
    """
    for f in filters:
        col_value = f.get("col")
        if col_value is None:
            logger.warning("Filter missing 'col' key: %s", f)
            continue

        match = None
        for col in columns:
            if not hasattr(col, verbose_attr) or not hasattr(col, column_attr):
                logger.warning(
                    "Column object %s missing expected attributes '%s' or '%s'",
                    col,
                    verbose_attr,
                    column_attr,
                )
                continue

            if getattr(col, verbose_attr) == col_value:
                match = getattr(col, column_attr)
                break

        if match:
            f["col"] = match


def _column_attribute(column: Any, attribute: str) -> Any:
    """Read a datasource column attribute from model or dictionary metadata."""
    if isinstance(column, dict):
        return column.get(attribute)
    return getattr(column, attribute, None)


def _sortable_physical_columns(columns: Iterable[Any]) -> list[str]:
    """Return trusted physical columns that support deterministic ordering."""
    sortable_types = {
        GenericDataType.NUMERIC,
        GenericDataType.STRING,
        GenericDataType.TEMPORAL,
        GenericDataType.BOOLEAN,
    }
    return [
        column_name
        for column in columns
        if (column_name := _column_attribute(column, "column_name"))
        and not _column_attribute(column, "expression")
        and _column_attribute(column, "is_active") is not False
        and _column_attribute(column, "type_generic") in sortable_types
    ]


def _prepare_configurable_detail_payload(
    payload: dict[str, Any],
    columns: Iterable[Any],
    detail_mode: DetailMode,
) -> dict[str, Any]:
    """Resolve search and stable ordering from trusted datasource metadata."""
    query_payload = dict(payload)
    query_payload["filters"] = list(payload.get("filters", []))
    query_payload["extras"] = {
        **(payload.get("extras") or {}),
        CONFIGURABLE_DRILL_DETAIL_NULL_ORDER_KEY: True,
    }
    search = query_payload.pop("search", None)
    columns = list(columns)
    sortable_columns = _sortable_physical_columns(columns)
    if not sortable_columns:
        raise DatasetSamplesFeatureError(
            "The dataset has no physical column that supports stable ordering.",
            "DRILL_DETAIL_STABLE_ORDER_UNAVAILABLE",
        )

    search_column_name: str | None = None
    search_value = search["value"].strip() if search else ""
    if search and search_value:
        if detail_mode != "server":
            raise DatasetSamplesFeatureError(
                "Structured search is only supported in server mode.",
                "DRILL_DETAIL_SEARCH_REQUIRES_SERVER_MODE",
                status=400,
            )
        search_column_name = search["column"]
        search_column = next(
            (
                column
                for column in columns
                if _column_attribute(column, "column_name") == search_column_name
            ),
            None,
        )
        if (
            search_column is None
            or _column_attribute(search_column, "expression")
            or _column_attribute(search_column, "is_active") is False
            or _column_attribute(search_column, "filterable") is False
            or _column_attribute(search_column, "type_generic")
            != GenericDataType.STRING
        ):
            raise DatasetSamplesFeatureError(
                "The selected search column is not a filterable physical text column.",
                "DRILL_DETAIL_INVALID_SEARCH_COLUMN",
                status=400,
            )
        query_payload["filters"].append(
            {
                "col": search_column_name,
                "op": FilterOperator.ILIKE,
                "val": f"{search_value}%",
            }
        )

    ordered_columns = (
        [search_column_name] if search_column_name is not None else []
    ) + [column for column in sortable_columns if column != search_column_name]
    query_payload["orderby"] = [(column, True) for column in ordered_columns]
    return query_payload


def _json_utf8_size(value: Any) -> int:
    """Return the UTF-8 byte size of a value in its JSON representation."""
    return len(
        json.simplejson.dumps(
            value,
            default=json.json_iso_dttm_ser,
            ensure_ascii=False,
            ignore_nan=True,
            separators=(",", ":"),
        ).encode("utf-8")
    )


def measure_detail_result(sample_data: dict[str, Any]) -> dict[str, int]:
    """Measure detail rows using their UTF-8 JSON representation."""
    rows = sample_data.get("data", [])
    columns = sample_data.get("colnames", [])
    cell_sizes = [
        _json_utf8_size(row.get(column)) for row in rows for column in columns
    ]
    return {
        "row_count": len(rows),
        "cell_count": len(rows) * len(columns),
        "serialized_bytes": _json_utf8_size({"data": rows, "colnames": columns}),
        "largest_cell_bytes": max(cell_sizes, default=0),
    }


def validate_bounded_client_result(sample_data: dict[str, Any]) -> dict[str, int]:
    """Reject bounded-client results that exceed any configured safety bound."""
    bounds = measure_detail_result(sample_data)
    if bounds["row_count"] > BOUNDED_CLIENT_MAX_ROWS:
        raise DatasetSamplesFeatureError(
            "The drill detail result exceeds the 1,000 row client limit.",
            "DRILL_DETAIL_ROW_LIMIT_EXCEEDED",
        )
    if bounds["cell_count"] > BOUNDED_CLIENT_MAX_CELLS:
        raise DatasetSamplesFeatureError(
            "The drill detail result exceeds the 50,000 cell client limit.",
            "DRILL_DETAIL_CELL_LIMIT_EXCEEDED",
        )
    if bounds["largest_cell_bytes"] > BOUNDED_CLIENT_MAX_CELL_BYTES:
        raise DatasetSamplesFeatureError(
            "A drill detail cell exceeds the 1 MiB client limit.",
            "DRILL_DETAIL_CELL_SIZE_EXCEEDED",
        )
    if bounds["serialized_bytes"] > BOUNDED_CLIENT_MAX_PAYLOAD_BYTES:
        raise DatasetSamplesFeatureError(
            "The drill detail result exceeds the 8 MiB client payload limit.",
            "DRILL_DETAIL_PAYLOAD_LIMIT_EXCEEDED",
        )
    return bounds


def _first_query_payload(query_context: Any) -> dict[str, Any]:
    """Return the first query payload using the legacy samples error contract."""
    try:
        return query_context.get_payload()["queries"][0]
    except (IndexError, KeyError) as exc:
        raise DatasetSamplesFailedError from exc


def _get_configurable_drill_samples(  # pylint: disable=too-many-arguments
    datasource: Any,
    force: bool,
    page: int,
    per_page: int,
    payload: dict[str, Any],
    form_data: dict[str, int] | None,
    detail_mode: DetailMode,
) -> dict[str, Any]:
    """Fetch configurable drill-detail data using server or bounded-client mode."""
    replace_verbose_with_column(payload.get("filters", []), datasource.columns)
    query_payload = _prepare_configurable_detail_payload(
        payload, datasource.columns, detail_mode
    )
    limit_clause = (
        {"row_offset": 0, "row_limit": BOUNDED_CLIENT_QUERY_ROWS}
        if detail_mode == "bounded_client"
        else get_limit_clause(page, per_page)
    )
    samples_instance = QueryContextFactory().create(
        datasource={"type": datasource.type, "id": datasource.id},
        queries=[{**query_payload, **limit_clause}],
        form_data=form_data,
        result_type=ChartDataResultType.DRILL_DETAIL,
        force=force,
    )

    count_star_instance = None
    if detail_mode == "server":
        count_payload = dict(query_payload)
        count_payload.pop("orderby", None)
        count_star_instance = QueryContextFactory().create(
            datasource={"type": datasource.type, "id": datasource.id},
            queries=[
                {
                    **count_payload,
                    "metrics": [
                        {
                            "expressionType": "SQL",
                            "sqlExpression": "COUNT(*)",
                            "label": "COUNT(*)",
                        }
                    ],
                }
            ],
            form_data=form_data,
            result_type=ChartDataResultType.FULL,
            force=force,
        )

    samples_instance.raise_for_access()
    if count_star_instance is not None:
        count_star_instance.raise_for_access()
        count_star_data = _first_query_payload(count_star_instance)
        if count_star_data.get("status") == QueryStatus.FAILED:
            raise DatasetSamplesFailedError(str(count_star_data.get("error") or ""))
    else:
        count_star_data = None

    sample_data = _first_query_payload(samples_instance)
    if sample_data.get("status") == QueryStatus.FAILED:
        if count_star_data is not None:
            QueryCacheManager.delete(count_star_data.get("cache_key"), CacheRegion.DATA)
        raise DatasetSamplesFailedError(str(sample_data.get("error") or ""))

    if detail_mode == "bounded_client":
        bounds = validate_bounded_client_result(sample_data)
        total_count = len(sample_data.get("data", []))
    else:
        assert count_star_data is not None
        bounds = measure_detail_result(sample_data)
        try:
            total_count = count_star_data["data"][0]["COUNT(*)"]
        except (IndexError, KeyError) as exc:
            raise DatasetSamplesFailedError from exc
    sample_data.update(
        {
            "bounds": bounds,
            "detail_mode": detail_mode,
            "page": page,
            "per_page": per_page,
            "total_count": total_count,
        }
    )
    return sample_data


def get_samples(  # pylint: disable=too-many-arguments
    datasource_type: str,
    datasource_id: int,
    force: bool = False,
    page: int = 1,
    per_page: int = 1000,
    payload: dict[str, Any] | None = None,
    dashboard_id: int | None = None,
    detail_mode: DetailMode | None = None,
) -> dict[str, Any]:
    datasource = DatasourceDAO.get_datasource(
        datasource_type=datasource_type,
        database_id_or_uuid=str(datasource_id),
    )

    form_data = {"dashboardId": dashboard_id} if dashboard_id else None
    if detail_mode is not None:
        return _get_configurable_drill_samples(
            datasource=datasource,
            force=force,
            page=page,
            per_page=per_page,
            payload=payload or {},
            form_data=form_data,
            detail_mode=detail_mode,
        )
    limit_clause = get_limit_clause(page, per_page)

    # todo(yongjie): Constructing count(*) and samples in the same query_context,
    if payload is None:
        # constructing samples query
        samples_instance = QueryContextFactory().create(
            datasource={
                "type": datasource.type,
                "id": datasource.id,
            },
            queries=[limit_clause],
            form_data=form_data,
            result_type=ChartDataResultType.SAMPLES,
            force=force,
        )
    else:
        # Use column names replacing verbose column names(Label)
        replace_verbose_with_column(payload.get("filters", []), datasource.columns)

        # constructing drill detail query
        # When query_type == 'samples' the `time filter` will be removed,
        # so it is not applicable drill detail query
        samples_instance = QueryContextFactory().create(
            datasource={
                "type": datasource.type,
                "id": datasource.id,
            },
            queries=[{**payload, **limit_clause}],
            form_data=form_data,
            result_type=ChartDataResultType.DRILL_DETAIL,
            force=force,
        )

    # constructing count(*) query
    count_star_metric = {
        "metrics": [
            {
                "expressionType": "SQL",
                "sqlExpression": "COUNT(*)",
                "label": "COUNT(*)",
            }
        ]
    }
    count_star_instance = QueryContextFactory().create(
        datasource={
            "type": datasource.type,
            "id": datasource.id,
        },
        queries=[{**payload, **count_star_metric} if payload else count_star_metric],
        form_data=form_data,
        result_type=ChartDataResultType.FULL,
        force=force,
    )

    try:
        # Enforce access control before fetching data.
        # This prevents users with "can samples on Datasource" permission from
        # reading samples from datasets they don't have access to.
        samples_instance.raise_for_access()
        count_star_instance.raise_for_access()

        count_star_data = count_star_instance.get_payload()["queries"][0]

        if count_star_data.get("status") == QueryStatus.FAILED:
            raise DatasetSamplesFailedError(count_star_data.get("error"))

        sample_data = samples_instance.get_payload()["queries"][0]

        if sample_data.get("status") == QueryStatus.FAILED:
            QueryCacheManager.delete(count_star_data.get("cache_key"), CacheRegion.DATA)
            raise DatasetSamplesFailedError(sample_data.get("error"))

        sample_data["page"] = page
        sample_data["per_page"] = per_page
        sample_data["total_count"] = count_star_data["data"][0]["COUNT(*)"]
        return sample_data
    except (IndexError, KeyError) as exc:
        raise DatasetSamplesFailedError from exc
