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
  ChartDataResponseResult,
  FeatureFlag,
  TableColorMetadata,
  VizType,
} from '@superset-ui/core';
import { render, screen } from 'spec/helpers/testing-library';
import { ChartPills, ChartPillsProps } from './ChartPills';

const createResponse = (
  overrides: Partial<ChartDataResponseResult> = {},
): ChartDataResponseResult => ({
  annotation_data: null,
  cache_key: null,
  cache_timeout: null,
  cached_dttm: null,
  queried_dttm: null,
  data: [],
  colnames: [],
  coltypes: [],
  error: null,
  is_cached: false,
  query: '',
  rowcount: 24,
  sql_rowcount: 97,
  stacktrace: null,
  status: 'success',
  from_dttm: null,
  to_dttm: null,
  ...overrides,
});

const createMetadata = (filteredRowCount: number): TableColorMetadata => ({
  status: 'ready',
  snapshot_id: 'snapshot',
  generation: 'generation',
  baseline_rowcount: 97,
  filtered_rowcount: filteredRowCount,
  source_page_size: 25,
  catalog: {},
  capabilities: {},
  styles: [],
  expires_in: 300,
});

const renderPills = (overrides: Partial<ChartPillsProps> = {}) =>
  render(
    <ChartPills
      chartStatus="success"
      chartUpdateStartTime={0}
      chartUpdateEndTime={100}
      refreshCachedQuery={jest.fn()}
      rowLimit={1000}
      formData={{ viz_type: VizType.Table }}
      queriesResponse={[createResponse()]}
      {...overrides}
    />,
  );

let previousFeatureFlags: typeof window.featureFlags;

beforeEach(() => {
  previousFeatureFlags = window.featureFlags;
  window.featureFlags = {
    ...previousFeatureFlags,
    [FeatureFlag.TableAlertFilters]: true,
  };
});

afterEach(() => {
  window.featureFlags = previousFeatureFlags;
});

test.each([
  { serverPagination: false, filteredRowCount: 0 },
  { serverPagination: false, filteredRowCount: 24 },
  { serverPagination: true, filteredRowCount: 0 },
  { serverPagination: true, filteredRowCount: 24 },
])(
  'ready color Table shows $filteredRowCount rows with server pagination $serverPagination',
  ({ serverPagination, filteredRowCount }) => {
    const response = createResponse({
      rowcount: serverPagination
        ? Math.min(filteredRowCount, 10)
        : filteredRowCount,
      table_color_metadata: createMetadata(filteredRowCount),
    });
    renderPills({
      formData: {
        viz_type: VizType.Table,
        server_pagination: serverPagination,
      },
      queriesResponse: serverPagination
        ? [response, createResponse({ data: [{ rowcount: 97 }] })]
        : [response],
    });

    expect(screen.getByTestId('row-count-label')).toHaveTextContent(
      `${filteredRowCount} rows`,
    );
    expect(response.sql_rowcount).toBe(97);
  },
);

test.each([false, true])(
  'flag-off Table preserves native row count with server pagination %s',
  serverPagination => {
    window.featureFlags = {
      ...window.featureFlags,
      [FeatureFlag.TableAlertFilters]: false,
    };
    const response = createResponse({
      table_color_metadata: createMetadata(0),
    });
    renderPills({
      formData: {
        viz_type: VizType.Table,
        server_pagination: serverPagination,
      },
      queriesResponse: serverPagination
        ? [response, createResponse({ data: [{ rowcount: 48 }] })]
        : [response],
    });

    expect(screen.getByTestId('row-count-label')).toHaveTextContent(
      `${serverPagination ? 48 : 97} rows`,
    );
  },
);

test.each([undefined, 'unavailable'] as const)(
  'Table without ready metadata (%s) preserves its SQL row count',
  status => {
    renderPills({
      queriesResponse: [
        createResponse({
          table_color_metadata: status
            ? {
                status,
                capabilities: {},
                reason: {
                  code: 'TABLE_COLOR_LIMIT',
                  message: 'Narrow the query.',
                },
              }
            : undefined,
        }),
      ],
    });

    expect(screen.getByTestId('row-count-label')).toHaveTextContent('97 rows');
  },
);

test.each([0, 48])(
  'ordinary server-paginated Table retains its count-query value %s',
  rowcount => {
    renderPills({
      formData: { viz_type: VizType.Table, server_pagination: true },
      queriesResponse: [
        createResponse(),
        createResponse({ data: [{ rowcount }] }),
      ],
    });

    expect(screen.getByTestId('row-count-label')).toHaveTextContent(
      `${rowcount} rows`,
    );
  },
);

test('other visualization types ignore color Table metadata and count queries', () => {
  renderPills({
    formData: { viz_type: VizType.Histogram },
    queriesResponse: [
      createResponse({ table_color_metadata: createMetadata(0) }),
      createResponse({ data: [{ rowcount: 48 }] }),
    ],
  });

  expect(screen.getByTestId('row-count-label')).toHaveTextContent('97 rows');
});

test('a loading color Table does not show a completed row count', () => {
  renderPills({
    chartStatus: 'loading',
    queriesResponse: [
      createResponse({ table_color_metadata: createMetadata(24) }),
    ],
  });

  expect(screen.queryByTestId('row-count-label')).not.toBeInTheDocument();
});
