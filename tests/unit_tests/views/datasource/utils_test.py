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
"""Tests for superset.views.datasource.utils module."""

from dataclasses import dataclass
from unittest.mock import MagicMock, patch

import pytest

from superset.errors import ErrorLevel, SupersetError, SupersetErrorType
from superset.exceptions import SupersetSecurityException
from superset.utils.core import GenericDataType


@dataclass
class DatasourceColumn:
    """Minimal datasource column metadata used by drill-detail unit tests."""

    column_name: str
    type_generic: GenericDataType
    expression: str | None = None
    filterable: bool = True
    is_active: bool = True
    verbose_name: str | None = None


@patch("superset.views.datasource.utils.get_limit_clause")
def test_get_samples_raises_security_exception_when_access_denied(
    mock_get_limit_clause: MagicMock,
):
    """
    Test that get_samples() enforces access control by calling raise_for_access().
    This verifies the fix for issue #31944 where users with "can samples on Datasource"
    permission could read samples from datasets they don't have access to.
    """
    mock_get_limit_clause.return_value = {"row_offset": 0, "row_limit": 100}

    mock_datasource = MagicMock()
    mock_datasource.type = "table"
    mock_datasource.id = 1
    mock_datasource.columns = []

    mock_samples_context = MagicMock()
    mock_count_context = MagicMock()

    # Simulate security exception when raise_for_access is called
    mock_samples_context.raise_for_access.side_effect = SupersetSecurityException(
        SupersetError(
            message="Access denied",
            error_type=SupersetErrorType.DATASOURCE_SECURITY_ACCESS_ERROR,
            level=ErrorLevel.WARNING,
        )
    )

    with (
        patch(
            "superset.views.datasource.utils.DatasourceDAO.get_datasource",
            return_value=mock_datasource,
        ),
        patch(
            "superset.views.datasource.utils.QueryContextFactory"
        ) as mock_factory_class,
    ):
        mock_factory = MagicMock()
        mock_factory_class.return_value = mock_factory

        # Return different mock contexts for samples vs count queries
        mock_factory.create.side_effect = [mock_samples_context, mock_count_context]

        from superset.views.datasource.utils import get_samples

        with pytest.raises(SupersetSecurityException) as exc_info:
            get_samples(
                datasource_type="table",
                datasource_id=1,
                force=False,
                page=1,
                per_page=100,
            )

        assert exc_info.value.error.error_type == (
            SupersetErrorType.DATASOURCE_SECURITY_ACCESS_ERROR
        )
        # Verify raise_for_access was called on the samples context
        mock_samples_context.raise_for_access.assert_called_once()


@patch("superset.views.datasource.utils.get_limit_clause")
def test_get_samples_calls_raise_for_access_on_both_contexts(
    mock_get_limit_clause: MagicMock,
):
    """
    Test that get_samples() calls raise_for_access() on both the samples
    and count_star query contexts before fetching data.
    """
    mock_get_limit_clause.return_value = {"row_offset": 0, "row_limit": 100}

    mock_datasource = MagicMock()
    mock_datasource.type = "table"
    mock_datasource.id = 1
    mock_datasource.columns = []

    mock_samples_context = MagicMock()
    mock_count_context = MagicMock()

    # Set up successful access check
    mock_samples_context.raise_for_access.return_value = None
    mock_count_context.raise_for_access.return_value = None

    # Set up successful payload responses
    mock_count_context.get_payload.return_value = {
        "queries": [{"data": [{"COUNT(*)": 100}], "status": "success"}]
    }
    mock_samples_context.get_payload.return_value = {
        "queries": [
            {
                "data": [{"col1": "val1"}],
                "status": "success",
                "cache_key": "test_key",
            }
        ]
    }

    with (
        patch(
            "superset.views.datasource.utils.DatasourceDAO.get_datasource",
            return_value=mock_datasource,
        ),
        patch(
            "superset.views.datasource.utils.QueryContextFactory"
        ) as mock_factory_class,
    ):
        mock_factory = MagicMock()
        mock_factory_class.return_value = mock_factory

        # Return different mock contexts for samples vs count queries
        mock_factory.create.side_effect = [mock_samples_context, mock_count_context]

        from superset.views.datasource.utils import get_samples

        result = get_samples(
            datasource_type="table",
            datasource_id=1,
            force=False,
            page=1,
            per_page=100,
        )

        # Verify both contexts had raise_for_access called
        mock_samples_context.raise_for_access.assert_called_once()
        mock_count_context.raise_for_access.assert_called_once()

        # Verify the result contains expected data
        assert result["data"] == [{"col1": "val1"}]
        assert result["total_count"] == 100


@patch("superset.views.datasource.utils.get_limit_clause")
def test_get_samples_count_star_access_denied(mock_get_limit_clause: MagicMock):
    """
    Test that get_samples() raises security exception when access to count_star
    query context is denied.
    """
    mock_get_limit_clause.return_value = {"row_offset": 0, "row_limit": 100}

    mock_datasource = MagicMock()
    mock_datasource.type = "table"
    mock_datasource.id = 1
    mock_datasource.columns = []

    mock_samples_context = MagicMock()
    mock_count_context = MagicMock()

    # Samples context allows access
    mock_samples_context.raise_for_access.return_value = None

    # Count context denies access
    mock_count_context.raise_for_access.side_effect = SupersetSecurityException(
        SupersetError(
            message="Access denied to count query",
            error_type=SupersetErrorType.DATASOURCE_SECURITY_ACCESS_ERROR,
            level=ErrorLevel.WARNING,
        )
    )

    with (
        patch(
            "superset.views.datasource.utils.DatasourceDAO.get_datasource",
            return_value=mock_datasource,
        ),
        patch(
            "superset.views.datasource.utils.QueryContextFactory"
        ) as mock_factory_class,
    ):
        mock_factory = MagicMock()
        mock_factory_class.return_value = mock_factory

        mock_factory.create.side_effect = [mock_samples_context, mock_count_context]

        from superset.views.datasource.utils import get_samples

        with pytest.raises(SupersetSecurityException) as exc_info:
            get_samples(
                datasource_type="table",
                datasource_id=1,
                force=False,
                page=1,
                per_page=100,
            )

        assert exc_info.value.error.error_type == (
            SupersetErrorType.DATASOURCE_SECURITY_ACCESS_ERROR
        )
        # Verify samples context was checked first
        mock_samples_context.raise_for_access.assert_called_once()
        # Verify count context was also checked
        mock_count_context.raise_for_access.assert_called_once()


def test_prepare_configurable_detail_payload_adds_trusted_search_and_order():
    """Search is compiled from metadata and ordered before remaining columns."""
    from superset.views.datasource.utils import _prepare_configurable_detail_payload

    columns = [
        DatasourceColumn("event_id", GenericDataType.NUMERIC),
        DatasourceColumn("customer_name", GenericDataType.STRING),
        DatasourceColumn("created_at", GenericDataType.TEMPORAL),
    ]

    payload = _prepare_configurable_detail_payload(
        {"search": {"column": "customer_name", "value": "Acme"}},
        columns,
        "server",
    )

    assert payload["filters"] == [
        {"col": "customer_name", "op": "ILIKE", "val": "Acme%"}
    ]
    assert payload["extras"] == {"__configurable_drill_detail_null_ordering": True}
    assert payload["orderby"] == [
        ("customer_name", True),
        ("event_id", True),
        ("created_at", True),
    ]


def test_prepare_configurable_detail_payload_ignores_blank_search():
    """Whitespace-only search is equivalent to no search."""
    from superset.views.datasource.utils import _prepare_configurable_detail_payload

    payload = _prepare_configurable_detail_payload(
        {"search": {"column": "customer_name", "value": "  \n "}},
        [DatasourceColumn("customer_name", GenericDataType.STRING)],
        "server",
    )

    assert payload["filters"] == []
    assert payload["orderby"] == [("customer_name", True)]


@pytest.mark.parametrize(
    "column",
    [
        DatasourceColumn("calculated", GenericDataType.STRING, expression="x || y"),
        DatasourceColumn("amount", GenericDataType.NUMERIC),
        DatasourceColumn("hidden", GenericDataType.STRING, filterable=False),
    ],
)
def test_prepare_configurable_detail_payload_rejects_untrusted_search_column(
    column: DatasourceColumn,
):
    """Only filterable physical text columns may be used for prefix search."""
    from superset.commands.dataset.exceptions import DatasetSamplesFeatureError
    from superset.views.datasource.utils import _prepare_configurable_detail_payload

    with pytest.raises(DatasetSamplesFeatureError) as exc_info:
        _prepare_configurable_detail_payload(
            {"search": {"column": column.column_name, "value": "x"}},
            [column],
            "server",
        )

    assert exc_info.value.error_code in {
        "DRILL_DETAIL_INVALID_SEARCH_COLUMN",
        "DRILL_DETAIL_STABLE_ORDER_UNAVAILABLE",
    }


def test_prepare_configurable_detail_payload_requires_stable_order():
    """Datasets containing only calculated fields cannot use configurable mode."""
    from superset.commands.dataset.exceptions import DatasetSamplesFeatureError
    from superset.views.datasource.utils import _prepare_configurable_detail_payload

    with pytest.raises(DatasetSamplesFeatureError) as exc_info:
        _prepare_configurable_detail_payload(
            {},
            [
                DatasourceColumn(
                    "calculated", GenericDataType.STRING, expression="x || y"
                )
            ],
            "bounded_client",
        )

    assert exc_info.value.error_code == "DRILL_DETAIL_STABLE_ORDER_UNAVAILABLE"


def test_validate_bounded_client_result_accepts_exact_limits(monkeypatch):
    """Each bounded-client threshold is inclusive at its documented boundary."""
    from superset.views.datasource import utils

    rows = [{"value": "x"} for _ in range(utils.BOUNDED_CLIENT_MAX_ROWS)]
    sample_data = {"data": rows, "colnames": ["value"]}
    payload_size = utils.measure_detail_result(sample_data)["serialized_bytes"]
    monkeypatch.setattr(utils, "BOUNDED_CLIENT_MAX_PAYLOAD_BYTES", payload_size)

    assert utils.validate_bounded_client_result(sample_data) == {
        "row_count": 1000,
        "cell_count": 1000,
        "serialized_bytes": payload_size,
        "largest_cell_bytes": 3,
    }


@pytest.mark.parametrize(
    ("sample_data", "error_code"),
    [
        (
            {"data": [{"value": 1}] * 1001, "colnames": ["value"]},
            "DRILL_DETAIL_ROW_LIMIT_EXCEEDED",
        ),
        (
            {
                "data": [{f"column_{index}": index for index in range(51)}] * 1000,
                "colnames": [f"column_{index}" for index in range(51)],
            },
            "DRILL_DETAIL_CELL_LIMIT_EXCEEDED",
        ),
        (
            {"data": [{"value": "x" * (1024 * 1024)}], "colnames": ["value"]},
            "DRILL_DETAIL_CELL_SIZE_EXCEEDED",
        ),
    ],
)
def test_validate_bounded_client_result_rejects_capacity_overflow(
    sample_data: dict[str, object], error_code: str
):
    """Bounded-client results fail atomically when any capacity limit is exceeded."""
    from superset.commands.dataset.exceptions import DatasetSamplesFeatureError
    from superset.views.datasource.utils import validate_bounded_client_result

    with pytest.raises(DatasetSamplesFeatureError) as exc_info:
        validate_bounded_client_result(sample_data)

    assert exc_info.value.error_code == error_code


def test_validate_bounded_client_result_rejects_payload_overflow(monkeypatch):
    """UTF-8 payload bytes are checked independently from rows and cells."""
    from superset.commands.dataset.exceptions import DatasetSamplesFeatureError
    from superset.views.datasource import utils

    rows = [{"value": "你好"}]
    sample_data = {"data": rows, "colnames": ["value"]}
    monkeypatch.setattr(
        utils,
        "BOUNDED_CLIENT_MAX_PAYLOAD_BYTES",
        utils.measure_detail_result(sample_data)["serialized_bytes"] - 1,
    )

    with pytest.raises(DatasetSamplesFeatureError) as exc_info:
        utils.validate_bounded_client_result(sample_data)

    assert exc_info.value.error_code == "DRILL_DETAIL_PAYLOAD_LIMIT_EXCEEDED"


def test_bounded_client_uses_k_plus_one_without_count_query():
    """Bounded-client mode executes one access-checked K+1 query."""
    mock_datasource = MagicMock(
        type="table",
        id=1,
        columns=[DatasourceColumn("event_id", GenericDataType.NUMERIC)],
    )
    mock_samples_context = MagicMock()
    mock_samples_context.get_payload.return_value = {
        "queries": [
            {
                "data": [{"event_id": 1}],
                "colnames": ["event_id"],
                "status": "success",
            }
        ]
    }

    with (
        patch(
            "superset.views.datasource.utils.DatasourceDAO.get_datasource",
            return_value=mock_datasource,
        ),
        patch(
            "superset.views.datasource.utils.QueryContextFactory"
        ) as mock_factory_class,
    ):
        mock_factory = mock_factory_class.return_value
        mock_factory.create.return_value = mock_samples_context

        from superset.views.datasource.utils import get_samples

        result = get_samples(
            datasource_type="table",
            datasource_id=1,
            page=9,
            per_page=50,
            payload={},
            detail_mode="bounded_client",
        )

    mock_factory.create.assert_called_once()
    query = mock_factory.create.call_args.kwargs["queries"][0]
    assert query["row_offset"] == 0
    assert query["row_limit"] == 1001
    mock_samples_context.raise_for_access.assert_called_once()
    assert result["total_count"] == 1
    assert result["bounds"]["row_count"] == 1
