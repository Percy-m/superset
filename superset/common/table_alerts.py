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
"""Trusted resolution and SQLAlchemy predicates for Table alert filters."""

from __future__ import annotations

import hashlib
import uuid
from collections import defaultdict
from decimal import Decimal, InvalidOperation
from typing import Any, cast, Literal, NotRequired, TYPE_CHECKING, TypedDict

import sqlalchemy as sa
from sqlalchemy.sql.elements import ColumnElement

from superset.exceptions import QueryObjectValidationError
from superset.superset_typing import Metric
from superset.utils import json
from superset.utils.core import is_adhoc_metric

if TYPE_CHECKING:
    from superset.connectors.sqla.models import SqlaTable
    from superset.models.slice import Slice

ALERT_FILTERS_EXTRA_KEY = "__table_alert_filter_groups"
ALERT_FILTER_FINGERPRINT_EXTRA_KEY = "__table_alert_filter_fingerprint"
ALERT_TOTALS_EXTRA_KEY = "__table_alert_totals"
INVALID_ALERT_RULE_MESSAGE = (
    "TABLE_ALERT_RULE_INVALID: invalid or stale table alert rule"
)

ALERT_LEVELS = frozenset({"RED", "YELLOW", "GREEN"})
ALERT_SUBJECT_KINDS = frozenset({"physical_column", "saved_metric"})
SINGLE_VALUE_OPERATORS = frozenset({"=", "≠", "<", ">", "≤", "≥"})
RANGE_OPERATORS = frozenset(
    {
        "< x <",
        "≤ x ≤",
        "≤ x <",
        "< x ≤",
    }
)

AlertSubjectKind = Literal["physical_column", "saved_metric"]


class AlertFilterReference(TypedDict):
    """Untrusted rule reference supplied by a Table client."""

    rule_id: str | uuid.UUID
    level: str


class ResolvedAlertRule(TypedDict):
    """Canonical trusted numeric comparison resolved from saved form data."""

    rule_id: str
    operator: str
    target_value: NotRequired[str]
    target_value_left: NotRequired[str]
    target_value_right: NotRequired[str]


class ResolvedAlertGroup(TypedDict):
    """Rules for one subject; rules are ORed and subjects are ANDed."""

    kind: AlertSubjectKind
    key: str
    rules: list[ResolvedAlertRule]


def _invalid_alert_rule() -> QueryObjectValidationError:
    return QueryObjectValidationError(INVALID_ALERT_RULE_MESSAGE)


def _decimal_string(value: object) -> str:
    if isinstance(value, bool) or value is None:
        raise _invalid_alert_rule()
    try:
        decimal_value = Decimal(str(value))
    except (InvalidOperation, ValueError) as ex:
        raise _invalid_alert_rule() from ex
    if not decimal_value.is_finite():
        raise _invalid_alert_rule()
    return format(decimal_value.normalize(), "f")


def _uuid4_string(value: object) -> str:
    try:
        parsed = value if isinstance(value, uuid.UUID) else uuid.UUID(str(value))
    except (AttributeError, TypeError, ValueError) as ex:
        raise _invalid_alert_rule() from ex
    if parsed.version != 4:
        raise _invalid_alert_rule()
    return str(parsed)


def _saved_metric_names(metrics: list[Metric] | None) -> set[str]:
    return {
        metric
        for metric in metrics or []
        if isinstance(metric, str) and not is_adhoc_metric(metric)
    }


def _physical_column_names(columns: list[object] | None) -> set[str]:
    return {column for column in columns or [] if isinstance(column, str)}


class TableRuleResolver:
    """Resolve client rule references against a trusted saved Table chart."""

    def __init__(self, slice_: Slice, datasource: SqlaTable) -> None:
        self._slice = slice_
        self._datasource = datasource

    def resolve(  # noqa: C901
        self,
        references: list[AlertFilterReference],
        query: dict[str, Any],
    ) -> tuple[list[ResolvedAlertGroup], str]:
        """Return canonical groups and a fingerprint or reject the whole request."""
        if (
            self._slice.viz_type != "table"
            or self._slice.datasource_type != "table"
            or self._slice.datasource_id != self._datasource.id
        ):
            raise _invalid_alert_rule()

        form_data = self._slice.form_data
        saved_groupby = _physical_column_names(form_data.get("groupby"))
        saved_metrics = _saved_metric_names(form_data.get("metrics"))
        query_columns = _physical_column_names(query.get("columns"))
        query_metrics = _saved_metric_names(query.get("metrics"))
        datasource_columns = {
            column.column_name: column for column in self._datasource.columns
        }
        datasource_metrics = {
            metric.metric_name: metric for metric in self._datasource.metrics
        }

        saved_rules = form_data.get("conditional_formatting")
        if not isinstance(saved_rules, list):
            raise _invalid_alert_rule()

        rules_by_id: dict[str, tuple[int, dict[str, Any]]] = {}
        for position, candidate in enumerate(saved_rules):
            if not isinstance(candidate, dict) or "ruleId" not in candidate:
                continue
            rule_id = _uuid4_string(candidate["ruleId"])
            if rule_id in rules_by_id:
                raise _invalid_alert_rule()
            rules_by_id[rule_id] = (position, candidate)

        normalized_references: set[tuple[str, str]] = set()
        for reference in references:
            if not isinstance(reference, dict):
                raise _invalid_alert_rule()
            level = reference.get("level")
            if level not in ALERT_LEVELS:
                raise _invalid_alert_rule()
            normalized_references.add((_uuid4_string(reference.get("rule_id")), level))
        if not normalized_references or len(normalized_references) > 50:
            raise _invalid_alert_rule()

        selected_rule_order: list[tuple[int, str, str]] = []
        for rule_id, level in normalized_references:
            saved_rule = rules_by_id.get(rule_id)
            if saved_rule is None:
                raise _invalid_alert_rule()
            selected_rule_order.append((saved_rule[0], rule_id, level))

        grouped_rules: dict[tuple[AlertSubjectKind, str], list[ResolvedAlertRule]] = (
            defaultdict(list)
        )
        for rule_id, requested_level in sorted(normalized_references):
            saved_rule = rules_by_id.get(rule_id)
            if saved_rule is None:
                raise _invalid_alert_rule()
            rule = saved_rule[1]
            if (
                rule.get("alertLevel") != requested_level
                or rule.get("filterable") is not True
                or rule.get("useGradient") is True
                or rule.get("objectFormatting") == "CELL_BAR"
            ):
                raise _invalid_alert_rule()

            subject = rule.get("subjectRef")
            if not isinstance(subject, dict):
                raise _invalid_alert_rule()
            kind_value = subject.get("kind")
            key = subject.get("key")
            if (
                kind_value not in ALERT_SUBJECT_KINDS
                or not isinstance(key, str)
                or not key
                or rule.get("column") != key
            ):
                raise _invalid_alert_rule()
            kind = cast(AlertSubjectKind, kind_value)

            if kind == "physical_column":
                column = datasource_columns.get(key)
                if (
                    key not in saved_groupby
                    or key not in query_columns
                    or column is None
                    or not column.is_numeric
                ):
                    raise _invalid_alert_rule()
            elif (
                key not in saved_metrics
                or key not in query_metrics
                or key not in datasource_metrics
            ):
                raise _invalid_alert_rule()

            operator = rule.get("operator")
            if not isinstance(operator, str):
                raise _invalid_alert_rule()
            resolved_rule: ResolvedAlertRule = {
                "rule_id": rule_id,
                "operator": operator,
            }
            if operator in SINGLE_VALUE_OPERATORS:
                resolved_rule["target_value"] = _decimal_string(rule.get("targetValue"))
            elif operator in RANGE_OPERATORS:
                left = _decimal_string(rule.get("targetValueLeft"))
                right = _decimal_string(rule.get("targetValueRight"))
                if Decimal(left) >= Decimal(right):
                    raise _invalid_alert_rule()
                resolved_rule["target_value_left"] = left
                resolved_rule["target_value_right"] = right
            else:
                raise _invalid_alert_rule()

            grouped_rules[(kind, key)].append(resolved_rule)

        groups: list[ResolvedAlertGroup] = []
        for (kind, key), rules in sorted(grouped_rules.items()):
            groups.append(
                {
                    "kind": kind,
                    "key": key,
                    "rules": sorted(rules, key=lambda item: item["rule_id"]),
                }
            )

        fingerprint_payload = {
            "groups": groups,
            "selected_rules": [
                {"rule_id": rule_id, "level": level}
                for _, rule_id, level in sorted(selected_rule_order)
            ],
        }
        canonical = json.dumps(
            fingerprint_payload,
            sort_keys=True,
            separators=(",", ":"),
        )
        fingerprint = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        return groups, fingerprint


def build_alert_group_clause(
    subject: ColumnElement, rules: list[ResolvedAlertRule]
) -> ColumnElement:
    """Compile one server-resolved subject group without accepting SQL text."""
    clauses: list[ColumnElement] = []
    for rule in rules:
        operator = rule["operator"]
        if operator in SINGLE_VALUE_OPERATORS:
            value = Decimal(rule["target_value"])
            clause = {
                "=": subject == value,
                "≠": subject != value,
                "<": subject < value,
                ">": subject > value,
                "≤": subject <= value,
                "≥": subject >= value,
            }[operator]
        else:
            left = Decimal(rule["target_value_left"])
            right = Decimal(rule["target_value_right"])
            lower = subject >= left if operator.startswith("≤") else subject > left
            upper = subject <= right if operator.endswith("≤") else subject < right
            clause = sa.and_(lower, upper)
        clauses.append(clause)
    if not clauses:
        raise _invalid_alert_rule()
    return sa.or_(*clauses)
