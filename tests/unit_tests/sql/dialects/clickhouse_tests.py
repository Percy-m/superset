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

import pytest
from sqlglot import Dialect, Dialects, exp, parse_one
from sqlglot.dialects.clickhouse import ClickHouse as SQLGlotClickHouse

from superset.sql.dialects.clickhouse import SupersetClickHouse
from superset.sql.parse import SQLGLOT_DIALECTS, SQLScript


@pytest.mark.parametrize("engine", ["clickhouse", "clickhousedb"])
def test_superset_clickhouse_dialect_is_scoped_to_clickhouse_engines(
    engine: str,
) -> None:
    assert SQLGLOT_DIALECTS[engine] is SupersetClickHouse


def test_other_engine_dialect_mappings_are_unchanged() -> None:
    assert SQLGLOT_DIALECTS["mysql"] is Dialects.MYSQL
    assert SQLGLOT_DIALECTS["postgresql"] is Dialects.POSTGRES
    assert SQLGLOT_DIALECTS["yql"] is Dialects.CLICKHOUSE
    assert type(Dialect.get_or_raise(Dialects.CLICKHOUSE)) is SQLGlotClickHouse
    assert type(Dialect.get_or_raise(SQLGLOT_DIALECTS["yql"])) is SQLGlotClickHouse
    assert SQLScript("SELECT toStartOfMonth(event_time)", "yql").format() == (
        "SELECT\n  dateTrunc('MONTH', event_time)"
    )


def test_stock_clickhouse_transforms_are_not_modified() -> None:
    assert exp.DateTrunc not in SQLGlotClickHouse.Generator.TRANSFORMS
    assert (
        SupersetClickHouse.Generator.TRANSFORMS[exp.TimestampTrunc]
        is not SQLGlotClickHouse.Generator.TRANSFORMS[exp.TimestampTrunc]
    )


@pytest.mark.parametrize(
    "unit",
    ["MINUTE", "HOUR", "DAY", "WEEK", "MONTH", "QUARTER", "YEAR"],
)
@pytest.mark.parametrize("expression_type", [exp.DateTrunc, exp.TimestampTrunc])
def test_static_date_trunc_units_are_lowercase(
    unit: str,
    expression_type: type[exp.DateTrunc] | type[exp.TimestampTrunc],
) -> None:
    expression = expression_type(this=exp.column("event_time"), unit=exp.Var(this=unit))

    assert expression.sql(SupersetClickHouse) == (
        f"dateTrunc('{unit.lower()}', event_time)"
    )


@pytest.mark.parametrize(
    ("function", "unit"),
    [
        ("toStartOfMinute", "minute"),
        ("toStartOfHour", "hour"),
        ("toStartOfDay", "day"),
        ("toMonday", "week"),
        ("toStartOfMonth", "month"),
        ("toStartOfQuarter", "quarter"),
        ("toStartOfYear", "year"),
    ],
)
@pytest.mark.parametrize("engine", ["clickhouse", "clickhousedb"])
def test_sql_script_formats_timestamp_trunc_and_preserves_limit(
    engine: str,
    function: str,
    unit: str,
) -> None:
    script = SQLScript(f"SELECT {function}(event_time)", engine)
    script.statements[0].set_limit_value(100)

    assert script.format() == (f"SELECT\n  dateTrunc('{unit}', event_time)\nLIMIT 100")


@pytest.mark.parametrize("engine", ["clickhouse", "clickhousedb"])
def test_sql_script_formats_standard_date_trunc(engine: str) -> None:
    assert SQLScript("SELECT DATE_TRUNC('MONTH', event_time)", engine).format() == (
        "SELECT\n  dateTrunc('month', event_time)"
    )


@pytest.mark.parametrize("engine", ["clickhouse", "clickhousedb"])
def test_anonymous_date_trunc_is_not_rewritten(engine: str) -> None:
    assert SQLScript("SELECT dateTrunc('MONTH', event_time)", engine).format() == (
        "SELECT\n  dateTrunc('MONTH', event_time)"
    )


def test_timestamp_trunc_preserves_timezone() -> None:
    expression = exp.TimestampTrunc(
        this=exp.column("event_time"),
        unit=exp.Literal.string("MONTH"),
        zone=exp.Literal.string("Asia/Shanghai"),
    )

    assert (
        expression.sql(SupersetClickHouse)
        == "dateTrunc('month', event_time, 'Asia/Shanghai')"
    )


def test_lowercase_date_trunc_is_idempotent() -> None:
    expression = exp.TimestampTrunc(
        this=exp.column("event_time"), unit=exp.Literal.string("month")
    )
    generated = expression.sql(SupersetClickHouse)

    assert generated == "dateTrunc('month', event_time)"
    assert (
        parse_one(generated, dialect=SupersetClickHouse).sql(SupersetClickHouse)
        == generated
    )


@pytest.mark.parametrize(
    "unit",
    [
        exp.Placeholder(this="unit"),
        exp.Parameter(this=exp.Var(this="unit")),
    ],
)
@pytest.mark.parametrize("expression_type", [exp.DateTrunc, exp.TimestampTrunc])
def test_dynamic_date_trunc_units_are_not_rewritten(
    unit: exp.Expression,
    expression_type: type[exp.DateTrunc] | type[exp.TimestampTrunc],
) -> None:
    expression = expression_type(this=exp.column("event_time"), unit=unit)

    generated = expression.sql(SupersetClickHouse)
    stock_generated = expression.sql(SQLGlotClickHouse)

    assert generated == stock_generated


def test_non_target_sql_content_is_unchanged() -> None:
    sql = (
        "SELECT 'MONTH' AS literal_value, `MONTH` AS quoted_identifier, "
        "custom_function('MONTH') AS custom_value FROM events /* MONTH */"
    )

    assert parse_one(sql, dialect=SupersetClickHouse).sql(
        SupersetClickHouse
    ) == parse_one(sql, dialect=SQLGlotClickHouse).sql(SQLGlotClickHouse)


def test_stock_dialects_retain_their_date_trunc_output() -> None:
    expression = exp.TimestampTrunc(
        this=exp.column("event_time"), unit=exp.Var(this="MONTH")
    )

    assert expression.sql(Dialects.MYSQL) == (
        "DATE_ADD('0000-01-01 00:00:00', INTERVAL "
        "(TIMESTAMPDIFF(MONTH, '0000-01-01 00:00:00', event_time)) MONTH)"
    )
    assert expression.sql(Dialects.POSTGRES) == "DATE_TRUNC('MONTH', event_time)"
    assert expression.sql(SQLGlotClickHouse) == "dateTrunc('MONTH', event_time)"
