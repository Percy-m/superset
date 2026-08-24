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

import os
import re
import subprocess
from pathlib import Path

import pytest

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
SEED_SCRIPT = REPOSITORY_ROOT / "scripts/tests/seed_clickhouse_21_3.sh"
BASH = "/bin/bash"

FAKE_DOCKER = """#!/usr/bin/env bash
set -euo pipefail

mutating_sql_pattern='^[[:space:]]*(ALTER|ATTACH|CREATE|DELETE|DETACH|DROP|'
mutating_sql_pattern+='INSERT|OPTIMIZE|RENAME|REPLACE|SYSTEM|TRUNCATE|UPDATE|USE)'
mutating_sql_pattern+='([[:space:]]|$)'

if [[ "$1" == "inspect" ]]; then
  printf 'true\\n'
  exit 0
fi

if [[ "$1" == "exec" && "$4" == "--query" ]]; then
  printf '%s\\n' "${FAKE_CLICKHOUSE_VERSION:-21.3.20.1}"
  exit 0
fi

if [[ "$1" == "exec" && "$2" == "-i" && "$5" == "--multiquery" ]]; then
  validation_sql="$(cat)"
  printf '%s\\n' "${validation_sql}" > "${FAKE_DOCKER_STDIN_LOG}"
  if [[ "${FORBID_MUTATING_SQL:-0}" == "1" ]] &&
    printf '%s\\n' "${validation_sql}" |
      grep -Eiq "${mutating_sql_pattern}"; then
    exit 97
  fi
  cat "${FAKE_DOCKER_VALIDATION_OUTPUT}"
  exit 0
fi

printf 'Unexpected fake docker invocation: %s\\n' "$*" >&2
exit 98
"""


def validation_output(case: str = "valid") -> str:
    """Build four-column validation output, optionally violating one rule."""
    row_count = 25 if case == "25_rows" else 27 if case == "27_rows" else 26
    rows = [
        [str(check_order), f"check_{check_order}", "observed", "PASS"]
        for check_order in range(1, row_count + 1)
    ]

    if case == "fail":
        rows[12][3] = "FAIL"
    elif case == "out_of_order":
        rows[11], rows[12] = rows[12], rows[11]
    elif case == "duplicate_order":
        rows[12][0] = rows[11][0]
    elif case == "duplicate_name":
        rows[12][1] = rows[11][1]
    elif case == "empty_name":
        rows[12][1] = ""
    elif case == "three_columns":
        rows[12] = rows[12][:3]
    elif case == "five_columns":
        rows[12].append("unexpected")

    return "\n".join("\t".join(row) for row in rows) + "\n"


def run_output_parser(
    tmp_path: Path,
    output: str,
    source: str,
) -> subprocess.CompletedProcess[str]:
    """Run the public parser path with validation data from a file or stdin."""
    command = [BASH, str(SEED_SCRIPT), "--check-output"]
    stdin = None
    if source == "file":
        validation_file = tmp_path / "validation.tsv"
        validation_file.write_text(output, encoding="utf-8")
        command.append(str(validation_file))
    else:
        command.append("-")
        stdin = output

    return subprocess.run(  # noqa: S603
        command,
        input=stdin,
        check=False,
        capture_output=True,
        text=True,
    )


def run_validate_only(
    tmp_path: Path,
    output: str,
) -> tuple[subprocess.CompletedProcess[str], Path]:
    """Run validate-only against a deterministic fake Docker command."""
    fake_docker = tmp_path / "docker"
    fake_docker.write_text(FAKE_DOCKER, encoding="utf-8")
    fake_docker.chmod(0o755)

    output_file = tmp_path / "validation.tsv"
    output_file.write_text(output, encoding="utf-8")
    stdin_log = tmp_path / "docker-stdin.sql"
    environment = os.environ.copy()
    environment.update(
        {
            "FAKE_DOCKER_STDIN_LOG": str(stdin_log),
            "FAKE_DOCKER_VALIDATION_OUTPUT": str(output_file),
            "FORBID_MUTATING_SQL": "1",
            "PATH": f"{tmp_path}{os.pathsep}{environment['PATH']}",
        }
    )

    result = subprocess.run(  # noqa: S603
        [BASH, str(SEED_SCRIPT), "--validate-only"],
        check=False,
        capture_output=True,
        env=environment,
        text=True,
    )
    return result, stdin_log


@pytest.mark.parametrize("source", ["file", "stdin"])
def test_validation_parser_accepts_exact_protocol(tmp_path: Path, source: str) -> None:
    result = run_output_parser(tmp_path, validation_output(), source)

    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize(
    "case",
    [
        "25_rows",
        "27_rows",
        "fail",
        "out_of_order",
        "duplicate_order",
        "duplicate_name",
        "empty_name",
        "three_columns",
        "five_columns",
    ],
)
def test_validate_only_rejects_invalid_protocol(tmp_path: Path, case: str) -> None:
    result, _ = run_validate_only(tmp_path, validation_output(case))

    assert result.returncode != 0
    assert "Invalid ClickHouse validation output" in result.stderr


def test_validate_only_never_executes_seed_sql(tmp_path: Path) -> None:
    result, stdin_log = run_validate_only(tmp_path, validation_output())

    assert result.returncode == 0, result.stderr
    assert "Validation gate passed: 26/26 checks" in result.stdout
    submitted_sql = stdin_log.read_text(encoding="utf-8")
    assert "date_trunc_lowercase_equivalence" in submitted_sql
    mutating_statement = re.compile(
        r"^\s*(ALTER|ATTACH|CREATE|DELETE|DETACH|DROP|INSERT|OPTIMIZE|RENAME|"
        r"REPLACE|SYSTEM|TRUNCATE|UPDATE|USE)(\s|$)",
        re.IGNORECASE | re.MULTILINE,
    )
    assert mutating_statement.search(submitted_sql) is None
