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
readonly CLICKHOUSE_TEST_EXPECTED_CHECKS=26

validate_clickhouse_output() {
  local validation_source="${1:--}"

  awk -F '\t' -v expected_checks="${CLICKHOUSE_TEST_EXPECTED_CHECKS}" '
    function reject(message) {
      printf "Invalid ClickHouse validation output at row %d: %s\n", NR, message > "/dev/stderr"
      rejected = 1
    }

    {
      if (NF != 4) {
        reject(sprintf("expected 4 tab-separated columns, found %d", NF))
        next
      }
      if ($1 !~ /^[0-9]+$/ || ($1 + 0) != NR || length($1) != length(NR "")) {
        reject(sprintf("expected check_order %d, found %s", NR, $1))
      }
      if ($2 == "") {
        reject("check_name must not be empty")
      } else if (seen_name[$2]++) {
        reject(sprintf("duplicate check_name %s", $2))
      }
      if ($4 != "PASS") {
        reject(sprintf("expected PASS status, found %s", $4))
      }
    }

    END {
      if (NR != expected_checks) {
        printf "Invalid ClickHouse validation output: expected %d rows, found %d\n", expected_checks, NR > "/dev/stderr"
        rejected = 1
      }
      if (rejected) {
        exit 1
      }
    }
  ' "${validation_source}"
}

check_clickhouse_container() {
  if [[ "$(docker inspect --format '{{.State.Running}}' "${CLICKHOUSE_TEST_CONTAINER}" 2>/dev/null || true)" != "true" ]]; then
    printf 'ClickHouse test container is not running: %s\n' "${CLICKHOUSE_TEST_CONTAINER}" >&2
    return 1
  fi
}

get_clickhouse_version() {
  docker exec "${CLICKHOUSE_TEST_CONTAINER}" clickhouse-client \
    --query 'SELECT version()'
}

validate_clickhouse_version() {
  local clickhouse_test_version="$1"

  if [[ "${clickhouse_test_version}" != 21.3.* ]]; then
    printf 'Expected ClickHouse 21.3.x, found %s in %s\n' \
      "${clickhouse_test_version}" "${CLICKHOUSE_TEST_CONTAINER}" >&2
    return 1
  fi
}

run_clickhouse_validation() (
  local validation_output_file
  validation_output_file="$(
    mktemp "${TMPDIR:-/tmp}/superset-clickhouse-21-3-validation.XXXXXX"
  )"
  trap 'rm -f "${validation_output_file}"' EXIT
  trap 'exit 1' HUP INT TERM

  docker exec -i "${CLICKHOUSE_TEST_CONTAINER}" clickhouse-client \
    --multiquery < "${CLICKHOUSE_TEST_VALIDATE}" > "${validation_output_file}"

  cat "${validation_output_file}"
  validate_clickhouse_output "${validation_output_file}"

  printf 'Validation gate passed: %s/%s checks\n' \
    "${CLICKHOUSE_TEST_EXPECTED_CHECKS}" "${CLICKHOUSE_TEST_EXPECTED_CHECKS}"
)

usage() {
  printf 'Usage: %s [--validate-only | --check-output [FILE|-]]\n' "$0" >&2
}

main() {
  local mode="seed"
  local validation_source="-"
  local clickhouse_test_version

  if [[ $# -gt 0 ]]; then
    case "$1" in
      --validate-only)
        mode="validate"
        shift
        ;;
      --check-output)
        mode="check-output"
        shift
        if [[ $# -gt 0 ]]; then
          validation_source="$1"
          shift
        fi
        ;;
      *)
        usage
        return 2
        ;;
    esac
  fi

  if [[ $# -ne 0 ]]; then
    usage
    return 2
  fi

  if [[ "${mode}" == "check-output" ]]; then
    validate_clickhouse_output "${validation_source}"
    return
  fi

  check_clickhouse_container
  clickhouse_test_version="$(get_clickhouse_version)"
  validate_clickhouse_version "${clickhouse_test_version}"

  if [[ "${mode}" == "seed" ]]; then
    printf 'Seeding ClickHouse %s in container %s\n' \
      "${clickhouse_test_version}" "${CLICKHOUSE_TEST_CONTAINER}"
    docker exec -i "${CLICKHOUSE_TEST_CONTAINER}" clickhouse-client \
      --multiquery < "${CLICKHOUSE_TEST_SEED}"
  fi

  printf 'Validating deterministic fixtures in ClickHouse %s\n' \
    "${clickhouse_test_version}"
  run_clickhouse_validation
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  main "$@"
fi
