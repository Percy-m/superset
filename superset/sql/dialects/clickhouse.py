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

from sqlglot import exp
from sqlglot.dialects.clickhouse import ClickHouse as SQLGlotClickHouse
from sqlglot.dialects.dialect import unit_to_str
from sqlglot.generator import Generator


def legacy_date_trunc_sql(
    generator: Generator,
    expression: exp.DateTrunc | exp.TimestampTrunc,
) -> str:
    """Generate ``dateTrunc`` with legacy-compatible static time units."""
    unit_expression = expression.args.get("unit")
    if type(unit_expression) not in (exp.Literal, exp.Var):
        if isinstance(expression, exp.TimestampTrunc):
            stock_transform = SQLGlotClickHouse.Generator.TRANSFORMS[exp.TimestampTrunc]
            return stock_transform(generator, expression)

        return generator.function_fallback_sql(expression)

    unit = unit_to_str(expression)
    if isinstance(unit, exp.Literal) and unit.is_string:
        unit = exp.Literal.string(unit.name.lower())

    return generator.func(
        "dateTrunc",
        unit,
        expression.this,
        expression.args.get("zone"),
    )


class SupersetClickHouse(SQLGlotClickHouse):
    """Superset's SQLGlot dialect for ClickHouse."""

    class Generator(SQLGlotClickHouse.Generator):
        TRANSFORMS = {
            **SQLGlotClickHouse.Generator.TRANSFORMS,
            exp.DateTrunc: legacy_date_trunc_sql,
            exp.TimestampTrunc: legacy_date_trunc_sql,
        }
