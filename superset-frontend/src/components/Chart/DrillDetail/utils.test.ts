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
import {
  filterBoundedClientRows,
  normalizeDrillPageLength,
  validateBoundedClientResult,
} from './utils';

test('normalizeDrillPageLength accepts only integers between one and two hundred', () => {
  expect(normalizeDrillPageLength(1)).toBe(1);
  expect(normalizeDrillPageLength('200')).toBe(200);
  expect(normalizeDrillPageLength(0)).toBe(50);
  expect(normalizeDrillPageLength(201)).toBe(50);
  expect(normalizeDrillPageLength(1.5)).toBe(50);
});

test('filterBoundedClientRows matches every column without case sensitivity', () => {
  const rows = [
    { customer: 'Acme 北京', amount: 42 },
    { customer: 'Beta', amount: 7 },
  ];

  expect(filterBoundedClientRows(rows, 'ACME')).toEqual([rows[0]]);
  expect(filterBoundedClientRows(rows, '42')).toEqual([rows[0]]);
  expect(filterBoundedClientRows(rows, '北京')).toEqual([rows[0]]);
  expect(filterBoundedClientRows(rows, '')).toBe(rows);
  expect(filterBoundedClientRows(rows, 'me 北')).toEqual([rows[0]]);
});

test('validateBoundedClientResult rejects more than one thousand rows', () => {
  expect(() =>
    validateBoundedClientResult(
      Array.from({ length: 1001 }, (_, id) => ({ id })),
      ['id'],
    ),
  ).toThrow('1,000 row');
});

test('validateBoundedClientResult rejects more than fifty thousand cells', () => {
  const columns = Array.from({ length: 51 }, (_, index) => `column_${index}`);
  const row = Object.fromEntries(columns.map(column => [column, column]));

  expect(() =>
    validateBoundedClientResult(Array(1000).fill(row), columns),
  ).toThrow('50,000 cell');
});

test('validateBoundedClientResult rejects a cell larger than one MiB', () => {
  expect(() =>
    validateBoundedClientResult(
      [{ value: 'x'.repeat(1024 * 1024) }],
      ['value'],
    ),
  ).toThrow('1 MiB');
});

test('validateBoundedClientResult measures total payload in UTF-8 bytes', () => {
  const columns = Array.from({ length: 10 }, (_, index) => `column_${index}`);
  const row = Object.fromEntries(
    columns.map(column => [column, '界'.repeat(300_000)]),
  );

  expect(() => validateBoundedClientResult([row], columns)).toThrow('8 MiB');
});
