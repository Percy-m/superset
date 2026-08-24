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


import hashlib
from unittest.mock import patch

from superset.utils import json
from superset.utils.log import (
    collect_request_payload,
    DBEventLogger,
    get_logger_from_status,
)
from tests.integration_tests.test_app import app


def test_log_from_status_exception() -> None:
    (func, log_level) = get_logger_from_status(500)
    assert func.__name__ == "exception"
    assert log_level == "exception"


def test_log_from_status_warning() -> None:
    (func, log_level) = get_logger_from_status(422)
    assert func.__name__ == "warning"
    assert log_level == "warning"


def test_log_from_status_info() -> None:
    (func, log_level) = get_logger_from_status(300)
    assert func.__name__ == "info"
    assert log_level == "info"


def test_sqllab_post_log_payload_excludes_query_and_tokens() -> None:
    sql = "SELECT 'secret-marker 数据质量🙂'"
    with app.test_request_context(
        "/sqllab/",
        method="POST",
        data={
            "csrf_token": "csrf-secret",
            "form_data": json.dumps({"dbid": "3", "sql": sql}),
            "guest_token": "guest-secret",
        },
    ) as request_context:
        request_context.match_request()
        payload = collect_request_payload()

    assert payload == {
        "path": "/sqllab/",
        "sql_navigation_status": "accepted",
        "sql_character_count": len(sql),
        "sql_utf8_byte_count": len(sql.encode("utf-8")),
        "sql_sha256_prefix": hashlib.sha256(sql.encode("utf-8")).hexdigest()[:12],
    }


def test_sqllab_post_log_payload_sanitizes_invalid_inputs() -> None:
    cases: tuple[tuple[dict[str, str], str], ...] = (
        ({}, "missing_payload"),
        ({"form_data": "not-json", "sql": "direct-secret"}, "invalid_payload"),
        ({"form_data": json.dumps({"sql": 42})}, "missing_sql"),
    )

    for form_data, expected_status in cases:
        with app.test_request_context(
            "/sqllab/?sql=query-secret",
            method="POST",
            data={"csrf_token": "csrf-secret", **form_data},
        ) as request_context:
            request_context.match_request()
            payload = collect_request_payload()

        assert payload == {
            "path": "/sqllab/",
            "sql_navigation_status": expected_status,
        }


def test_sqllab_execute_log_payload_excludes_query_and_tokens() -> None:
    sql = "SELECT 'execution-secret 数据质量🙂'"
    with app.test_request_context(
        "/api/v1/sqllab/execute/?sql=query-secret",
        method="POST",
        json={
            "database_id": 7,
            "sql": sql,
            "runAsync": False,
            "queryLimit": 1000,
            "select_as_cta": False,
            "schema": "private_schema",
            "templateParams": '{"token": "template-secret"}',
            "guest_token": "guest-secret",
        },
    ) as request_context:
        request_context.match_request()
        payload = collect_request_payload()

    assert payload == {
        "path": "/api/v1/sqllab/execute/",
        "database_id": 7,
        "runAsync": False,
        "queryLimit": 1000,
        "select_as_cta": False,
        "sql_execution_payload_status": "accepted",
        "sql_character_count": len(sql),
        "sql_utf8_byte_count": len(sql.encode("utf-8")),
        "sql_sha256_prefix": hashlib.sha256(sql.encode("utf-8")).hexdigest()[:12],
    }


def test_sqllab_execute_log_payload_sanitizes_invalid_inputs() -> None:
    cases: tuple[tuple[object, dict[str, object]], ...] = (
        (
            {"database_id": 7, "runAsync": True, "queryLimit": 50},
            {
                "database_id": 7,
                "runAsync": True,
                "queryLimit": 50,
                "sql_execution_payload_status": "missing_sql",
            },
        ),
        (["not", "an", "object"], {"sql_execution_payload_status": "invalid_payload"}),
    )

    for json_payload, expected_diagnostics in cases:
        with app.test_request_context(
            "/api/v1/sqllab/execute/?sql=query-secret",
            method="POST",
            json=json_payload,
        ) as request_context:
            request_context.match_request()
            payload = collect_request_payload()

        assert payload == {
            "path": "/api/v1/sqllab/execute/",
            **expected_diagnostics,
        }


def test_sqllab_execute_log_preserves_curated_payload_contract() -> None:
    with (
        app.test_request_context(
            "/api/v1/sqllab/execute/",
            method="POST",
            json={
                "database_id": 7,
                "sql": "SELECT 1",
                "runAsync": False,
                "queryLimit": 1000,
                "select_as_cta": False,
            },
        ) as request_context,
        patch.object(DBEventLogger, "log") as log,
    ):
        request_context.match_request()
        DBEventLogger().log_with_context(
            action="sqllab.execute",
            log_to_statsd=False,
        )

    assert log.call_args.kwargs["curated_payload"] == {
        "runAsync": False,
        "queryLimit": 1000,
        "select_as_cta": False,
    }


def test_sqllab_diagnostics_reject_invalid_unicode_without_raising() -> None:
    with app.test_request_context(
        "/api/v1/sqllab/execute/",
        method="POST",
        data=b'{"database_id":7,"sql":"\\ud800","runAsync":false}',
        content_type="application/json",
    ) as request_context:
        request_context.match_request()
        execute_payload = collect_request_payload()

    assert execute_payload == {
        "path": "/api/v1/sqllab/execute/",
        "database_id": 7,
        "runAsync": False,
        "sql_execution_payload_status": "invalid_sql_encoding",
    }

    with app.test_request_context(
        "/sqllab/",
        method="POST",
        data={"form_data": '{"sql":"\\ud800"}'},
    ) as request_context:
        request_context.match_request()
        navigation_payload = collect_request_payload()

    assert navigation_payload == {
        "path": "/sqllab/",
        "sql_navigation_status": "invalid_sql_encoding",
    }


def test_sqllab_get_log_payload_keeps_legacy_collection_behavior() -> None:
    with app.test_request_context("/sqllab/?custom_value=visible") as request_context:
        request_context.match_request()
        payload = collect_request_payload()

    assert payload["custom_value"] == "visible"
    assert "sql_navigation_status" not in payload
