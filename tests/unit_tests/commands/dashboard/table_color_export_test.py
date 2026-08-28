# Licensed to the Apache Software Foundation (ASF) under one
# or more contributor license agreements. See the NOTICE file
# distributed with this work for additional information
# regarding copyright ownership. The ASF licenses this file
# to you under the Apache License, Version 2.0 (the
# "License"); you may not use this file except in compliance
# with the License. You may obtain a copy of the License at
#
#   http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing,
# software distributed under the License is distributed on an
# "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY
# KIND, either express or implied. See the License for the
# specific language governing permissions and limitations
# under the License.

import copy
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import Mock

import pandas as pd
import pytest
from marshmallow import ValidationError
from pytest_mock import MockerFixture

from superset.commands.dashboard.exceptions import DashboardXlsxChartFailedError
from superset.commands.dashboard.export_xlsx import (
    DashboardFilterStateResolver,
    DashboardXlsxWorkItem,
    ExportDashboardXlsxCommand,
)
from superset.common.chart_data import ChartDataResultFormat, ChartDataResultType
from superset.common.query_context import QueryContext
from superset.common.table_color_context import dashboard_filter_fingerprint
from superset.common.table_color_schema import (
    TableColorFilterError,
    TableColorSelection,
)
from superset.dashboards.schemas import DashboardXlsxExportSchema
from superset.models.dashboard import Dashboard
from superset.models.slice import Slice
from superset.utils import json
from superset.utils.core import GenericDataType

EXPORT_MODULE = "superset.commands.dashboard.export_xlsx"
REFERENCE = {"snapshot_id": "owned-snapshot", "generation": "saved-generation"}
SELECTIONS: list[TableColorSelection] = [{"column": "profit", "colors": ["GREEN"]}]
EXTRA_FORM_DATA: dict[str, Any] = {
    "filters": [{"col": "region", "op": "IN", "val": ["APAC"]}],
    "time_range": "2026",
}


def _chart(chart_id: int) -> Slice:
    """Build the saved chart fields used by snapshot orchestration."""
    return cast(
        Slice,
        SimpleNamespace(
            id=chart_id,
            slice_name=f"Table {chart_id}",
            form_data={"viz_type": "table", "row_limit": 1000},
            viz_type="table",
        ),
    )


def _dashboard(charts: list[Slice]) -> Dashboard:
    """Keep dashboard layout and native scope local to the test."""
    return cast(
        Dashboard,
        SimpleNamespace(
            id=17,
            slices=charts,
            position={
                f"CHART-{chart.id}": {
                    "type": "CHART",
                    "meta": {"chartId": chart.id},
                }
                for chart in charts
            },
            json_metadata=json.dumps(
                {
                    "native_filter_configuration": [
                        {
                            "id": "NATIVE_FILTER-region",
                            "chartsInScope": [chart.id for chart in charts],
                        }
                    ],
                }
            ),
        ),
    )


@pytest.fixture
def snapshot_export(mocker: MockerFixture) -> SimpleNamespace:
    """A valid owned snapshot; cache, access and query execution are mocked."""
    chart = _chart(1)
    dashboard = _dashboard([chart])
    command = ExportDashboardXlsxCommand(
        dashboard, ["TAB-a"], color_snapshots={"1": dict(REFERENCE)}
    )
    query_context = Mock(spec=QueryContext, slice_=chart, datasource=Mock(id=7))
    snapshot = {
        "saved_chart_id": 1,
        "dashboard_id": 17,
        "generation": REFERENCE["generation"],
        "dashboard_filters": dashboard_filter_fingerprint(EXTRA_FORM_DATA),
        "theme_mode": "dark",
        "source_context_queries": [
            {
                "columns": ["region"],
                "metrics": ["profit"],
                "row_limit": 25,
                "row_offset": 50,
                "alert_filters": [{"rule_id": "retired", "level": "RED"}],
                "extras": {"where": "", "__table_alert_fingerprint": "retired"},
            }
        ],
    }
    access = mocker.patch(
        f"{EXPORT_MODULE}.TableColorContext.resolve",
        return_value=SimpleNamespace(owner="owner-binding"),
    )
    store = mocker.patch(f"{EXPORT_MODULE}.TableColorSnapshotStore")
    store.return_value.load.return_value = snapshot
    create = mocker.patch(
        f"{EXPORT_MODULE}.QueryContextFactory.create",
        return_value=Mock(spec=QueryContext),
    )
    mocker.patch(f"{EXPORT_MODULE}.is_feature_enabled", return_value=True)
    return SimpleNamespace(
        command=command,
        context=query_context,
        dashboard=dashboard,
        snapshot=snapshot,
        access=access,
        store=store,
        create=create,
    )


def test_export_snapshot_reuses_owned_source_and_preserves_frozen_theme(
    snapshot_export: SimpleNamespace,
) -> None:
    """Client references never replace saved query contents or snapshot paint tokens."""
    original = copy.deepcopy(snapshot_export.snapshot)
    reference = {**REFERENCE, "theme_mode": "default"}
    result = snapshot_export.command._snapshot_context(
        snapshot_export.context, reference, EXTRA_FORM_DATA, SELECTIONS
    )
    assert result is snapshot_export.create.return_value
    snapshot_export.store.return_value.load.assert_called_once_with(
        REFERENCE["snapshot_id"], "owner-binding"
    )
    snapshot_export.access.assert_called_once()
    args = snapshot_export.create.call_args.kwargs
    assert args["queries"][0] == {
        "columns": ["region"],
        "metrics": ["profit"],
        "row_limit": 25,
        "row_offset": 0,
        "extras": {"where": ""},
        "table_color_filter": {
            "version": 2,
            "snapshot_id": REFERENCE["snapshot_id"],
            "selections": SELECTIONS,
            "theme_mode": "dark",
        },
    }
    assert args["form_data"]["dashboardId"] == 17
    assert args["form_data"]["extra_form_data"] == EXTRA_FORM_DATA
    assert args["result_type"] == ChartDataResultType.RESULTS
    assert args["result_format"] == ChartDataResultFormat.JSON
    assert args["force"] is False
    assert snapshot_export.snapshot == original
    snapshot_export.context.get_df_payload.assert_not_called()


@pytest.mark.parametrize(
    "override",
    [
        {"saved_chart_id": 2},
        {"dashboard_id": 18},
        {"generation": "another-generation"},
        {"dashboard_filters": "another-scope"},
    ],
)
def test_export_rejects_changed_chart_dashboard_generation_or_native_scope(
    snapshot_export: SimpleNamespace, override: dict[str, object]
) -> None:
    """Snapshot IDs are insufficient when any bound context differs."""
    snapshot_export.snapshot.update(override)
    with pytest.raises(TableColorFilterError) as error:
        snapshot_export.command._with_color_snapshot(
            snapshot_export.context, 1, EXTRA_FORM_DATA, SELECTIONS
        )
    assert error.value.status == 409
    assert error.value.code == "TABLE_COLOR_FILTER_CONTEXT_CHANGED"
    snapshot_export.create.assert_not_called()


def test_export_revalidates_access_before_loading_a_snapshot(
    snapshot_export: SimpleNamespace,
) -> None:
    """Loss of dashboard/dataset/RLS access cannot be bypassed by a known token."""
    error = TableColorFilterError("TABLE_COLOR_FILTER_ACCESS_DENIED", "Denied.", 403)
    snapshot_export.access.side_effect = error
    with pytest.raises(TableColorFilterError) as caught:
        snapshot_export.command._with_color_snapshot(
            snapshot_export.context, 1, EXTRA_FORM_DATA, SELECTIONS
        )
    assert caught.value is error
    snapshot_export.store.assert_not_called()
    snapshot_export.create.assert_not_called()


@pytest.mark.parametrize(
    ("code", "status"),
    [
        ("TABLE_COLOR_FILTER_ACCESS_DENIED", 403),
        ("TABLE_COLOR_FILTER_SNAPSHOT_EXPIRED", 410),
        ("TABLE_COLOR_FILTER_STORE_UNAVAILABLE", 503),
    ],
)
def test_export_preserves_owner_expiry_and_store_errors_without_requery(
    snapshot_export: SimpleNamespace, code: str, status: int
) -> None:
    """An unavailable snapshot is never silently replaced with a new SQL result."""
    error = TableColorFilterError(code, "Snapshot is unavailable.", status)
    snapshot_export.store.return_value.load.side_effect = error
    with pytest.raises(TableColorFilterError) as caught:
        snapshot_export.command._with_color_snapshot(
            snapshot_export.context, 1, EXTRA_FORM_DATA, SELECTIONS
        )
    assert caught.value is error
    snapshot_export.store.return_value.load.assert_called_once_with(
        REFERENCE["snapshot_id"], "owner-binding"
    )
    snapshot_export.create.assert_not_called()


def test_export_detects_native_filter_change_after_snapshot_creation(
    snapshot_export: SimpleNamespace,
) -> None:
    """Resolve live native state before comparing it with the immutable baseline."""
    resolver = DashboardFilterStateResolver(
        snapshot_export.dashboard,
        {
            "NATIVE_FILTER-region": {
                "extraFormData": {
                    "filters": [{"col": "region", "op": "IN", "val": ["EMEA"]}],
                    "time_range": "2026",
                }
            },
            "1": {
                "ownState": {"alertFilter": {"version": 2, "selections": SELECTIONS}}
            },
        },
    )
    extra, selections = resolver.resolve(1)
    assert selections == SELECTIONS
    with pytest.raises(
        TableColorFilterError, match="TABLE_COLOR_FILTER_CONTEXT_CHANGED"
    ):
        snapshot_export.command._with_color_snapshot(
            snapshot_export.context, 1, extra, selections
        )
    snapshot_export.create.assert_not_called()


def test_export_requires_a_snapshot_for_color_selection_but_not_ordinary_tables(
    snapshot_export: SimpleNamespace,
) -> None:
    """Without a selection or token the established export path remains intact."""
    command = ExportDashboardXlsxCommand(snapshot_export.dashboard, ["TAB-a"])
    assert (
        command._with_color_snapshot(snapshot_export.context, 1, {}, [])
        is snapshot_export.context
    )
    with pytest.raises(TableColorFilterError) as error:
        command._with_color_snapshot(snapshot_export.context, 1, {}, SELECTIONS)
    assert error.value.code == "TABLE_COLOR_FILTER_SNAPSHOT_EXPIRED"
    assert error.value.status == 410
    snapshot_export.store.assert_not_called()


def test_export_color_flag_off_ignores_snapshot_and_color_state(
    snapshot_export: SimpleNamespace, mocker: MockerFixture
) -> None:
    """Turning the feature off keeps native Tab export behavior."""
    mocker.patch(f"{EXPORT_MODULE}.is_feature_enabled", return_value=False)
    assert (
        snapshot_export.command._with_color_snapshot(
            snapshot_export.context, 1, EXTRA_FORM_DATA, SELECTIONS
        )
        is snapshot_export.context
    )
    snapshot_export.access.assert_not_called()
    snapshot_export.store.assert_not_called()


def test_color_export_consumes_frozen_values_styles_and_totals_without_sql() -> None:
    """XLSX writes the same visible cells and paint, retaining long integers."""
    context = Mock(spec=QueryContext, table_color_filter={"version": 2})
    styles = [{"profit": {"colors": ["GREEN"], "backgroundColor": "#5ac189"}}]
    context.get_payload.return_value = {
        "queries": [
            {
                "data": [{"profit": 9007199254740993}],
                "colnames": ["profit"],
                "coltypes": [GenericDataType.NUMERIC],
                "table_color_metadata": {
                    "styles": styles,
                    "totals": {"profit": 9007199254740993},
                },
            }
        ]
    }
    dataframe, column_types, totals = ExportDashboardXlsxCommand._execute_chart(context)
    assert dataframe.loc[0, "profit"] == 9007199254740993
    assert dataframe.attrs["table_color_styles"] == styles
    assert column_types == [GenericDataType.NUMERIC]
    assert totals == {"profit": 9007199254740993}
    context.raise_for_access.assert_called_once_with()
    context.get_df_payload.assert_not_called()


@pytest.mark.parametrize(
    "reference",
    [
        {},
        {"snapshot_id": "id"},
        {"generation": "generation"},
        {"snapshot_id": "", "generation": "g"},
        {"snapshot_id": "s" * 257, "generation": "g"},
        {"snapshot_id": "s", "generation": "g" * 257},
        {"snapshot_id": "s", "generation": "g", "sql": "client-rule"},
        {"snapshot_id": "s", "generation": "g", "theme_mode": "system"},
    ],
)
def test_tab_export_schema_rejects_unbounded_or_client_owned_snapshot_fields(
    reference: dict[str, str],
) -> None:
    """Only bounded opaque snapshot references enter the export command."""
    with pytest.raises(ValidationError):
        DashboardXlsxExportSchema().load(
            {
                "tabIds": ["TAB-a"],
                "colorSnapshots": {"1": reference},
            }
        )


def test_tab_export_schema_accepts_at_most_ten_snapshot_references() -> None:
    """Snapshot references share the existing ten-Table export bound."""
    schema = DashboardXlsxExportSchema()
    references = {str(index): dict(REFERENCE) for index in range(10)}
    assert (
        len(
            schema.load({"tabIds": ["TAB-a"], "colorSnapshots": references})[
                "colorSnapshots"
            ]
        )
        == 10
    )
    with pytest.raises(ValidationError):
        schema.load(
            {"tabIds": ["TAB-a"], "colorSnapshots": {**references, "10": REFERENCE}}
        )


@pytest.mark.parametrize(
    ("failure_code", "status"),
    [
        ("TABLE_COLOR_FILTER_CONTEXT_CHANGED", 409),
        ("TABLE_COLOR_FILTER_SNAPSHOT_EXPIRED", 410),
        ("TABLE_COLOR_FILTER_ACCESS_DENIED", 403),
    ],
)
def test_second_sheet_color_failure_discards_the_entire_workbook(
    tmp_path: Path, mocker: MockerFixture, failure_code: str, status: int
) -> None:
    """A successful first sheet cannot leak as a partial file if another fails."""
    charts = [_chart(1), _chart(2)]
    command = ExportDashboardXlsxCommand(_dashboard(charts), ["TAB-a"])
    mocker.patch.object(
        command,
        "_collect_work_items",
        return_value=(
            [DashboardXlsxWorkItem(chart, chart.slice_name) for chart in charts],
            [],
        ),
    )
    error = TableColorFilterError(
        failure_code, "Reload the Table before export.", status
    )
    mocker.patch.object(
        command,
        "_query_context",
        side_effect=[Mock(spec=QueryContext, datasource=Mock()), error],
    )
    execute = mocker.patch.object(
        command,
        "_execute_chart",
        return_value=(pd.DataFrame({"profit": [7]}), [GenericDataType.NUMERIC], None),
    )
    mocker.patch(f"{EXPORT_MODULE}.TableRuleResolver")
    temporary_path = tmp_path / "partial-color-export.xlsx"
    temporary_path.touch()
    mocker.patch(
        f"{EXPORT_MODULE}.tempfile.NamedTemporaryFile",
        return_value=SimpleNamespace(name=str(temporary_path), close=Mock()),
    )
    writer = mocker.patch(f"{EXPORT_MODULE}.StyledWorkbookWriter").return_value
    with pytest.raises(TableColorFilterError) as caught:
        command.run()
    assert caught.value is error
    assert execute.call_count == 1
    assert writer.write_dataframe.call_count == 1
    writer.close.assert_called_once_with()
    assert not temporary_path.exists()


def test_workbook_close_failure_is_atomic_and_hides_low_level_details(
    tmp_path: Path, mocker: MockerFixture
) -> None:
    """Completing all Sheets still does not publish a failed Workbook close."""
    chart = _chart(1)
    command = ExportDashboardXlsxCommand(_dashboard([chart]), ["TAB-a"])
    mocker.patch.object(
        command,
        "_collect_work_items",
        return_value=([DashboardXlsxWorkItem(chart, "Table 1")], []),
    )
    mocker.patch.object(
        command,
        "_query_context",
        return_value=Mock(spec=QueryContext, datasource=Mock()),
    )
    mocker.patch.object(
        command,
        "_execute_chart",
        return_value=(pd.DataFrame({"profit": [7]}), [GenericDataType.NUMERIC], None),
    )
    mocker.patch(f"{EXPORT_MODULE}.TableRuleResolver")
    temporary_path = tmp_path / "failed-close.xlsx"
    temporary_path.touch()
    mocker.patch(
        f"{EXPORT_MODULE}.tempfile.NamedTemporaryFile",
        return_value=SimpleNamespace(name=str(temporary_path), close=Mock()),
    )
    writer = mocker.patch(f"{EXPORT_MODULE}.StyledWorkbookWriter").return_value
    writer.close.side_effect = OSError("SELECT private-data FROM private-database")
    with pytest.raises(DashboardXlsxChartFailedError) as error:
        command.run()
    assert "private-data" not in str(error.value)
    assert "private-database" not in str(error.value)
    assert writer.close.call_count == 2
    assert not temporary_path.exists()
