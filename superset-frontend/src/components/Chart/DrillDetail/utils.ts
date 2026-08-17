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
import { omit } from 'lodash';
import {
  ensureIsArray,
  QueryFormData,
  BinaryQueryObjectFilterClause,
  buildQueryObject,
  DataRecord,
} from '@superset-ui/core';

export const BOUNDED_CLIENT_MAX_ROWS = 1000;
export const BOUNDED_CLIENT_MAX_CELLS = 50_000;
export const BOUNDED_CLIENT_MAX_PAYLOAD_BYTES = 8 * 1024 * 1024;
export const BOUNDED_CLIENT_MAX_CELL_BYTES = 1024 * 1024;

const utf8JsonSize = (value: unknown) =>
  new TextEncoder().encode(JSON.stringify(value) ?? '').byteLength;

export function validateBoundedClientResult(
  rows: DataRecord[],
  columns: string[],
) {
  if (rows.length > BOUNDED_CLIENT_MAX_ROWS) {
    throw new Error('Drill detail exceeds the 1,000 row client limit');
  }
  if (rows.length * columns.length > BOUNDED_CLIENT_MAX_CELLS) {
    throw new Error('Drill detail exceeds the 50,000 cell client limit');
  }
  if (
    rows.some(row =>
      columns.some(
        column => utf8JsonSize(row[column]) > BOUNDED_CLIENT_MAX_CELL_BYTES,
      ),
    )
  ) {
    throw new Error('A drill detail cell exceeds the 1 MiB client limit');
  }
  if (
    utf8JsonSize({ data: rows, colnames: columns }) >
    BOUNDED_CLIENT_MAX_PAYLOAD_BYTES
  ) {
    throw new Error('Drill detail exceeds the 8 MiB client payload limit');
  }
}

export function filterBoundedClientRows(rows: DataRecord[], search: string) {
  if (!search) {
    return rows;
  }
  const normalizedSearch = search.toLowerCase();
  return rows.filter(row =>
    Object.values(row).some(
      value =>
        value != null && String(value).toLowerCase().includes(normalizedSearch),
    ),
  );
}

export function normalizeDrillPageLength(value: unknown, fallback = 50) {
  const numericValue = Number(value);
  return Number.isInteger(numericValue) &&
    numericValue >= 1 &&
    numericValue <= 200
    ? numericValue
    : fallback;
}

export function getDrillPayload(
  queryFormData?: QueryFormData,
  drillFilters?: BinaryQueryObjectFilterClause[],
) {
  if (!queryFormData) {
    return undefined;
  }
  const queryObject = buildQueryObject(queryFormData);
  const extras = omit(queryObject.extras, 'having');
  const filters = [
    ...ensureIsArray(queryObject.filters),
    ...ensureIsArray(drillFilters).map(f => omit(f, 'formattedVal')),
  ];
  return {
    granularity: queryObject.granularity,
    time_range: queryObject.time_range,
    filters,
    extras,
  };
}
