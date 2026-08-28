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
"""Evaluate classic Table paints after query post-processing.

The reference semantics are the frontend color formatter and Table cell renderer.
This module never translates a formatting condition into a database predicate.
Cross-language fixtures exercise the two implementations against the same inputs.
"""

from __future__ import annotations

import math
import numbers
import re
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import cast, Literal, Mapping, Sequence, TypedDict, TypeGuard

import pandas as pd
from PIL import ImageColor
from typing_extensions import NotRequired

from superset.utils import json

TablePaintColor = Literal["GREEN", "YELLOW", "RED"]
PAINT_COLORS: tuple[TablePaintColor, ...] = ("GREEN", "YELLOW", "RED")
GRADIENT_UNSUPPORTED = "TABLE_COLOR_FILTER_GRADIENT_UNSUPPORTED"
GRADIENT_MESSAGE = (
    "This column contains gradient formatting; color filtering is unavailable."
)
DEFAULT_TABLE_THEME = {
    "colorSuccess": "#5ac189",
    "colorWarning": "#fcc700",
    "colorError": "#e04355",
    "colorFill": "rgba(0, 0, 0, 0.15)",
}
_COLOR_FAMILIES: dict[str, TablePaintColor] = {
    "colorsuccess": "GREEN",
    "colorwarning": "YELLOW",
    "colorerror": "RED",
    "#52c41a": "GREEN",
    "#00ff00": "GREEN",
    "#0f0": "GREEN",
    "#faad14": "YELLOW",
    "#ffff00": "YELLOW",
    "#ff0": "YELLOW",
    "#ff4d4f": "RED",
    "#f5222d": "RED",
    "#ff0000": "RED",
    "#f00": "RED",
}
_RANGES = frozenset({"< x <", "≤ x ≤", "≤ x <", "< x ≤"})
_OPERATORS = _RANGES | {
    "None",
    "=",
    "≠",
    ">",
    "≥",
    "<",
    "≤",
    "begins with",
    "ends with",
    "containing",
    "not containing",
    "is true",
    "is false",
    "is null",
    "is not null",
}
_MISSING = object()


class TableCellBar(TypedDict):
    """Visible cell-bar geometry, frozen in its original page context."""

    color: str
    width: float
    offset: float
    min: float
    max: float


class TableCellArrow(TypedDict):
    """A visible time-comparison arrow."""

    color: str
    symbol: str


class TableCellPaint(TypedDict):
    """Final explicit paints, excluding automatically selected readable text."""

    backgroundColor: NotRequired[str]
    textColor: NotRequired[str]
    cellBar: NotRequired[TableCellBar]
    arrow: NotRequired[TableCellArrow]
    colors: list[TablePaintColor]


class TableColorReason(TypedDict):
    """Non-sensitive reason why a target cannot be filtered."""

    code: str
    message: str


class ColumnFilterCapability(TypedDict):
    """Filter-entry eligibility of one visible result column."""

    enabled: bool
    supported: bool
    reason: NotRequired[TableColorReason]


class TableColorStyleResult(TypedDict):
    """Display/export values and their row-aligned immutable paints."""

    records: list[dict[str, object]]
    columns: list[str]
    coltypes: list[int]
    styles: list[dict[str, TableCellPaint]]
    catalog: dict[str, list[TablePaintColor]]
    capabilities: dict[str, ColumnFilterCapability]


@dataclass(frozen=True)
class _TemporalValue:
    """Preserve JavaScript Date object identity and numeric coercion."""

    milliseconds: float


@dataclass(frozen=True)
class _Column:
    key: str
    label: str
    data_type: int
    metric: bool
    percent: bool
    config: Mapping[str, object]


def _number(value: object) -> TypeGuard[float]:
    return isinstance(value, numbers.Real) and not isinstance(value, bool)


def _js_number(value: object) -> float:
    if isinstance(value, _TemporalValue):
        return value.milliseconds
    if value is None:
        return 0.0
    if isinstance(value, bool):
        return float(value)
    if _number(value) or isinstance(value, Decimal):
        return float(value)
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return 0.0
        try:
            if re.fullmatch(r"0[xX][0-9a-fA-F]+|0[bB][01]+|0[oO][0-7]+", stripped):
                return float(int(stripped, 0))
            return float(stripped)
        except ValueError:
            return math.nan
    return math.nan


def _blank(value: object) -> bool:
    return (
        value is _MISSING
        or value is None
        or (_number(value) and math.isnan(value))
        or (isinstance(value, str) and not value.strip())
    )


def _strict_equal(left: object, right: object) -> bool:
    if _number(left) and _number(right):
        return float(left) == float(right)
    if isinstance(left, _TemporalValue) or isinstance(right, _TemporalValue):
        return left is right
    return type(left) is type(right) and left == right


def _less(left: object, right: object, *, inclusive: bool = False) -> bool:
    if isinstance(left, str) and isinstance(right, str):
        lhs = left.encode("utf-16-be", errors="surrogatepass")
        rhs = right.encode("utf-16-be", errors="surrogatepass")
        return lhs <= rhs if inclusive else lhs < rhs
    lhs_number, rhs_number = _js_number(left), _js_number(right)
    return lhs_number <= rhs_number if inclusive else lhs_number < rhs_number


def _js_round(value: float) -> float:
    if not math.isfinite(value):
        return value
    lower = math.floor(value)
    return float(lower if value - lower < 0.5 else lower + 1)


def _formatter_round(value: float) -> float:
    """Mirror the native decimal e-shift round, including scientific notation."""
    if not math.isfinite(value) or (value and not 1e-6 <= abs(value) < 1e21):
        return math.nan
    shifted = float(Decimal(str(value)).scaleb(2))
    rounded = _js_round(shifted)
    if not math.isfinite(rounded) or abs(rounded) >= 1e21:
        return math.nan
    return rounded / 100


def _extrema(values: Sequence[object]) -> tuple[float, float]:
    numeric = [_js_number(value) for value in values]
    if any(math.isnan(value) for value in numeric):
        return math.nan, math.nan
    return min(numeric, default=math.inf), max(numeric, default=-math.inf)


def _css_rgba(value: str) -> tuple[int, int, int, float] | None:
    candidate = value.strip()
    if candidate.lower() == "transparent":
        return 0, 0, 0, 0.0
    match = re.fullmatch(
        r"rgba?\(\s*(\d+(?:\.\d+)?)\s*,\s*(\d+(?:\.\d+)?)\s*,"
        r"\s*(\d+(?:\.\d+)?)(?:\s*,\s*(\d*(?:\.\d+)?))?\s*\)",
        candidate,
        re.IGNORECASE,
    )
    if match:
        red, green, blue = (int(_js_round(float(part))) for part in match.groups()[:3])
        alpha = float(match.group(4)) if match.group(4) else 1.0
        if (
            all(0 <= channel <= 255 for channel in (red, green, blue))
            and 0 <= alpha <= 1
        ):
            return red, green, blue, alpha
        return None
    try:
        red, green, blue, alpha_byte = ImageColor.getcolor(candidate, "RGBA")
    except (ValueError, TypeError):
        return None
    return red, green, blue, alpha_byte / 255.0


def _visible_color(value: str | None) -> bool:
    parsed = _css_rgba(value) if value else None
    return parsed is not None and parsed[3] > 0


def _text_color(value: str) -> str:
    parsed = _css_rgba(value)
    return f"rgb({parsed[0]}, {parsed[1]}, {parsed[2]})" if parsed else value


def _family(value: object) -> TablePaintColor | None:
    return _COLOR_FAMILIES.get(value.lower()) if isinstance(value, str) else None


def _rule_ready(rule: Mapping[str, object]) -> bool:
    operator = rule.get("operator")
    return isinstance(rule.get("column"), str) and (
        operator == "None"
        or (
            isinstance(operator, str)
            and (
                ("targetValueLeft" in rule and "targetValueRight" in rule)
                if operator in _RANGES
                else "targetValue" in rule
            )
        )
    )


def _rule_color(  # noqa: C901
    rule: Mapping[str, object],
    value: object,
    bounds: tuple[float, float],
    theme: Mapping[str, str],
) -> str | None:
    """Match getColorFunction, including its JS coercion and NULL boundaries."""
    operator, scheme = rule.get("operator"), rule.get("colorScheme")
    if not _rule_ready(rule) or not isinstance(scheme, str):
        return None
    if _blank(value) and operator != "is null":
        return None
    target = rule.get("targetValue", _MISSING)
    left = rule.get("targetValueLeft", _MISSING)
    right = rule.get("targetValueRight", _MISSING)
    minimum, maximum = bounds
    cutoff, extreme = target, target
    matches = False
    if operator == "None":
        cutoff, extreme = (minimum, maximum) if _number(value) else (value, value)
        matches = not _number(value) or minimum <= value <= maximum
    elif operator in {">", "≥", "<", "≤"} and _number(target):
        if operator in {">", "≥"}:
            matches = _less(target, value, inclusive=operator == "≥")
            extreme = maximum
        else:
            matches = _less(value, target, inclusive=operator == "≤")
            extreme = minimum
    elif operator == "=":
        matches = _strict_equal(value, target)
    elif operator == "≠" and _number(target):
        matches = not _strict_equal(value, target)
        extreme = minimum if abs(target - minimum) > abs(maximum - target) else maximum
    elif operator in _RANGES:
        matches = _less(left, value, inclusive=str(operator).startswith("≤")) and _less(
            value, right, inclusive=str(operator).endswith("≤")
        )
        cutoff, extreme = left, right
    elif isinstance(value, str) and isinstance(target, str):
        if operator == "begins with":
            matches = value.startswith(target)
        elif operator == "ends with":
            matches = value.endswith(target)
        elif operator == "containing":
            matches = target.lower() in value.lower()
        elif operator == "not containing":
            matches = target.lower() not in value.lower()
    if operator == "is true":
        matches = isinstance(value, bool) and value
    elif operator == "is false":
        matches = isinstance(value, bool) and not value
    elif operator == "is null":
        matches = value is None
    elif operator == "is not null":
        matches = isinstance(value, bool)
    if not matches:
        return None
    color = theme.get(scheme, scheme)
    if rule.get("useGradient") is False:
        return color
    opacity = 1.0
    if not _strict_equal(cutoff, extreme) and _number(value):
        minimum_opacity = 0.0 if operator == "None" else 0.05
        start, end = _js_number(cutoff), _js_number(extreme)
        if not math.isnan(start) and not math.isnan(end):
            delta = end - start
            if delta == 0:
                opacity = math.nan if value == start else 1.0
            else:
                rounded_opacity = _formatter_round(
                    abs((1.0 - minimum_opacity) / delta * (value - start))
                    + minimum_opacity
                )
                opacity = (
                    math.nan
                    if math.isnan(rounded_opacity)
                    else min(1.0, rounded_opacity)
                )
    # addAlpha concatenates the hex byte; invalid legacy CSS is not repaired here.
    suffix = f"{int(_js_round(opacity * 255)):02X}" if math.isfinite(opacity) else "AN"
    return color + suffix


def _metric_names(value: object) -> set[str]:
    if not isinstance(value, list):
        return set()
    names: set[str] = set()
    for item in value:
        if isinstance(item, str):
            names.add(item)
        elif isinstance(item, dict) and isinstance(item.get("label"), str):
            names.add(item["label"])
    return names


def _as_mapping(value: object) -> Mapping[str, object]:
    return cast(Mapping[str, object], value) if isinstance(value, dict) else {}


def _rules(form_data: Mapping[str, object]) -> list[Mapping[str, object]]:
    value = form_data.get("conditional_formatting")
    return (
        [_as_mapping(item) for item in value if isinstance(item, dict)]
        if isinstance(value, list)
        else []
    )


def _time_comparison(form_data: Mapping[str, object]) -> tuple[bool, str]:
    shifts = form_data.get("time_compare")
    values = shifts if isinstance(shifts, list) else [shifts] if shifts else []
    enabled = (
        bool(values)
        and form_data.get("query_mode") == "aggregate"
        and form_data.get("comparison_type") == "values"
    )
    ordered = [value for value in values if value not in {"custom", "inherit"}]
    if "custom" in values:
        ordered.append(form_data.get("start_date_offset"))
    if "inherit" in values:
        ordered.append("inherit")
    return enabled, str(ordered[0]) if ordered else "undefined"


def _truthy(value: object) -> bool:
    if value is _MISSING or value is None:
        return False
    if _number(value):
        return value != 0 and not math.isnan(value)
    return bool(value)


def _differences(original: object, previous: object) -> tuple[float, float]:
    main, prior = _js_number(original), _js_number(previous)
    difference = main - prior
    if not _truthy(original) and not _truthy(previous):
        percent = 0.0
    elif not _truthy(original) or not _truthy(previous):
        percent = 1.0 if _truthy(original) else -1.0
    else:
        percent = difference / abs(prior)
    return difference, percent


def _columns(
    records: Sequence[Mapping[str, object]],
    columns: Sequence[str],
    coltypes: Sequence[int],
    form_data: Mapping[str, object],
) -> list[_Column]:
    metrics = _metric_names(form_data.get("metrics"))
    raw_percent = _metric_names(form_data.get("percent_metrics"))
    percent_metrics = {"%" + value for value in raw_percent}
    config = _as_mapping(form_data.get("column_config"))
    # processColumns filters percent-only source metrics before assigning coltypes.
    visible_sources = [
        key for key in columns if not (key in raw_percent and key not in metrics)
    ]
    return [
        _Column(
            key=key,
            label=key,
            data_type=int(coltypes[index]) if index < len(coltypes) else 1,
            metric=key in metrics
            and all(row.get(key) is None or _number(row.get(key)) for row in records),
            percent=key in percent_metrics,
            config=_as_mapping(config.get(key)),
        )
        for index, key in enumerate(visible_sources)
    ]


def _display_data(  # noqa: C901
    records: Sequence[Mapping[str, object]],
    columns: Sequence[_Column],
    form_data: Mapping[str, object],
    comparison_main_label: str,
) -> tuple[list[dict[str, object]], list[_Column], bool, str]:
    comparison, suffix = _time_comparison(form_data)
    if not comparison:
        return [dict(row) for row in records], list(columns), False, suffix
    config = _as_mapping(form_data.get("column_config"))
    converted: list[_Column] = []
    for column in columns:
        if suffix in column.key:
            continue
        if (column.metric or column.percent) and column.data_type == 0:
            for label in ("Main", "#", "△", "%"):
                key = f"{label} {column.key}"
                converted.append(
                    _Column(
                        key,
                        comparison_main_label if label == "Main" else label,
                        0,
                        column.metric,
                        column.percent,
                        _as_mapping(config.get(key)),
                    )
                )
        elif not column.metric and not column.percent:
            converted.append(column)
    output: list[dict[str, object]] = []
    metric_keys = {column.key for column in columns if column.metric or column.percent}
    for row in records:
        display = {key: value for key, value in row.items() if key not in metric_keys}
        for column in columns:
            if (
                (column.metric or column.percent)
                and column.data_type == 0
                and suffix not in column.key
            ):
                main = row.get(column.key) or 0
                previous = row.get(f"{column.key}__{suffix}") or 0
                difference, percent = _differences(main, previous)
                for label, value in (
                    ("Main", main),
                    ("#", previous),
                    ("△", difference),
                    ("%", percent),
                ):
                    display[f"{label} {column.key}"] = value
        output.append(display)
    return output, converted, True, suffix


def _rule_targets(rule: Mapping[str, object], columns: Sequence[_Column]) -> list[str]:
    target = rule.get("columnFormatting") or rule.get("column")
    if target == "ENTIRE_ROW":
        return [column.key for column in columns]
    return [column.key for column in columns if column.key == target]


def resolve_color_capabilities(
    form_data: Mapping[str, object], columns: Sequence[str], coltypes: Sequence[int]
) -> dict[str, ColumnFilterCapability]:
    """Resolve filter targets without relying on SQL subjects or rule levels.

    ``columns`` are final visible/display keys, including time-comparison prefixes.
    A gradient on any reachable target disables filtering for that target.
    """
    configs = _as_mapping(form_data.get("column_config"))
    metadata = [
        _Column(
            key,
            key,
            int(coltypes[index]) if index < len(coltypes) else 1,
            False,
            False,
            _as_mapping(configs.get(key)),
        )
        for index, key in enumerate(columns)
        if _as_mapping(configs.get(key)).get("visible") is not False
    ]
    data_types = {
        key: int(coltypes[index]) if index < len(coltypes) else 1
        for index, key in enumerate(columns)
    }
    result: dict[str, ColumnFilterCapability] = {
        column.key: {"enabled": False, "supported": True} for column in metadata
    }
    for rule in _rules(form_data):
        comparison_rule = rule.get("colorScheme") in {"Green", "Red"}
        if not comparison_rule and not (
            _rule_ready(rule)
            and rule.get("operator") in _OPERATORS
            and isinstance(rule.get("colorScheme"), str)
        ):
            continue
        targets = _rule_targets(rule, metadata)
        gradient = (
            not comparison_rule
            and data_types.get(str(rule.get("column"))) == 0
            and rule.get("useGradient") is not False
            and rule.get("objectFormatting") not in {"TEXT_COLOR", "CELL_BAR"}
            and not rule.get("toTextColor")
        )
        for target in targets:
            if rule.get("filterable") is True:
                result[target]["enabled"] = True
            if gradient:
                result[target]["supported"] = False
                result[target]["reason"] = {
                    "code": GRADIENT_UNSUPPORTED,
                    "message": GRADIENT_MESSAGE,
                }
    return result


def _basic_style(
    percent: float, scheme: object
) -> tuple[str, str, TablePaintColor | None]:
    if percent == 0:
        return "rgba(0,0,0,0.2)", "", None
    positive = percent > 0
    green = positive if scheme == "Green" else not positive
    return (
        f"rgba({'0,150,0' if green else '150,0,0'},0.2)",
        "↑" if positive else "↓",
        "GREEN" if green else "RED",
    )


def _temporal(value: object) -> _TemporalValue:
    if value is None:
        return _TemporalValue(0)
    if _number(value) or isinstance(value, bool):
        return _TemporalValue(float(value))
    try:
        timestamp = pd.Timestamp(value)
        if timestamp.tzinfo is None:
            timestamp = timestamp.tz_localize(timezone.utc)
        return _TemporalValue(timestamp.timestamp() * 1000)
    except (ValueError, TypeError, OverflowError):
        return _TemporalValue(math.nan)


def _browser_value(value: object) -> object:
    if _number(value):
        return float(value)
    if isinstance(value, (datetime, date)):
        return _temporal(value).milliseconds
    return value


def _resolve_page(  # noqa: C901
    records: Sequence[Mapping[str, object]],
    columns: Sequence[str],
    coltypes: Sequence[int],
    form_data: Mapping[str, object],
    theme: Mapping[str, str],
    comparison_main_label: str,
) -> TableColorStyleResult:
    serialized_records = cast(
        list[dict[str, object]],
        json.loads(json.dumps(records, default=json.json_int_dttm_ser)),
    )
    browser_records = [
        {key: _browser_value(value) for key, value in row.items()}
        for row in serialized_records
    ]
    native_columns = _columns(browser_records, columns, coltypes, form_data)
    display, display_columns, comparison, suffix = _display_data(
        records, native_columns, form_data, comparison_main_label
    )
    browser_rows, _, _, _ = _display_data(
        browser_records, native_columns, form_data, comparison_main_label
    )
    visible = [
        column
        for column in display_columns
        if column.config.get("visible") is not False
    ]
    if not comparison:
        for row in browser_rows:
            for column in display_columns:
                if column.data_type == 2:
                    row[column.key] = _temporal(row.get(column.key, _MISSING))
    rules = _rules(form_data)
    formatters = [rule for rule in rules if _rule_ready(rule)]
    basic_rules = [
        rule
        for rule in rules
        if rule.get("column") and rule.get("colorScheme") in {"Green", "Red"}
    ]
    has_basic = (
        comparison
        and form_data.get("comparison_color_enabled") is True
        and bool(records)
    )
    bounds = {
        str(rule.get("column")): _extrema(
            [row.get(str(rule.get("column")), _MISSING) for row in browser_rows]
        )
        for rule in formatters
    }
    ordered_formatters = {
        column.key: [
            rule
            for rule in formatters
            if (rule.get("columnFormatting") or rule.get("column")) == column.key
        ]
        + [rule for rule in formatters if rule.get("columnFormatting") == "ENTIRE_ROW"]
        for column in visible
    }
    numeric_values = {
        column.key: [
            cast(float, candidate[column.key])
            for candidate in browser_rows
            if _number(candidate.get(column.key))
            and not math.isnan(cast(float, candidate[column.key]))
        ]
        for column in visible
    }
    bar_ranges = {
        column.key: (
            (0.0, max(map(abs, numeric_values[column.key]), default=math.nan))
            if column.config.get(
                "alignPositiveNegative", form_data.get("align_pn", True)
            )
            else (
                min(numeric_values[column.key], default=math.nan),
                max(numeric_values[column.key], default=math.nan),
            )
        )
        for column in visible
    }
    page_styles: list[dict[str, TableCellPaint]] = []
    for row_index, row in enumerate(browser_rows):
        basic: dict[str, tuple[str, str, TablePaintColor | None]] = {}
        basic_columns: dict[str, tuple[str, str, TablePaintColor | None]] = {}
        for column in native_columns:
            if (
                not (column.metric or column.percent)
                or column.data_type != 0
                or suffix in column.key
            ):
                continue
            raw_row = browser_records[row_index]
            _, percent = _differences(
                raw_row.get(column.key) or 0,
                raw_row.get(f"{column.key}__{suffix}") or 0,
            )
            basic[column.key] = _basic_style(
                percent, form_data.get("comparison_color_scheme", "Green")
            )
            for rule in basic_rules:
                rule_column = str(rule["column"])
                if column.key in rule_column:
                    basic_columns[rule_column] = _basic_style(
                        percent, rule.get("colorScheme")
                    )
        styles: dict[str, TableCellPaint] = {}
        for column in visible:
            key, value = column.key, row.get(column.key, _MISSING)
            paint: TableCellPaint = {"colors": []}
            families: dict[str, TablePaintColor | None] = {}
            origin_key = key[len(column.label) :].strip()
            arrow_symbol = ""
            arrow_family: TablePaintColor | None = None
            suppress_bar = False
            if not formatters and has_basic and origin_key in basic:
                background, symbol, family = basic[origin_key]
                paint["backgroundColor"] = background
                families["background"] = family
                if column.label == comparison_main_label:
                    arrow_symbol, arrow_family = symbol, family
            show_bars = column.config.get(
                "showCellBars", form_data.get("show_cell_bars", True)
            )
            bar_color: str | None = None
            bar_family: TablePaintColor | None = None
            for rule in ordered_formatters[key]:
                source = str(rule["column"])
                result = _rule_color(
                    rule, row.get(source, _MISSING), bounds[source], theme
                )
                if not result:
                    continue
                family = _family(rule.get("colorScheme"))
                if rule.get("objectFormatting") == "TEXT_COLOR" or rule.get(
                    "toTextColor"
                ):
                    paint["textColor"] = _text_color(result)
                    families["text"] = family
                elif rule.get("objectFormatting") == "CELL_BAR":
                    if show_bars:
                        bar_color, bar_family = result[:-2] + "99", family
                else:
                    paint["backgroundColor"] = result
                    families["background"] = family
                    suppress_bar = True
            if basic_rules:
                if key in basic_columns:
                    background, symbol, family = basic_columns[key]
                    paint["backgroundColor"] = background
                    families["background"] = family
                    arrow_symbol = (
                        symbol if column.label == comparison_main_label else ""
                    )
                    arrow_family = family
                else:
                    arrow_symbol, arrow_family = "", None
            if arrow_symbol:
                arrow_color = (
                    theme["colorSuccess"]
                    if arrow_family == "GREEN"
                    else theme["colorError"]
                )
                paint["arrow"] = {"color": arrow_color, "symbol": arrow_symbol}
                families["arrow"] = (
                    arrow_family if _visible_color(arrow_color) else None
                )
            if (
                show_bars
                and not has_basic
                and not suppress_bar
                and (
                    column.metric
                    or column.percent
                    or form_data.get("query_mode") == "raw"
                )
                and _number(value)
                and numeric_values[key]
            ):
                align = column.config.get(
                    "alignPositiveNegative", form_data.get("align_pn", True)
                )
                minimum, maximum = bar_ranges[key]
                extent = (
                    maximum if align else abs(max(maximum, 0)) + abs(min(minimum, 0))
                )
                width = (
                    abs(_js_round(value / extent * 100))
                    if align and extent
                    else _js_round(abs(value) / extent * 100)
                    if extent
                    else math.nan
                )
                offset = (
                    0.0
                    if align
                    else _js_round(
                        min(abs(min(minimum, 0)) + value, abs(min(minimum, 0)))
                        / extent
                        * 100
                    )
                    if extent
                    else math.nan
                )
                if bar_color is None:
                    color_pn = column.config.get(
                        "colorPositiveNegative", form_data.get("color_pn", True)
                    )
                    if color_pn:
                        bar_family = "RED" if value < 0 else "GREEN"
                        bar_color = (
                            theme["colorError" if value < 0 else "colorSuccess"] + "50"
                        )
                    else:
                        bar_color = theme["colorFill"]
                if (
                    math.isfinite(width)
                    and math.isfinite(offset)
                    and width > 0
                    and _visible_color(bar_color)
                ):
                    paint["cellBar"] = {
                        "color": bar_color,
                        "width": width,
                        "offset": offset,
                        "min": minimum,
                        "max": maximum,
                    }
                    families["bar"] = bar_family
            if not _visible_color(paint.get("backgroundColor")):
                families["background"] = None
            if not _visible_color(paint.get("textColor")):
                families["text"] = None
            paint["colors"] = [
                family for family in PAINT_COLORS if family in families.values()
            ]
            styles[key] = paint
        page_styles.append(styles)
    visible_keys = [column.key for column in visible]
    visible_types = [column.data_type for column in visible]
    return {
        "records": [{key: row.get(key) for key in visible_keys} for row in display],
        "columns": visible_keys,
        "coltypes": visible_types,
        "styles": page_styles,
        "catalog": {
            key: [
                color
                for color in PAINT_COLORS
                if any(color in row[key]["colors"] for row in page_styles)
            ]
            for key in visible_keys
        },
        "capabilities": resolve_color_capabilities(
            form_data,
            [column.key for column in display_columns],
            [column.data_type for column in display_columns],
        ),
    }


def resolve_table_color_styles(
    records: Sequence[Mapping[str, object]],
    columns: Sequence[str],
    coltypes: Sequence[int],
    form_data: Mapping[str, object],
    theme: Mapping[str, str] | None = None,
    source_page_size: int = 0,
    comparison_main_label: str = "Main",
) -> TableColorStyleResult:
    """Return final visible paints without changing the supplied query values.

    Callers must execute query post-processing with its original page context
    before invoking this function. ``source_page_size`` can freeze paint ranges
    across multiple already-post-processed original pages; zero means one page.
    Theme values and the translated comparison label must come from trusted
    server context. Display keys keep the native, untranslated ``Main`` prefix.
    """
    if source_page_size < 0:
        raise ValueError("source_page_size must be non-negative")
    resolved_theme = {**DEFAULT_TABLE_THEME, **(theme or {})}
    page_size = source_page_size or max(len(records), 1)
    result: TableColorStyleResult | None = None
    for offset in range(0, max(len(records), 1), page_size):
        page = _resolve_page(
            records[offset : offset + page_size],
            columns,
            coltypes,
            form_data,
            resolved_theme,
            comparison_main_label,
        )
        if result is None:
            result = page
        else:
            result["records"].extend(page["records"])
            result["styles"].extend(page["styles"])
            for key, colors in page["catalog"].items():
                result["catalog"][key] = [
                    color
                    for color in PAINT_COLORS
                    if color in colors or color in result["catalog"].get(key, [])
                ]
    assert result is not None
    return result
