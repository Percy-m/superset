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
from unittest.mock import MagicMock, patch

from superset.common.query_actions import _get_drill_detail


def build_drill_query(orderby: list[tuple[str, bool]], configurable: bool) -> MagicMock:
    """Build the minimal query object needed by the drill-detail action."""
    column = MagicMock()
    column.column_name = "first_column"
    datasource = MagicMock()
    datasource.columns = [column]

    query_object = MagicMock()
    query_object.datasource = datasource
    query_object.orderby = orderby
    query_object.extras = (
        {"__configurable_drill_detail_null_ordering": True} if configurable else {}
    )
    return query_object


def test_legacy_drill_detail_keeps_first_column_ordering() -> None:
    """Legacy callers ignore a supplied order and retain their existing behavior."""
    query_object = build_drill_query([("other_column", False)], configurable=False)

    with patch("superset.common.query_actions._get_full", return_value={}) as get_full:
        _get_drill_detail(MagicMock(), query_object)

    executed_query = get_full.call_args.args[1]
    assert executed_query.orderby == [("first_column", True)]


def test_configurable_drill_detail_preserves_stable_ordering() -> None:
    """FR-01 retains the trusted deterministic order assembled by the endpoint."""
    requested_order = [("search_column", True), ("other_column", True)]
    query_object = build_drill_query(requested_order, configurable=True)

    with patch("superset.common.query_actions._get_full", return_value={}) as get_full:
        _get_drill_detail(MagicMock(), query_object)

    executed_query = get_full.call_args.args[1]
    assert executed_query.orderby == requested_order
