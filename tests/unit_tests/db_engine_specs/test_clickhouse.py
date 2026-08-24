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

import re
from datetime import datetime
from pathlib import Path
from typing import Any, Optional
from unittest.mock import MagicMock, Mock

import pytest
from packaging.requirements import Requirement
from sqlalchemy.engine.url import make_url
from sqlalchemy.types import (
    Boolean,
    Date,
    DateTime,
    DECIMAL,
    Float,
    Integer,
    String,
    TypeEngine,
)
from urllib3.connection import HTTPConnection
from urllib3.exceptions import NewConnectionError

from superset.utils.core import GenericDataType
from tests.unit_tests.db_engine_specs.utils import (
    assert_column_spec,
    assert_convert_dttm,
)
from tests.unit_tests.fixtures.common import dttm  # noqa: F401


@pytest.mark.parametrize(
    "target_type,expected_result",
    [
        ("Date", "toDate('2019-01-02')"),
        ("DateTime", "toDateTime('2019-01-02 03:04:05')"),
        ("UnknownType", None),
    ],
)
def test_convert_dttm(
    target_type: str,
    expected_result: Optional[str],
    dttm: datetime,  # noqa: F811
) -> None:
    from superset.db_engine_specs.clickhouse import (
        ClickHouseEngineSpec as spec,  # noqa: N813
    )

    assert_convert_dttm(spec, target_type, expected_result, dttm)


def test_execute_connection_error() -> None:
    from superset.db_engine_specs.clickhouse import ClickHouseEngineSpec
    from superset.db_engine_specs.exceptions import SupersetDBAPIDatabaseError

    database = Mock()
    cursor = Mock()
    cursor.execute.side_effect = NewConnectionError(
        HTTPConnection("localhost"), "Exception with sensitive data"
    )
    with pytest.raises(SupersetDBAPIDatabaseError) as excinfo:
        ClickHouseEngineSpec.execute(cursor, "SELECT col1 from table1", database)
    assert str(excinfo.value) == "Connection failed"


@pytest.mark.parametrize("engine", ["clickhouse", "clickhousedb"])
def test_date_trunc_datepart_error_is_sanitized(engine: str) -> None:
    from superset.db_engine_specs.clickhouse import (
        ClickHouseConnectEngineSpec,
        ClickHouseEngineSpec,
    )

    spec = (
        ClickHouseEngineSpec if engine == "clickhouse" else ClickHouseConnectEngineSpec
    )
    error = Exception(
        "Received ClickHouse exception, Code: 36. DB::Exception: "
        "MONTH doesn't look like datepart name in date_trunc while processing "
        "sensitive_expression "
        "at http://database.internal:8123"
    )

    message = spec.extract_error_message(error)

    assert message == (
        "ClickHouse rejected the date truncation unit. Use a lowercase unit."
    )
    assert "sensitive_expression" not in message
    assert "database.internal" not in message


@pytest.mark.parametrize("engine", ["clickhouse", "clickhousedb"])
def test_unrelated_clickhouse_errors_keep_default_message(engine: str) -> None:
    from superset.db_engine_specs.clickhouse import (
        ClickHouseConnectEngineSpec,
        ClickHouseEngineSpec,
    )

    spec = (
        ClickHouseEngineSpec if engine == "clickhouse" else ClickHouseConnectEngineSpec
    )
    error = Exception("Code: 36. Another ClickHouse validation error")

    assert spec.extract_error_message(error) == f"{engine} error: {error}"


@pytest.mark.parametrize("engine", ["clickhouse", "clickhousedb"])
def test_date_trunc_phrase_in_sql_does_not_reclassify_error(engine: str) -> None:
    from superset.db_engine_specs.clickhouse import (
        ClickHouseConnectEngineSpec,
        ClickHouseEngineSpec,
    )

    spec = (
        ClickHouseEngineSpec if engine == "clickhouse" else ClickHouseConnectEngineSpec
    )
    error = Exception(
        "Code: 36. DB::Exception: Another validation error\n"
        "[SQL: SELECT 'doesn''t look like datepart name in dateTrunc']"
    )

    assert spec.extract_error_message(error) == f"{engine} error: {error}"


@pytest.mark.parametrize(
    "target_type,expected_result",
    [
        ("Date", "toDate('2019-01-02')"),
        ("DateTime", "toDateTime('2019-01-02 03:04:05')"),
        ("UnknownType", None),
    ],
)
def test_connect_convert_dttm(
    target_type: str,
    expected_result: Optional[str],
    dttm: datetime,  # noqa: F811
) -> None:
    from superset.db_engine_specs.clickhouse import (
        ClickHouseEngineSpec as spec,  # noqa: N813
    )

    assert_convert_dttm(spec, target_type, expected_result, dttm)


@pytest.mark.parametrize(
    "native_type,sqla_type,attrs,generic_type,is_dttm",
    [
        ("String", String, None, GenericDataType.STRING, False),
        ("LowCardinality(String)", String, None, GenericDataType.STRING, False),
        ("Nullable(String)", String, None, GenericDataType.STRING, False),
        (
            "LowCardinality(Nullable(String))",
            String,
            None,
            GenericDataType.STRING,
            False,
        ),
        ("Array(UInt8)", String, None, GenericDataType.STRING, False),
        ("Enum('hello', 'world')", String, None, GenericDataType.STRING, False),
        ("Enum('UInt32', 'Bool')", String, None, GenericDataType.STRING, False),
        (
            "LowCardinality(Enum('hello', 'world'))",
            String,
            None,
            GenericDataType.STRING,
            False,
        ),
        (
            "Nullable(Enum('hello', 'world'))",
            String,
            None,
            GenericDataType.STRING,
            False,
        ),
        (
            "LowCardinality(Nullable(Enum('hello', 'world')))",
            String,
            None,
            GenericDataType.STRING,
            False,
        ),
        ("FixedString(16)", String, None, GenericDataType.STRING, False),
        ("Nullable(FixedString(16))", String, None, GenericDataType.STRING, False),
        (
            "LowCardinality(Nullable(FixedString(16)))",
            String,
            None,
            GenericDataType.STRING,
            False,
        ),
        ("UUID", String, None, GenericDataType.STRING, False),
        ("Int8", Integer, None, GenericDataType.NUMERIC, False),
        ("Int16", Integer, None, GenericDataType.NUMERIC, False),
        ("Int32", Integer, None, GenericDataType.NUMERIC, False),
        ("Int64", Integer, None, GenericDataType.NUMERIC, False),
        ("Int128", Integer, None, GenericDataType.NUMERIC, False),
        ("Int256", Integer, None, GenericDataType.NUMERIC, False),
        ("Nullable(Int256)", Integer, None, GenericDataType.NUMERIC, False),
        (
            "LowCardinality(Nullable(Int256))",
            Integer,
            None,
            GenericDataType.NUMERIC,
            False,
        ),
        ("UInt8", Integer, None, GenericDataType.NUMERIC, False),
        ("UInt16", Integer, None, GenericDataType.NUMERIC, False),
        ("UInt32", Integer, None, GenericDataType.NUMERIC, False),
        ("UInt64", Integer, None, GenericDataType.NUMERIC, False),
        ("UInt128", Integer, None, GenericDataType.NUMERIC, False),
        ("UInt256", Integer, None, GenericDataType.NUMERIC, False),
        ("Nullable(UInt256)", Integer, None, GenericDataType.NUMERIC, False),
        (
            "LowCardinality(Nullable(UInt256))",
            Integer,
            None,
            GenericDataType.NUMERIC,
            False,
        ),
        ("Float32", Float, None, GenericDataType.NUMERIC, False),
        ("Float64", Float, None, GenericDataType.NUMERIC, False),
        ("Decimal(1, 2)", DECIMAL, None, GenericDataType.NUMERIC, False),
        ("Decimal32(2)", DECIMAL, None, GenericDataType.NUMERIC, False),
        ("Decimal64(2)", DECIMAL, None, GenericDataType.NUMERIC, False),
        ("Decimal128(2)", DECIMAL, None, GenericDataType.NUMERIC, False),
        ("Decimal256(2)", DECIMAL, None, GenericDataType.NUMERIC, False),
        ("Bool", Boolean, None, GenericDataType.BOOLEAN, False),
        ("Nullable(Bool)", Boolean, None, GenericDataType.BOOLEAN, False),
        ("Date", Date, None, GenericDataType.TEMPORAL, True),
        ("Nullable(Date)", Date, None, GenericDataType.TEMPORAL, True),
        ("LowCardinality(Nullable(Date))", Date, None, GenericDataType.TEMPORAL, True),
        ("Date32", Date, None, GenericDataType.TEMPORAL, True),
        ("Datetime", DateTime, None, GenericDataType.TEMPORAL, True),
        ("Nullable(Datetime)", DateTime, None, GenericDataType.TEMPORAL, True),
        (
            "LowCardinality(Nullable(Datetime))",
            DateTime,
            None,
            GenericDataType.TEMPORAL,
            True,
        ),
        ("Datetime('UTC')", DateTime, None, GenericDataType.TEMPORAL, True),
        ("Datetime64(3)", DateTime, None, GenericDataType.TEMPORAL, True),
        ("Datetime64(3, 'UTC')", DateTime, None, GenericDataType.TEMPORAL, True),
    ],
)
def test_connect_get_column_spec(
    native_type: str,
    sqla_type: type[TypeEngine],
    attrs: Optional[dict[str, Any]],
    generic_type: GenericDataType,
    is_dttm: bool,
) -> None:
    from superset.db_engine_specs.clickhouse import (
        ClickHouseConnectEngineSpec as spec,  # noqa: N813
    )

    assert_column_spec(spec, native_type, sqla_type, attrs, generic_type, is_dttm)


@pytest.mark.parametrize("schema", [None, "superset_quality_21_3"])
def test_get_view_names_uses_clickhouse_system_metadata(schema: Optional[str]) -> None:
    from superset.db_engine_specs.clickhouse import (
        ClickHouseConnectEngineSpec,
        ClickHouseEngineSpec,
    )

    for spec in (ClickHouseEngineSpec, ClickHouseConnectEngineSpec):
        database = MagicMock()
        inspector = MagicMock()
        connection = database.get_raw_connection.return_value.__enter__.return_value
        cursor = connection.cursor.return_value
        cursor.fetchall.return_value = [("drill_wide_flat",)]

        assert spec.get_view_names(database, inspector, schema) == {"drill_wide_flat"}

        sql, params = cursor.execute.call_args.args
        assert "SELECT name FROM system.tables" in sql
        assert "engine = 'View'" in sql
        if schema:
            assert "database = %(schema)s" in sql
            assert schema not in sql
            assert params == {"schema": schema}
        else:
            assert "database = currentDatabase()" in sql
            assert params == {}
        database.get_raw_connection.assert_called_once_with(schema=schema)


def test_get_view_names_propagates_database_errors() -> None:
    from superset.db_engine_specs.clickhouse import ClickHouseConnectEngineSpec

    database = MagicMock()
    inspector = MagicMock()
    connection = database.get_raw_connection.return_value.__enter__.return_value
    connection.cursor.return_value.execute.side_effect = RuntimeError("metadata error")

    with pytest.raises(RuntimeError, match="metadata error"):
        ClickHouseConnectEngineSpec.get_view_names(database, inspector, "analytics")


def test_get_view_names_maps_legacy_connection_errors() -> None:
    from superset.db_engine_specs.clickhouse import ClickHouseEngineSpec
    from superset.db_engine_specs.exceptions import SupersetDBAPIDatabaseError

    database = MagicMock()
    inspector = MagicMock()
    connection = database.get_raw_connection.return_value.__enter__.return_value
    connection.cursor.return_value.execute.side_effect = NewConnectionError(
        HTTPConnection("localhost"), "sensitive metadata error"
    )

    with pytest.raises(SupersetDBAPIDatabaseError, match="Connection failed"):
        ClickHouseEngineSpec.get_view_names(database, inspector, "analytics")


def test_get_table_names_excludes_clickhouse_views() -> None:
    from superset.db_engine_specs.clickhouse import (
        ClickHouseConnectEngineSpec,
        ClickHouseEngineSpec,
    )

    for spec in (ClickHouseEngineSpec, ClickHouseConnectEngineSpec):
        database = MagicMock()
        inspector = MagicMock()
        inspector.get_table_names.return_value = ["fact_sales", "drill_wide_flat"]
        connection = database.get_raw_connection.return_value.__enter__.return_value
        connection.cursor.return_value.fetchall.return_value = [("drill_wide_flat",)]

        assert spec.get_table_names(database, inspector, "superset_quality_21_3") == {
            "fact_sales"
        }


def test_connect_metadata_matches_supported_driver_range() -> None:
    from superset.db_engine_specs.clickhouse import ClickHouseConnectEngineSpec

    package = "clickhouse-connect>=0.13.0,<1.0"
    metadata = ClickHouseConnectEngineSpec.metadata
    pyproject_path = Path(__file__).resolve().parents[3] / "pyproject.toml"
    pyproject = pyproject_path.read_text(encoding="utf-8")
    declared_match = re.search(
        r'^clickhouse\s*=\s*\["([^"]+)"\]\s*$', pyproject, re.MULTILINE
    )

    assert declared_match is not None
    declared_package = declared_match.group(1)
    assert Requirement(package) == Requirement(declared_package)
    assert metadata["pypi_packages"] == [package]
    assert metadata["version_requirements"] == package
    assert metadata["drivers"][0]["pypi_package"] == package
    assert package in metadata["install_instructions"]
    assert all(
        compatible["pypi_packages"] == [package]
        for compatible in metadata["compatible_databases"]
    )


@pytest.mark.parametrize(
    "schema, expected_result",
    [
        (None, "clickhousedb+connect://localhost:443/__default__"),
        (
            "new_schema",
            "clickhousedb+connect://localhost:443/new_schema",
        ),
    ],
)
def test_adjust_engine_params_fully_qualified(
    schema: str, expected_result: str
) -> None:
    from superset.db_engine_specs.clickhouse import (
        ClickHouseConnectEngineSpec as spec,  # noqa: N813
    )

    url = make_url("clickhousedb+connect://localhost:443/__default__")

    uri = spec.adjust_engine_params(url, {}, None, schema)[0]
    assert str(uri) == expected_result
