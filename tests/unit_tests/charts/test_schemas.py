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

from typing import Any

import pytest
from flask import current_app
from marshmallow import ValidationError

from superset.charts.schemas import (
    ChartDataProphetOptionsSchema,
    ChartDataQueryObjectSchema,
    get_time_grain_choices,
)
from superset.common.query_context_factory import QueryContextFactory
from superset.common.table_color_schema import TableColorFilterSchema


def test_get_time_grain_choices(app_context: None) -> None:
    """Test that get_time_grain_choices returns values with config addons"""
    # Save original config
    original_addons = current_app.config.get("TIME_GRAIN_ADDONS", {})

    try:
        # Test with no addons
        current_app.config["TIME_GRAIN_ADDONS"] = {}
        choices = get_time_grain_choices()
        # Should have at least the basic time grains
        assert "P1D" in choices
        assert "P1W" in choices
        assert "P1M" in choices
        assert "P1Y" in choices

        # Test with addons
        current_app.config["TIME_GRAIN_ADDONS"] = {
            "PT5M": "5 minutes",
            "P2W": "2 weeks",
        }
        choices = get_time_grain_choices()
        assert "PT5M" in choices
        assert "P2W" in choices
        assert "P1D" in choices  # Still has built-in choices
    finally:
        # Restore original config
        current_app.config["TIME_GRAIN_ADDONS"] = original_addons


def test_chart_data_prophet_options_schema_time_grain_validation(
    app_context: None,
) -> None:
    """Test that ChartDataProphetOptionsSchema validates time_grain choices"""
    schema = ChartDataProphetOptionsSchema()

    # Valid time grain should pass
    valid_data = {
        "time_grain": "P1D",
        "periods": 7,
        "confidence_interval": 0.8,
    }
    result = schema.load(valid_data)
    assert result["time_grain"] == "P1D"

    # Invalid time grain should fail
    invalid_data = {
        "time_grain": "invalid_grain",
        "periods": 7,
        "confidence_interval": 0.8,
    }
    with pytest.raises(ValidationError) as exc_info:
        schema.load(invalid_data)
    assert "time_grain" in exc_info.value.messages
    assert "Must be one of" in str(exc_info.value.messages["time_grain"])

    # Empty time grain should fail (required field)
    missing_data = {
        "periods": 7,
        "confidence_interval": 0.8,
    }
    with pytest.raises(ValidationError) as exc_info:
        schema.load(missing_data)
    assert "time_grain" in exc_info.value.messages


def test_chart_data_query_object_schema_time_grain_sqla_validation(
    app_context: None,
) -> None:
    """Test that ChartDataQueryObjectSchema validates time_grain_sqla in extras"""
    schema = ChartDataQueryObjectSchema()

    # Valid time grain should pass (time_grain_sqla is in extras)
    valid_data = {
        "datasource": {"type": "table", "id": 1},
        "metrics": ["count"],
        "extras": {
            "time_grain_sqla": "P1W",
        },
    }
    result = schema.load(valid_data)
    assert "extras" in result
    assert result["extras"]["time_grain_sqla"] == "P1W"

    # Invalid time grain should fail
    invalid_data = {
        "datasource": {"type": "table", "id": 1},
        "metrics": ["count"],
        "extras": {
            "time_grain_sqla": "not_a_grain",
        },
    }
    with pytest.raises(ValidationError) as exc_info:
        schema.load(invalid_data)
    assert "extras" in exc_info.value.messages
    assert "time_grain_sqla" in exc_info.value.messages["extras"]
    assert "Must be one of" in str(exc_info.value.messages["extras"]["time_grain_sqla"])

    # None should be allowed (allow_none=True)
    none_data = {
        "datasource": {"type": "table", "id": 1},
        "metrics": ["count"],
        "extras": {
            "time_grain_sqla": None,
        },
    }
    result = schema.load(none_data)
    assert result["extras"]["time_grain_sqla"] is None


def test_chart_data_query_object_schema_validates_result_color_selections(
    app_context: None,
) -> None:
    """Chart requests carry color selections, not rule IDs or client thresholds."""
    schema = ChartDataQueryObjectSchema()
    valid = schema.load(
        {
            "metrics": ["gross_revenue"],
            "table_color_filter": {
                "version": 2,
                "selections": [
                    {"column": "gross_revenue", "colors": ["RED", "GREEN"]},
                    {"column": "gross_revenue", "colors": ["YELLOW", "RED"]},
                ],
            },
        }
    )
    assert valid["table_color_filter"] == {
        "version": 2,
        "theme_mode": "default",
        "selections": [
            {"column": "gross_revenue", "colors": ["GREEN", "YELLOW", "RED"]}
        ],
    }
    assert valid["metrics"] == ["gross_revenue"]


def test_chart_data_query_object_schema_limits_color_selection_columns(
    app_context: None,
) -> None:
    """A query accepts at most one hundred target-column selections."""
    schema = ChartDataQueryObjectSchema()
    selections = [
        {"column": f"column_{index}", "colors": ["GREEN"]} for index in range(101)
    ]
    valid = schema.load(
        {"table_color_filter": {"version": 2, "selections": selections[:100]}}
    )
    assert len(valid["table_color_filter"]["selections"]) == 100
    with pytest.raises(ValidationError) as exc_info:
        schema.load({"table_color_filter": {"version": 2, "selections": selections}})
    assert "table_color_filter" in exc_info.value.messages


def test_table_color_schema_defaults_and_normalized_column_order() -> None:
    """Defaults create an empty snapshot selection and deterministic cache input."""
    schema = TableColorFilterSchema()
    assert schema.load({"version": 2}) == {
        "version": 2,
        "theme_mode": "default",
        "selections": [],
    }
    result = schema.load(
        {
            "version": 2,
            "theme_mode": "dark",
            "snapshot_id": "snapshot",
            "form_data_key": "owned-draft",
            "selections": [
                {"column": "z", "colors": ["RED", "GREEN"]},
                {"column": "a", "colors": ["YELLOW", "YELLOW"]},
            ],
        }
    )
    assert result["selections"] == [
        {"column": "a", "colors": ["YELLOW"]},
        {"column": "z", "colors": ["GREEN", "RED"]},
    ]
    assert result["snapshot_id"] == "snapshot"
    assert result["form_data_key"] == "owned-draft"
    assert result["theme_mode"] == "dark"


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"version": 1},
        {"version": "2"},
        {"version": 2.0},
        {"version": True},
        {"version": 2, "theme_mode": "system"},
        {"version": 2, "snapshot_id": ""},
        {"version": 2, "snapshot_id": "s" * 257},
        {"version": 2, "form_data_key": "d" * 257},
        {"version": 2, "selections": None},
        {"version": 2, "selections": "RED"},
    ],
)
def test_table_color_schema_rejects_invalid_versions_and_runtime_references(
    payload: dict[str, Any],
) -> None:
    """Types, supported modes and opaque-reference lengths are strictly bounded."""
    with pytest.raises(ValidationError):
        ChartDataQueryObjectSchema().load({"table_color_filter": payload})


@pytest.mark.parametrize(
    "selection",
    [
        {},
        {"column": "", "colors": ["GREEN"]},
        {"column": "c" * 1025, "colors": ["GREEN"]},
        {"column": 12, "colors": ["GREEN"]},
        {"column": "metric", "colors": []},
        {"column": "metric", "colors": ["RED"] * 4},
        {"column": "metric", "colors": ["green"]},
        {"column": "metric", "colors": ["BLUE"]},
        {"column": "metric", "colors": ["#ff0000"]},
        {"column": "metric", "colors": [None]},
    ],
)
def test_table_color_schema_rejects_invalid_columns_and_colors(
    selection: dict[str, Any],
) -> None:
    """Only the three supported final paint categories may be selected."""
    with pytest.raises(ValidationError):
        TableColorFilterSchema().load({"version": 2, "selections": [selection]})


@pytest.mark.parametrize(
    "extra",
    [
        {"threshold": 42},
        {"rule_id": "old-rule"},
        {"sql": "SUM(value)"},
        {"backgroundColor": "#ff0000"},
        {"alertLevel": "RED"},
    ],
)
def test_table_color_schema_rejects_client_rule_and_style_fields(
    extra: dict[str, Any],
) -> None:
    """Unknown fields fail at both the request and individual-selection boundary."""
    schema = TableColorFilterSchema()
    with pytest.raises(ValidationError, match="Unknown field"):
        schema.load({"version": 2, **extra})
    with pytest.raises(ValidationError, match="Unknown field"):
        schema.load(
            {
                "version": 2,
                "selections": [{"column": "metric", "colors": ["GREEN"], **extra}],
            }
        )


def test_table_color_schema_bounds_utf8_request_bytes_not_character_count() -> None:
    """Valid individual fields can still exceed the 64-KiB complete-object bound."""
    schema = TableColorFilterSchema()
    ascii_payload = {
        "version": 2,
        "selections": [
            {"column": f"{index}" + "x" * 500, "colors": ["GREEN"]}
            for index in range(50)
        ],
    }
    assert len(schema.load(ascii_payload)["selections"]) == 50
    unicode_payload = {
        "version": 2,
        "selections": [
            {"column": f"{index}" + "中" * 500, "colors": ["GREEN"]}
            for index in range(50)
        ],
    }
    with pytest.raises(ValidationError, match="request is too large"):
        schema.load(unicode_payload)


def test_table_color_schema_accepts_boundary_lengths() -> None:
    """Maximum-length column names and references remain usable."""
    result = TableColorFilterSchema().load(
        {
            "version": 2,
            "snapshot_id": "s" * 256,
            "form_data_key": "d" * 256,
            "selections": [
                {"column": "c" * 1024, "colors": ["GREEN", "YELLOW", "RED"]}
            ],
        }
    )
    assert len(result["selections"][0]["column"]) == 1024


def test_chart_data_schema_legacy_fields_are_accepted_only_for_factory_discard() -> (
    None
):
    """Old saved query contexts cannot re-enable the retired SQL filtering engine."""
    parsed = ChartDataQueryObjectSchema().load(
        {
            "metrics": ["profit"],
            "alert_filters": [{"rule_id": "not-a-uuid", "level": "PURPLE"}],
            "is_table_alert_totals": True,
        }
    )
    normalized = QueryContextFactory._normalize_color_query(parsed)
    assert normalized == ChartDataQueryObjectSchema().load({"metrics": ["profit"]})


@pytest.mark.parametrize(
    "app",
    [{"TIME_GRAIN_ADDONS": {"PT10M": "10 minutes"}}],
    indirect=True,
)
def test_time_grain_validation_with_config_addons(app_context: None) -> None:
    """Test that validation includes TIME_GRAIN_ADDONS from config"""
    schema = ChartDataProphetOptionsSchema()

    # Custom time grain should now be valid
    custom_data = {
        "time_grain": "PT10M",
        "periods": 5,
        "confidence_interval": 0.9,
    }
    result = schema.load(custom_data)
    assert result["time_grain"] == "PT10M"
