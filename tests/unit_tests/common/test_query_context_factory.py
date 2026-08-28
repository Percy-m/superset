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
import copy
from typing import Any
from unittest.mock import Mock, patch

import pytest
from marshmallow import ValidationError

from superset.common.chart_data import ChartDataResultFormat
from superset.common.query_context_factory import QueryContextFactory
from superset.common.query_object import QueryObject
from superset.common.table_color_schema import TableColorFilterError
from superset.exceptions import QueryObjectValidationError
from superset.models.slice import Slice


class TestQueryContextFactory:
    def setup_method(self):
        self.factory = QueryContextFactory()

    def test_extract_tooltip_columns_string_items(self):
        """Test _extract_tooltip_columns with string items in tooltip_contents"""
        form_data = {"tooltip_contents": ["column1", "column2", "column3"]}

        result = self.factory._extract_tooltip_columns(form_data)

        assert result == ["column1", "column2", "column3"]

    @patch("superset.common.query_context_factory.is_feature_enabled")
    def test_styled_xlsx_requires_matching_saved_classic_table(
        self, mock_is_feature_enabled
    ):
        """Only a matching saved classic Table can request server-owned styles."""
        mock_is_feature_enabled.return_value = True
        slice_ = Slice(
            id=9,
            viz_type="table",
            datasource_type="table",
            datasource_id=7,
        )
        datasource = Mock(id=7)

        with patch.object(self.factory, "_convert_to_model", return_value=datasource):
            query_context = self.factory.create(
                current_slice=slice_,
                datasource={"id": 7, "type": "table"},
                queries=[],
                form_data={"slice_id": 9},
                result_format=ChartDataResultFormat.XLSX,
                result_format_options={"styled": True},
            )
            assert query_context.result_format_options == {"styled": True}

            slice_.viz_type = "pie"
            with pytest.raises(
                QueryObjectValidationError,
                match="STYLED_XLSX_UNSUPPORTED",
            ):
                self.factory.create(
                    current_slice=slice_,
                    datasource={"id": 7, "type": "table"},
                    queries=[],
                    form_data={"slice_id": 9},
                    result_format=ChartDataResultFormat.XLSX,
                    result_format_options={"styled": True},
                )

    def test_normalize_color_query_discards_retired_inputs_without_mutation(
        self,
    ) -> None:
        """Color state and retired private extras never enter the SQL query."""
        query = {
            "columns": ["region"],
            "metrics": ["gross_revenue"],
            "row_limit": 20,
            "table_color_filter": {"version": 2},
            "alert_filters": [{"rule_id": "ignored", "level": "RED"}],
            "is_table_alert_totals": True,
            "extras": {
                "where": "region IS NOT NULL",
                "having": "SUM(amount) > 0",
                "__table_alert_filters": [{"operator": ">", "target": 0}],
                "__table_alert_fingerprint": "retired",
                "__table_color_styles": "client-forged",
            },
        }
        original = copy.deepcopy(query)

        result = self.factory._normalize_color_query(query)

        assert result == {
            "columns": ["region"],
            "metrics": ["gross_revenue"],
            "row_limit": 20,
            "extras": {
                "where": "region IS NOT NULL",
                "having": "SUM(amount) > 0",
            },
        }
        assert query == original

    @patch("superset.common.query_context_factory.is_feature_enabled")
    def test_color_request_disabled_ignores_even_invalid_client_fields(
        self, mock_is_feature_enabled: Mock
    ) -> None:
        """Flag-off requests retain ordinary query behavior without color parsing."""
        mock_is_feature_enabled.return_value = False
        queries = [{"table_color_filter": {"version": 99}, "alert_filters": "old"}]
        original = copy.deepcopy(queries)
        assert self.factory._get_table_color_request(queries) is None
        assert queries == original

    @patch(
        "superset.common.query_context_factory.is_feature_enabled", return_value=True
    )
    def test_color_request_normalizes_three_colors_and_leaves_input_unchanged(
        self, mock_is_feature_enabled: Mock
    ) -> None:
        """Selections are canonical context state, not trusted SQL predicates."""
        queries = [
            {
                "table_color_filter": {
                    "version": 2,
                    "selections": [
                        {"column": "gross_revenue", "colors": ["RED", "GREEN"]},
                        {"column": "gross_revenue", "colors": ["YELLOW"]},
                    ],
                }
            },
            {"is_rowcount": True},
        ]
        original = copy.deepcopy(queries)
        result = self.factory._get_table_color_request(queries)
        mock_is_feature_enabled.assert_called_once_with("TABLE_ALERT_FILTERS")
        assert result == {
            "version": 2,
            "theme_mode": "default",
            "selections": [
                {"column": "gross_revenue", "colors": ["GREEN", "YELLOW", "RED"]}
            ],
        }
        assert queries == original

    @pytest.mark.parametrize(
        "queries",
        [
            [{}, {"table_color_filter": {"version": 2}}],
            [
                {"table_color_filter": {"version": 2}},
                {"table_color_filter": {"version": 1}},
            ],
            [{"table_color_filter": {"version": 2}, "alert_filters": []}],
            [{"table_color_filter": {"version": 2}}, {"is_table_alert_totals": False}],
        ],
    )
    @patch(
        "superset.common.query_context_factory.is_feature_enabled", return_value=True
    )
    def test_color_request_rejects_conflicting_or_mixed_protocols(
        self, mock_is_feature_enabled: Mock, queries: list[dict[str, Any]]
    ) -> None:
        """Mixed engines and secondary-query-only state are explicit errors."""
        with pytest.raises(TableColorFilterError, match="TABLE_COLOR_FILTER_INVALID"):
            self.factory._get_table_color_request(queries)
        mock_is_feature_enabled.assert_called_once_with("TABLE_ALERT_FILTERS")

    @patch(
        "superset.common.query_context_factory.is_feature_enabled", return_value=True
    )
    def test_color_request_direct_call_is_validated_and_legacy_only_is_ignored(
        self, mock_is_feature_enabled: Mock
    ) -> None:
        """Non-REST callers cannot bypass the versioned schema."""
        with pytest.raises(ValidationError):
            self.factory._get_table_color_request(
                [{"table_color_filter": {"version": 2, "threshold": 1}}]
            )
        assert (
            self.factory._get_table_color_request(
                [{"alert_filters": [{"level": "RED"}], "is_table_alert_totals": True}]
            )
            is None
        )
        assert self.factory._get_table_color_request([]) is None
        mock_is_feature_enabled.assert_called_with("TABLE_ALERT_FILTERS")

    @patch(
        "superset.common.query_context_factory.is_feature_enabled", return_value=True
    )
    def test_color_request_is_context_metadata_without_changing_query_construction(
        self, mock_is_feature_enabled: Mock
    ) -> None:
        """The source SQL objects and immutable caller payload remain unaffected."""
        request = {
            "version": 2,
            "selections": [{"column": "profit", "colors": ["GREEN"]}],
        }
        queries = [
            {
                "columns": ["region"],
                "metrics": ["profit"],
                "table_color_filter": request,
            },
            {"is_rowcount": True, "columns": ["region"]},
        ]
        original = copy.deepcopy(queries)
        query_objects = [Mock(spec=QueryObject), Mock(spec=QueryObject)]
        datasource = Mock(id=7)
        with (
            patch.object(self.factory, "_convert_to_model", return_value=datasource),
            patch.object(
                self.factory._query_object_factory, "create", side_effect=query_objects
            ) as create,
            patch.object(
                self.factory,
                "_process_query_object",
                side_effect=lambda _source, _form, query: query,
            ),
        ):
            result = self.factory.create(
                datasource={"id": 7, "type": "table"}, queries=queries
            )
        assert result.queries == query_objects
        mock_is_feature_enabled.assert_any_call("TABLE_ALERT_FILTERS")
        assert result.table_color_filter == {**request, "theme_mode": "default"}
        assert create.call_args_list[0].kwargs == {
            "datasource": {"id": 7, "type": "table"},
            "server_pagination": False,
            "columns": ["region"],
            "metrics": ["profit"],
        }
        assert "table_color_filter" not in create.call_args_list[1].kwargs
        assert (
            result.cache_values["queries"][0]["table_color_filter"]
            == result.table_color_filter
        )
        assert "table_color_filter" not in result.cache_values["queries"][1]
        assert queries == original

    def test_extract_tooltip_columns_dict_items(self):
        """Test _extract_tooltip_columns with dict items in tooltip_contents"""
        form_data = {
            "tooltip_contents": [
                {"item_type": "column", "column_name": "LAT"},
                {"item_type": "column", "column_name": "LON"},
                {"item_type": "metric", "metric_name": "count"},
                {"item_type": "column", "column_name": "CITY"},
            ]
        }

        result = self.factory._extract_tooltip_columns(form_data)

        assert result == ["LAT", "LON", "CITY"]

    def test_extract_tooltip_columns_mixed_items(self):
        """Test _extract_tooltip_columns with mixed string and dict items"""
        form_data = {
            "tooltip_contents": [
                "string_column",
                {"item_type": "column", "column_name": "dict_column"},
                {"item_type": "invalid", "column_name": "invalid_column"},
                {"item_type": "column"},
            ]
        }

        result = self.factory._extract_tooltip_columns(form_data)

        assert result == ["string_column", "dict_column"]

    def test_extract_tooltip_columns_empty(self):
        """Test _extract_tooltip_columns with empty or missing tooltip_contents"""
        form_data = {"tooltip_contents": []}

        result = self.factory._extract_tooltip_columns(form_data)

        assert result == []

        form_data = {}

        result = self.factory._extract_tooltip_columns(form_data)

        assert result == []

    def test_get_existing_column_names_dict_columns(self):
        """Test _get_existing_column_names with dict columns"""
        columns = [
            {"column_name": "col1"},
            {"column_name": "col2"},
            {"column_name": "col3"},
        ]

        result = self.factory._get_existing_column_names(columns)

        assert result == {"col1", "col2", "col3"}

    def test_get_existing_column_names_string_columns(self):
        """Test _get_existing_column_names with string columns"""
        columns = ["col1", "col2", "col3"]

        result = self.factory._get_existing_column_names(columns)

        assert result == {"col1", "col2", "col3"}

    def test_get_existing_column_names_mixed_columns(self):
        """Test _get_existing_column_names with mixed column types"""
        columns = [
            {"column_name": "dict_col1"},
            "string_col1",
            {"column_name": "dict_col2"},
            "string_col2",
        ]

        result = self.factory._get_existing_column_names(columns)

        assert result == {"dict_col1", "string_col1", "dict_col2", "string_col2"}

    def test_get_existing_column_names_empty(self):
        """Test _get_existing_column_names with empty columns"""
        columns = []

        result = self.factory._get_existing_column_names(columns)

        assert result == set()

    def test_get_existing_column_names_invalid_dict(self):
        """Test _get_existing_column_names with invalid dict columns"""
        columns = [
            {"column_name": "valid_col"},
            {"invalid_key": "invalid_col"},
            {"column_name": None},
            {"column_name": 123},
        ]

        result = self.factory._get_existing_column_names(columns)

        assert result == {"valid_col"}

    def test_append_missing_tooltip_columns(self):
        """Test _append_missing_tooltip_columns"""
        query_object = Mock(spec=QueryObject)
        query_object.columns = []

        tooltip_columns = ["tooltip_col1", "tooltip_col2", "existing_col"]
        existing_columns = {"existing_col"}

        with patch.object(self.factory, "_find_column_definition") as mock_find:
            mock_find.side_effect = (
                lambda qo, col: f"def_{col}" if col != "tooltip_col2" else None
            )

            self.factory._append_missing_tooltip_columns(
                query_object, tooltip_columns, existing_columns
            )

            assert len(query_object.columns) == 2
            assert "def_tooltip_col1" in query_object.columns
            assert "tooltip_col2" in query_object.columns
            assert "existing_col" not in query_object.columns

    def test_find_column_definition_found(self):
        """Test _find_column_definition when column is found"""
        query_object = Mock(spec=QueryObject)
        query_object.datasource = Mock()
        query_object.datasource.columns = [
            Mock(column_name="found_col"),
            Mock(column_name="other_col"),
        ]

        result = self.factory._find_column_definition(query_object, "found_col")

        assert result.column_name == "found_col"

    def test_find_column_definition_not_found(self):
        """Test _find_column_definition when column is not found"""
        query_object = Mock(spec=QueryObject)
        query_object.datasource = Mock()
        query_object.datasource.columns = [
            Mock(column_name="other_col1"),
            Mock(column_name="other_col2"),
        ]

        result = self.factory._find_column_definition(query_object, "missing_col")

        assert result is None

    def test_find_column_definition_no_datasource(self):
        """Test _find_column_definition when datasource is None"""
        query_object = Mock(spec=QueryObject)
        query_object.datasource = None

        result = self.factory._find_column_definition(query_object, "any_col")

        assert result is None

    def test_find_column_definition_no_columns_attr(self):
        """Test _find_column_definition when datasource has no columns attribute"""
        query_object = Mock(spec=QueryObject)
        query_object.datasource = Mock()
        del query_object.datasource.columns

        result = self.factory._find_column_definition(query_object, "any_col")

        assert result is None

    def test_add_tooltip_columns_no_form_data(self):
        """Test _add_tooltip_columns with no form_data"""
        query_object = Mock(spec=QueryObject)
        query_object.columns = Mock()
        query_object.columns.append = Mock()

        self.factory._add_tooltip_columns(query_object, None)

        query_object.columns.append.assert_not_called()

    def test_add_tooltip_columns_no_tooltip_columns(self):
        """Test _add_tooltip_columns with no tooltip columns"""
        query_object = Mock(spec=QueryObject)
        query_object.columns = Mock()
        query_object.columns.append = Mock()
        form_data = {"tooltip_contents": []}

        self.factory._add_tooltip_columns(query_object, form_data)

        query_object.columns.append.assert_not_called()

    def test_add_tooltip_columns_with_tooltip_columns(self):
        """Test _add_tooltip_columns with tooltip columns"""
        query_object = Mock(spec=QueryObject)
        query_object.columns = []
        form_data = {"tooltip_contents": ["tooltip_col1", "tooltip_col2"]}

        with (
            patch.object(self.factory, "_extract_tooltip_columns") as mock_extract,
            patch.object(
                self.factory, "_get_existing_column_names"
            ) as mock_get_existing,
            patch.object(
                self.factory, "_append_missing_tooltip_columns"
            ) as mock_append,
        ):
            mock_extract.return_value = ["tooltip_col1", "tooltip_col2"]
            mock_get_existing.return_value = set()

            self.factory._add_tooltip_columns(query_object, form_data)

            mock_extract.assert_called_once_with(form_data)
            mock_get_existing.assert_called_once_with(query_object.columns)
            mock_append.assert_called_once_with(
                query_object, ["tooltip_col1", "tooltip_col2"], set()
            )

    @patch("superset.common.query_context_factory.DatasourceDAO")
    def test_convert_to_model(self, mock_dao):
        """Test _convert_to_model"""
        datasource = {"type": "table", "id": 123}
        mock_dao.get_datasource.return_value = Mock()

        result = self.factory._convert_to_model(datasource)

        mock_dao.get_datasource.assert_called_once()
        assert result is not None

    @patch("superset.common.query_context_factory.ChartDAO")
    def test_get_slice_found(self, mock_dao):
        """Test _get_slice when slice is found"""
        slice_id = 123
        mock_slice = Mock(spec=Slice)
        mock_dao.find_by_id.return_value = mock_slice

        result = self.factory._get_slice(slice_id)

        mock_dao.find_by_id.assert_called_once_with(slice_id)
        assert result == mock_slice

    @patch("superset.common.query_context_factory.ChartDAO")
    def test_get_slice_not_found(self, mock_dao):
        """Test _get_slice when slice is not found"""
        slice_id = 123
        mock_dao.find_by_id.return_value = None

        result = self.factory._get_slice(slice_id)

        mock_dao.find_by_id.assert_called_once_with(slice_id)
        assert result is None

    def test_apply_granularity_with_x_axis(self):
        """Test _apply_granularity with x_axis in form_data"""
        query_object = Mock(spec=QueryObject)
        query_object.granularity = "P1D"
        query_object.columns = ["ds", "other_col"]
        query_object.post_processing = []
        query_object.filter = []

        form_data = {"x_axis": "ds"}
        datasource = Mock()
        datasource.columns = [{"column_name": "ds", "is_dttm": True}]

        self.factory._apply_granularity(query_object, form_data, datasource)

        assert query_object.columns == ["P1D", "other_col"]

    def test_apply_granularity_with_x_axis_dict(self):
        """Test _apply_granularity with x_axis as dict in form_data"""
        query_object = Mock(spec=QueryObject)
        query_object.granularity = "P1D"
        query_object.columns = [{"sqlExpression": "ds", "label": "ds"}, "other_col"]
        query_object.post_processing = []
        query_object.filter = []

        form_data = {"x_axis": {"sqlExpression": "ds"}}
        datasource = Mock()
        datasource.columns = [{"column_name": "ds", "is_dttm": True}]

        self.factory._apply_granularity(query_object, form_data, datasource)

        assert query_object.columns[0]["sqlExpression"] == "P1D"
        assert query_object.columns[0]["label"] == "P1D"

    def test_apply_granularity_with_pivot_post_processing(self):
        """Test _apply_granularity with pivot post_processing"""
        query_object = Mock(spec=QueryObject)
        query_object.granularity = "P1D"
        query_object.columns = ["ds", "other_col"]
        query_object.post_processing = [
            {"operation": "pivot", "options": {"index": ["ds"]}}
        ]
        query_object.filter = []

        form_data = {"x_axis": "ds"}
        datasource = Mock()
        datasource.columns = [{"column_name": "ds", "is_dttm": True}]

        self.factory._apply_granularity(query_object, form_data, datasource)

        assert query_object.post_processing[0]["options"]["index"] == ["P1D"]

    def test_apply_granularity_with_temporal_filter(self):
        """Test _apply_granularity with temporal filter"""
        query_object = Mock(spec=QueryObject)
        query_object.granularity = "P1D"
        query_object.columns = ["other_col"]
        query_object.post_processing = []
        query_object.filter = [
            {"col": "ds", "op": "TEMPORAL_RANGE", "val": "2023-01-01 : 2023-01-31"}
        ]

        form_data = {}
        datasource = Mock()
        datasource.columns = [{"column_name": "ds", "is_dttm": True}]

        self.factory._apply_granularity(query_object, form_data, datasource)

        assert len(query_object.filter) == 0

    def test_apply_granularity_with_granularity_in_temporal_filters(self):
        """Test _apply_granularity when granularity is already in temporal filters"""
        query_object = Mock(spec=QueryObject)
        query_object.granularity = "P1D"
        query_object.columns = ["other_col"]
        query_object.post_processing = []
        query_object.filter = [
            {"col": "P1D", "op": "TEMPORAL_RANGE", "val": "2023-01-01 : 2023-01-31"},
            {"col": "ds", "op": "TEMPORAL_RANGE", "val": "2023-01-01 : 2023-01-31"},
        ]

        form_data = {}
        datasource = Mock()
        datasource.columns = [{"column_name": "ds", "is_dttm": True}]

        self.factory._apply_granularity(query_object, form_data, datasource)

        assert len(query_object.filter) == 1
        assert query_object.filter[0]["col"] == "ds"

    def test_apply_granularity_no_granularity(self):
        """Test _apply_granularity when no granularity is set"""
        query_object = Mock(spec=QueryObject)
        query_object.granularity = None
        query_object.columns = ["ds", "other_col"]
        query_object.post_processing = []
        query_object.filter = []

        form_data = {"x_axis": "ds"}
        datasource = Mock()
        datasource.columns = [{"column_name": "ds", "is_dttm": True}]

        self.factory._apply_granularity(query_object, form_data, datasource)

        assert query_object.columns == ["ds", "other_col"]

    def test_apply_granularity_x_axis_not_temporal(self):
        """Test _apply_granularity when x_axis is not a temporal column"""
        query_object = Mock(spec=QueryObject)
        query_object.granularity = "P1D"
        query_object.columns = ["ds", "other_col"]
        query_object.post_processing = []
        query_object.filter = []

        form_data = {"x_axis": "other_col"}
        datasource = Mock()
        datasource.columns = [{"column_name": "ds", "is_dttm": True}]

        self.factory._apply_granularity(query_object, form_data, datasource)

        assert query_object.columns == ["ds", "other_col"]

    def test_apply_filters_with_time_range(self):
        """Test _apply_filters with time_range"""
        query_object = Mock(spec=QueryObject)
        query_object.time_range = "2023-01-01 : 2023-01-31"
        query_object.filter = [
            {"col": "ds", "op": "TEMPORAL_RANGE", "val": "old_value"},
            {"col": "other_col", "op": "==", "val": "value"},
        ]

        self.factory._apply_filters(query_object)

        assert query_object.filter[0]["val"] == "2023-01-01 : 2023-01-31"

        assert query_object.filter[1]["val"] == "value"

    def test_apply_filters_no_time_range(self):
        """Test _apply_filters without time_range"""
        query_object = Mock(spec=QueryObject)
        query_object.time_range = None
        query_object.filter = [
            {"col": "ds", "op": "TEMPORAL_RANGE", "val": "old_value"}
        ]

        self.factory._apply_filters(query_object)

        assert query_object.filter[0]["val"] == "old_value"

    def test_apply_filters_no_temporal_filters(self):
        """Test _apply_filters with no temporal filters"""
        query_object = Mock(spec=QueryObject)
        query_object.time_range = "2023-01-01 : 2023-01-31"
        query_object.filter = [{"col": "other_col", "op": "==", "val": "value"}]

        self.factory._apply_filters(query_object)

        assert query_object.filter[0]["val"] == "value"

    def test_add_currency_column_no_form_data(self):
        """Test _add_currency_column when form_data is None."""
        query_object = Mock(spec=QueryObject)
        query_object.columns = ["col1"]
        datasource = Mock()

        self.factory._add_currency_column(query_object, None, datasource)

        assert query_object.columns == ["col1"]

    def test_add_currency_column_no_columns(self):
        """Test _add_currency_column when query_object has no columns."""
        query_object = Mock(spec=QueryObject)
        query_object.columns = []
        form_data = {
            "viz_type": "pivot_table_v2",
            "currency_format": {"symbol": "AUTO"},
        }
        datasource = Mock()
        datasource.currency_code_column = "currency_code"

        self.factory._add_currency_column(query_object, form_data, datasource)

        assert query_object.columns == []

    def test_add_currency_column_unsupported_viz_type(self):
        """Test _add_currency_column with unsupported viz type."""
        query_object = Mock(spec=QueryObject)
        query_object.columns = ["col1"]
        form_data = {"viz_type": "pie", "currency_format": {"symbol": "AUTO"}}
        datasource = Mock()
        datasource.currency_code_column = "currency_code"

        self.factory._add_currency_column(query_object, form_data, datasource)

        assert query_object.columns == ["col1"]

    def test_add_currency_column_symbol_not_auto(self):
        """Test _add_currency_column when symbol is not AUTO."""
        query_object = Mock(spec=QueryObject)
        query_object.columns = ["col1"]
        form_data = {"viz_type": "pivot_table_v2", "currency_format": {"symbol": "USD"}}
        datasource = Mock()
        datasource.currency_code_column = "currency_code"

        self.factory._add_currency_column(query_object, form_data, datasource)

        assert query_object.columns == ["col1"]

    def test_add_currency_column_no_currency_column_on_datasource(self):
        """Test _add_currency_column when datasource has no currency column."""
        query_object = Mock(spec=QueryObject)
        query_object.columns = ["col1"]
        form_data = {
            "viz_type": "pivot_table_v2",
            "currency_format": {"symbol": "AUTO"},
        }
        datasource = Mock()
        datasource.currency_code_column = None

        self.factory._add_currency_column(query_object, form_data, datasource)

        assert query_object.columns == ["col1"]

    def test_add_currency_column_already_in_query(self):
        """Test _add_currency_column when currency column already exists."""
        query_object = Mock(spec=QueryObject)
        query_object.columns = ["col1", "currency_code"]
        form_data = {
            "viz_type": "pivot_table_v2",
            "currency_format": {"symbol": "AUTO"},
        }
        datasource = Mock()
        datasource.currency_code_column = "currency_code"

        self.factory._add_currency_column(query_object, form_data, datasource)

        assert query_object.columns == ["col1", "currency_code"]

    def test_add_currency_column_adds_column_for_pivot_table(self):
        """Test _add_currency_column adds column for pivot_table_v2 viz type"""
        query_object = Mock(spec=QueryObject)
        query_object.columns = ["col1"]
        form_data = {
            "viz_type": "pivot_table_v2",
            "currency_format": {"symbol": "AUTO"},
        }
        datasource = Mock()
        datasource.currency_code_column = "currency_code"

        self.factory._add_currency_column(query_object, form_data, datasource)

        assert query_object.columns == ["col1", "currency_code"]

    def test_add_currency_column_skips_table_viz_type(self):
        """Test _add_currency_column does not add column for table viz type."""
        query_object = Mock(spec=QueryObject)
        query_object.columns = ["col1"]
        form_data = {"viz_type": "table", "currency_format": {"symbol": "AUTO"}}
        datasource = Mock()
        datasource.currency_code_column = "currency_code"

        self.factory._add_currency_column(query_object, form_data, datasource)

        assert query_object.columns == ["col1"]
