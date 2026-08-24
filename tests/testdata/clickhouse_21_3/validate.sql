-- Licensed to the Apache Software Foundation (ASF) under one
-- or more contributor license agreements.  See the NOTICE file
-- distributed with this work for additional information
-- regarding copyright ownership.  The ASF licenses this file
-- to you under the Apache License, Version 2.0 (the
-- "License"); you may not use this file except in compliance
-- with the License.  You may obtain a copy of the License at
--
--   http://www.apache.org/licenses/LICENSE-2.0
--
-- Unless required by applicable law or agreed to in writing,
-- software distributed under the License is distributed on an
-- "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY
-- KIND, either express or implied.  See the License for the
-- specific language governing permissions and limitations
-- under the License.

SELECT check_order, check_name, observed, status
FROM
(
  SELECT
    1 AS check_order,
    'server_version' AS check_name,
    version() AS observed,
    if(startsWith(version(), '21.3.'), 'PASS', 'FAIL') AS status
  UNION ALL
  SELECT
    2,
    'physical_table_count',
    toString(count()),
    if(count() >= 7, 'PASS', 'FAIL')
  FROM system.tables
  WHERE database = 'superset_quality_21_3' AND engine != 'View'
  UNION ALL
  SELECT
    3,
    'dim_customer_rows',
    toString(count()),
    if(count() = 10000, 'PASS', 'FAIL')
  FROM superset_quality_21_3.dim_customer
  UNION ALL
  SELECT
    4,
    'dim_product_rows',
    toString(count()),
    if(count() = 10000, 'PASS', 'FAIL')
  FROM superset_quality_21_3.dim_product
  UNION ALL
  SELECT
    5,
    'fact_sales_rows',
    toString(count()),
    if(count() = 20000, 'PASS', 'FAIL')
  FROM superset_quality_21_3.fact_sales
  UNION ALL
  SELECT
    6,
    'fact_events_rows',
    toString(count()),
    if(count() = 12000, 'PASS', 'FAIL')
  FROM superset_quality_21_3.fact_events
  UNION ALL
  SELECT
    7,
    'fact_inventory_rows',
    toString(count()),
    if(count() = 10000, 'PASS', 'FAIL')
  FROM superset_quality_21_3.fact_inventory
  UNION ALL
  SELECT
    8,
    'export_edge_cases_rows',
    toString(count()),
    if(count() = 10000, 'PASS', 'FAIL')
  FROM superset_quality_21_3.export_edge_cases
  UNION ALL
  SELECT
    9,
    'drill_wide_rows',
    toString(count()),
    if(count() = 10000, 'PASS', 'FAIL')
  FROM superset_quality_21_3.drill_wide
  UNION ALL
  SELECT
    10,
    'complex_sql_cases_rows',
    toString(count()),
    if(count() = 10000, 'PASS', 'FAIL')
  FROM superset_quality_21_3.complex_sql_cases
  UNION ALL
  SELECT
    11,
    'customer_key_uniqueness',
    concat(toString(count()), '/', toString(uniqExact(customer_id))),
    if(count() = uniqExact(customer_id), 'PASS', 'FAIL')
  FROM superset_quality_21_3.dim_customer
  UNION ALL
  SELECT
    12,
    'sales_key_uniqueness',
    concat(toString(count()), '/', toString(uniqExact(sale_id))),
    if(count() = uniqExact(sale_id), 'PASS', 'FAIL')
  FROM superset_quality_21_3.fact_sales
  UNION ALL
  SELECT
    13,
    'sales_customer_orphans',
    toString(count()),
    if(count() = 0, 'PASS', 'FAIL')
  FROM superset_quality_21_3.fact_sales AS sales
  LEFT JOIN superset_quality_21_3.dim_customer AS customer
    ON sales.customer_id = customer.customer_id
  WHERE customer.customer_id = 0
  UNION ALL
  SELECT
    14,
    'sales_product_orphans',
    toString(count()),
    if(count() = 0, 'PASS', 'FAIL')
  FROM superset_quality_21_3.fact_sales AS sales
  LEFT JOIN superset_quality_21_3.dim_product AS product
    ON sales.product_id = product.product_id
  WHERE product.product_id = 0
  UNION ALL
  SELECT
    15,
    'customer_email_nulls',
    toString(countIf(isNull(email))),
    if(countIf(isNull(email)) > 0, 'PASS', 'FAIL')
  FROM superset_quality_21_3.dim_customer
  UNION ALL
  SELECT
    16,
    'alert_bucket_coverage',
    toString(uniqExact(alert_bucket)),
    if(uniqExact(alert_bucket) = 3, 'PASS', 'FAIL')
  FROM superset_quality_21_3.fact_sales
  UNION ALL
  SELECT
    17,
    'server_search_prefix_rows',
    toString(countIf(customer_name ILIKE 'acme%')),
    if(countIf(customer_name ILIKE 'acme%') = 2500, 'PASS', 'FAIL')
  FROM superset_quality_21_3.dim_customer
  UNION ALL
  SELECT
    18,
    'wide_view_column_count',
    toString(count()),
    if(count() = 52, 'PASS', 'FAIL')
  FROM system.columns
  WHERE database = 'superset_quality_21_3' AND table = 'drill_wide_flat'
  UNION ALL
  SELECT
    19,
    'payload_total_over_8_mib',
    toString(sum(length(payload_512k))),
    if(sum(length(payload_512k)) > 8 * 1024 * 1024, 'PASS', 'FAIL')
  FROM superset_quality_21_3.export_edge_cases
  WHERE row_id <= 20
  UNION ALL
  SELECT
    20,
    'single_cell_over_1_mib',
    toString(max(length(oversized_cell))),
    if(max(length(oversized_cell)) > 1024 * 1024, 'PASS', 'FAIL')
  FROM superset_quality_21_3.export_edge_cases
  UNION ALL
  SELECT
    21,
    'long_integer_minimum',
    toString(min(long_integer)),
    if(min(long_integer) >= 1000000000000000, 'PASS', 'FAIL')
  FROM superset_quality_21_3.export_edge_cases
  UNION ALL
  SELECT
    22,
    'formula_prefix_coverage',
    toString(uniqExact(substring(formula_like, 1, 1))),
    if(uniqExact(substring(formula_like, 1, 1)) >= 7, 'PASS', 'FAIL')
  FROM superset_quality_21_3.export_edge_cases
  UNION ALL
  SELECT
    23,
    'complex_sql_bytes',
    toString(max(length(full_query))),
    if(max(length(full_query)) >= 12000, 'PASS', 'FAIL')
  FROM superset_quality_21_3.complex_sql_cases
  UNION ALL
  SELECT
    24,
    'complex_sql_line_count',
    toString(max(length(splitByChar('\n', full_query)) - 1)),
    if(max(length(splitByChar('\n', full_query)) - 1) >= 300, 'PASS', 'FAIL')
  FROM superset_quality_21_3.complex_sql_cases
  UNION ALL
  SELECT
    25,
    'inventory_expected_anomalies',
    toString(countIf(expected_anomaly = 1)),
    if(countIf(expected_anomaly = 1) > 0, 'PASS', 'FAIL')
  FROM superset_quality_21_3.fact_inventory
  UNION ALL
  SELECT
    26,
    'date_trunc_lowercase_equivalence',
    concat(
      'rows=', toString(count()),
      ';minute_mismatch=', toString(countIf(
        dateTrunc('minute', event_time) != toStartOfMinute(event_time)
      )),
      ';hour_mismatch=', toString(countIf(
        dateTrunc('hour', event_time) != toStartOfHour(event_time)
      )),
      ';day_mismatch=', toString(countIf(
        dateTrunc('day', event_time) != toStartOfDay(event_time)
      )),
      ';week_mismatch=', toString(countIf(
        dateTrunc('week', event_time) != toMonday(event_time)
      )),
      ';month_mismatch=', toString(countIf(
        dateTrunc('month', event_time) != toStartOfMonth(event_time)
      )),
      ';quarter_mismatch=', toString(countIf(
        dateTrunc('quarter', event_time) != toStartOfQuarter(event_time)
      )),
      ';year_mismatch=', toString(countIf(
        dateTrunc('year', event_time) != toStartOfYear(event_time)
      ))
    ),
    if(
      count() = 12000
      AND countIf(
        dateTrunc('minute', event_time) != toStartOfMinute(event_time)
      ) = 0
      AND countIf(
        dateTrunc('hour', event_time) != toStartOfHour(event_time)
      ) = 0
      AND countIf(
        dateTrunc('day', event_time) != toStartOfDay(event_time)
      ) = 0
      AND countIf(
        dateTrunc('week', event_time) != toMonday(event_time)
      ) = 0
      AND countIf(
        dateTrunc('month', event_time) != toStartOfMonth(event_time)
      ) = 0
      AND countIf(
        dateTrunc('quarter', event_time) != toStartOfQuarter(event_time)
      ) = 0
      AND countIf(
        dateTrunc('year', event_time) != toStartOfYear(event_time)
      ) = 0,
      'PASS',
      'FAIL'
    )
  FROM superset_quality_21_3.fact_events
)
ORDER BY check_order
FORMAT TabSeparatedRaw;
