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
from typing import Any, cast

import pytest
import sqlalchemy as sa

from superset.common.table_alerts import (
    AlertFilterReference,
    build_alert_group_clause,
    TableRuleResolver,
)
from superset.connectors.sqla.models import SqlaTable
from superset.exceptions import QueryObjectValidationError
from superset.models.slice import Slice

RED_RULE_ID = "772a548e-72f7-4ac8-a8ff-fdb7465b3ccd"
YELLOW_RULE_ID = "99ab3f13-81cb-433b-8615-1290f4ddf6cc"
GREEN_RULE_ID = "90924ca6-92f3-401f-95a7-7db5e3a52a80"


def make_rule(
    rule_id: str,
    *,
    subject_kind: str,
    subject_key: str,
    level: str,
    operator: str,
    target_value: int,
) -> dict[str, Any]:
    """Build a saved deterministic formatting rule for resolver tests."""
    return {
        "ruleId": rule_id,
        "subjectRef": {"kind": subject_kind, "key": subject_key},
        "alertLevel": level,
        "filterable": True,
        "column": subject_key,
        "operator": operator,
        "targetValue": target_value,
        "useGradient": False,
        "objectFormatting": "BACKGROUND_COLOR",
    }


def make_resolver(rules: list[dict[str, Any]]) -> TableRuleResolver:
    """Build a resolver with physical and saved-metric numeric subjects."""
    slice_ = SimpleNamespace(
        viz_type="table",
        datasource_type="table",
        datasource_id=7,
        form_data={
            "groupby": ["quantity"],
            "metrics": ["gross_revenue"],
            "conditional_formatting": rules,
        },
    )
    datasource = SimpleNamespace(
        id=7,
        columns=[SimpleNamespace(column_name="quantity", is_numeric=True)],
        metrics=[SimpleNamespace(metric_name="gross_revenue")],
    )
    return TableRuleResolver(
        cast(Slice, slice_),
        cast(SqlaTable, datasource),
    )


def test_table_rule_resolver_groups_same_subject_with_or_and_subjects_with_and() -> (
    None
):
    """Canonical groups preserve OR-within and AND-across semantics."""
    resolver = make_resolver(
        [
            make_rule(
                RED_RULE_ID,
                subject_kind="saved_metric",
                subject_key="gross_revenue",
                level="RED",
                operator="<",
                target_value=0,
            ),
            make_rule(
                YELLOW_RULE_ID,
                subject_kind="saved_metric",
                subject_key="gross_revenue",
                level="YELLOW",
                operator="<",
                target_value=100,
            ),
            make_rule(
                GREEN_RULE_ID,
                subject_kind="physical_column",
                subject_key="quantity",
                level="GREEN",
                operator="≥",
                target_value=10,
            ),
        ]
    )

    groups, fingerprint = resolver.resolve(
        [
            {"rule_id": YELLOW_RULE_ID, "level": "YELLOW"},
            {"rule_id": GREEN_RULE_ID, "level": "GREEN"},
            {"rule_id": RED_RULE_ID, "level": "RED"},
            {"rule_id": RED_RULE_ID, "level": "RED"},
        ],
        {"columns": ["quantity"], "metrics": ["gross_revenue"]},
    )

    assert [(group["kind"], group["key"]) for group in groups] == [
        ("physical_column", "quantity"),
        ("saved_metric", "gross_revenue"),
    ]
    assert len(groups[0]["rules"]) == 1
    assert len(groups[1]["rules"]) == 2
    assert len(fingerprint) == 64


def test_table_rule_resolver_fingerprint_is_selection_order_independent() -> None:
    """Selection order and duplicate references do not fragment the cache."""
    rules = [
        make_rule(
            RED_RULE_ID,
            subject_kind="saved_metric",
            subject_key="gross_revenue",
            level="RED",
            operator="<",
            target_value=0,
        ),
        make_rule(
            YELLOW_RULE_ID,
            subject_kind="saved_metric",
            subject_key="gross_revenue",
            level="YELLOW",
            operator="<",
            target_value=100,
        ),
    ]
    resolver = make_resolver(rules)
    query = {"columns": ["quantity"], "metrics": ["gross_revenue"]}

    _, first = resolver.resolve(
        [
            {"rule_id": RED_RULE_ID, "level": "RED"},
            {"rule_id": YELLOW_RULE_ID, "level": "YELLOW"},
        ],
        query,
    )
    _, second = resolver.resolve(
        [
            {"rule_id": YELLOW_RULE_ID, "level": "YELLOW"},
            {"rule_id": RED_RULE_ID, "level": "RED"},
            {"rule_id": RED_RULE_ID, "level": "RED"},
        ],
        query,
    )

    assert first == second


def test_table_rule_resolver_fingerprint_tracks_saved_rule_order() -> None:
    """Changing saved priority invalidates cache even when predicates commute."""
    rules = [
        make_rule(
            RED_RULE_ID,
            subject_kind="saved_metric",
            subject_key="gross_revenue",
            level="RED",
            operator="<",
            target_value=0,
        ),
        make_rule(
            YELLOW_RULE_ID,
            subject_kind="saved_metric",
            subject_key="gross_revenue",
            level="YELLOW",
            operator="<",
            target_value=100,
        ),
    ]
    references: list[AlertFilterReference] = [
        {"rule_id": RED_RULE_ID, "level": "RED"},
        {"rule_id": YELLOW_RULE_ID, "level": "YELLOW"},
    ]
    query = {"columns": ["quantity"], "metrics": ["gross_revenue"]}

    _, first = make_resolver(rules).resolve(references, query)
    _, second = make_resolver(list(reversed(rules))).resolve(references, query)

    assert first != second


def test_table_rule_resolver_rejects_forged_level_without_exposing_rule() -> None:
    """A client cannot change the level attached to a saved rule."""
    resolver = make_resolver(
        [
            make_rule(
                RED_RULE_ID,
                subject_kind="saved_metric",
                subject_key="gross_revenue",
                level="RED",
                operator="<",
                target_value=0,
            )
        ]
    )

    with pytest.raises(QueryObjectValidationError, match="TABLE_ALERT_RULE_INVALID"):
        resolver.resolve(
            [{"rule_id": RED_RULE_ID, "level": "GREEN"}],
            {"columns": ["quantity"], "metrics": ["gross_revenue"]},
        )


def test_table_rule_resolver_rejects_gradient_and_missing_query_subjects() -> None:
    """Non-deterministic rules and subjects absent from a query are rejected."""
    rule = make_rule(
        RED_RULE_ID,
        subject_kind="saved_metric",
        subject_key="gross_revenue",
        level="RED",
        operator="<",
        target_value=0,
    )
    rule["useGradient"] = True

    with pytest.raises(QueryObjectValidationError, match="TABLE_ALERT_RULE_INVALID"):
        make_resolver([rule]).resolve(
            [{"rule_id": RED_RULE_ID, "level": "RED"}],
            {"columns": ["quantity"], "metrics": ["gross_revenue"]},
        )

    rule["useGradient"] = False
    with pytest.raises(QueryObjectValidationError, match="TABLE_ALERT_RULE_INVALID"):
        make_resolver([rule]).resolve(
            [{"rule_id": RED_RULE_ID, "level": "RED"}],
            {"columns": ["quantity"], "metrics": []},
        )


@pytest.mark.parametrize(
    "rule_update",
    [
        {"filterable": False},
        {"objectFormatting": "CELL_BAR"},
    ],
)
def test_table_rule_resolver_rejects_non_filterable_and_cell_bar_rules(
    rule_update: dict[str, Any],
) -> None:
    """Legacy/non-filterable rules and Cell Bars cannot become predicates."""
    rule = make_rule(
        RED_RULE_ID,
        subject_kind="saved_metric",
        subject_key="gross_revenue",
        level="RED",
        operator="<",
        target_value=0,
    )
    rule.update(rule_update)

    with pytest.raises(QueryObjectValidationError, match="TABLE_ALERT_RULE_INVALID"):
        make_resolver([rule]).resolve(
            [{"rule_id": RED_RULE_ID, "level": "RED"}],
            {"columns": ["quantity"], "metrics": ["gross_revenue"]},
        )


def test_build_alert_group_clause_uses_or_and_decimal_boundaries() -> None:
    """Resolved rules compile as SQLAlchemy expressions with no SQL input."""
    amount = sa.column("amount")
    clause = build_alert_group_clause(
        amount,
        [
            {
                "rule_id": RED_RULE_ID,
                "operator": "<",
                "target_value": "0.01",
            },
            {
                "rule_id": YELLOW_RULE_ID,
                "operator": "≤ x <",
                "target_value_left": "10.25",
                "target_value_right": "20.75",
            },
            {
                "rule_id": GREEN_RULE_ID,
                "operator": "≠",
                "target_value": "30",
            },
        ],
    )

    sql = str(clause.compile(compile_kwargs={"literal_binds": True}))
    assert " OR " in sql
    assert "amount < 0.01" in sql
    assert "amount >= 10.25" in sql
    assert "amount < 20.75" in sql
    assert "amount != 30" in sql
