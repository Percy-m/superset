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

from types import SimpleNamespace
from typing import cast

import pytest

from superset.common.table_alerts import (
    ResolvedTableStyleRule,
    table_style_rule_matches,
    TableRuleResolver,
)
from superset.connectors.sqla.models import SqlaTable
from superset.exceptions import QueryObjectValidationError
from superset.models.slice import Slice


def make_resolver(rules: list[dict[str, object]]) -> TableRuleResolver:
    """Build a saved Table style resolver without alert predicate metadata."""
    slice_ = SimpleNamespace(
        viz_type="table",
        datasource_type="table",
        datasource_id=7,
        form_data={"conditional_formatting": rules},
    )
    return TableRuleResolver(
        cast(Slice, slice_),
        cast(SqlaTable, SimpleNamespace(id=7)),
    )


def test_table_rule_resolver_reads_saved_style_priority_and_targets() -> None:
    """XLSX styles preserve saved order and never accept client rule payloads."""
    rules = [
        {
            "column": "gross_revenue",
            "operator": "<",
            "targetValue": 0,
            "colorScheme": "#F5222D",
            "objectFormatting": "BACKGROUND_COLOR",
            "columnFormatting": "ENTIRE_ROW",
            "useGradient": False,
        },
        {
            "column": "gross_revenue",
            "operator": "≥",
            "targetValue": 0,
            "colorScheme": "#52C41A",
            "objectFormatting": "TEXT_COLOR",
            "columnFormatting": "quantity",
            "useGradient": False,
        },
        {
            "column": "gross_revenue",
            "operator": "None",
            "colorScheme": "#1677FF",
            "objectFormatting": "CELL_BAR",
            "useGradient": False,
        },
    ]

    resolved = make_resolver(rules).resolve_styles(["gross_revenue", "quantity"])

    assert [(rule.dimension, rule.target_column) for rule in resolved] == [
        ("background", None),
        ("font", "quantity"),
        ("data_bar", "gross_revenue"),
    ]
    assert [rule.color for rule in resolved] == ["#F5222D", "#52C41A", "#1677FF"]


@pytest.mark.parametrize(
    ("operator", "value", "expected"),
    [
        ("<", -1, True),
        ("≥", 10, True),
        ("≤ x <", 20, True),
        ("containing", "ClickHouse 21.3", True),
        ("is null", None, True),
        ("is not null", None, False),
    ],
)
def test_table_style_rule_matches_legacy_xlsx_comparators(
    operator: str,
    value: object,
    expected: bool,
) -> None:
    """The unfiltered XLSX path retains its saved comparison behavior."""
    rule = ResolvedTableStyleRule(
        source_column="value",
        target_column="value",
        dimension="background",
        color="#FF0000",
        operator=operator,
        target_value=(
            "House" if operator == "containing" else 0 if operator == "<" else 10
        ),
        target_value_left=10,
        target_value_right=30,
        use_gradient=False,
    )

    assert table_style_rule_matches(rule, value) is expected


@pytest.mark.parametrize("target", [None, True, "bad", "Infinity", "NaN"])
def test_static_styles_skip_invalid_numeric_thresholds(target: object) -> None:
    """Invalid thresholds never become Excel conditional formatting rules."""
    assert (
        make_resolver(
            [
                {
                    "column": "value",
                    "operator": ">",
                    "targetValue": target,
                    "colorScheme": "colorSuccess",
                }
            ]
        ).resolve_styles(["value"])
        == []
    )


@pytest.mark.parametrize("left,right", [(1, 1), (2, 1), (None, 1), (0, "NaN")])
def test_static_styles_skip_invalid_ranges(left: object, right: object) -> None:
    """Ranges must retain the legacy strictly ordered finite endpoints."""
    assert (
        make_resolver(
            [
                {
                    "column": "value",
                    "operator": "≤ x ≤",
                    "targetValueLeft": left,
                    "targetValueRight": right,
                    "colorScheme": "colorSuccess",
                }
            ]
        ).resolve_styles(["value"])
        == []
    )


@pytest.mark.parametrize(
    "overrides",
    [
        {"viz_type": "bar"},
        {"datasource_type": "other"},
        {"datasource_id": 8},
    ],
)
def test_static_styles_require_saved_classic_table(
    overrides: dict[str, object],
) -> None:
    """The old XLSX route continues to validate its saved chart and dataset."""
    properties = {
        "viz_type": "table",
        "datasource_type": "table",
        "datasource_id": 7,
        "form_data": {},
        **overrides,
    }
    resolver = TableRuleResolver(
        cast(Slice, SimpleNamespace(**properties)),
        cast(SqlaTable, SimpleNamespace(id=7)),
    )
    with pytest.raises(QueryObjectValidationError, match="STYLED_XLSX_UNSUPPORTED"):
        resolver.resolve_styles(["value"])


def test_static_styles_preserve_legacy_targets_and_skip_entire_row_bars() -> None:
    """Removing SQL filtering does not reinterpret legacy export styles."""
    resolved = make_resolver(
        [
            {
                "column": "value",
                "operator": "None",
                "colorScheme": "#ff0000",
                "toAllRow": True,
            },
            {
                "column": "value",
                "operator": "None",
                "colorScheme": "#ff0000",
                "columnFormatting": "ENTIRE_ROW",
                "objectFormatting": "CELL_BAR",
            },
            {
                "column": "missing",
                "operator": "None",
                "colorScheme": "#ff0000",
            },
            {
                "column": "value",
                "operator": "None",
                "colorScheme": "#ff0000",
                "columnFormatting": "missing",
            },
            {
                "column": "value",
                "operator": "None",
                "colorScheme": "#ff0000",
                "toTextColor": True,
            },
        ]
    ).resolve_styles(["value"])
    assert [(rule.dimension, rule.target_column) for rule in resolved] == [
        ("background", None),
        ("font", "value"),
    ]
    assert all(rule.use_gradient for rule in resolved)
