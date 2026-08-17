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

-- Deterministic ClickHouse 21.3 fixtures for the data-quality BI design.
-- The script only rebuilds the named objects in the dedicated test database.

CREATE DATABASE IF NOT EXISTS superset_quality_21_3;

DROP TABLE IF EXISTS superset_quality_21_3.drill_wide_flat;
DROP TABLE IF EXISTS superset_quality_21_3.complex_sql_cases;
DROP TABLE IF EXISTS superset_quality_21_3.drill_wide;
DROP TABLE IF EXISTS superset_quality_21_3.export_edge_cases;
DROP TABLE IF EXISTS superset_quality_21_3.fact_inventory;
DROP TABLE IF EXISTS superset_quality_21_3.fact_events;
DROP TABLE IF EXISTS superset_quality_21_3.fact_sales;
DROP TABLE IF EXISTS superset_quality_21_3.dim_product;
DROP TABLE IF EXISTS superset_quality_21_3.dim_customer;

CREATE TABLE superset_quality_21_3.dim_customer
(
  customer_id UInt32,
  customer_code String,
  customer_name String,
  segment LowCardinality(String),
  country_code FixedString(2),
  email Nullable(String),
  signup_date Date,
  is_active UInt8,
  note Nullable(String)
)
ENGINE = MergeTree()
ORDER BY customer_id;

INSERT INTO superset_quality_21_3.dim_customer
SELECT
  toUInt32(number + 1) AS customer_id,
  concat('CUST-', toString(number + 1)) AS customer_code,
  multiIf(
    number % 4 = 0, concat('Acme ', toString(number + 1)),
    number % 4 = 1, concat('北京客户 ', toString(number + 1)),
    number % 4 = 2, concat('München ', toString(number + 1)),
    concat('🙂 Customer ', toString(number + 1))
  ) AS customer_name,
  arrayElement(
    ['enterprise', 'mid_market', 'small_business', 'consumer'],
    toUInt32(number % 4 + 1)
  ) AS segment,
  arrayElement(['CN', 'US', 'DE', 'BR', 'JP'], toUInt32(number % 5 + 1)) AS country_code,
  if(
    number % 17 = 0,
    CAST(NULL AS Nullable(String)),
    concat('customer', toString(number + 1), '@example.test')
  ) AS email,
  toDate('2024-01-01') + toInt32(number % 730) AS signup_date,
  toUInt8(number % 13 != 0) AS is_active,
  if(
    number % 29 = 0,
    CAST(NULL AS Nullable(String)),
    concat('客户备注-', toString(number % 100))
  ) AS note
FROM numbers(10000);

CREATE TABLE superset_quality_21_3.dim_product
(
  product_id UInt32,
  sku String,
  product_name String,
  category LowCardinality(String),
  unit_price Decimal(12, 2),
  launched_on Date,
  discontinued_at Nullable(DateTime),
  is_fragile UInt8
)
ENGINE = MergeTree()
ORDER BY product_id;

INSERT INTO superset_quality_21_3.dim_product
SELECT
  toUInt32(number + 1) AS product_id,
  concat('SKU-', toString(number + 1)) AS sku,
  multiIf(
    number % 3 = 0, concat('标准产品 ', toString(number + 1)),
    number % 3 = 1, concat('Premium Gerät ', toString(number + 1)),
    concat('Export 📦 ', toString(number + 1))
  ) AS product_name,
  arrayElement(
    ['hardware', 'software', 'service', 'accessory', 'subscription'],
    toUInt32(number % 5 + 1)
  ) AS category,
  toDecimal64(10 + number % 990, 2) AS unit_price,
  toDate('2023-01-01') + toInt32(number % 1000) AS launched_on,
  if(
    number % 101 = 0,
    toNullable(toDateTime('2025-01-01 00:00:00') + toInt32(number)),
    CAST(NULL AS Nullable(DateTime))
  ) AS discontinued_at,
  toUInt8(number % 11 = 0) AS is_fragile
FROM numbers(10000);

CREATE TABLE superset_quality_21_3.fact_sales
(
  sale_id UInt64,
  sale_date Date,
  event_time DateTime,
  customer_id UInt32,
  product_id UInt32,
  region LowCardinality(String),
  channel LowCardinality(String),
  quantity Int32,
  revenue Decimal(18, 2),
  cost Decimal(18, 2),
  margin_ratio Float64,
  quality_score Nullable(Float64),
  alert_bucket LowCardinality(String),
  expected_anomaly UInt8
)
ENGINE = MergeTree()
PARTITION BY toYYYYMM(sale_date)
ORDER BY (sale_date, region, sale_id);

INSERT INTO superset_quality_21_3.fact_sales
SELECT
  sale_id,
  sale_date,
  event_time,
  customer_id,
  product_id,
  region,
  channel,
  quantity,
  revenue,
  cost,
  round((toFloat64(revenue) - toFloat64(cost)) / toFloat64(revenue), 4) AS margin_ratio,
  if(
    sale_id % 23 = 0,
    CAST(NULL AS Nullable(Float64)),
    toNullable(round(toFloat64(sale_id % 101) / 100, 2))
  ) AS quality_score,
  multiIf(revenue < 0, 'RED', revenue < 100, 'YELLOW', 'GREEN') AS alert_bucket,
  toUInt8(revenue < 0 OR quantity < 0) AS expected_anomaly
FROM
(
  SELECT
    number + 1 AS sale_id,
    toDate('2025-01-01') + toInt32(number % 365) AS sale_date,
    toDateTime('2025-01-01 00:00:00') + toInt32(number * 60) AS event_time,
    toUInt32(number % 10000 + 1) AS customer_id,
    toUInt32(number * 7 % 10000 + 1) AS product_id,
    arrayElement(['APAC', 'AMER', 'EMEA', 'LATAM'], toUInt32(number % 4 + 1)) AS region,
    arrayElement(['direct', 'partner', 'online'], toUInt32(number % 3 + 1)) AS channel,
    if(number % 211 = 0, toInt32(-1), toInt32(number % 8 + 1)) AS quantity,
    multiIf(
      number % 97 = 0, toDecimal64(-100, 2),
      number % 10 < 3, toDecimal64(50 + number % 50, 2),
      toDecimal64(500 + number % 500, 2)
    ) AS revenue,
    toDecimal64(40 + number % 260, 2) AS cost
  FROM numbers(20000)
);

CREATE TABLE superset_quality_21_3.fact_events
(
  event_id UInt64,
  session_id UInt64,
  user_id UInt32,
  event_time DateTime,
  event_name LowCardinality(String),
  search_text String,
  payload String,
  detail_note Nullable(String),
  duplicate_sort_key UInt32
)
ENGINE = MergeTree()
PARTITION BY toYYYYMM(event_time)
ORDER BY (event_time, event_name, duplicate_sort_key, event_id);

INSERT INTO superset_quality_21_3.fact_events
SELECT
  number + 1 AS event_id,
  toUInt64(intDiv(number, 4) + 1) AS session_id,
  toUInt32(number % 10000 + 1) AS user_id,
  toDateTime('2025-03-01 00:00:00') + toInt32(number * 30) AS event_time,
  arrayElement(
    ['page_view', 'search', 'filter_change', 'cross_filter', 'export'],
    toUInt32(number % 5 + 1)
  ) AS event_name,
  multiIf(
    number % 4 = 0, concat('Acme order ', toString(number)),
    number % 4 = 1, concat('北京订单 ', toString(number)),
    number % 4 = 2, concat('München Auftrag ', toString(number)),
    concat('🙂 export ', toString(number))
  ) AS search_text,
  concat('{"event_id":', toString(number + 1), ',"source":"fixture"}') AS payload,
  if(
    number % 19 = 0,
    CAST(NULL AS Nullable(String)),
    concat('detail-', toString(number % 250))
  ) AS detail_note,
  toUInt32(number % 100) AS duplicate_sort_key
FROM numbers(12000);

CREATE TABLE superset_quality_21_3.fact_inventory
(
  snapshot_date Date,
  warehouse_id UInt16,
  product_id UInt32,
  on_hand Int32,
  reserved Int32,
  reorder_point UInt32,
  status LowCardinality(String),
  long_serial UInt64,
  expected_anomaly UInt8
)
ENGINE = MergeTree()
PARTITION BY toYYYYMM(snapshot_date)
ORDER BY (snapshot_date, warehouse_id, product_id);

INSERT INTO superset_quality_21_3.fact_inventory
SELECT
  toDate('2025-06-01') + toInt32(number % 90) AS snapshot_date,
  toUInt16(number % 20 + 1) AS warehouse_id,
  toUInt32(number % 10000 + 1) AS product_id,
  if(number % 211 = 0, toInt32(-5), toInt32(number % 500)) AS on_hand,
  if(number % 157 = 0, toInt32(600), toInt32(number % 100)) AS reserved,
  toUInt32(25 + number % 75) AS reorder_point,
  multiIf(number % 211 = 0, 'invalid', number % 10 < 2, 'low', 'normal') AS status,
  toUInt64(1000000000000000) + number AS long_serial,
  toUInt8(number % 211 = 0 OR number % 157 = 0) AS expected_anomaly
FROM numbers(10000);

CREATE TABLE superset_quality_21_3.export_edge_cases
(
  row_id UInt64,
  formula_like String,
  long_integer UInt64,
  unicode_text String,
  nullable_text Nullable(String),
  control_text String,
  payload_512k String,
  oversized_cell String
)
ENGINE = MergeTree()
ORDER BY row_id;

INSERT INTO superset_quality_21_3.export_edge_cases
SELECT
  number + 1 AS row_id,
  arrayElement(
    ['=1+1', '+SUM(A1:A2)', '-42', '@cmd', '\tformula', '\rformula', 'safe'],
    toUInt32(number % 7 + 1)
  ) AS formula_like,
  toUInt64(1000000000000000) + number AS long_integer,
  multiIf(
    number % 3 = 0, concat('中文-', toString(number)),
    number % 3 = 1, concat('emoji-🙂-', toString(number)),
    concat('München-', toString(number))
  ) AS unicode_text,
  if(
    number % 31 = 0,
    CAST(NULL AS Nullable(String)),
    concat('nullable-', toString(number))
  ) AS nullable_text,
  if(number % 10 = 0, concat('control', char(1), 'value'), 'safe') AS control_text,
  concat('payload-', toString(number)) AS payload_512k,
  'ok' AS oversized_cell
FROM numbers(10000)
WHERE number >= 20;

-- Keep large expressions in a 20-row block. ClickHouse 21.3 evaluates both
-- branches of vectorized conditionals, so placing them in the 10k-row block
-- would allocate the large constant once per row before filtering.
INSERT INTO superset_quality_21_3.export_edge_cases
SELECT
  number + 1 AS row_id,
  arrayElement(
    ['=1+1', '+SUM(A1:A2)', '-42', '@cmd', '\tformula', '\rformula', 'safe'],
    toUInt32(number % 7 + 1)
  ) AS formula_like,
  toUInt64(1000000000000000) + number AS long_integer,
  multiIf(
    number % 3 = 0, concat('中文-', toString(number)),
    number % 3 = 1, concat('emoji-🙂-', toString(number)),
    concat('München-', toString(number))
  ) AS unicode_text,
  if(
    number % 31 = 0,
    CAST(NULL AS Nullable(String)),
    concat('nullable-', toString(number))
  ) AS nullable_text,
  if(number % 10 = 0, concat('control', char(1), 'value'), 'safe') AS control_text,
  repeat('数🙂', 65536) AS payload_512k,
  if(number = 0, repeat('大', 400000), 'ok') AS oversized_cell
FROM numbers(20);

CREATE TABLE superset_quality_21_3.drill_wide
(
  row_id UInt64,
  search_key String,
  metrics Array(Int32)
)
ENGINE = MergeTree()
ORDER BY (search_key, row_id);

INSERT INTO superset_quality_21_3.drill_wide
SELECT
  number + 1 AS row_id,
  concat(arrayElement(['alpha', 'beta', 'gamma', 'delta'], toUInt32(number % 4 + 1)), '-', toString(number)) AS search_key,
  arrayMap(x -> toInt32(number % 100 + x), range(50)) AS metrics
FROM numbers(10000);

CREATE VIEW superset_quality_21_3.drill_wide_flat AS
SELECT
  row_id,
  search_key,
  metrics[1] AS metric_01,
  metrics[2] AS metric_02,
  metrics[3] AS metric_03,
  metrics[4] AS metric_04,
  metrics[5] AS metric_05,
  metrics[6] AS metric_06,
  metrics[7] AS metric_07,
  metrics[8] AS metric_08,
  metrics[9] AS metric_09,
  metrics[10] AS metric_10,
  metrics[11] AS metric_11,
  metrics[12] AS metric_12,
  metrics[13] AS metric_13,
  metrics[14] AS metric_14,
  metrics[15] AS metric_15,
  metrics[16] AS metric_16,
  metrics[17] AS metric_17,
  metrics[18] AS metric_18,
  metrics[19] AS metric_19,
  metrics[20] AS metric_20,
  metrics[21] AS metric_21,
  metrics[22] AS metric_22,
  metrics[23] AS metric_23,
  metrics[24] AS metric_24,
  metrics[25] AS metric_25,
  metrics[26] AS metric_26,
  metrics[27] AS metric_27,
  metrics[28] AS metric_28,
  metrics[29] AS metric_29,
  metrics[30] AS metric_30,
  metrics[31] AS metric_31,
  metrics[32] AS metric_32,
  metrics[33] AS metric_33,
  metrics[34] AS metric_34,
  metrics[35] AS metric_35,
  metrics[36] AS metric_36,
  metrics[37] AS metric_37,
  metrics[38] AS metric_38,
  metrics[39] AS metric_39,
  metrics[40] AS metric_40,
  metrics[41] AS metric_41,
  metrics[42] AS metric_42,
  metrics[43] AS metric_43,
  metrics[44] AS metric_44,
  metrics[45] AS metric_45,
  metrics[46] AS metric_46,
  metrics[47] AS metric_47,
  metrics[48] AS metric_48,
  metrics[49] AS metric_49,
  metrics[50] AS metric_50
FROM superset_quality_21_3.drill_wide;

CREATE TABLE superset_quality_21_3.complex_sql_cases
(
  case_id UInt64,
  query_group UInt32,
  line_no UInt16,
  sql_fragment String,
  full_query String
)
ENGINE = MergeTree()
ORDER BY (query_group, line_no, case_id);

INSERT INTO superset_quality_21_3.complex_sql_cases
SELECT
  number + 1 AS case_id,
  toUInt32(intDiv(number, 300) + 1) AS query_group,
  toUInt16(number % 300 + 1) AS line_no,
  concat(
    'SELECT ',
    toString(number),
    ' AS line_',
    toString(number),
    ', \'测试🙂\' AS unicode_literal -- fixture line ',
    toString(number)
  ) AS sql_fragment,
  if(
    number = 0,
    repeat('SELECT \'测试🙂\' AS value, 1234567890 AS id;\n', 300),
    ''
  ) AS full_query
FROM numbers(10000);
