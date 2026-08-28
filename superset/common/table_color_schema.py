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
"""Strict public contracts for result-color filtering, without client rules."""

from __future__ import annotations

from typing import Any, TypedDict

from marshmallow import (
    fields,
    post_load,
    pre_load,
    RAISE,
    Schema,
    validate,
    ValidationError,
)

from superset.exceptions import QueryObjectValidationError
from superset.utils import json

COLOR_ORDER = ("GREEN", "YELLOW", "RED")
MAX_REQUEST_BYTES = 64 * 1024


class TableColorSelection(TypedDict):
    """Colors selected in one visible result column."""

    column: str
    colors: list[str]


class TableColorRequest(TypedDict, total=False):
    """References to trusted formatting and an immutable result snapshot."""

    version: int
    selections: list[TableColorSelection]
    snapshot_id: str
    form_data_key: str
    theme_mode: str
    view_rows: list[int]


class TableColorFilterError(QueryObjectValidationError):
    """A non-sensitive color-filter failure with a stable HTTP status."""

    def __init__(self, code: str, message: str, status: int = 400) -> None:
        self.code = code
        self.status = status
        super().__init__(f"{code}: {message}")

    def reason(self) -> dict[str, str]:
        """Return the same reason used by disabled controls and API errors."""
        return {"code": self.code, "message": self.message}


class TableColorSelectionSchema(Schema):
    """Reject thresholds, SQL, arbitrary colors and oversized column names."""

    class Meta:
        unknown = RAISE

    column = fields.String(required=True, validate=validate.Length(min=1, max=1024))
    colors = fields.List(
        fields.String(validate=validate.OneOf(COLOR_ORDER)),
        required=True,
        validate=validate.Length(min=1, max=3),
    )


class TableColorFilterSchema(Schema):
    """Versioned, bounded request shared by Chart Data and export callers."""

    class Meta:
        unknown = RAISE

    version = fields.Integer(required=True, strict=True, validate=validate.Equal(2))
    selections = fields.List(
        fields.Nested(TableColorSelectionSchema),
        load_default=list,
        validate=validate.Length(max=100),
    )
    snapshot_id = fields.String(validate=validate.Length(min=1, max=256))
    form_data_key = fields.String(validate=validate.Length(min=1, max=256))
    theme_mode = fields.String(
        load_default="default", validate=validate.OneOf(("default", "dark"))
    )
    view_rows = fields.List(
        fields.Integer(strict=True, validate=validate.Range(min=0)),
    )

    @pre_load
    def check_size(self, data: object, **kwargs: Any) -> object:
        """Bound the complete object, including unknown fields before validation."""
        if not isinstance(data, dict):
            raise ValidationError("TABLE_COLOR_FILTER_INVALID: expected an object")
        try:
            size = len(json.dumps(data, ensure_ascii=False).encode("utf-8"))
        except (TypeError, ValueError) as ex:
            raise ValidationError("TABLE_COLOR_FILTER_INVALID: invalid object") from ex
        if size > MAX_REQUEST_BYTES:
            raise ValidationError("TABLE_COLOR_FILTER_INVALID: request is too large")
        return data

    @post_load
    def normalize(self, data: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
        """Merge repeated columns and use one deterministic order for each color."""
        view_rows = data.get("view_rows", [])
        if len(set(view_rows)) != len(view_rows):
            raise ValidationError(
                "TABLE_COLOR_FILTER_INVALID: duplicate current-view row references"
            )
        selections: dict[str, set[str]] = {}
        for selection in data["selections"]:
            selections.setdefault(selection["column"], set()).update(
                selection["colors"]
            )
        data["selections"] = [
            {
                "column": column,
                "colors": [color for color in COLOR_ORDER if color in colors],
            }
            for column, colors in sorted(selections.items())
        ]
        return data
