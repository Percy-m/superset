#!/usr/bin/env bash
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

set -euo pipefail

readonly CLICKHOUSE_TEST_CONTAINER="${CLICKHOUSE_TEST_CONTAINER:-superset-clickhouse-21-3}"
readonly CLICKHOUSE_TEST_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
readonly CLICKHOUSE_TEST_SEED="${CLICKHOUSE_TEST_ROOT}/tests/testdata/clickhouse_21_3/seed.sql"
readonly CLICKHOUSE_TEST_VALIDATE="${CLICKHOUSE_TEST_ROOT}/tests/testdata/clickhouse_21_3/validate.sql"

if [[ "$(docker inspect --format '{{.State.Running}}' "${CLICKHOUSE_TEST_CONTAINER}" 2>/dev/null || true)" != "true" ]]; then
  printf 'ClickHouse test container is not running: %s\n' "${CLICKHOUSE_TEST_CONTAINER}" >&2
  exit 1
fi

clickhouse_test_version="$(
  docker exec "${CLICKHOUSE_TEST_CONTAINER}" clickhouse-client --query 'SELECT version()'
)"

if [[ "${clickhouse_test_version}" != 21.3.* ]]; then
  printf 'Expected ClickHouse 21.3.x, found %s in %s\n' \
    "${clickhouse_test_version}" "${CLICKHOUSE_TEST_CONTAINER}" >&2
  exit 1
fi

printf 'Seeding ClickHouse %s in container %s\n' \
  "${clickhouse_test_version}" "${CLICKHOUSE_TEST_CONTAINER}"
docker exec -i "${CLICKHOUSE_TEST_CONTAINER}" clickhouse-client --multiquery \
  < "${CLICKHOUSE_TEST_SEED}"

printf 'Validating deterministic fixtures\n'
docker exec -i "${CLICKHOUSE_TEST_CONTAINER}" clickhouse-client --multiquery \
  < "${CLICKHOUSE_TEST_VALIDATE}"
