/**
 * Licensed to the Apache Software Foundation (ASF) under one
 * or more contributor license agreements.  See the NOTICE file
 * distributed with this work for additional information
 * regarding copyright ownership.  The ASF licenses this file
 * to you under the Apache License, Version 2.0 (the
 * "License"); you may not use this file except in compliance
 * with the License.  You may obtain a copy of the License at
 *
 *   http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing,
 * software distributed under the License is distributed on an
 * "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY
 * KIND, either express or implied.  See the License for the
 * specific language governing permissions and limitations
 * under the License.
 */
import xlsxFilename from './xlsxFilename';

test.each([
  ['FR-01 ClickHouse Drill Detail', 'FR-01 ClickHouse Drill Detail'],
  ['数据质量 😀', '数据质量 😀'],
  ['profit/region: "A"\r\n', 'profit_region_ _A___'],
  [undefined, 'chart'],
  [' ... ', 'chart'],
])(
  'names XLSX using chart title %s and a zero-padded timestamp',
  (title, expected) => {
    expect(xlsxFilename(title, new Date(2026, 7, 2, 3, 4, 5))).toBe(
      `${expected}_20260802030405.xlsx`,
    );
  },
);
