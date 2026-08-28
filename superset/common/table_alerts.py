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
"""Trusted static style resolution for the legacy unfiltered XLSX export."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Collection, Literal, TYPE_CHECKING

from superset.exceptions import QueryObjectValidationError

if TYPE_CHECKING:
    from superset.connectors.sqla.models import SqlaTable
    from superset.models.slice import Slice

STYLED_XLSX_UNSUPPORTED_MESSAGE = (
    "STYLED_XLSX_UNSUPPORTED: styled XLSX requires a saved classic Table chart"
)

RANGE_OPERATORS = frozenset(
    {
        "< x <",
        "≤ x ≤",
        "≤ x <",
        "< x ≤",
    }
)

TableStyleDimension = Literal["background", "font", "data_bar"]

STYLE_OPERATORS = frozenset(
    {
        "None",
        "=",
        "≠",
        "<",
        ">",
        "≤",
        "≥",
        "< x <",
        "≤ x ≤",
        "≤ x <",
        "< x ≤",
        "begins with",
        "ends with",
        "containing",
        "not containing",
        "is true",
        "is false",
        "is null",
        "is not null",
    }
)


@dataclass(frozen=True)
class ResolvedTableStyleRule:
    """A saved conditional-formatting rule safe for static XLSX evaluation."""

    source_column: str
    target_column: str | None
    dimension: TableStyleDimension
    color: str
    operator: str
    target_value: object = None
    target_value_left: object = None
    target_value_right: object = None
    use_gradient: bool = True


def _decimal_string(value: object) -> str:
    """Normalize a finite legacy XLSX threshold without SQL/filter metadata."""
    if isinstance(value, bool) or value is None:
        raise ValueError("Style threshold must be a finite number")
    try:
        decimal_value = Decimal(str(value))
    except (InvalidOperation, ValueError) as ex:
        raise ValueError("Style threshold must be a finite number") from ex
    if not decimal_value.is_finite():
        raise ValueError("Style threshold must be a finite number")
    return format(decimal_value.normalize(), "f")


class TableRuleResolver:
    """Resolve legacy XLSX styles from a trusted saved Table chart."""

    def __init__(self, slice_: Slice, datasource: SqlaTable) -> None:
        self._slice = slice_
        self._datasource = datasource

    def resolve_styles(  # noqa: C901
        self, columns: Collection[str]
    ) -> list[ResolvedTableStyleRule]:
        """Resolve saved Table conditional formatting without client style input."""
        if (
            self._slice.viz_type != "table"
            or self._slice.datasource_type != "table"
            or self._slice.datasource_id != self._datasource.id
        ):
            raise QueryObjectValidationError(STYLED_XLSX_UNSUPPORTED_MESSAGE)

        available_columns = set(columns)
        saved_rules = self._slice.form_data.get("conditional_formatting")
        if not isinstance(saved_rules, list):
            return []

        resolved: list[ResolvedTableStyleRule] = []
        for candidate in saved_rules:
            if not isinstance(candidate, dict):
                continue
            source_column = candidate.get("column")
            operator = candidate.get("operator")
            color = candidate.get("colorScheme")
            if (
                not isinstance(source_column, str)
                or source_column not in available_columns
                or operator not in STYLE_OPERATORS
                or not isinstance(color, str)
                or not color
            ):
                continue

            target_setting = candidate.get("columnFormatting")
            entire_row = (
                target_setting == "ENTIRE_ROW" or candidate.get("toAllRow") is True
            )
            target_column = None if entire_row else source_column
            if (
                not entire_row
                and isinstance(target_setting, str)
                and target_setting not in {"", "BACKGROUND_COLOR", "TEXT_COLOR"}
            ):
                target_column = target_setting
            if target_column is not None and target_column not in available_columns:
                continue

            object_formatting = candidate.get("objectFormatting")
            if object_formatting == "CELL_BAR":
                if entire_row:
                    continue
                dimension: TableStyleDimension = "data_bar"
            elif (
                object_formatting == "TEXT_COLOR"
                or candidate.get("toTextColor") is True
            ):
                dimension = "font"
            else:
                dimension = "background"

            target_value = candidate.get("targetValue")
            target_value_left = candidate.get("targetValueLeft")
            target_value_right = candidate.get("targetValueRight")
            if operator in {"<", ">", "≤", "≥"}:
                try:
                    _decimal_string(target_value)
                except ValueError:
                    continue
            elif operator in RANGE_OPERATORS:
                try:
                    left = Decimal(_decimal_string(target_value_left))
                    right = Decimal(_decimal_string(target_value_right))
                except ValueError:
                    continue
                if left >= right:
                    continue
            elif operator in {"=", "≠"} and target_value is None:
                continue
            elif operator in {
                "begins with",
                "ends with",
                "containing",
                "not containing",
            } and not isinstance(target_value, str):
                continue

            resolved.append(
                ResolvedTableStyleRule(
                    source_column=source_column,
                    target_column=target_column,
                    dimension=dimension,
                    color=color,
                    operator=operator,
                    target_value=target_value,
                    target_value_left=target_value_left,
                    target_value_right=target_value_right,
                    use_gradient=candidate.get("useGradient") is not False,
                )
            )
        return resolved


def _as_decimal(value: object) -> Decimal | None:
    """Read a finite legacy numeric operand without coercing NULL or booleans."""
    if isinstance(value, bool) or value is None:
        return None
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    return parsed if parsed.is_finite() else None


def table_style_rule_matches(  # noqa: C901
    rule: ResolvedTableStyleRule, value: object
) -> bool:
    """Evaluate the legacy static-XLSX comparison semantics."""
    operator = rule.operator
    if value is None:
        return operator == "is null"
    if operator == "is null":
        return False
    if operator == "is not null":
        return True
    if operator == "is true":
        return value is True
    if operator == "is false":
        return value is False
    if operator == "None":
        return True

    if operator in {"begins with", "ends with", "containing", "not containing"}:
        if not isinstance(value, str) or not isinstance(rule.target_value, str):
            return False
        if operator == "begins with":
            return value.startswith(rule.target_value)
        if operator == "ends with":
            return value.endswith(rule.target_value)
        contains = rule.target_value.lower() in value.lower()
        return contains if operator == "containing" else not contains

    value_decimal = _as_decimal(value)
    target_decimal = _as_decimal(rule.target_value)
    if operator in {"=", "≠"}:
        matches = (
            value_decimal == target_decimal
            if value_decimal is not None and target_decimal is not None
            else value == rule.target_value
        )
        return matches if operator == "=" else not matches
    if value_decimal is None:
        return False
    if operator in {"<", ">", "≤", "≥"}:
        if target_decimal is None:
            return False
        return {
            "<": value_decimal < target_decimal,
            ">": value_decimal > target_decimal,
            "≤": value_decimal <= target_decimal,
            "≥": value_decimal >= target_decimal,
        }[operator]

    left = _as_decimal(rule.target_value_left)
    right = _as_decimal(rule.target_value_right)
    if left is None or right is None:
        return False
    lower = value_decimal >= left if operator.startswith("≤") else value_decimal > left
    upper = value_decimal <= right if operator.endswith("≤") else value_decimal < right
    return lower and upper
