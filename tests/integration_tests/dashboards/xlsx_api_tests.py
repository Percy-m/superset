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

import tempfile
from pathlib import Path
from unittest.mock import patch

from superset.commands.dashboard.exceptions import (
    DashboardXlsxInvalidTabError,
    DashboardXlsxNoTableError,
)
from superset.commands.dashboard.export_xlsx import DashboardXlsxExportResult
from tests.integration_tests.base_tests import SupersetTestCase
from tests.integration_tests.conftest import with_feature_flags


class TestDashboardXlsxApi(SupersetTestCase):
    """Exercise HTTP gating, validation, error mapping, and file lifecycle."""

    endpoint = "/api/v1/dashboard/17/export_xlsx/"

    @with_feature_flags(
        STYLED_XLSX_EXPORT=False,
        DASHBOARD_TAB_XLSX_EXPORT=False,
    )
    def test_feature_flags_disabled_return_not_found(self) -> None:
        self.login("admin")

        response = self.client.post(
            self.endpoint,
            json={"tabIds": ["TAB-a"], "dataMask": {}},
        )

        assert response.status_code == 404

    @with_feature_flags(
        STYLED_XLSX_EXPORT=True,
        DASHBOARD_TAB_XLSX_EXPORT=True,
    )
    @patch("superset.dashboards.api.DashboardDAO.get_by_id_or_slug")
    def test_invalid_request_body_returns_bad_request(self, mock_get_dashboard) -> None:
        self.login("admin")
        mock_get_dashboard.return_value = object()

        response = self.client.post(
            self.endpoint,
            json={"tabIds": ["TAB-a", "TAB-a"], "dataMask": {}},
        )

        assert response.status_code == 400
        assert response.json["message"] == {
            "_schema": ["tabIds must not contain duplicate values"]
        }

    @with_feature_flags(
        STYLED_XLSX_EXPORT=True,
        DASHBOARD_TAB_XLSX_EXPORT=True,
    )
    @patch("superset.dashboards.api.ExportDashboardXlsxCommand")
    @patch("superset.dashboards.api.DashboardDAO.get_by_id_or_slug")
    def test_success_returns_xlsx_and_removes_file_on_close(
        self,
        mock_get_dashboard,
        mock_command,
    ) -> None:
        self.login("admin")
        mock_get_dashboard.return_value = object()
        with tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False) as temporary:
            workbook = Path(temporary.name)
        workbook.write_bytes(b"PK\x03\x04complete")
        mock_command.return_value.run.return_value = DashboardXlsxExportResult(
            workbook,
            "dashboard_17.xlsx",
        )

        with patch(
            "superset.dashboards.api.security_manager.can_access", return_value=True
        ):
            response = self.client.post(
                self.endpoint,
                json={"tabIds": ["TAB-a"], "dataMask": {"3": {}}},
            )

        assert response.status_code == 200
        assert response.data == b"PK\x03\x04complete"
        assert response.headers["Content-Disposition"].endswith(
            "filename=dashboard_17.xlsx"
        )
        response.close()
        assert not workbook.exists()

    @with_feature_flags(
        STYLED_XLSX_EXPORT=True,
        DASHBOARD_TAB_XLSX_EXPORT=True,
    )
    @patch("superset.dashboards.api.ExportDashboardXlsxCommand")
    @patch("superset.dashboards.api.DashboardDAO.get_by_id_or_slug")
    def test_command_errors_map_to_400_and_422(
        self,
        mock_get_dashboard,
        mock_command,
    ) -> None:
        self.login("admin")
        mock_get_dashboard.return_value = object()
        with patch(
            "superset.dashboards.api.security_manager.can_access", return_value=True
        ):
            mock_command.return_value.run.side_effect = DashboardXlsxInvalidTabError()
            invalid_tab = self.client.post(
                self.endpoint,
                json={"tabIds": ["TAB-stale"]},
            )
            mock_command.return_value.run.side_effect = DashboardXlsxNoTableError()
            no_table = self.client.post(
                self.endpoint,
                json={"tabIds": ["TAB-a"]},
            )

        assert invalid_tab.status_code == 400
        assert no_table.status_code == 422
