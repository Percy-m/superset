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
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from flask import Flask
from werkzeug.http import parse_options_header

from superset.common.chart_data import ChartDataResultFormat, ChartDataResultType


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
