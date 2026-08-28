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

from pathlib import Path
from types import SimpleNamespace
from typing import cast
from unittest.mock import MagicMock, patch

import pytest
from marshmallow import ValidationError

from superset.commands.dashboard.exceptions import (
    DashboardXlsxChartFailedError,
    DashboardXlsxInvalidTabError,
    DashboardXlsxTableLimitExceededError,
)
from superset.commands.dashboard.export_xlsx import (
    DashboardFilterStateResolver,
    DashboardXlsxWorkItem,
    ExportDashboardXlsxCommand,
)
from superset.dashboards.schemas import DashboardXlsxExportSchema
from superset.models.dashboard import Dashboard
from superset.models.slice import Slice
from superset.utils import json


def make_chart(chart_id: int, title: str, viz_type: str = "table") -> Slice:
    """Build the saved Slice fields consumed by layout resolution."""
    return cast(
        Slice,
        SimpleNamespace(
            id=chart_id,
            slice_name=title,
            viz_type=viz_type,
            form_data={},
        ),
    )


def make_dashboard(
    layout: dict[str, object],
    charts: list[Slice],
    metadata: dict[str, object] | None = None,
) -> Dashboard:
    """Build the saved Dashboard fields consumed by the export command."""
    return cast(
        Dashboard,
        SimpleNamespace(
            id=17,
            position=layout,
            slices=charts,
            json_metadata=json.dumps(metadata or {}),
        ),
    )


def test_dashboard_xlsx_schema_is_strict_and_rejects_duplicate_tabs() -> None:
    schema = DashboardXlsxExportSchema()

    assert schema.load({"tabIds": ["TAB-a"], "dataMask": {}}) == {
        "tabIds": ["TAB-a"],
        "dataMask": {},
    }
    with pytest.raises(ValidationError):
        schema.load({"tabIds": ["TAB-a", "TAB-a"]})
    with pytest.raises(ValidationError):
        schema.load({"tabIds": ["TAB-a"], "unexpected": True})


def test_dashboard_state_resolver_keeps_only_scoped_filters_and_alert_refs() -> None:
    layout: dict[str, object] = {
        "CHART-1": {
            "id": "CHART-1",
            "type": "CHART",
            "parents": ["ROOT_ID"],
            "meta": {"chartId": 1},
        },
        "CHART-2": {
            "id": "CHART-2",
            "type": "CHART",
            "parents": ["ROOT_ID"],
            "meta": {"chartId": 2},
        },
    }
    metadata: dict[str, object] = {
        "native_filter_configuration": [{"id": "FILTER-a", "chartsInScope": [2]}],
        "chart_configuration": {"1": {"crossFilters": {"chartsInScope": [2]}}},
    }
    data_mask: dict[str, object] = {
        "FILTER-a": {
            "extraFormData": {
                "filters": [
                    {"col": "region", "op": "IN", "val": ["APAC"]},
                    {"col": "unsafe", "op": "SQL", "val": "x"},
                ],
                "time_range": "2026-01-01 : 2026-12-31",
            }
        },
        "1": {
            "extraFormData": {"filters": [{"col": "category", "op": "==", "val": "A"}]}
        },
        "2": {
            "ownState": {
                "alertFilters": [
                    {
                        "ruleId": "772a548e-72f7-4ac8-a8ff-fdb7465b3ccd",
                        "level": "RED",
                    },
                    {"ruleId": "forged", "level": "PURPLE"},
                ]
            }
        },
    }

    resolver = DashboardFilterStateResolver(
        make_dashboard(layout, [], metadata),
        data_mask,
    )
    extra_form_data, alert_filters = resolver.resolve(2)

    assert extra_form_data == {
        "time_range": "2026-01-01 : 2026-12-31",
        "filters": [
            {"col": "region", "op": "IN", "val": ["APAC"], "isExtra": True},
            {"col": "category", "op": "==", "val": "A", "isExtra": True},
        ],
    }
    assert alert_filters == [
        {
            "rule_id": "772a548e-72f7-4ac8-a8ff-fdb7465b3ccd",
            "level": "RED",
        }
    ]
    assert resolver.dropped_state_count == 2


def test_dashboard_xlsx_collects_depth_first_deduplicates_and_notes_skips() -> None:
    table_a = make_chart(1, "Revenue [APAC]")
    non_table = make_chart(2, "Pie", "pie")
    table_b = make_chart(3, "Revenue [APAC]")
    layout = {
        "TAB-parent": {
            "id": "TAB-parent",
            "type": "TAB",
            "children": ["ROW-a", "TAB-child"],
        },
        "ROW-a": {
            "id": "ROW-a",
            "type": "ROW",
            "children": ["CHART-1", "CHART-2"],
        },
        "TAB-child": {
            "id": "TAB-child",
            "type": "TAB",
            "children": ["CHART-3", "CHART-1"],
        },
        "CHART-1": {
            "id": "CHART-1",
            "type": "CHART",
            "children": [],
            "meta": {"chartId": 1, "sliceName": "Revenue [APAC]"},
        },
        "CHART-2": {
            "id": "CHART-2",
            "type": "CHART",
            "children": [],
            "meta": {"chartId": 2, "sliceName": "Pie"},
        },
        "CHART-3": {
            "id": "CHART-3",
            "type": "CHART",
            "children": [],
            "meta": {"chartId": 3, "sliceName": "Revenue [APAC]"},
        },
    }

    command = ExportDashboardXlsxCommand(
        make_dashboard(layout, [table_a, non_table, table_b]),
        ["TAB-parent", "TAB-child"],
    )
    work_items, notes = command._collect_work_items()

    assert [(item.chart.id, item.sheet_name) for item in work_items] == [
        (1, "Revenue APAC"),
        (3, "Revenue APAC (2)"),
    ]
    assert {note["reason"] for note in notes} == {
        "Duplicate chart skipped",
        "Sheet name normalized",
        "Unsupported visualization skipped",
    }


def test_dashboard_xlsx_exports_root_when_dashboard_has_no_tabs() -> None:
    chart = make_chart(1, "FR-01 ClickHouse Drill Detail")
    layout = {
        "ROOT_ID": {
            "id": "ROOT_ID",
            "type": "ROOT",
            "children": ["GRID_ID"],
        },
        "GRID_ID": {
            "id": "GRID_ID",
            "type": "GRID",
            "children": ["ROW-FR01"],
        },
        "ROW-FR01": {
            "id": "ROW-FR01",
            "type": "ROW",
            "children": ["CHART-FR01"],
        },
        "CHART-FR01": {
            "id": "CHART-FR01",
            "type": "CHART",
            "children": [],
            "meta": {
                "chartId": 1,
                "sliceName": "FR-01 ClickHouse Drill Detail",
            },
        },
    }

    work_items, notes = ExportDashboardXlsxCommand(
        make_dashboard(layout, [chart]),
        ["ROOT_ID"],
    )._collect_work_items()

    assert [(item.chart.id, item.sheet_name) for item in work_items] == [
        (1, "FR-01 ClickHouse Drill Detail"),
    ]
    assert notes == []


def test_dashboard_xlsx_rejects_root_selection_when_dashboard_has_tabs() -> None:
    layout: dict[str, object] = {
        "ROOT_ID": {
            "id": "ROOT_ID",
            "type": "ROOT",
            "children": ["TAB-a"],
        },
        "TAB-a": {
            "id": "TAB-a",
            "type": "TAB",
            "children": [],
        },
    }

    with pytest.raises(DashboardXlsxInvalidTabError):
        ExportDashboardXlsxCommand(
            make_dashboard(layout, []),
            ["ROOT_ID"],
        )._collect_work_items()


def test_dashboard_xlsx_rejects_more_than_ten_tables_without_truncating() -> None:
    charts = [make_chart(chart_id, f"Table {chart_id}") for chart_id in range(1, 12)]
    layout: dict[str, object] = {
        "TAB-a": {
            "id": "TAB-a",
            "type": "TAB",
            "children": [f"CHART-{chart.id}" for chart in charts],
        }
    }
    layout.update(
        {
            f"CHART-{chart.id}": {
                "id": f"CHART-{chart.id}",
                "type": "CHART",
                "children": [],
                "meta": {"chartId": chart.id, "sliceName": chart.slice_name},
            }
            for chart in charts
        }
    )

    with pytest.raises(DashboardXlsxTableLimitExceededError):
        ExportDashboardXlsxCommand(
            make_dashboard(layout, charts),
            ["TAB-a"],
        )._collect_work_items()


def test_dashboard_xlsx_failure_closes_writer_and_removes_temporary_file(
    tmp_path: Path,
) -> None:
    chart = make_chart(1, "Table")
    dashboard = make_dashboard({}, [chart])
    command = ExportDashboardXlsxCommand(dashboard, ["TAB-a"])
    temporary_path = tmp_path / "partial.xlsx"
    temporary_file = temporary_path.open("w+b")
    writer = MagicMock()

    with (
        patch.object(
            command,
            "_collect_work_items",
            return_value=([DashboardXlsxWorkItem(chart, "Table")], []),
        ),
        patch.object(command, "_query_context", return_value=MagicMock()),
        patch.object(
            command,
            "_execute_chart",
            side_effect=DashboardXlsxChartFailedError(),
        ),
        patch(
            "superset.commands.dashboard.export_xlsx.tempfile.NamedTemporaryFile",
            return_value=temporary_file,
        ),
        patch(
            "superset.commands.dashboard.export_xlsx.StyledWorkbookWriter",
            return_value=writer,
        ),
        pytest.raises(DashboardXlsxChartFailedError),
    ):
        command.run()

    writer.close.assert_called_once_with()
    assert not temporary_path.exists()
