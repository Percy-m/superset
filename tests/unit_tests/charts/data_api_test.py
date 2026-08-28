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

from datetime import datetime
from inspect import unwrap
from types import SimpleNamespace
from typing import Any
from unittest.mock import Mock, patch

import pytest
from flask import Flask, Response
from werkzeug.http import parse_options_header

from superset.common.chart_data import ChartDataResultFormat, ChartDataResultType
from superset.common.table_color_schema import TableColorFilterError


@pytest.mark.parametrize(
    ("saved_title", "form_title", "expected_title"),
    [
        ("质量分析 😀", "Untrusted title", "质量分析 😀"),
        (None, "Unsaved chart", "Unsaved chart"),
        (None, None, "chart"),
    ],
)
def test_single_chart_xlsx_response_uses_title_and_timestamp(
    app: Flask,
    saved_title: str | None,
    form_title: str | None,
    expected_title: str,
) -> None:
    from superset.charts.data.api import ChartDataRestApi

    api = ChartDataRestApi.__new__(ChartDataRestApi)
    query_context = SimpleNamespace(
        result_type=ChartDataResultType.FULL,
        result_format=ChartDataResultFormat.XLSX,
        slice_=SimpleNamespace(slice_name=saved_title) if saved_title else None,
        form_data={"slice_name": form_title},
    )
    with (
        app.test_request_context(),
        patch(
            "superset.charts.data.api.security_manager.can_access", return_value=True
        ),
        patch("superset.utils.excel.datetime") as clock,
    ):
        clock.now.return_value = datetime(2026, 8, 2, 3, 4, 5)
        response = api._send_chart_response(
            {"query_context": query_context, "queries": [{"data": b"PK-test"}]},
        )
        disposition, options = parse_options_header(
            response.headers["Content-Disposition"]
        )
        assert disposition == "attachment"
        assert options["filename"] == f"{expected_title}_20260802030405.xlsx"
        assert response.mimetype == (
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )
        response.direct_passthrough = False
        assert response.get_data() == b"PK-test"
        response.close()


@pytest.mark.parametrize(
    ("status", "code"),
    [
        (400, "TABLE_COLOR_FILTER_INVALID"),
        (403, "TABLE_COLOR_FILTER_ACCESS_DENIED"),
        (409, "TABLE_COLOR_FILTER_CONTEXT_CHANGED"),
        (410, "TABLE_COLOR_FILTER_SNAPSHOT_EXPIRED"),
        (422, "TABLE_COLOR_FILTER_LIMIT_EXCEEDED"),
        (503, "TABLE_COLOR_FILTER_STORE_UNAVAILABLE"),
        (504, "TABLE_COLOR_FILTER_TIMEOUT"),
    ],
)
def test_color_errors_preserve_status_and_safe_code_in_data_response(
    app: Flask, status: int, code: str
) -> None:
    """Color errors never become a generic 422 or expose an underlying SQL cause."""
    from superset.charts.data.api import ChartDataRestApi

    api = ChartDataRestApi.__new__(ChartDataRestApi)
    error = TableColorFilterError(
        code, "The color request could not be completed.", status
    )
    error.__cause__ = RuntimeError("SELECT secret FROM private_database")
    command = Mock()
    command.run.side_effect = error
    log_payload = Mock()
    with (
        app.test_request_context(),
        patch.object(api, "_send_chart_response") as send_chart,
    ):
        # Exercise response handling without invoking the unrelated audit-log wrapper.
        response = unwrap(ChartDataRestApi._get_data_response)(
            api, command, add_extra_log_payload=log_payload
        )
    assert response.status_code == status
    assert response.get_json()["error_code"] == code
    assert code in response.get_json()["message"]
    assert "SELECT" not in response.get_data(as_text=True)
    assert "private_database" not in response.get_data(as_text=True)
    assert "secret" not in response.get_data(as_text=True)
    command.run.assert_called_once_with(force_cached=False)
    send_chart.assert_not_called()
    log_payload.assert_not_called()


@pytest.mark.parametrize(
    "selections", [[], [{"column": "profit", "colors": ["GREEN"]}]]
)
def test_color_snapshot_csv_exports_never_reexecute_streaming_sql(
    app: Flask, selections: list[dict[str, object]], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A frozen snapshot remains authoritative even above the streaming threshold."""
    from superset.charts.data.api import ChartDataRestApi

    api = ChartDataRestApi.__new__(ChartDataRestApi)
    monkeypatch.setitem(app.config, "CSV_STREAMING_ROW_THRESHOLD", 1)
    query_context = SimpleNamespace(
        result_format=ChartDataResultFormat.CSV,
        result_type=ChartDataResultType.RESULTS,
        table_color_filter={"version": 2, "selections": selections},
        form_data={"viz_type": "table", "row_limit": 1000},
    )
    result = {"query_context": query_context, "queries": [{"data": "profit\n7\n"}]}
    with (
        app.test_request_context(),
        patch(
            "superset.charts.data.api.security_manager.can_access", return_value=True
        ),
        patch.object(api, "_create_streaming_csv_response") as stream,
    ):
        assert api._should_use_streaming(result, query_context.form_data) is False
        response = api._send_chart_response(result, query_context.form_data)
    assert response.status_code == 200
    assert response.get_data(as_text=True) == "profit\n7\n"
    stream.assert_not_called()


@pytest.mark.parametrize(
    ("result_format", "actual_count", "form_limit", "context_limit", "expected"),
    [
        (ChartDataResultFormat.CSV, 500, 10, 10, True),
        (ChartDataResultFormat.CSV, 499, 1000, 1000, False),
        (ChartDataResultFormat.CSV, None, 500, 10, True),
        (ChartDataResultFormat.CSV, None, None, 500, True),
        (ChartDataResultFormat.CSV, None, 10, 1000, False),
        (ChartDataResultFormat.CSV, None, None, None, False),
        (ChartDataResultFormat.XLSX, 1000, 1000, 1000, False),
        (ChartDataResultFormat.JSON, 1000, 1000, 1000, False),
    ],
)
def test_ordinary_csv_streaming_preserves_threshold_and_rowcount_precedence(
    app: Flask,
    result_format: ChartDataResultFormat,
    actual_count: int | None,
    form_limit: int | None,
    context_limit: int | None,
    expected: bool,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Only the new color path changes; native CSV row-count decisions stay intact."""
    from superset.charts.data.api import ChartDataRestApi

    api = ChartDataRestApi.__new__(ChartDataRestApi)
    monkeypatch.setitem(app.config, "CSV_STREAMING_ROW_THRESHOLD", 500)
    form_data: dict[str, Any] = {"viz_type": "table"}
    if form_limit is not None:
        form_data["row_limit"] = form_limit
    result = {
        "query_context": SimpleNamespace(
            result_format=result_format,
            table_color_filter=None,
            form_data={"row_limit": context_limit} if context_limit else {},
        ),
        "queries": [{"data": []}, {"data": [{"rowcount": actual_count}]}],
    }
    assert api._should_use_streaming(result, form_data) is expected


def test_ordinary_csv_still_dispatches_to_streaming_response(
    app: Flask, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The legacy export path remains available when color metadata is absent."""
    from superset.charts.data.api import ChartDataRestApi

    api = ChartDataRestApi.__new__(ChartDataRestApi)
    monkeypatch.setitem(app.config, "CSV_STREAMING_ROW_THRESHOLD", 500)
    form_data = {"viz_type": "table", "row_limit": 1000}
    result = {
        "query_context": SimpleNamespace(
            result_format=ChartDataResultFormat.CSV,
            result_type=ChartDataResultType.RESULTS,
            table_color_filter=None,
            form_data=form_data,
        ),
        "queries": [{"data": "profit\n7\n"}],
    }
    streamed = Response("streamed data", mimetype="text/csv")
    with (
        app.test_request_context(),
        patch(
            "superset.charts.data.api.security_manager.can_access", return_value=True
        ),
        patch.object(
            api, "_create_streaming_csv_response", return_value=streamed
        ) as stream,
    ):
        assert api._send_chart_response(result, form_data) is streamed
    stream.assert_called_once_with(result, form_data, filename=None, expected_rows=None)
