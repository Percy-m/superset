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
"""Safe constant-memory XLSX writing for saved classic Table charts."""

from __future__ import annotations

import io
import math
import numbers
import re
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, BinaryIO, cast, Collection, Mapping, Sequence

import pandas as pd
import xlsxwriter
from PIL import ImageColor
from xlsxwriter.format import Format
from xlsxwriter.worksheet import Worksheet

from superset.common.table_alerts import (
    ResolvedTableStyleRule,
    table_style_rule_matches,
)
from superset.common.table_color_styles import PAINT_COLORS, TableCellPaint
from superset.utils.core import GenericDataType

MAX_SHEET_UTF8_BYTES = 8 * 1024 * 1024
MAX_CELL_UTF8_BYTES = 1024 * 1024
XLSX_DATA_LIMIT_MESSAGE = (
    "STYLED_XLSX_DATA_LIMIT_EXCEEDED: table data exceeds the XLSX safety limit"
)
XLSX_STYLE_ALIGNMENT_MESSAGE = (
    "STYLED_XLSX_STALE_STYLES: frozen table styles do not match the exported data"
)

_ILLEGAL_CONTROL_CHARACTERS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")
_ILLEGAL_SHEET_CHARACTERS = re.compile(r"[\[\]:*?/\\]")
_FORMULA_PREFIXES = frozenset({"=", "+", "-", "@", "\t", "\r"})
_THEME_COLORS = {
    "colorSuccess": "#52C41A",
    "colorWarning": "#FAAD14",
    "colorError": "#FF4D4F",
    "colorInfo": "#1677FF",
    "colorPrimary": "#1677FF",
}


class StyledExcelError(ValueError):
    """Raised before returning a partial or unsafe styled workbook."""


def sanitize_excel_string(value: str) -> str:
    """Remove forbidden XML controls and neutralize formula-like prefixes."""
    sanitized = _ILLEGAL_CONTROL_CHARACTERS.sub("", value)
    if sanitized and sanitized[0] in _FORMULA_PREFIXES:
        return f"'{sanitized}"
    return sanitized


def resolve_sheet_name(
    title: str | None,
    slice_id: int,
    used_names: Collection[str],
) -> tuple[str, bool]:
    """Return an Excel-safe, stable, case-insensitively unique sheet name."""
    fallback = f"Table {slice_id}"
    original = title or fallback
    base = " ".join(_ILLEGAL_SHEET_CHARACTERS.sub(" ", original).split()).strip()
    base = base[:31].strip() or fallback[:31]
    used = {name.casefold() for name in used_names}
    candidate = base
    suffix_number = 2
    while candidate.casefold() in used:
        suffix = f" ({suffix_number})"
        candidate = f"{base[: 31 - len(suffix)].rstrip()}{suffix}"
        suffix_number += 1
    return candidate, candidate != original


def _normalize_color(value: str) -> str | None:
    value = _THEME_COLORS.get(value, value).strip()
    short_match = re.fullmatch(r"#([0-9a-fA-F]{3})", value)
    if short_match:
        value = "#" + "".join(character * 2 for character in short_match.group(1))
    long_match = re.fullmatch(r"#([0-9a-fA-F]{6})(?:[0-9a-fA-F]{2})?", value)
    if long_match:
        return f"#{long_match.group(1).upper()}"
    rgb_match = re.fullmatch(
        r"rgba?\(\s*(\d{1,3})\s*,\s*(\d{1,3})\s*,\s*(\d{1,3})(?:\s*,[^)]*)?\)",
        value,
    )
    if rgb_match:
        channels = tuple(int(channel) for channel in rgb_match.groups())
        if all(0 <= channel <= 255 for channel in channels):
            return "#" + "".join(f"{channel:02X}" for channel in channels)
    return None


def _blend_with_white(color: str, opacity: float) -> str:
    channels = [int(color[index : index + 2], 16) for index in (1, 3, 5)]
    blended = [round(channel * opacity + 255 * (1 - opacity)) for channel in channels]
    return "#" + "".join(f"{channel:02X}" for channel in blended)


def _frozen_color(value: str | None) -> str | None:
    """Use a saved final CSS paint without recalculating conditional gradients."""
    if value is None:
        return None
    opacity = 1.0
    color = _normalize_color(value)
    if color is None:
        try:
            red, green, blue, alpha = ImageColor.getcolor(value, "RGBA")
        except (TypeError, ValueError):
            return None
        color = f"#{red:02X}{green:02X}{blue:02X}"
        opacity = alpha / 255
    elif re.fullmatch(r"#[0-9a-fA-F]{8}", value):
        opacity = int(value[-2:], 16) / 255
    elif match := re.fullmatch(r"rgba\([^,]+,[^,]+,[^,]+,\s*([0-9.]+)\s*\)", value):
        opacity = float(match.group(1))
    if opacity <= 0:
        return None
    return _blend_with_white(color, min(opacity, 1.0))


def _frozen_table_styles(  # noqa: C901
    dataframe: pd.DataFrame, columns: Sequence[str]
) -> list[dict[str, TableCellPaint]] | None:
    """Validate internal snapshot row/key alignment before using DataFrame attrs.

    This attribute is assigned by the trusted snapshot exporter, not loaded from
    an XLSX request body. Validation also catches accidental stale attrs after a
    caller changes the frame's row selection or column names.
    """
    raw = dataframe.attrs.get("table_color_styles")
    if raw is None:
        return None
    if not isinstance(raw, list) or len(raw) != len(dataframe):
        raise StyledExcelError(XLSX_STYLE_ALIGNMENT_MESSAGE)
    for row in raw:
        if not isinstance(row, dict) or set(row) != set(columns):
            raise StyledExcelError(XLSX_STYLE_ALIGNMENT_MESSAGE)
        for paint in row.values():
            if not isinstance(paint, dict):
                raise StyledExcelError(XLSX_STYLE_ALIGNMENT_MESSAGE)
            colors = paint.get("colors")
            if not isinstance(colors, list) or any(
                color not in PAINT_COLORS for color in colors
            ):
                raise StyledExcelError(XLSX_STYLE_ALIGNMENT_MESSAGE)
            for key in ("backgroundColor", "textColor"):
                if key in paint and not isinstance(paint[key], str):
                    raise StyledExcelError(XLSX_STYLE_ALIGNMENT_MESSAGE)
            if "cellBar" in paint:
                bar = paint["cellBar"]
                if not isinstance(bar, dict) or not isinstance(bar.get("color"), str):
                    raise StyledExcelError(XLSX_STYLE_ALIGNMENT_MESSAGE)
                for key in ("width", "offset", "min", "max"):
                    value = bar.get(key)
                    if (
                        isinstance(value, bool)
                        or not isinstance(value, numbers.Real)
                        or not math.isfinite(value)
                    ):
                        raise StyledExcelError(XLSX_STYLE_ALIGNMENT_MESSAGE)
                if bar["width"] <= 0 or bar["max"] < bar["min"]:
                    raise StyledExcelError(XLSX_STYLE_ALIGNMENT_MESSAGE)
    return cast(list[dict[str, TableCellPaint]], raw)


def _readable_text_color(background: str) -> str:
    red, green, blue = (int(background[index : index + 2], 16) for index in (1, 3, 5))
    luminance = (0.2126 * red + 0.7152 * green + 0.0722 * blue) / 255
    return "#000000" if luminance > 0.55 else "#FFFFFF"


def _finite_float(value: object) -> float | None:
    try:
        numeric = float(cast(Any, value))
    except (TypeError, ValueError):
        return None
    return numeric if math.isfinite(numeric) else None


def _numeric_values(values: Sequence[object]) -> list[float]:
    result: list[float] = []
    for value in values:
        if isinstance(value, bool) or value is None:
            continue
        if (numeric := _finite_float(value)) is not None:
            result.append(numeric)
    return result


def _gradient_bounds(
    rule: ResolvedTableStyleRule,
    minimum: float,
    maximum: float,
) -> tuple[float, float] | None:
    if rule.operator in {">", "≥"}:
        target = _finite_float(rule.target_value)
        return (target, maximum) if target is not None else None
    if rule.operator in {"<", "≤"}:
        target = _finite_float(rule.target_value)
        return (target, minimum) if target is not None else None
    if rule.operator in {"< x <", "≤ x ≤", "≤ x <", "< x ≤"}:
        left = _finite_float(rule.target_value_left)
        right = _finite_float(rule.target_value_right)
        return (left, right) if left is not None and right is not None else None
    if rule.operator in {"=", "≠"}:
        return None
    return minimum, maximum


def _gradient_opacity(
    rule: ResolvedTableStyleRule,
    value: object,
    values: Sequence[object],
) -> float:
    if not rule.use_gradient:
        return 1.0
    numeric_value = _finite_float(value)
    if numeric_value is None:
        return 1.0
    numeric_values = _numeric_values(values)
    if not numeric_values:
        return 1.0

    minimum = min(numeric_values)
    maximum = max(numeric_values)
    minimum_opacity = 0.0 if rule.operator == "None" else 0.05
    bounds = _gradient_bounds(rule, minimum, maximum)
    if bounds is None:
        return 1.0
    cutoff, extreme = bounds
    if extreme == cutoff:
        return 1.0
    opacity = (
        abs(((1.0 - minimum_opacity) / (extreme - cutoff)) * (numeric_value - cutoff))
        + minimum_opacity
    )
    return max(min(round(opacity, 2), 1.0), minimum_opacity)


def _d3_to_excel_format(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    match = re.fullmatch(r"(,)?\.(\d+)([f%])", value)
    if not match:
        return None
    grouping, precision, kind = match.groups()
    base = "#,##0" if grouping else "0"
    if decimals := "0" * int(precision):
        base = f"{base}.{decimals}"
    return f"{base}%" if kind == "%" else base


def _is_missing(value: object) -> bool:
    if value is None:
        return True
    try:
        missing = pd.isna(value)
    except (TypeError, ValueError):
        return False
    return bool(missing) if isinstance(missing, (bool, numbers.Integral)) else False


def _safe_cell_value(value: object) -> object:  # noqa: C901
    if _is_missing(value):
        return None
    if isinstance(value, str):
        sanitized = sanitize_excel_string(value)
        if len(sanitized.encode("utf-8")) > MAX_CELL_UTF8_BYTES:
            raise StyledExcelError(XLSX_DATA_LIMIT_MESSAGE)
        return sanitized
    if isinstance(value, bool):
        return value
    if isinstance(value, numbers.Integral):
        integer = int(value)
        return str(integer) if abs(integer) >= 10**15 else integer
    if isinstance(value, Decimal):
        if value.is_finite() and value == value.to_integral_value():
            integer = int(value)
            if abs(integer) >= 10**15:
                return str(integer)
        return float(value) if value.is_finite() else str(value)
    if isinstance(value, numbers.Real):
        numeric = float(value)
        return numeric if math.isfinite(numeric) else str(value)
    if isinstance(value, (datetime, date)):
        if isinstance(value, datetime) and value.tzinfo is not None:
            return value.isoformat()
        return value
    return sanitize_excel_string(str(value))


def validate_dataframe_size(df: pd.DataFrame) -> None:
    """Reject oversized text before a workbook can be returned."""
    total_bytes = 0
    for column in df.columns:
        total_bytes += len(str(column).encode("utf-8"))
    for row in df.itertuples(index=False, name=None):
        for value in row:
            if isinstance(value, str):
                cell_bytes = len(value.encode("utf-8"))
                if cell_bytes > MAX_CELL_UTF8_BYTES:
                    raise StyledExcelError(XLSX_DATA_LIMIT_MESSAGE)
                total_bytes += cell_bytes
                if total_bytes > MAX_SHEET_UTF8_BYTES:
                    raise StyledExcelError(XLSX_DATA_LIMIT_MESSAGE)


class StyledWorkbookWriter:
    """Write one Chart result at a time into a constant-memory workbook."""

    def __init__(self, output: str | Path | BinaryIO) -> None:
        self._workbook = xlsxwriter.Workbook(
            output,
            {"constant_memory": True, "nan_inf_to_errors": True},
        )
        self._formats: dict[tuple[object, ...], Format] = {}
        self._closed = False
        self.invalid_color_count = 0

    def _format(
        self,
        *,
        background: str | None = None,
        font: str | None = None,
        bold: bool = False,
        number_format: str | None = None,
    ) -> Format | None:
        if not any((background, font, bold, number_format)):
            return None
        key = (background, font, bold, number_format)
        if key not in self._formats:
            properties: dict[str, object] = {}
            if background:
                properties["bg_color"] = background
            if font:
                properties["font_color"] = font
            if bold:
                properties["bold"] = True
            if number_format:
                properties["num_format"] = number_format
            self._formats[key] = self._workbook.add_format(properties)
        return self._formats[key]

    def _rule_color(
        self,
        rule: ResolvedTableStyleRule,
        value: object,
        values: Sequence[object],
    ) -> str | None:
        color = _normalize_color(rule.color)
        if color is None:
            self.invalid_color_count += 1
            return None
        if rule.use_gradient:
            color = _blend_with_white(color, _gradient_opacity(rule, value, values))
        return color

    @staticmethod
    def _write_value(
        worksheet: Worksheet,
        row: int,
        column: int,
        value: object,
        cell_format: Format | None,
    ) -> None:
        safe_value = _safe_cell_value(value)
        if safe_value is None:
            worksheet.write_blank(row, column, None, cell_format)
        elif isinstance(safe_value, datetime):
            worksheet.write_datetime(row, column, safe_value, cell_format)
        elif isinstance(safe_value, date):
            worksheet.write_datetime(
                row,
                column,
                datetime.combine(safe_value, datetime.min.time()),
                cell_format,
            )
        else:
            worksheet.write(row, column, safe_value, cell_format)

    def write_dataframe(  # noqa: C901
        self,
        sheet_name: str,
        df: pd.DataFrame,
        rules: Sequence[ResolvedTableStyleRule],
        column_types: Sequence[GenericDataType] | None = None,
        column_config: Mapping[str, object] | None = None,
        totals: Mapping[str, object] | None = None,
    ) -> None:
        """Write a DataFrame and optional totals row without retaining other sheets."""
        validate_dataframe_size(df)
        columns = [str(column) for column in df.columns]
        frozen_styles = _frozen_table_styles(df, columns)
        worksheet = self._workbook.add_worksheet(sheet_name)
        header_format = self._format(background="#E8E8E8", bold=True)
        for column_index, column_name in enumerate(columns):
            worksheet.write(
                0, column_index, sanitize_excel_string(column_name), header_format
            )

        values_by_column = {
            column: df.iloc[:, index].tolist() for index, column in enumerate(columns)
        }
        column_indices = {column: index for index, column in enumerate(columns)}
        number_formats: dict[str, str] = {}
        for column, column_type in zip(
            columns,
            column_types or (),
            strict=False,
        ):
            if column_type == GenericDataType.TEMPORAL:
                number_formats[column] = "yyyy-mm-dd hh:mm:ss"
        for column, config in (column_config or {}).items():
            if isinstance(config, Mapping):
                excel_format = _d3_to_excel_format(config.get("d3NumberFormat"))
                if excel_format:
                    number_formats[column] = excel_format

        data_bar_rules: dict[str, ResolvedTableStyleRule] = {}
        for rule in rules if frozen_styles is None else ():
            if rule.dimension == "data_bar" and rule.target_column:
                data_bar_rules[rule.target_column] = rule

        for row_index, row_values in enumerate(
            df.itertuples(index=False, name=None), start=1
        ):
            row = dict(zip(columns, row_values, strict=True))
            styles: dict[str, dict[str, str]] = {column: {} for column in columns}
            for rule in rules if frozen_styles is None else ():
                source_value = row.get(rule.source_column)
                if rule.dimension == "data_bar" or not table_style_rule_matches(
                    rule, source_value
                ):
                    continue
                color = self._rule_color(
                    rule,
                    source_value,
                    values_by_column[rule.source_column],
                )
                if color is None:
                    continue
                targets = (
                    columns if rule.target_column is None else [rule.target_column]
                )
                for target in targets:
                    styles[target][rule.dimension] = color

            for column_index, (column_name, value) in enumerate(
                zip(columns, row_values, strict=True)
            ):
                style = styles[column_name]
                background = style.get("background")
                font = style.get("font")
                if frozen_styles is not None:
                    frozen = frozen_styles[row_index - 1][column_name]
                    background = _frozen_color(frozen.get("backgroundColor"))
                    font = _frozen_color(frozen.get("textColor"))
                    if bar := frozen.get("cellBar"):
                        bar_color = _normalize_color(bar["color"])
                        if bar_color:
                            worksheet.conditional_format(
                                row_index,
                                column_index,
                                row_index,
                                column_index,
                                {
                                    "type": "data_bar",
                                    "bar_color": bar_color,
                                    "bar_solid": True,
                                    "bar_negative_color_same": True,
                                    "bar_negative_border_color_same": True,
                                    "min_type": "num",
                                    "min_value": bar["min"],
                                    "max_type": "num",
                                    "max_value": bar["max"],
                                },
                            )
                if background and not font:
                    font = _readable_text_color(background)
                cell_format = self._format(
                    background=background,
                    font=font,
                    number_format=number_formats.get(column_name),
                )
                self._write_value(
                    worksheet,
                    row_index,
                    column_index,
                    value,
                    cell_format,
                )

        last_data_row = len(df.index)
        if totals:
            totals_format = self._format(bold=True)
            totals_row = last_data_row + 1
            for column_index, column_name in enumerate(columns):
                self._write_value(
                    worksheet,
                    totals_row,
                    column_index,
                    totals.get(column_name),
                    totals_format,
                )
            last_data_row = totals_row

        for column_name, rule in data_bar_rules.items():
            data_bar_column_index = column_indices.get(column_name)
            if data_bar_column_index is None or len(df.index) == 0:
                continue
            matching_values = [
                value
                for value in values_by_column[column_name]
                if table_style_rule_matches(rule, value)
            ]
            numeric_values = _numeric_values(matching_values)
            color = _normalize_color(rule.color)
            if not numeric_values or color is None:
                if color is None:
                    self.invalid_color_count += 1
                continue
            worksheet.conditional_format(
                1,
                data_bar_column_index,
                len(df.index),
                data_bar_column_index,
                {
                    "type": "data_bar",
                    "bar_color": color,
                    "min_type": "num",
                    "min_value": min(numeric_values),
                    "max_type": "num",
                    "max_value": max(numeric_values),
                },
            )

        for column_index, column_name in enumerate(columns):
            width = min(
                60,
                max(
                    len(column_name) + 2,
                    max(
                        (
                            len(str(value))
                            for value in df.iloc[:, column_index].head(200)
                        ),
                        default=0,
                    )
                    + 2,
                ),
            )
            worksheet.set_column(column_index, column_index, width)
        worksheet.freeze_panes(1, 0)

    def write_notes(self, notes: Sequence[Mapping[str, object]]) -> None:
        """Write non-sensitive skipped-chart and normalization diagnostics."""
        worksheet = self._workbook.add_worksheet("_导出说明")
        headers = ("Chart ID", "Title", "Reason")
        header_format = self._format(background="#E8E8E8", bold=True)
        for column_index, header in enumerate(headers):
            worksheet.write(0, column_index, header, header_format)
        for row_index, note in enumerate(notes, start=1):
            values = (note.get("chart_id"), note.get("title"), note.get("reason"))
            for column_index, value in enumerate(values):
                self._write_value(worksheet, row_index, column_index, value, None)
        worksheet.set_column(0, 0, 12)
        worksheet.set_column(1, 1, 32)
        worksheet.set_column(2, 2, 40)

    def close(self) -> None:
        if not self._closed:
            self._workbook.close()
            self._closed = True


def dataframe_to_styled_xlsx(
    df: pd.DataFrame,
    rules: Sequence[ResolvedTableStyleRule],
    *,
    sheet_name: str,
    column_types: Sequence[GenericDataType] | None = None,
    column_config: Mapping[str, object] | None = None,
) -> bytes:
    """Return one complete styled XLSX document for a Table query result."""
    output = io.BytesIO()
    writer = StyledWorkbookWriter(output)
    writer.write_dataframe(
        sheet_name,
        df,
        rules,
        column_types=column_types,
        column_config=column_config,
    )
    writer.close()
    return output.getvalue()
