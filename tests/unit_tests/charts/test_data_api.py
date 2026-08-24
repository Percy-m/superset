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

from io import BytesIO
from types import SimpleNamespace
from typing import Any
from unittest.mock import Mock, patch
from zipfile import ZipFile

import pytest

from superset.charts.data.api import ChartDataRestApi
from superset.common.chart_data import ChartDataResultFormat, ChartDataResultType


def _multi_query_xlsx_result(
    *,
    styled: bool,
    xlsx_primary_query_only: bool,
) -> dict[str, Any]:
    return {
        "query_context": SimpleNamespace(
            result_type=ChartDataResultType.FULL,
            result_format=ChartDataResultFormat.XLSX,
            result_format_options={
                "styled": styled,
                "xlsx_primary_query_only": xlsx_primary_query_only,
            },
        ),
        "queries": [{"data": b"primary"}, {"data": b"totals"}],
    }


def test_styled_multi_query_xlsx_returns_primary_workbook(
    app_context: None,
) -> None:
    """An explicit styled XLSX projection returns the primary workbook."""
    with (
        patch(
            "superset.charts.data.api.security_manager.can_access",
            return_value=True,
        ),
        patch(
            "superset.charts.data.api.is_feature_enabled",
            return_value=True,
        ),
    ):
        response = ChartDataRestApi._send_chart_response(
            Mock(),
            _multi_query_xlsx_result(
                styled=True,
                xlsx_primary_query_only=True,
            ),
        )

    assert response.mimetype == (
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
    assert response.headers["Content-Disposition"].endswith(".xlsx")
    assert response.get_data() == b"primary"


@pytest.mark.parametrize(
    ("feature_enabled", "styled", "xlsx_primary_query_only"),
    [(False, True, True), (True, False, True), (True, True, False)],
)
def test_multi_query_xlsx_keeps_legacy_bundle_without_styled_feature(
    app_context: None,
    feature_enabled: bool,
    styled: bool,
    xlsx_primary_query_only: bool,
) -> None:
    """Without a valid explicit projection, multi-query XLSX remains a ZIP."""
    with (
        patch(
            "superset.charts.data.api.security_manager.can_access",
            return_value=True,
        ),
        patch(
            "superset.charts.data.api.is_feature_enabled",
            return_value=feature_enabled,
        ),
    ):
        response = ChartDataRestApi._send_chart_response(
            Mock(),
            _multi_query_xlsx_result(
                styled=styled,
                xlsx_primary_query_only=xlsx_primary_query_only,
            ),
        )

    assert response.mimetype == "application/zip"
    with ZipFile(BytesIO(response.get_data())) as bundle:
        assert bundle.namelist() == ["query_1.xlsx", "query_2.xlsx"]
        assert bundle.read("query_1.xlsx") == b"primary"
        assert bundle.read("query_2.xlsx") == b"totals"


def test_multi_query_csv_keeps_legacy_bundle(app_context: None) -> None:
    """The XLSX-only response projection does not alter CSV responses."""
    result = _multi_query_xlsx_result(
        styled=True,
        xlsx_primary_query_only=False,
    )
    result["query_context"].result_format = ChartDataResultFormat.CSV
    result["queries"] = [{"data": "primary"}, {"data": "totals"}]
    api = Mock()
    api._should_use_streaming.return_value = False
    with patch(
        "superset.charts.data.api.security_manager.can_access",
        return_value=True,
    ):
        response = ChartDataRestApi._send_chart_response(api, result)

    assert response.mimetype == "application/zip"
    with ZipFile(BytesIO(response.get_data())) as bundle:
        assert bundle.namelist() == ["query_1.csv", "query_2.csv"]
        assert bundle.read("query_1.csv").endswith(b"primary")
        assert bundle.read("query_2.csv").endswith(b"totals")
