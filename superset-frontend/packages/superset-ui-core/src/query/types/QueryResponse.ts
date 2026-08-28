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
import { GenericDataType } from '@apache-superset/core/common';
import { TimeseriesDataRecord } from '../../chart';
import { AnnotationData } from './AnnotationLayer';

/**
 * Primitive types for data field values.
 */
export type DataRecordValue = number | string | boolean | Date | null | bigint;

export interface DataRecord {
  [key: string]: DataRecordValue;
}

/** Colors derived from the final visible conditional formatting, not rule levels. */
export type TablePaintColor = 'GREEN' | 'YELLOW' | 'RED';

export interface TableCellPaint {
  backgroundColor?: string;
  /** Explicit formatter text color; readable contrast is resolved by the renderer. */
  textColor?: string;
  cellBar?: {
    color: string;
    width: number;
    offset: number;
    min: number;
    max: number;
  };
  arrow?: { color: string; symbol: string };
  colors: TablePaintColor[];
}

export interface ColumnFilterCapability {
  enabled: boolean;
  supported: boolean;
  reason?: { code: string; message: string };
}

export interface TableColorSelection {
  column: string;
  colors: TablePaintColor[];
}

/** Runtime state; snapshot references must not be persisted in a permalink. */
export interface TableColorFilterState {
  version: 2;
  selections: TableColorSelection[];
  snapshotId?: string;
  generation?: string;
}

export type TableColorMetadata =
  | {
      status: 'ready';
      snapshot_id: string;
      generation: string;
      baseline_rowcount: number;
      filtered_rowcount: number;
      source_page_size: number;
      row_offset?: number;
      /** Global snapshot indices aligned with this response's data/styles. */
      row_indices?: number[];
      theme_mode?: 'default' | 'dark';
      catalog: Record<string, TablePaintColor[]>;
      capabilities: Record<string, ColumnFilterCapability>;
      styles: Record<string, TableCellPaint>[];
      expires_in: number;
      selections?: TableColorSelection[];
      totals?: DataRecord;
      /** Client-only notice when retaining the last successful response. */
      request_error?: string;
    }
  | {
      status: 'unavailable';
      capabilities: Record<string, ColumnFilterCapability>;
      reason: { code: string; message: string };
    };

/**
 * Queried data for charts. The `queries` field from `POST /chart/data`.
 * See superset/charts/schemas.py for the class of the same name.
 */
export interface ChartDataResponseResult {
  /**
   * Data for the annotation layer.
   */
  annotation_data: AnnotationData | null;
  cache_key: string | null;
  cache_timeout: number | null;
  cached_dttm: string | null;
  /**
   * UTC timestamp when the query was executed (ISO 8601 format).
   * For cached queries, this is when the original query ran.
   */
  queried_dttm: string | null;
  /**
   * Array of data records as dictionary
   */
  data: DataRecord[];
  /**
   * Name of each column, for retaining the order of the output columns.
   */
  colnames: string[];
  /**
   * Generic data types, based on the final output pandas dataframe.
   */
  coltypes: GenericDataType[];
  error: string | null;
  is_cached: boolean;
  query: string;
  rowcount: number;
  sql_rowcount: number;
  stacktrace: string | null;
  status:
    | 'stopped'
    | 'failed'
    | 'pending'
    | 'running'
    | 'scheduled'
    | 'success'
    | 'timed_out';
  from_dttm: number | null;
  to_dttm: number | null;
  // TODO(hainenber): define proper type for below attributes
  rejected_filters?: any[];
  applied_filters?: any[];
  /**
   * Detected ISO 4217 currency code when AUTO mode is used.
   * Returns the currency code if all filtered data contains a single currency,
   * or null if multiple currencies are present.
   */
  detected_currency?: string | null;
  /** Immutable Table coloring context shared by filtering, pagination and export. */
  table_color_metadata?: TableColorMetadata;
}

export interface TimeseriesChartDataResponseResult extends ChartDataResponseResult {
  data: TimeseriesDataRecord[];
  label_map: Record<string, string[]>;
}

/**
 * Query response from /api/v1/chart/data
 */
export interface ChartDataResponse {
  queries: ChartDataResponseResult[];
}

export default {};
