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
"""Server-owned formatting and access bindings for Table color snapshots."""

from __future__ import annotations

import copy
import math
from dataclasses import dataclass
from typing import Any, TYPE_CHECKING

from flask import current_app, g
from flask_babel import gettext
from PIL import ImageColor

from superset.commands.explore.form_data.utils import check_access
from superset.common.table_color_schema import TableColorFilterError, TableColorRequest
from superset.daos.dashboard import DashboardDAO
from superset.extensions import cache_manager, security_manager
from superset.utils import json
from superset.utils.cache import generate_cache_key
from superset.utils.core import apply_max_row_limit, DatasourceType, get_user_id

if TYPE_CHECKING:
    from superset.common.query_context import QueryContext
    from superset.models.dashboard import Dashboard


def access_denied() -> TableColorFilterError:
    """Avoid distinguishing missing objects from inaccessible objects."""
    return TableColorFilterError(
        "TABLE_COLOR_FILTER_ACCESS_DENIED",
        "Color filter context is not accessible.",
        403,
    )


def owner_fingerprint() -> str:
    """Bind snapshots to the authenticated identity, including guest token scope."""
    user = getattr(g, "user", None)
    identity: object = (
        {"guest": user.guest_token}
        if user is not None and security_manager.is_guest_user(user)
        else {
            "user_id": get_user_id(),
            "roles": sorted(role.id for role in getattr(user, "roles", [])),
        }
    )
    return generate_cache_key({"identity": identity}, "table-color-owner-")


def dashboard_filter_fingerprint(extra_form_data: dict[str, Any]) -> str:
    """Compare native/cross interaction state without persisting its raw values."""
    filters = [
        {
            key: value
            for key, value in item.items()
            if key in {"col", "op", "val", "grain"}
        }
        for item in extra_form_data.get("filters", [])
        if isinstance(item, dict)
    ]
    normalized = {
        key: extra_form_data[key]
        for key in ("time_range", "time_grain_sqla", "granularity_sqla")
        if extra_form_data.get(key) is not None
    }
    normalized["filters"] = sorted(
        filters,
        key=lambda item: json.dumps(
            item, sort_keys=True, default=json.json_int_dttm_ser
        ),
    )
    return generate_cache_key(normalized, "table-color-dashboard-filters-")


def _dashboard(query_context: QueryContext) -> Dashboard | None:
    form_data = query_context.form_data or {}
    dashboard_id = form_data.get("dashboardId")
    if dashboard_id is None:
        return None
    if isinstance(dashboard_id, bool) or not str(dashboard_id).isdigit():
        raise access_denied()
    dashboard = DashboardDAO.find_by_id(int(dashboard_id))
    if dashboard is None or query_context.slice_ not in dashboard.slices:
        raise access_denied()
    security_manager.raise_for_access(dashboard=dashboard)
    return dashboard


def _draft_form_data(query_context: QueryContext, key: str) -> dict[str, Any]:
    """Use the existing temporary state with an additional owner check."""
    state = cache_manager.explore_form_data_cache.get(key)
    if (
        not isinstance(state, dict)
        or get_user_id() is None
        or state.get("owner") != get_user_id()
        or state.get("datasource_id") != query_context.datasource.id
        or state.get("datasource_type") != DatasourceType.TABLE
        or state.get("chart_id")
        != (query_context.slice_.id if query_context.slice_ else None)
    ):
        raise access_denied()
    check_access(state["datasource_id"], state.get("chart_id"), DatasourceType.TABLE)
    try:
        data = json.loads(state["form_data"])
    except (KeyError, TypeError, ValueError) as ex:
        raise access_denied() from ex
    if not isinstance(data, dict):
        raise access_denied()
    return data


def _normalized_form_data(form_data: dict[str, Any]) -> tuple[dict[str, Any], int]:
    """Drop retired rule attributes and retain the native chart result limit."""
    if form_data.get("viz_type") != "table":
        raise access_denied()
    form_data = copy.deepcopy(form_data)
    rules = form_data.get("conditional_formatting")
    if not isinstance(rules, list) or not any(
        isinstance(rule, dict) and rule.get("filterable") is True for rule in rules
    ):
        raise TableColorFilterError(
            "TABLE_COLOR_FILTER_INVALID", "This Table has no enabled color filter."
        )
    for rule in rules:
        if isinstance(rule, dict):
            rule.pop("alertLevel", None)
            rule.pop("subjectRef", None)
    try:
        configured_limit = int(
            form_data.get("row_limit") or current_app.config["ROW_LIMIT"]
        )
    except (TypeError, ValueError) as ex:
        raise TableColorFilterError(
            "TABLE_COLOR_FILTER_INVALID", "Invalid result scope."
        ) from ex
    row_limit = apply_max_row_limit(
        configured_limit, server_pagination=bool(form_data.get("server_pagination"))
    )
    if row_limit <= 0:
        raise TableColorFilterError(
            "TABLE_COLOR_FILTER_INVALID", "Invalid result scope."
        )
    return form_data, row_limit


@dataclass(frozen=True)
class TableColorContext:
    """Trusted form data and the unfiltered result scope, not the display page."""

    form_data: dict[str, Any]
    row_limit: int
    owner: str
    theme: dict[str, str]
    dashboard_id: int | None
    comparison_main_label: str = "Main"

    @classmethod
    def resolve(
        cls, query_context: QueryContext, request: TableColorRequest
    ) -> TableColorContext:
        """Recheck permissions before reading either a draft or a saved formatter."""
        query_context.raise_for_access()
        if query_context.datasource.type != DatasourceType.TABLE:
            raise access_denied()
        dashboard = _dashboard(query_context)
        if key := request.get("form_data_key"):
            if dashboard is not None or security_manager.is_guest_user():
                raise access_denied()
            form_data = _draft_form_data(query_context, key)
        elif query_context.slice_ is not None:
            if (
                query_context.slice_.datasource_type != "table"
                or query_context.slice_.datasource_id != query_context.datasource.id
            ):
                raise access_denied()
            form_data = query_context.slice_.form_data
        else:
            raise access_denied()
        form_data, row_limit = _normalized_form_data(form_data)
        return cls(
            form_data=form_data,
            row_limit=row_limit,
            owner=owner_fingerprint(),
            theme=_theme_tokens(dashboard, request.get("theme_mode", "default")),
            dashboard_id=dashboard.id if dashboard else None,
            comparison_main_label=gettext("Main"),
        )


def _theme_tokens(dashboard: Dashboard | None, mode: str) -> dict[str, str]:
    """Read configured color tokens; never accept client-provided CSS or RGB rules."""
    # Import locally because the bootstrap helpers also import QueryContext.
    from superset.views.base import (
        get_theme_bootstrap_data,  # pylint: disable=import-outside-toplevel
    )

    themes = get_theme_bootstrap_data()["theme"]
    config = themes.get(mode) or themes["default"]
    tokens = dict(config.get("token") or {})
    if dashboard is not None and dashboard.theme is not None:
        try:
            dashboard_theme = json.loads(dashboard.theme.json_data)
            tokens.update(dashboard_theme.get("token") or {})
        except (TypeError, ValueError, AttributeError):
            pass
    result = {key: value for key, value in tokens.items() if isinstance(value, str)}
    if mode == "dark":
        # Ant Design's dark semantic base colors use palette index 6: an 85%
        # mix of the configured seed over #141414, with JavaScript rounding.
        for key, fallback in (
            ("colorSuccess", "#5ac189"),
            ("colorWarning", "#fcc700"),
            ("colorError", "#e04355"),
            ("colorPrimary", "#2893b3"),
        ):
            try:
                channels = ImageColor.getcolor(result.get(key, fallback), "RGB")
            except (TypeError, ValueError) as ex:
                raise TableColorFilterError(
                    "TABLE_COLOR_FILTER_THEME_UNSUPPORTED",
                    "Color filtering cannot resolve this theme's color tokens.",
                    422,
                ) from ex
            result[key] = "#" + "".join(
                f"{math.floor(channel * 0.85 + 20 * 0.15 + 0.5):02x}"
                for channel in channels
            )
        result.setdefault("colorFill", "rgba(255,255,255,0.18)")
    else:
        result.setdefault("colorFill", "rgba(0,0,0,0.15)")
    return result
