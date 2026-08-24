<!--
Licensed to the Apache Software Foundation (ASF) under one
or more contributor license agreements.  See the NOTICE file
distributed with this work for additional information
regarding copyright ownership.  The ASF licenses this file
to you under the Apache License, Version 2.0 (the
"License"); you may not use this file except in compliance
with the License.  You may obtain a copy of the License at

  http://www.apache.org/licenses/LICENSE-2.0

Unless required by applicable law or agreed to in writing,
software distributed under the License is distributed on an
"AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY
KIND, either express or implied.  See the License for the
specific language governing permissions and limitations
under the License.
-->

# ClickHouse 21.3 data-quality BI fixtures

This directory contains deterministic test data for
`docs/designs/superset-data-quality-bi-detailed-design.md`. The fixtures target
ClickHouse `21.3.x` and use a dedicated `superset_quality_21_3` database.

## Data inventory

| Object              |   Rows | Grain                          | Primary scenarios                                              |
| ------------------- | -----: | ------------------------------ | -------------------------------------------------------------- |
| `dim_customer`      | 10,000 | One customer                   | Prefix/Unicode search, nullable fields, stable dimensions      |
| `dim_product`       | 10,000 | One product                    | Decimal, Date/DateTime, dimension joins                        |
| `fact_sales`        | 20,000 | One sale                       | Aggregate Table, WHERE/HAVING alerts, totals, RLS/filter scope |
| `fact_events`       | 12,000 | One event                      | Server pagination, repeated sort keys, cross filters, tabs     |
| `fact_inventory`    | 10,000 | Product/warehouse/day snapshot | Negative/outlier alerts, UInt64 Excel export                   |
| `export_edge_cases` | 10,000 | One export row                 | Formula prefixes, Unicode, control chars, 8 MiB/1 MiB limits   |
| `drill_wide`        | 10,000 | One wide-detail source row     | Array-backed wide result and 50,000-cell boundary              |
| `drill_wide_flat`   |   View | One projected detail row       | 52-column Drill Detail result                                  |
| `complex_sql_cases` | 10,000 | One SQL fixture line/case      | 300-line and 12,000-byte SQL transport                         |

The negative values, nullable cells, control characters, formula-like strings,
and oversized payloads are intentional fixtures, not accidental quality defects.

## Validated baseline

The initial local validation on 2026-08-14 used ClickHouse `21.3.20.1` and
produced eight physical tables, one view, and 92,000 physical rows. All 25
checks available at that time passed. The validation gate was extended and
rerun without reseeding on 2026-08-24; all 26 checks passed, including the
seven-unit lowercase `dateTrunc` equivalence check. Selected profile evidence:

| Check                                 |                               Observed |
| ------------------------------------- | -------------------------------------: |
| Customer/sales key uniqueness         |                                   100% |
| Sales-to-customer/product orphan rows |                                  0 / 0 |
| Customer email/note null rate         |                          5.89% / 3.45% |
| Sales alert distribution              | GREEN 69.28%, YELLOW 29.68%, RED 1.03% |
| Inventory expected anomaly rate       |                                  1.11% |
| English `ILIKE 'acme%'` matches       |                                  2,500 |
| Wide view columns                     |                                     52 |
| First 20 payload bytes                |                              9,175,040 |
| Largest cell                          |                        1,200,000 bytes |
| Long SQL fixture                      |               300 lines / 14,400 bytes |
| Lowercase `dateTrunc` mismatches      |  minute through year: 0 for every unit |

## Build and validate

The helper checks the server version, rebuilds only the named fixture objects,
and runs all validation queries. It accepts the result only when it contains
exactly 26 `TabSeparatedRaw` rows, ordered from 1 through 26, with four columns,
unique nonempty check names, and a `PASS` status for every row:

```bash
scripts/tests/seed_clickhouse_21_3.sh
```

Override the existing local container name if necessary:

```bash
CLICKHOUSE_TEST_CONTAINER=my-clickhouse-21-3 \
  scripts/tests/seed_clickhouse_21_3.sh
```

Validate existing fixtures without running `seed.sql`, `DROP`, `CREATE`, or
`INSERT` statements:

```bash
scripts/tests/seed_clickhouse_21_3.sh --validate-only
```

The strict output parser can also check captured output from a file or stdin.
These modes do not connect to Docker:

```bash
scripts/tests/seed_clickhouse_21_3.sh --check-output validation.tsv
scripts/tests/seed_clickhouse_21_3.sh --check-output - < validation.tsv
```

Equivalent direct commands:

```bash
docker exec -i superset-clickhouse-21-3 clickhouse-client --multiquery \
  < tests/testdata/clickhouse_21_3/seed.sql
docker exec -i superset-clickhouse-21-3 clickhouse-client --multiquery \
  < tests/testdata/clickhouse_21_3/validate.sql
```

The direct validation command does not apply the helper's 26-row protocol
checks, so use `--validate-only` for the executable test gate.

The seed is idempotent by replacement: it drops and recreates only the listed
tables/view inside `superset_quality_21_3`. Do not put manual data in this test
database; rerunning the script intentionally removes it.

## Superset connection

For the recommended ClickHouse Connect driver, use the local HTTP endpoint:

```text
clickhousedb+connect://default:@127.0.0.1:8123/superset_quality_21_3
```

Local proxy settings must bypass `127.0.0.1` and `localhost`; otherwise the
driver can return an HTTP 502 even when the container is healthy.
