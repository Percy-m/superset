# Licensed to the Apache Software Foundation (ASF) under one
# or more contributor license agreements. See the NOTICE file
# distributed with this work for additional information
# regarding copyright ownership. The ASF licenses this file
# to you under the Apache License, Version 2.0 (the
# "License"); you may not use this file except in compliance
# with the License. You may obtain a copy of the License at
#
#   http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing,
# software distributed under the License is distributed on an
# "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY
# KIND, either express or implied. See the License for the
# specific language governing permissions and limitations
# under the License.

import copy
from types import SimpleNamespace
from typing import Any
from unittest.mock import Mock

import pytest
from flask import current_app, g
from pytest_mock import MockerFixture

from superset.common.query_context import QueryContext
from superset.common.table_color_context import (
    _theme_tokens,
    dashboard_filter_fingerprint,
    owner_fingerprint,
    TableColorContext,
)
from superset.common.table_color_schema import TableColorFilterError
from superset.utils import json
from superset.utils.core import DatasourceType

CONTEXT_MODULE = "superset.common.table_color_context"
PALETTE = {
    "colorSuccess": "#5ac189",
    "colorWarning": "#fcc700",
    "colorError": "#e04355",
    "colorPrimary": "#2893b3",
}


@pytest.fixture
def color_context(mocker: MockerFixture, monkeypatch: pytest.MonkeyPatch) -> Mock:
    """A saved Table and authenticated context with no real database or cache reads."""
    slice_ = Mock(
        id=9,
        datasource_id=7,
        datasource_type="table",
        form_data={
            "viz_type": "table",
            "datasource": "7__table",
            "row_limit": 1000,
            "conditional_formatting": [
                {
                    "column": "profit",
                    "operator": "<",
                    "targetValue": 0,
                    "colorScheme": "colorSuccess",
                    "useGradient": False,
                    "filterable": True,
                    "alertLevel": "RED",
                    "subjectRef": {"kind": "saved_metric", "key": "profit"},
                }
            ],
        },
    )
    context = Mock(
        spec=QueryContext,
        datasource=Mock(id=7, type=DatasourceType.TABLE),
        slice_=slice_,
        form_data={"slice_id": 9},
    )
    monkeypatch.setattr(g, "user", SimpleNamespace(roles=[]), raising=False)
    monkeypatch.setitem(current_app.config, "ROW_LIMIT", 2000)
    monkeypatch.setitem(current_app.config, "SQL_MAX_ROW", 5000)
    monkeypatch.setitem(current_app.config, "TABLE_VIZ_MAX_ROW_SERVER", 9000)
    mocker.patch(f"{CONTEXT_MODULE}.get_user_id", return_value=11)
    mocker.patch(f"{CONTEXT_MODULE}.security_manager.is_guest_user", return_value=False)
    mocker.patch(f"{CONTEXT_MODULE}.security_manager.raise_for_access")
    mocker.patch(f"{CONTEXT_MODULE}.check_access")
    mocker.patch(
        "superset.views.base.get_theme_bootstrap_data",
        return_value={
            "theme": {"default": {"token": {**PALETTE, "fontSize": 14}}, "dark": None}
        },
    )
    return context


def test_saved_table_context_uses_server_rules_and_does_not_mutate_slice(
    color_context: Mock, mocker: MockerFixture
) -> None:
    """Untrusted request form data cannot replace the saved rule or its scope."""
    original = copy.deepcopy(color_context.slice_.form_data)
    color_context.form_data["row_limit"] = 1
    color_context.form_data["conditional_formatting"] = [{"filterable": False}]
    mocker.patch(f"{CONTEXT_MODULE}.gettext", return_value="主要")
    context = TableColorContext.resolve(color_context, {"version": 2})
    color_context.raise_for_access.assert_called_once_with()
    assert context.row_limit == 1000
    assert (
        context.form_data["conditional_formatting"][0]["colorScheme"] == "colorSuccess"
    )
    assert "alertLevel" not in context.form_data["conditional_formatting"][0]
    assert "subjectRef" not in context.form_data["conditional_formatting"][0]
    assert context.comparison_main_label == "主要"
    assert context.owner.startswith("table-color-owner-")
    assert color_context.slice_.form_data == original
    assert context.theme == {**PALETTE, "colorFill": "rgba(0,0,0,0.15)"}


@pytest.mark.parametrize(
    "invalid_context",
    ["dataset_type", "dataset_id", "slice_type", "missing_slice", "viz_type"],
)
def test_color_context_rejects_untrusted_or_non_table_sources(
    color_context: Mock, invalid_context: str
) -> None:
    """Source mismatch and missing charts return the same non-sensitive denial."""
    if invalid_context == "dataset_type":
        color_context.datasource.type = "query"
    elif invalid_context == "dataset_id":
        color_context.slice_.datasource_id = 8
    elif invalid_context == "slice_type":
        color_context.slice_.datasource_type = "query"
    elif invalid_context == "missing_slice":
        color_context.slice_ = None
    else:
        color_context.slice_.form_data["viz_type"] = "pivot_table_v2"
    with pytest.raises(TableColorFilterError) as error:
        TableColorContext.resolve(color_context, {"version": 2})
    assert error.value.code == "TABLE_COLOR_FILTER_ACCESS_DENIED"
    assert error.value.status == 403


@pytest.mark.parametrize(
    "rules", [None, [], {}, [{"filterable": False}], [{"filterable": "true"}]]
)
def test_context_requires_an_explicitly_enabled_rule(
    color_context: Mock, rules: object
) -> None:
    """A disabled configuration cannot be made filterable by a client request."""
    color_context.slice_.form_data["conditional_formatting"] = rules
    with pytest.raises(TableColorFilterError, match="TABLE_COLOR_FILTER_INVALID"):
        TableColorContext.resolve(color_context, {"version": 2})


@pytest.mark.parametrize(
    ("server_pagination", "expected"), [(False, 5000), (True, 9000)]
)
def test_context_preserves_existing_row_limit_caps(
    color_context: Mock, server_pagination: bool, expected: int
) -> None:
    """The full-result scope obeys native caps rather than client display page size."""
    color_context.slice_.form_data.update(
        row_limit=10000, server_pagination=server_pagination
    )
    context = TableColorContext.resolve(color_context, {"version": 2})
    assert context.row_limit == expected


@pytest.mark.parametrize("row_limit", ["bad-limit", -1, {"invalid": "limit"}])
def test_context_rejects_invalid_result_scope(
    color_context: Mock, row_limit: object
) -> None:
    """Invalid saved result limits fail explicitly without executing a query."""
    color_context.slice_.form_data["row_limit"] = row_limit
    with pytest.raises(TableColorFilterError, match="TABLE_COLOR_FILTER_INVALID"):
        TableColorContext.resolve(color_context, {"version": 2})


def test_owner_fingerprint_is_role_order_independent_and_identity_scoped(
    color_context: Mock, mocker: MockerFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Role membership changes and another user cannot reuse an owner binding."""
    monkeypatch.setattr(
        g, "user", SimpleNamespace(roles=[SimpleNamespace(id=3), SimpleNamespace(id=1)])
    )
    original = owner_fingerprint()
    g.user.roles.reverse()
    assert owner_fingerprint() == original
    g.user.roles.append(SimpleNamespace(id=4))
    assert owner_fingerprint() != original
    g.user.roles.pop()
    mocker.patch(f"{CONTEXT_MODULE}.get_user_id", return_value=12)
    assert owner_fingerprint() != original


def test_guest_owner_fingerprint_includes_resource_and_rls_scope(
    color_context: Mock, mocker: MockerFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Guest tokens with a different resource or RLS binding have distinct owners."""
    mocker.patch(f"{CONTEXT_MODULE}.security_manager.is_guest_user", return_value=True)
    monkeypatch.setattr(
        g,
        "user",
        SimpleNamespace(
            guest_token={
                "resources": [{"type": "dashboard", "id": "first"}],
                "rls_rules": [{"clause": "private-filter-value"}],
            }
        ),
    )
    original = owner_fingerprint()
    g.user.guest_token["resources"][0]["id"] = "second"
    assert owner_fingerprint() != original
    g.user.guest_token["resources"][0]["id"] = "first"
    g.user.guest_token["rls_rules"][0]["clause"] = "another-private-value"
    assert owner_fingerprint() != original
    assert "private-filter-value" not in original


def _draft_state(context: Mock) -> dict[str, Any]:
    """Build existing Explore-cache metadata for the authenticated test owner."""
    return {
        "owner": 11,
        "datasource_id": 7,
        "datasource_type": DatasourceType.TABLE,
        "chart_id": context.slice_.id if context.slice_ else None,
        "form_data": json.dumps(
            {
                **context.slice_.form_data,
                "row_limit": 800,
            }
        ),
    }


def test_owned_draft_uses_cache_and_checks_dataset_access(
    color_context: Mock, mocker: MockerFixture
) -> None:
    """The cached draft, not the request's submitted rule body, supplies formatting."""
    cache = mocker.patch(f"{CONTEXT_MODULE}.cache_manager")
    cache.explore_form_data_cache.get.return_value = _draft_state(color_context)
    check_access = mocker.patch(f"{CONTEXT_MODULE}.check_access")
    context = TableColorContext.resolve(
        color_context, {"version": 2, "form_data_key": "owned-draft"}
    )
    assert context.row_limit == 800
    cache.explore_form_data_cache.get.assert_called_once_with("owned-draft")
    check_access.assert_called_once_with(7, 9, DatasourceType.TABLE)


def test_unsaved_explore_draft_does_not_require_a_saved_slice(
    color_context: Mock, mocker: MockerFixture
) -> None:
    """A matching owner and dataset can color-filter a new Explore draft."""
    state = _draft_state(color_context)
    state["chart_id"] = None
    color_context.slice_ = None
    cache = mocker.patch(f"{CONTEXT_MODULE}.cache_manager")
    cache.explore_form_data_cache.get.return_value = state
    check_access = mocker.patch(f"{CONTEXT_MODULE}.check_access")
    context = TableColorContext.resolve(
        color_context, {"version": 2, "form_data_key": "unsaved-draft"}
    )
    assert context.row_limit == 800
    check_access.assert_called_once_with(7, None, DatasourceType.TABLE)


def test_dataset_access_denial_precedes_cache_reads(
    color_context: Mock, mocker: MockerFixture
) -> None:
    """Permission failures are not bypassed by a plausible snapshot or draft key."""
    color_context.raise_for_access.side_effect = PermissionError("denied")
    cache = mocker.patch(f"{CONTEXT_MODULE}.cache_manager")
    with pytest.raises(PermissionError):
        TableColorContext.resolve(
            color_context, {"version": 2, "form_data_key": "draft"}
        )
    cache.explore_form_data_cache.get.assert_not_called()


@pytest.mark.parametrize(
    "override",
    [
        {"owner": 12},
        {"datasource_id": 8},
        {"datasource_type": "query"},
        {"chart_id": 10},
        {"form_data": "private-invalid-json"},
        {"form_data": "[]"},
    ],
)
def test_draft_rejects_foreign_or_corrupt_cache_state(
    color_context: Mock, mocker: MockerFixture, override: dict[str, Any]
) -> None:
    """Ownership, datasource and chart bindings must all match before draft use."""
    cache = mocker.patch(f"{CONTEXT_MODULE}.cache_manager")
    cache.explore_form_data_cache.get.return_value = {
        **_draft_state(color_context),
        **override,
    }
    with pytest.raises(TableColorFilterError) as error:
        TableColorContext.resolve(
            color_context, {"version": 2, "form_data_key": "foreign-draft"}
        )
    assert error.value.code == "TABLE_COLOR_FILTER_ACCESS_DENIED"
    assert "private-invalid-json" not in str(error.value)


def test_expired_draft_and_anonymous_owner_fail_closed(
    color_context: Mock, mocker: MockerFixture
) -> None:
    """Missing state and anonymous callers never fall back to submitted form data."""
    cache = mocker.patch(f"{CONTEXT_MODULE}.cache_manager")
    cache.explore_form_data_cache.get.return_value = None
    with pytest.raises(TableColorFilterError, match="TABLE_COLOR_FILTER_ACCESS_DENIED"):
        TableColorContext.resolve(
            color_context, {"version": 2, "form_data_key": "expired"}
        )
    cache.explore_form_data_cache.get.return_value = _draft_state(color_context)
    mocker.patch(f"{CONTEXT_MODULE}.get_user_id", return_value=None)
    with pytest.raises(TableColorFilterError, match="TABLE_COLOR_FILTER_ACCESS_DENIED"):
        TableColorContext.resolve(
            color_context, {"version": 2, "form_data_key": "owned-draft"}
        )


@pytest.mark.parametrize("dashboard_id", [True, -1, "not-an-id", "1.5"])
def test_context_rejects_invalid_dashboard_identifiers(
    color_context: Mock, dashboard_id: object, mocker: MockerFixture
) -> None:
    """No lookup is attempted for invalid dashboard identifiers."""
    color_context.form_data["dashboardId"] = dashboard_id
    lookup = mocker.patch(f"{CONTEXT_MODULE}.DashboardDAO.find_by_id")
    with pytest.raises(TableColorFilterError, match="TABLE_COLOR_FILTER_ACCESS_DENIED"):
        TableColorContext.resolve(color_context, {"version": 2})
    lookup.assert_not_called()


def test_dashboard_context_requires_membership_and_access(
    color_context: Mock, mocker: MockerFixture
) -> None:
    """A chart must belong to the requested readable dashboard."""
    color_context.form_data["dashboardId"] = "3"
    dashboard = Mock(id=3, slices=[color_context.slice_], theme=None)
    mocker.patch(f"{CONTEXT_MODULE}.DashboardDAO.find_by_id", return_value=dashboard)
    access = mocker.patch(f"{CONTEXT_MODULE}.security_manager.raise_for_access")
    context = TableColorContext.resolve(color_context, {"version": 2})
    assert context.dashboard_id == 3
    access.assert_called_once_with(dashboard=dashboard)
    dashboard.slices = []
    with pytest.raises(TableColorFilterError, match="TABLE_COLOR_FILTER_ACCESS_DENIED"):
        TableColorContext.resolve(color_context, {"version": 2})


@pytest.mark.parametrize("guest", [False, True])
def test_dashboard_or_guest_cannot_use_explore_draft(
    color_context: Mock, mocker: MockerFixture, guest: bool
) -> None:
    """A draft key is never accepted as a dashboard or guest-token trust source."""
    mocker.patch(f"{CONTEXT_MODULE}.security_manager.is_guest_user", return_value=guest)
    if not guest:
        color_context.form_data["dashboardId"] = 3
        mocker.patch(
            f"{CONTEXT_MODULE}.DashboardDAO.find_by_id",
            return_value=Mock(id=3, slices=[color_context.slice_], theme=None),
        )
    cache = mocker.patch(f"{CONTEXT_MODULE}.cache_manager")
    with pytest.raises(TableColorFilterError, match="TABLE_COLOR_FILTER_ACCESS_DENIED"):
        TableColorContext.resolve(
            color_context, {"version": 2, "form_data_key": "owned-draft"}
        )
    cache.explore_form_data_cache.get.assert_not_called()


def test_theme_tokens_match_native_default_and_dark_semantic_palette(
    color_context: Mock,
) -> None:
    """Expected tokens were checked with Ant Design's native darkAlgorithm."""
    assert _theme_tokens(None, "default") == {
        **PALETTE,
        "colorFill": "rgba(0,0,0,0.15)",
    }
    assert _theme_tokens(None, "dark") == {
        "colorSuccess": "#50a777",
        "colorWarning": "#d9ac03",
        "colorError": "#c13c4b",
        "colorPrimary": "#25809b",
        "colorFill": "rgba(255,255,255,0.18)",
    }


def test_dashboard_theme_overrides_are_server_owned_and_invalid_dark_colors_fail(
    color_context: Mock,
) -> None:
    """Dashboard tokens override seeds; unusable seeds fail explicitly."""
    dashboard = Mock(
        theme=Mock(json_data=json.dumps({"token": {"colorSuccess": "#52c41a"}}))
    )
    assert _theme_tokens(dashboard, "default")["colorSuccess"] == "#52c41a"
    assert _theme_tokens(dashboard, "dark")["colorSuccess"] == "#49aa19"
    dashboard.theme.json_data = json.dumps(
        {"token": {"colorError": "var(--private-token)"}}
    )
    with pytest.raises(TableColorFilterError) as error:
        _theme_tokens(dashboard, "dark")
    assert error.value.code == "TABLE_COLOR_FILTER_THEME_UNSUPPORTED"
    assert error.value.status == 422


def test_dashboard_filter_fingerprint_is_canonical_without_transient_fields() -> None:
    """Only query-affecting native/cross state contributes to the opaque binding."""
    filters = [
        {"col": "region", "op": "IN", "val": ["APAC"], "label": "unused"},
        {"col": "channel", "op": "IN", "val": ["Online"]},
    ]
    original = dashboard_filter_fingerprint({"filters": filters, "time_range": "2026"})
    assert (
        dashboard_filter_fingerprint(
            {"filters": list(reversed(filters)), "time_range": "2026", "page": 8}
        )
        == original
    )
    assert (
        dashboard_filter_fingerprint({"filters": filters, "time_range": "2025"})
        != original
    )
    assert "APAC" not in original
