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
"""Exercise complete color QueryContexts and their actual SQL call budget."""

import copy
from dataclasses import replace
from types import SimpleNamespace
from typing import Any
from unittest.mock import Mock

import pandas as pd
import pytest
from flask import Flask
from flask_caching import Cache
from marshmallow import ValidationError

from superset.common import table_color_query, table_color_snapshot
from superset.common.chart_data import ChartDataResultFormat, ChartDataResultType
from superset.common.db_query_status import QueryStatus
from superset.common.query_context import QueryContext
from superset.common.query_object import QueryObject
from superset.common.table_color_context import TableColorContext
from superset.common.table_color_schema import (
    TableColorFilterError,
    TableColorFilterSchema,
)


class ColorQueryHarness:
    """Use the real processor, post-processing and cache with an observed SQL stub."""

    def __init__(
        self,
        app: Flask,
        monkeypatch: pytest.MonkeyPatch,
        *,
        server: bool = True,
        rows: int = 6,
        page_size: int = 2,
    ) -> None:
        app.config["TABLE_ALERT_FILTER_ALLOW_IN_MEMORY_CACHE"] = True
        self.cache = Cache(app, config={"CACHE_TYPE": "SimpleCache"})
        monkeypatch.setattr(
            table_color_snapshot,
            "cache_manager",
            SimpleNamespace(data_cache=self.cache),
        )
        self.data = pd.DataFrame(
            {
                "region": [f"r{i}" for i in range(rows)],
                "amount": list(range(1, rows + 1)),
            }
        )
        self.form_data: dict[str, Any] = {
            "viz_type": "table",
            "query_mode": "aggregate",
            "row_limit": 50000,
            "server_pagination": server,
            "server_page_length": page_size,
            "metrics": ["amount"],
            "groupby": ["region"],
            "show_cell_bars": False,
            "conditional_formatting": [
                {
                    "column": "amount",
                    "operator": ">",
                    "targetValue": 3,
                    "colorScheme": "colorSuccess",
                    "useGradient": False,
                    "filterable": True,
                }
            ],
        }
        self.trusted = TableColorContext(self.form_data, 50000, "owner-1", {}, None)
        self.datasource = Mock(
            columns=[], columns_types={}, metrics=[], cache_timeout=300
        )
        queries = [
            QueryObject(
                columns=["region"],
                metrics=["amount"],
                row_limit=page_size if server else 50000,
            )
        ]
        if server:
            queries.append(
                QueryObject(
                    columns=["region"],
                    metrics=["amount"],
                    row_limit=50000,
                    is_rowcount=True,
                )
            )
        self.context = QueryContext(
            datasource=self.datasource,
            queries=queries,
            slice_=None,
            form_data=self.form_data,
            result_type=ChartDataResultType.FULL,
            result_format=ChartDataResultFormat.JSON,
            cache_values={"queries": [{} for _ in queries]},
            table_color_filter={"version": 2, "selections": []},
        )
        monkeypatch.setattr(self.context, "raise_for_access", Mock())
        monkeypatch.setattr(
            self.context, "get_df_payload", Mock(side_effect=self.fetch)
        )
        self.rls = "rls-1"
        monkeypatch.setattr(
            self.context,
            "query_cache_key",
            Mock(side_effect=lambda query: query.cache_key(rls=self.rls)),
        )
        monkeypatch.setattr(
            table_color_query.TableColorContext,
            "resolve",
            Mock(side_effect=lambda *_: self.trusted),
        )
        monkeypatch.setattr(
            table_color_query,
            "security_manager",
            SimpleNamespace(is_guest_user=lambda: False),
        )
        self.calls: list[dict[str, Any]] = []

    def fetch(self, query: QueryObject, *args: Any, **kwargs: Any) -> dict[str, Any]:
        """Record each independent SQL request and honor its limit and offset."""
        self.calls.append(copy.deepcopy(dict(query.to_dict())))
        if query.is_rowcount:
            data = pd.DataFrame({"rowcount": [len(self.data)]})
        elif not query.columns:
            data = pd.DataFrame({"amount": [self.data.amount.sum()]})
        else:
            stop = query.row_offset + query.row_limit if query.row_limit else None
            data = self.data.iloc[query.row_offset : stop].copy()
        return {
            "df": data,
            "status": QueryStatus.SUCCESS,
            "error": None,
            "is_cached": False,
            "query": "SELECT fixture",
            "sql_rowcount": len(data),
            "applied_filter_columns": [],
            "rejected_filter_columns": [],
        }

    def select(self, snapshot_id: str, column: str = "amount") -> None:
        """Select the green rows from one immutable result."""
        self.context.table_color_filter = {
            "version": 2,
            "snapshot_id": snapshot_id,
            "selections": [{"column": column, "colors": ["GREEN"]}],
        }
        self.context.queries[0].row_offset = 0

    def result(self) -> dict[str, Any]:
        return self.context.get_payload()["queries"][0]


@pytest.mark.parametrize("server", [False, True])
def test_query_color_selection_clear_and_pagination_never_repeat_sql(
    app: Flask, monkeypatch: pytest.MonkeyPatch, server: bool
) -> None:
    harness = ColorQueryHarness(app, monkeypatch, server=server)
    original = harness.result()
    metadata = original["table_color_metadata"]
    assert metadata["baseline_rowcount"] == 6
    assert metadata["color_counts"] == {
        "region": {"GREEN": 0, "YELLOW": 0, "RED": 0},
        "amount": {"GREEN": 3, "YELLOW": 0, "RED": 0},
    }
    assert len(harness.calls) == 1
    harness.select(metadata["snapshot_id"])
    selected = harness.result()
    assert selected["table_color_metadata"]["filtered_rowcount"] == 3
    assert selected["table_color_metadata"]["color_counts"] == metadata["color_counts"]
    assert [row["amount"] for row in selected["data"]] == (
        [4, 5] if server else [4, 5, 6]
    )
    assert selected["table_color_metadata"]["totals"] == {"amount": 15}
    assert selected["table_color_metadata"]["row_indices"] == (
        [3, 4] if server else [3, 4, 5]
    )
    if server:
        harness.context.queries[0].row_offset = 2
        last_page = harness.result()
        assert [row["amount"] for row in last_page["data"]] == [6]
        assert last_page["table_color_metadata"]["row_indices"] == [5]
        assert (
            last_page["table_color_metadata"]["color_counts"]
            == metadata["color_counts"]
        )
    assert harness.context.table_color_filter is not None
    harness.context.table_color_filter["selections"] = []
    harness.context.queries[0].row_offset = 0
    cleared = harness.result()
    assert cleared["data"] == original["data"]
    assert cleared["table_color_metadata"]["color_counts"] == metadata["color_counts"]
    assert len(harness.calls) == 1


@pytest.mark.parametrize("server", [False, True])
def test_color_counts_ignore_selections_in_every_column(
    app: Flask, monkeypatch: pytest.MonkeyPatch, server: bool
) -> None:
    """Every menu counts the complete baseline, including other-column rejects."""
    harness = ColorQueryHarness(app, monkeypatch, server=server)
    harness.data["balance"] = [6, 5, 4, 3, 2, 1]
    harness.form_data["metrics"].append("balance")
    harness.context.queries[0].metrics = ["amount", "balance"]
    harness.form_data["conditional_formatting"].append(
        {
            **harness.form_data["conditional_formatting"][0],
            "column": "balance",
            "operator": "<",
            "colorScheme": "colorError",
        }
    )
    original = harness.result()["table_color_metadata"]
    expected = {
        "region": {"GREEN": 0, "YELLOW": 0, "RED": 0},
        "amount": {"GREEN": 3, "YELLOW": 0, "RED": 0},
        "balance": {"GREEN": 0, "YELLOW": 0, "RED": 2},
    }
    assert original["color_counts"] == expected
    harness.select(original["snapshot_id"])
    assert harness.result()["table_color_metadata"]["color_counts"] == expected
    assert harness.context.table_color_filter is not None
    harness.context.table_color_filter["selections"].append(
        {"column": "balance", "colors": ["RED"]}
    )
    selected = harness.result()
    assert [row["amount"] for row in selected["data"]] == [5, 6]
    assert selected["table_color_metadata"]["color_counts"] == expected
    harness.context.table_color_filter["selections"] = [
        {"column": "balance", "colors": ["GREEN"]}
    ]
    empty = harness.result()
    assert empty["data"] == []
    assert empty["table_color_metadata"]["color_counts"] == expected
    assert len(harness.calls) == 1


@pytest.mark.parametrize("server", [False, True])
@pytest.mark.parametrize("rows", [0, 3])
def test_color_counts_include_zeros_for_empty_or_uncolored_results(
    app: Flask, monkeypatch: pytest.MonkeyPatch, server: bool, rows: int
) -> None:
    """An empty catalog reports real zeros instead of missing-count metadata."""
    harness = ColorQueryHarness(app, monkeypatch, server=server, rows=rows)
    metadata = harness.result()["table_color_metadata"]
    assert metadata["status"] == "ready"
    assert metadata["color_counts"] == {
        "region": {"GREEN": 0, "YELLOW": 0, "RED": 0},
        "amount": {"GREEN": 0, "YELLOW": 0, "RED": 0},
    }
    assert len(harness.calls) == 1


@pytest.mark.parametrize("server", [False, True])
def test_color_counts_include_explicit_bars_but_not_default_bars(
    app: Flask, monkeypatch: pytest.MonkeyPatch, server: bool
) -> None:
    """Identical-looking default bars cannot add to explicit formatter counts."""
    harness = ColorQueryHarness(app, monkeypatch, server=server)
    harness.form_data["show_cell_bars"] = True
    harness.form_data["conditional_formatting"][0]["objectFormatting"] = "CELL_BAR"
    original = harness.result()["table_color_metadata"]
    assert original["color_counts"]["amount"] == {
        "GREEN": 3,
        "YELLOW": 0,
        "RED": 0,
    }
    assert original["styles"][0]["amount"]["cellBar"]
    assert original["styles"][0]["amount"]["colors"] == []
    harness.select(original["snapshot_id"])
    selected = harness.result()["table_color_metadata"]
    assert selected["filtered_rowcount"] == 3
    assert selected["color_counts"] == original["color_counts"]
    assert len(harness.calls) == 1


@pytest.mark.parametrize("server", [False, True])
def test_color_counts_derive_from_old_cached_styles_without_sql_or_repainting(
    app: Flask, monkeypatch: pytest.MonkeyPatch, server: bool
) -> None:
    """Both indexed and ID-based reads support snapshots without stored counts."""
    harness = ColorQueryHarness(app, monkeypatch, server=server)
    original = harness.result()["table_color_metadata"]
    key = f"{table_color_snapshot.PREFIX}{original['snapshot_id']}"
    legacy = harness.cache.get(key)
    legacy.pop("color_counts", None)
    legacy["styles"][0]["amount"]["colors"] = ["RED", "RED"]
    legacy["catalog"]["amount"].append("RED")
    assert harness.cache.set(key, legacy, timeout=60)
    monkeypatch.setattr(
        table_color_query,
        "resolve_table_color_styles",
        Mock(side_effect=AssertionError("Cached rows must not be repainted")),
    )
    monkeypatch.setattr(
        harness.context,
        "get_df_payload",
        Mock(side_effect=AssertionError("Cached rows must not repeat SQL")),
    )
    expected = {
        "region": {"GREEN": 0, "YELLOW": 0, "RED": 0},
        "amount": {"GREEN": 3, "YELLOW": 0, "RED": 1},
    }
    indexed = harness.result()["table_color_metadata"]
    assert indexed["snapshot_id"] == original["snapshot_id"]
    assert indexed["color_counts"] == expected
    harness.select(original["snapshot_id"])
    selected = harness.result()["table_color_metadata"]
    assert selected["color_counts"] == expected
    assert selected["filtered_rowcount"] == 3
    assert harness.cache.get(key) == legacy
    assert len(harness.calls) == 1


@pytest.mark.parametrize("rows", [999, 1000, 1001])
@pytest.mark.parametrize("server", [False, True])
def test_actual_result_boundary_not_configured_row_limit(
    app: Flask, monkeypatch: pytest.MonkeyPatch, rows: int, server: bool
) -> None:
    harness = ColorQueryHarness(
        app, monkeypatch, rows=rows, server=server, page_size=20
    )
    result = harness.result()
    assert result["table_color_metadata"]["status"] == (
        "ready" if rows <= 1000 else "unavailable"
    )
    base_calls = [query for query in harness.calls if not query["is_rowcount"]]
    assert len(base_calls) == 1
    assert base_calls[0]["row_limit"] == (1001 if server else 50000)
    assert len(result["data"]) == (20 if server else rows)
    if rows > 1000:
        assert "snapshot_id" not in result["table_color_metadata"]
        assert "color_counts" not in result["table_color_metadata"]
        assert (
            result["table_color_metadata"]["reason"]["code"]
            == "TABLE_COLOR_FILTER_LIMIT_EXCEEDED"
        )


def test_selected_oversized_query_fails_without_truncated_success(
    app: Flask, monkeypatch: pytest.MonkeyPatch
) -> None:
    harness = ColorQueryHarness(app, monkeypatch, rows=1001)
    assert harness.context.table_color_filter is not None
    harness.context.table_color_filter["selections"] = [
        {"column": "amount", "colors": ["GREEN"]}
    ]
    with pytest.raises(TableColorFilterError, match="LIMIT_EXCEEDED"):
        harness.result()


@pytest.mark.parametrize("page_size, offset", [(2000, 0), (20, 1100)])
def test_oversized_snapshot_preserves_large_or_later_ordinary_page(
    app: Flask, monkeypatch: pytest.MonkeyPatch, page_size: int, offset: int
) -> None:
    harness = ColorQueryHarness(app, monkeypatch, rows=1500, page_size=page_size)
    harness.context.queries[0].row_offset = offset
    result = harness.result()
    assert result["table_color_metadata"]["status"] == "unavailable"
    assert [row["amount"] for row in result["data"]] == list(
        range(offset + 1, min(1500, offset + page_size) + 1)
    )
    base_calls = [query for query in harness.calls if not query["is_rowcount"]]
    assert len(base_calls) == 1
    assert base_calls[0]["row_limit"] == max(1001, offset + page_size)


@pytest.mark.parametrize("server", [False, True])
def test_native_percentage_denominators_remain_frozen(
    app: Flask, monkeypatch: pytest.MonkeyPatch, server: bool
) -> None:
    harness = ColorQueryHarness(app, monkeypatch, server=server)
    harness.context.queries[0].post_processing = [
        {
            "operation": "contribution",
            "options": {"columns": ["amount"], "rename_columns": ["%amount"]},
        }
    ]
    harness.form_data["conditional_formatting"][0].update(
        column="%amount", targetValue=0.5 if server else 0.2
    )
    original = harness.result()
    harness.select(original["table_color_metadata"]["snapshot_id"], "%amount")
    selected = harness.result()
    if server:
        assert [row["amount"] for row in selected["data"]] == [2, 4]
        assert [row["%amount"] for row in selected["data"]] == pytest.approx(
            [2 / 3, 4 / 7]
        )
        harness.context.queries[0].row_offset = 2
        assert harness.result()["data"][0]["%amount"] == pytest.approx(6 / 11)
    else:
        assert [row["amount"] for row in selected["data"]] == [5, 6]
        assert [row["%amount"] for row in selected["data"]] == pytest.approx(
            [5 / 21, 6 / 21]
        )
    assert len(harness.calls) == 1


def test_contribution_totals_executed_once_and_never_on_snapshot_hit(
    app: Flask, monkeypatch: pytest.MonkeyPatch
) -> None:
    harness = ColorQueryHarness(app, monkeypatch)
    harness.context.queries[0].post_processing = [
        {
            "operation": "contribution",
            "options": {"columns": ["amount"], "rename_columns": ["%amount"]},
        }
    ]
    totals = QueryObject(metrics=["amount"], row_limit=50000)
    harness.context.queries.append(totals)
    harness.context.cache_values["queries"].append({})
    harness.form_data["conditional_formatting"][0].update(
        column="%amount", targetValue=0.2
    )
    original = harness.result()
    assert totals.row_limit is None
    assert len(harness.calls) == 2
    harness.select(original["table_color_metadata"]["snapshot_id"], "%amount")
    assert [row["%amount"] for row in harness.result()["data"]] == pytest.approx(
        [5 / 21, 6 / 21]
    )
    assert len(harness.calls) == 2


@pytest.mark.parametrize("change", ["rls", "rule", "page"])
def test_snapshot_rejects_changed_context_without_querying(
    app: Flask, monkeypatch: pytest.MonkeyPatch, change: str
) -> None:
    harness = ColorQueryHarness(app, monkeypatch)
    harness.select(harness.result()["table_color_metadata"]["snapshot_id"])
    if change == "rls":
        harness.rls = "rls-2"
    elif change == "rule":
        harness.form_data["conditional_formatting"][0]["targetValue"] = 1
    else:
        harness.context.queries[0].row_limit = 3
    with pytest.raises(TableColorFilterError, match="CONTEXT_CHANGED") as error:
        harness.result()
    assert error.value.status == 409
    assert len(harness.calls) == 1


@pytest.mark.parametrize("server", [False, True])
@pytest.mark.parametrize("change", ["query", "rule"])
def test_color_counts_rebuild_after_base_query_or_rule_change(
    app: Flask, monkeypatch: pytest.MonkeyPatch, server: bool, change: str
) -> None:
    """A new baseline counts its new paints without accepting stale references."""
    harness = ColorQueryHarness(app, monkeypatch, server=server)
    original = harness.result()["table_color_metadata"]
    harness.select(original["snapshot_id"])
    if change == "query":
        harness.context.queries[0].filter.append(
            {"col": "amount", "op": "<=", "val": 4}
        )
        harness.data = harness.data[harness.data.amount <= 4]
    else:
        harness.form_data["conditional_formatting"][0]["targetValue"] = 1
    with pytest.raises(TableColorFilterError, match="CONTEXT_CHANGED"):
        harness.result()
    assert len(harness.calls) == 1
    harness.context.table_color_filter = {"version": 2, "selections": []}
    rebuilt = harness.result()["table_color_metadata"]
    assert rebuilt["snapshot_id"] != original["snapshot_id"]
    assert rebuilt["generation"] != original["generation"]
    assert rebuilt["color_counts"]["amount"] == {
        "GREEN": 1 if change == "query" else 5,
        "YELLOW": 0,
        "RED": 0,
    }
    assert (
        harness.result()["table_color_metadata"]["color_counts"]
        == rebuilt["color_counts"]
    )
    assert len(harness.calls) == 2


@pytest.mark.parametrize("server", [False, True])
def test_snapshot_style_revision_rejects_old_reference_and_rebuilds_catalog(
    app: Flask, monkeypatch: pytest.MonkeyPatch, server: bool
) -> None:
    harness = ColorQueryHarness(app, monkeypatch, server=server)
    monkeypatch.setattr(
        table_color_query, "TABLE_COLOR_STYLE_REVISION", 1, raising=False
    )
    original = harness.result()["table_color_metadata"]
    monkeypatch.setattr(table_color_query, "TABLE_COLOR_STYLE_REVISION", 2)
    harness.select(original["snapshot_id"])
    with pytest.raises(TableColorFilterError, match="CONTEXT_CHANGED") as error:
        harness.result()
    assert error.value.status == 409
    assert len(harness.calls) == 1

    harness.context.table_color_filter = {"version": 2, "selections": []}
    rebuilt = harness.result()["table_color_metadata"]
    assert rebuilt["snapshot_id"] != original["snapshot_id"]
    assert rebuilt["generation"] != original["generation"]
    assert len(harness.calls) == 2
    assert (
        harness.result()["table_color_metadata"]["snapshot_id"]
        == rebuilt["snapshot_id"]
    )
    assert len(harness.calls) == 2


def test_snapshot_owner_is_not_authorized_by_opaque_id(
    app: Flask, monkeypatch: pytest.MonkeyPatch
) -> None:
    harness = ColorQueryHarness(app, monkeypatch)
    harness.select(harness.result()["table_color_metadata"]["snapshot_id"])
    harness.trusted = replace(harness.trusted, owner="another-owner")
    with pytest.raises(TableColorFilterError, match="ACCESS_DENIED") as error:
        harness.result()
    assert error.value.status == 403
    assert len(harness.calls) == 1


@pytest.mark.parametrize("server", [False, True])
def test_color_export_returns_all_matches_using_frozen_result(
    app: Flask, monkeypatch: pytest.MonkeyPatch, server: bool
) -> None:
    harness = ColorQueryHarness(app, monkeypatch, server=server)
    harness.form_data["conditional_formatting"].append(
        {
            **harness.form_data["conditional_formatting"][0],
            "operator": "≤",
            "colorScheme": "colorError",
        }
    )
    original = harness.result()["table_color_metadata"]
    assert original["color_counts"]["amount"] == {
        "GREEN": 3,
        "YELLOW": 0,
        "RED": 3,
    }
    harness.select(original["snapshot_id"])
    harness.context.result_type = ChartDataResultType.RESULTS
    harness.context.queries[0].row_limit = 50000
    exported = harness.result()
    assert [row["amount"] for row in exported["data"]] == [4, 5, 6]
    assert len(exported["table_color_metadata"]["styles"]) == 3
    assert exported["table_color_metadata"]["color_counts"] == original["color_counts"]
    assert len(harness.calls) == 1


@pytest.mark.parametrize("server", [False, True])
@pytest.mark.parametrize("view_rows, amounts", [([5, 3], [6, 4]), ([], [])])
def test_current_view_export_projects_frozen_rows_in_client_order(
    app: Flask,
    monkeypatch: pytest.MonkeyPatch,
    server: bool,
    view_rows: list[int],
    amounts: list[int],
) -> None:
    harness = ColorQueryHarness(app, monkeypatch, server=server)
    original = harness.result()
    metadata = original["table_color_metadata"]
    snapshot = harness.cache.get(
        f"{table_color_snapshot.PREFIX}{metadata['snapshot_id']}"
    )
    harness.select(metadata["snapshot_id"])
    harness.context.result_type = ChartDataResultType.RESULTS
    assert harness.context.table_color_filter is not None
    harness.context.table_color_filter["view_rows"] = view_rows
    exported = harness.result()
    assert [row["amount"] for row in exported["data"]] == amounts
    assert exported["table_color_metadata"]["row_indices"] == view_rows
    assert exported["table_color_metadata"]["styles"] == [
        snapshot["styles"][index] for index in view_rows
    ]
    assert exported["table_color_metadata"]["color_counts"] == metadata["color_counts"]
    assert len(harness.calls) == 1


@pytest.mark.parametrize("view_rows", [[0], [6], [-1], [3, 3], [True]])
def test_current_view_export_rejects_invalid_or_unselected_references(
    app: Flask, monkeypatch: pytest.MonkeyPatch, view_rows: list[int]
) -> None:
    harness = ColorQueryHarness(app, monkeypatch, server=False)
    harness.select(harness.result()["table_color_metadata"]["snapshot_id"])
    harness.context.result_type = ChartDataResultType.RESULTS
    assert harness.context.table_color_filter is not None
    harness.context.table_color_filter["view_rows"] = view_rows
    with pytest.raises(TableColorFilterError, match="TABLE_COLOR_FILTER_INVALID"):
        harness.result()
    assert len(harness.calls) == 1


@pytest.mark.parametrize("missing", ["export", "snapshot"])
def test_current_view_projection_requires_snapshot_export(
    app: Flask, monkeypatch: pytest.MonkeyPatch, missing: str
) -> None:
    harness = ColorQueryHarness(app, monkeypatch, server=False)
    if missing == "snapshot":
        harness.context.result_type = ChartDataResultType.RESULTS
    else:
        harness.select(harness.result()["table_color_metadata"]["snapshot_id"])
    assert harness.context.table_color_filter is not None
    harness.context.table_color_filter["view_rows"] = []
    with pytest.raises(TableColorFilterError, match="TABLE_COLOR_FILTER_INVALID"):
        harness.result()


@pytest.mark.parametrize("view_rows", [[1, 1], [-1], ["1"], [True], [0.5]])
def test_current_view_schema_rejects_invalid_references(view_rows: list[Any]) -> None:
    with pytest.raises(ValidationError):
        TableColorFilterSchema().load({"version": 2, "view_rows": view_rows})


@pytest.mark.parametrize("server", [False, True])
def test_force_refresh_rebuilds_without_reusing_snapshot(
    app: Flask, monkeypatch: pytest.MonkeyPatch, server: bool
) -> None:
    harness = ColorQueryHarness(app, monkeypatch, server=server)
    original = harness.result()["table_color_metadata"]
    harness.select(original["snapshot_id"])
    harness.data.loc[5, "amount"] = 1
    harness.context.force = True
    refreshed = harness.result()
    assert refreshed["table_color_metadata"]["snapshot_id"] != original["snapshot_id"]
    assert [row["amount"] for row in refreshed["data"]] == [4, 5]
    assert original["color_counts"]["amount"]["GREEN"] == 3
    assert refreshed["table_color_metadata"]["color_counts"]["amount"]["GREEN"] == 2
    assert len(harness.calls) == 2


def test_page_budget_checks_visit_each_row_once_before_painting(
    app: Flask, monkeypatch: pytest.MonkeyPatch
) -> None:
    harness = ColorQueryHarness(app, monkeypatch, rows=100, page_size=1)
    check = table_color_snapshot.ColorSnapshotBudget.check_rows
    visits: list[int] = []

    def check_rows(
        budget: table_color_snapshot.ColorSnapshotBudget,
        records: list[dict[str, Any]],
    ) -> int:
        visits.append(len(records))
        return check(budget, records)

    monkeypatch.setattr(
        table_color_snapshot.ColorSnapshotBudget, "check_rows", check_rows
    )
    assert harness.result()["table_color_metadata"]["status"] == "ready"
    # Two publication checks each visit raw/display values once; page checks are
    # linear, rather than checking all earlier pages again for every next page.
    assert visits.count(1) == 100
    assert sum(visits) <= 500


@pytest.mark.parametrize("operation", ["get", "add", "set"])
def test_cache_outage_keeps_ordinary_rows_without_duplicate_query(
    app: Flask, monkeypatch: pytest.MonkeyPatch, operation: str
) -> None:
    harness = ColorQueryHarness(app, monkeypatch)
    monkeypatch.setattr(
        harness.cache,
        operation,
        Mock(side_effect=ConnectionError("private backend detail")),
    )
    result = harness.result()
    assert [row["amount"] for row in result["data"]] == [1, 2]
    metadata = result["table_color_metadata"]
    assert metadata["status"] == "unavailable"
    assert metadata["reason"]["code"] == "TABLE_COLOR_FILTER_CACHE_UNAVAILABLE"
    assert "private" not in str(metadata)
    assert len([query for query in harness.calls if not query["is_rowcount"]]) == 1


def test_publication_metadata_size_failure_preserves_ordinary_result(
    app: Flask, monkeypatch: pytest.MonkeyPatch
) -> None:
    harness = ColorQueryHarness(app, monkeypatch)
    monkeypatch.setattr(
        table_color_snapshot.TableColorSnapshotStore,
        "save",
        Mock(
            side_effect=table_color_snapshot.ColorSnapshotBudget.from_config().exceeded()
        ),
    )
    result = harness.result()
    assert [row["amount"] for row in result["data"]] == [1, 2]
    assert result["table_color_metadata"]["status"] == "unavailable"
    assert result["table_color_metadata"]["reason"]["code"] == (
        "TABLE_COLOR_FILTER_LIMIT_EXCEEDED"
    )
    assert len(harness.calls) == 1


def test_unknown_and_gradient_targets_cannot_report_applied(
    app: Flask, monkeypatch: pytest.MonkeyPatch
) -> None:
    harness = ColorQueryHarness(app, monkeypatch)
    harness.form_data["conditional_formatting"][0]["useGradient"] = True
    snapshot = harness.result()["table_color_metadata"]["snapshot_id"]
    harness.select(snapshot)
    with pytest.raises(TableColorFilterError, match="GRADIENT_UNSUPPORTED"):
        harness.result()
    harness.select(snapshot, "missing")
    with pytest.raises(TableColorFilterError, match="TABLE_COLOR_FILTER_INVALID"):
        harness.result()
    assert len(harness.calls) == 1


def test_zero_matches_retains_catalog_and_recovers_page_zero(
    app: Flask, monkeypatch: pytest.MonkeyPatch
) -> None:
    harness = ColorQueryHarness(app, monkeypatch)
    original = harness.result()
    harness.select(original["table_color_metadata"]["snapshot_id"])
    assert harness.context.table_color_filter is not None
    harness.context.table_color_filter["selections"][0]["colors"] = ["RED"]
    harness.context.queries[0].row_offset = 4
    result = harness.result()
    assert result["data"] == []
    assert result["table_color_metadata"]["row_offset"] == 0
    assert (
        result["table_color_metadata"]["catalog"]
        == original["table_color_metadata"]["catalog"]
    )
    assert (
        result["table_color_metadata"]["color_counts"]
        == original["table_color_metadata"]["color_counts"]
    )
    assert len(harness.calls) == 1
