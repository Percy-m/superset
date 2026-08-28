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
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from superset.common.table_color_styles import (
    resolve_color_capabilities,
    resolve_table_color_styles,
)
from superset.utils import json

FIXTURE_DATA = json.loads(
    (Path(__file__).parents[2] / "testdata/table_color_filters/styles.json").read_text()
)
FIXTURES = FIXTURE_DATA["cases"]


@pytest.mark.parametrize("case", FIXTURES, ids=[case["name"] for case in FIXTURES])
def test_frontend_backend_style_golden(case: dict[str, Any]) -> None:
    """The same declared inputs and expected paints are used by the TS suite."""
    original = copy.deepcopy(case)
    actual = resolve_table_color_styles(
        case["records"],
        case["columns"],
        case["coltypes"],
        case["form_data"],
        {**FIXTURE_DATA["default_theme"], **case.get("theme", {})},
        case.get("source_page_size", 0),
    )
    for key, expected in case["expected"].items():
        assert dict(actual)[key] == expected
    assert case == original


@pytest.mark.parametrize(
    ("operator", "target", "values", "expected"),
    [
        ("=", 1, [1, "1", True, None], [True, False, False, False]),
        ("≠", 1, [1, "1", True, None], [False, True, True, False]),
        (">", 1, [2, "2", True, False], [True, True, False, False]),
        ("≥", 1, [1, "1", True, False], [True, True, True, False]),
        ("<", 1, [0, "0", True, False], [True, True, False, True]),
        ("≤", 1, [1, "1", True, False], [True, True, True, True]),
        ("begins with", "Ab", ["Abc", "abc", "xAb", None], [True, False, False, False]),
        ("ends with", "Ab", ["xAb", "xab", "Abx", None], [True, False, False, False]),
        (
            "containing",
            "Ab",
            ["xAby", "xaby", "other", None],
            [True, True, False, False],
        ),
        (
            "not containing",
            "Ab",
            ["xAby", "xaby", "other", ""],
            [False, False, True, False],
        ),
        ("is true", 0, [True, False, 1, None], [True, False, False, False]),
        ("is false", 0, [True, False, 0, None], [False, True, False, False]),
        ("is null", 0, [True, False, 0, None], [False, False, False, True]),
        ("is not null", 0, [True, False, 0, "x"], [True, True, False, False]),
        ("None", 0, [True, False, "x", " "], [True, True, True, False]),
    ],
)
def test_native_comparators_preserve_javascript_boundaries(
    operator: str, target: object, values: list[object], expected: list[bool]
) -> None:
    """Numeric coercion, strict equality and Boolean operators are not SQL semantics."""
    result = resolve_table_color_styles(
        [{"value": value} for value in values],
        ["value"],
        [1],
        {
            "show_cell_bars": False,
            "conditional_formatting": [
                {
                    "column": "value",
                    "operator": operator,
                    "targetValue": target,
                    "colorScheme": "colorSuccess",
                    "useGradient": False,
                    "filterable": True,
                }
            ],
        },
    )
    assert [bool(row["value"]["colors"]) for row in result["styles"]] == expected


@pytest.mark.parametrize(
    ("operator", "expected"),
    [
        ("< x <", [False, True, False]),
        ("≤ x ≤", [True, True, True]),
        ("≤ x <", [True, True, False]),
        ("< x ≤", [False, True, True]),
    ],
)
def test_all_native_range_boundaries(operator: str, expected: list[bool]) -> None:
    """All four open/closed interval choices use the frontend endpoint behavior."""
    result = resolve_table_color_styles(
        [{"value": value} for value in (0, 1, 2)],
        ["value"],
        [0],
        {
            "show_cell_bars": False,
            "conditional_formatting": [
                {
                    "column": "value",
                    "operator": operator,
                    "targetValueLeft": 0,
                    "targetValueRight": 2,
                    "colorScheme": "colorError",
                    "useGradient": False,
                }
            ],
        },
    )
    assert [bool(row["value"]["colors"]) for row in result["styles"]] == expected


def test_missing_unary_target_matches_native_no_formatter_behavior() -> None:
    """Native serialized unary operators still need their dummy target value."""
    result = resolve_table_color_styles(
        [{"value": None}],
        ["value"],
        [3],
        {
            "conditional_formatting": [
                {
                    "column": "value",
                    "operator": "is null",
                    "colorScheme": "colorSuccess",
                    "useGradient": False,
                    "filterable": True,
                }
            ]
        },
    )
    assert result["styles"] == [{"value": {"colors": []}}]


def test_temporal_objects_do_not_act_like_raw_strings_or_null() -> None:
    """DateWithFormatter wraps null too; only another source can color that date."""
    result = resolve_table_color_styles(
        [{"date": None}, {"date": "2026-08-28"}],
        ["date"],
        [2],
        {
            "conditional_formatting": [
                {
                    "column": "date",
                    "operator": "is null",
                    "targetValue": 0,
                    "colorScheme": "colorError",
                    "useGradient": False,
                }
            ]
        },
    )
    assert result["styles"] == [{"date": {"colors": []}}, {"date": {"colors": []}}]


def test_percent_result_values_not_original_metric_drive_color() -> None:
    """A contribution output retains the denominator already used by post-processing."""
    result = resolve_table_color_styles(
        [{"amount": 90, "%amount": 0.9}, {"amount": 10, "%amount": 0.1}],
        ["amount", "%amount"],
        [0, 0],
        {
            "metrics": [],
            "percent_metrics": ["amount"],
            "show_cell_bars": False,
            "conditional_formatting": [
                {
                    "column": "%amount",
                    "operator": ">",
                    "targetValue": 0.5,
                    "colorScheme": "colorWarning",
                    "useGradient": False,
                    "filterable": True,
                }
            ],
        },
    )
    assert result["columns"] == ["%amount"]
    assert result["records"] == [{"%amount": 0.9}, {"%amount": 0.1}]
    assert result["styles"][0]["%amount"]["colors"] == ["YELLOW"]
    assert result["styles"][1]["%amount"]["colors"] == []


def test_gradient_on_other_target_blocks_that_target_not_condition_source() -> None:
    """Mixed pure/gradient tables keep unrelated plain targets available."""
    capabilities = resolve_color_capabilities(
        {
            "conditional_formatting": [
                {
                    "column": "source",
                    "columnFormatting": "target",
                    "objectFormatting": "BACKGROUND_COLOR",
                    "operator": "None",
                    "colorScheme": "colorSuccess",
                    "filterable": True,
                },
                {
                    "column": "source",
                    "operator": "None",
                    "colorScheme": "colorSuccess",
                    "useGradient": False,
                    "filterable": True,
                },
            ]
        },
        ["source", "target"],
        [0, 1],
    )
    assert capabilities["source"] == {"enabled": True, "supported": True}
    assert capabilities["target"]["enabled"] is True
    assert capabilities["target"]["supported"] is False


def test_background_suppresses_cell_bar_even_if_later_bar_rule_matches() -> None:
    """Invisible bars must not contribute an additional selected color."""
    result = resolve_table_color_styles(
        [{"value": 10}],
        ["value"],
        [0],
        {
            "query_mode": "raw",
            "conditional_formatting": [
                {
                    "column": "value",
                    "operator": "None",
                    "colorScheme": "colorError",
                    "useGradient": False,
                },
                {
                    "column": "value",
                    "operator": "None",
                    "colorScheme": "colorSuccess",
                    "objectFormatting": "CELL_BAR",
                    "filterable": True,
                },
            ],
        },
    )
    assert result["styles"] == [
        {"value": {"colors": ["RED"], "backgroundColor": "#e04355"}}
    ]


@pytest.mark.parametrize(("value", "bar_color"), [(5, "#5ac18950"), (-5, "#e0435550")])
def test_default_cell_bar_paint_does_not_contribute_filter_colors(
    value: int, bar_color: str
) -> None:
    """Default positive/negative bars keep their rendering data, not filter colors."""
    result = resolve_table_color_styles(
        [{"value": value}],
        ["value"],
        [0],
        {"query_mode": "raw", "show_cell_bars": True, "color_pn": True},
    )
    assert result["styles"] == [
        {
            "value": {
                "cellBar": {
                    "color": bar_color,
                    "width": 100,
                    "offset": 0,
                    "min": 0,
                    "max": 5,
                },
                "colors": [],
            }
        }
    ]
    assert result["catalog"] == {"value": []}
    assert result["capabilities"] == {"value": {"enabled": False, "supported": True}}


@pytest.mark.parametrize("show_bars", [True, False])
def test_only_matched_visible_conditional_cell_bars_contribute_filter_colors(
    show_bars: bool,
) -> None:
    """A visible matching bar counts; hidden, zero and default fallback bars do not."""
    result = resolve_table_color_styles(
        [{"value": -5}, {"value": 0}, {"value": 5}],
        ["value"],
        [0],
        {
            "query_mode": "raw",
            "show_cell_bars": show_bars,
            "color_pn": True,
            "conditional_formatting": [
                {
                    "column": "value",
                    "operator": "≤",
                    "targetValue": 0,
                    "colorScheme": "colorSuccess",
                    "useGradient": False,
                    "objectFormatting": "CELL_BAR",
                    "filterable": True,
                }
            ],
        },
    )
    assert [row["value"]["colors"] for row in result["styles"]] == [
        ["GREEN"] if show_bars else [],
        [],
        [],
    ]
    assert result["catalog"] == {"value": ["GREEN"] if show_bars else []}
    assert result["styles"][1]["value"] == {"colors": []}
    if show_bars:
        assert result["styles"][0]["value"]["cellBar"]["color"] == "#5ac199"
        assert result["styles"][2]["value"]["cellBar"]["color"] == "#5ac18950"
    else:
        assert all("cellBar" not in row["value"] for row in result["styles"])


@pytest.mark.parametrize("value", [-5, 5])
def test_default_cell_bars_do_not_add_colors_to_explicit_text(value: int) -> None:
    """An enabled column exposes its explicit text color without default bar colors."""
    result = resolve_table_color_styles(
        [{"value": value}],
        ["value"],
        [0],
        {
            "query_mode": "raw",
            "show_cell_bars": True,
            "conditional_formatting": [
                {
                    "column": "value",
                    "operator": "None",
                    "colorScheme": "colorWarning",
                    "useGradient": False,
                    "objectFormatting": "TEXT_COLOR",
                    "filterable": True,
                }
            ],
        },
    )
    assert result["styles"][0]["value"]["colors"] == ["YELLOW"]
    assert result["styles"][0]["value"]["textColor"] == "rgb(252, 199, 0)"
    assert result["styles"][0]["value"]["cellBar"]["color"] == (
        "#e0435550" if value < 0 else "#5ac18950"
    )
    assert result["catalog"] == {"value": ["YELLOW"]}


def test_empty_result_keeps_enabled_target_capability_and_empty_catalog() -> None:
    """A zero-row result must still expose clear/retry-capable column metadata."""
    result = resolve_table_color_styles(
        [],
        ["value"],
        [0],
        {
            "conditional_formatting": [
                {
                    "column": "value",
                    "operator": "None",
                    "colorScheme": "colorSuccess",
                    "useGradient": False,
                    "filterable": True,
                }
            ]
        },
    )
    assert result["styles"] == []
    assert result["catalog"] == {"value": []}
    assert result["capabilities"] == {"value": {"enabled": True, "supported": True}}


def test_numeric_transport_matches_browser_without_mutating_export_precision() -> None:
    """JavaScript compares doubles; the XLSX source must still retain exact integers."""
    original = 9007199254740993
    result = resolve_table_color_styles(
        [{"value": original}],
        ["value"],
        [0],
        {
            "show_cell_bars": False,
            "conditional_formatting": [
                {
                    "column": "value",
                    "operator": "=",
                    "targetValue": 9007199254740992,
                    "colorScheme": "colorSuccess",
                    "useGradient": False,
                }
            ],
        },
    )
    assert result["styles"][0]["value"]["colors"] == ["GREEN"]
    assert result["records"] == [{"value": original}]


def test_decimal_aggregate_is_numeric_after_json_transport() -> None:
    """Decimal query output remains a numeric metric, including comparison fields."""
    result = resolve_table_color_styles(
        [{"value": Decimal("1.25"), "value__1 year ago": Decimal("1.0")}],
        ["value", "value__1 year ago"],
        [0, 0],
        {
            "query_mode": "aggregate",
            "metrics": ["value"],
            "time_compare": ["1 year ago"],
            "comparison_type": "values",
            "show_cell_bars": False,
            "conditional_formatting": [
                {
                    "column": "% value",
                    "operator": "≥",
                    "targetValue": 0.25,
                    "colorScheme": "colorSuccess",
                    "useGradient": False,
                    "filterable": True,
                }
            ],
        },
    )
    assert result["records"][0]["Main value"] == Decimal("1.25")
    assert result["records"][0]["% value"] == 0.25
    assert result["styles"][0]["% value"]["colors"] == ["GREEN"]


def test_nan_is_the_null_value_serialized_by_chart_data() -> None:
    """Server NaN must not bypass a native is-null rule after JSON serialization."""
    result = resolve_table_color_styles(
        [{"value": float("nan")}],
        ["value"],
        [0],
        {
            "show_cell_bars": False,
            "conditional_formatting": [
                {
                    "column": "value",
                    "operator": "is null",
                    "targetValue": 0,
                    "colorScheme": "colorError",
                    "useGradient": False,
                }
            ],
        },
    )
    assert result["styles"][0]["value"]["colors"] == ["RED"]


def test_original_page_cell_bar_bounds_are_not_replaced_by_complete_result_range() -> (
    None
):
    """The same value may have different bar geometry on different source pages."""
    result = resolve_table_color_styles(
        [{"value": 10}, {"value": 20}, {"value": 10}, {"value": 200}],
        ["value"],
        [0],
        {
            "query_mode": "raw",
            "conditional_formatting": [
                {
                    "column": "value",
                    "operator": "None",
                    "colorScheme": "colorSuccess",
                    "objectFormatting": "CELL_BAR",
                    "filterable": True,
                }
            ],
        },
        source_page_size=2,
    )
    assert result["styles"][0]["value"]["cellBar"]["width"] == 50
    assert result["styles"][2]["value"]["cellBar"]["width"] == 5
    assert result["styles"][0]["value"]["cellBar"]["max"] == 20
    assert result["styles"][2]["value"]["cellBar"]["max"] == 200


def test_comparison_main_label_preserves_native_localized_key_lookup() -> None:
    """The native label-length lookup remains distinct from untranslated keys."""
    result = resolve_table_color_styles(
        [{"value": 20, "value__1 year ago": 10}],
        ["value", "value__1 year ago"],
        [0, 0],
        {
            "query_mode": "aggregate",
            "metrics": ["value"],
            "time_compare": ["1 year ago"],
            "comparison_type": "values",
            "comparison_color_enabled": True,
            "show_cell_bars": False,
        },
        comparison_main_label="主值",
    )
    assert result["columns"] == ["Main value", "# value", "△ value", "% value"]
    assert result["records"][0]["Main value"] == 20
    assert result["styles"][0]["Main value"] == {"colors": []}
    assert result["styles"][0]["# value"]["colors"] == ["GREEN"]


def test_column_comparison_rule_uses_translated_main_label_for_arrow() -> None:
    """An explicit comparison rule identifies the translated Main display label."""
    result = resolve_table_color_styles(
        [{"value": 20, "value__1 year ago": 10}],
        ["value", "value__1 year ago"],
        [0, 0],
        {
            "query_mode": "aggregate",
            "metrics": ["value"],
            "time_compare": ["1 year ago"],
            "comparison_type": "values",
            "show_cell_bars": False,
            "conditional_formatting": [
                {
                    "column": "Main value",
                    "colorScheme": "Green",
                    "filterable": True,
                }
            ],
        },
        comparison_main_label="主值",
    )
    assert result["styles"][0]["Main value"]["arrow"] == {
        "color": "#5ac189",
        "symbol": "↑",
    }


def test_cell_bar_just_below_half_percent_has_no_visible_color() -> None:
    """Math.round must not promote a zero-width native bar at the half boundary."""
    result = resolve_table_color_styles(
        [{"value": 0.004999999999999999}, {"value": 1}],
        ["value"],
        [0],
        {"query_mode": "raw"},
    )
    assert result["styles"][0]["value"] == {"colors": []}
    assert result["styles"][1]["value"]["cellBar"]["width"] == 100


def test_native_gradient_scientific_notation_retains_invalid_paint_boundary() -> None:
    """The native e-shift helper creates an invalid alpha for scientific notation."""
    result = resolve_table_color_styles(
        [{"value": 0}, {"value": 1e-7}, {"value": 1}],
        ["value"],
        [0],
        {
            "show_cell_bars": False,
            "conditional_formatting": [
                {
                    "column": "value",
                    "operator": "None",
                    "colorScheme": "colorError",
                }
            ],
        },
    )
    assert result["styles"][1]["value"] == {
        "backgroundColor": "#e04355AN",
        "colors": [],
    }
