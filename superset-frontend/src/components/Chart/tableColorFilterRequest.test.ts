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
  DatasourceType,
  FeatureFlag,
  QueryContext,
  QueryObject,
  QueryFormData,
  SupersetClient,
  TableColorMetadata,
  TableColorFilterRequest,
  TableColorSelection,
} from '@superset-ui/core';
import {
  areTableColorSelectionsEqual,
  clearTableColorFilterRequestCache,
  getTableColorFilterErrorMessage,
  normalizeTableColorSelections,
  prepareTableColorFilterRequest,
  rememberTableColorFilterResponse,
} from './tableColorFilterRequest';

const makePayload = (): QueryContext & {
  queries: (QueryObject & { table_color_filter: TableColorFilterRequest })[];
} => ({
  datasource: { id: 1, type: DatasourceType.Table },
  force: false,
  result_format: 'json',
  result_type: 'full',
  form_data: {
    datasource: '1__table',
    viz_type: 'table',
    slice_id: 42,
    row_limit: 1000,
    server_pagination: true,
    server_page_length: 20,
    conditional_formatting: [
      { column: 'profit', filterable: true, colorScheme: 'colorSuccess' },
    ],
  },
  queries: [
    {
      columns: ['category'],
      metrics: ['profit'],
      row_limit: 20,
      row_offset: 0,
      table_color_filter: { version: 2, selections: [] },
    },
  ],
});

const ready: TableColorMetadata = {
  status: 'ready',
  snapshot_id: 'snapshot-1',
  generation: 'generation-1',
  baseline_rowcount: 100,
  filtered_rowcount: 20,
  source_page_size: 20,
  catalog: { profit: ['GREEN', 'YELLOW', 'RED'] },
  capabilities: { profit: { enabled: true, supported: true } },
  styles: [],
  selections: [],
  expires_in: 300,
};

beforeEach(() => {
  clearTableColorFilterRequestCache();
  window.featureFlags = { [FeatureFlag.TableAlertFilters]: true };
  window.history.replaceState({}, '', '/explore/');
});

afterEach(() => {
  jest.restoreAllMocks();
  clearTableColorFilterRequestCache();
  window.featureFlags = {};
  window.history.replaceState({}, '', '/');
});

const appliedSelections: TableColorSelection[] = [
  { column: 'error_count', colors: ['RED'] },
  { column: 'gross_profit', colors: ['GREEN', 'YELLOW'] },
];

test.each<{
  name: string;
  selection: TableColorSelection[];
  equal: boolean;
}>([
  {
    name: 'reversed columns',
    selection: [appliedSelections[1], appliedSelections[0]],
    equal: true,
  },
  {
    name: 'reversed colors',
    selection: [
      appliedSelections[0],
      { column: 'gross_profit', colors: ['YELLOW', 'GREEN'] },
    ],
    equal: true,
  },
  {
    name: 'repeated columns and colors use the server union semantics',
    selection: [
      { column: 'gross_profit', colors: ['YELLOW', 'YELLOW'] },
      appliedSelections[0],
      { column: 'gross_profit', colors: ['GREEN'] },
    ],
    equal: true,
  },
  {
    name: 'a missing column',
    selection: [appliedSelections[1]],
    equal: false,
  },
  {
    name: 'a different selected color',
    selection: [
      { column: 'gross_profit', colors: ['GREEN', 'RED'] },
      appliedSelections[0],
    ],
    equal: false,
  },
  {
    name: 'colors moved to another column',
    selection: [
      { column: 'gross_profit', colors: ['RED'] },
      { column: 'error_count', colors: ['GREEN', 'YELLOW'] },
    ],
    equal: false,
  },
  { name: 'a pending clear', selection: [], equal: false },
])('color selection comparison handles $name', ({ selection, equal }) => {
  expect(areTableColorSelectionsEqual(selection, appliedSelections)).toBe(
    equal,
  );
  expect(areTableColorSelectionsEqual(appliedSelections, selection)).toBe(
    equal,
  );
});

test('selection normalization does not reorder or mutate the original selection arrays', () => {
  const selection: TableColorSelection[] = [
    { column: 'gross_profit', colors: ['YELLOW', 'GREEN', 'YELLOW'] },
    { column: 'error_count', colors: ['RED'] },
  ];
  selection.forEach(entry => {
    Object.freeze(entry.colors);
    Object.freeze(entry);
  });
  Object.freeze(selection);
  expect(normalizeTableColorSelections(selection)).toEqual(appliedSelections);
  expect(selection[0].colors).toEqual(['YELLOW', 'GREEN', 'YELLOW']);
  expect(selection[0].column).toBe('gross_profit');
});

test('missing selections mean no active colors, without collapsing a nonempty filter', () => {
  expect(areTableColorSelectionsEqual(undefined, [])).toBe(true);
  expect(areTableColorSelectionsEqual([], undefined)).toBe(true);
  expect(areTableColorSelectionsEqual(undefined, appliedSelections)).toBe(
    false,
  );
  expect(
    areTableColorSelectionsEqual([{ column: 'gross_profit', colors: [] }], []),
  ).toBe(false);
});

test.each([
  ['TABLE_COLOR_FILTER_SNAPSHOT_EXPIRED', 410, 'Reload the table'],
  ['TABLE_COLOR_FILTER_CONTEXT_CHANGED', 409, 'Refresh the table'],
  ['TABLE_COLOR_FILTER_LIMIT_EXCEEDED', 422, '1,000 rows by default'],
  ['TABLE_COLOR_FILTER_CACHE_UNAVAILABLE', 503, 'service recovers'],
  ['TABLE_COLOR_FILTER_BUILD_IN_PROGRESS', 503, 'Wait for it to finish'],
  ['TABLE_COLOR_FILTER_TIMEOUT', 504, 'Narrow the query'],
])(
  'maps %s to a safe recovery instruction',
  async (errorCode, status, action) => {
    const response = new Response(
      JSON.stringify({
        error_code: errorCode,
        message: 'private SQL WHERE secret = 123',
      }),
      { status: Number(status) },
    );
    const message = await getTableColorFilterErrorMessage({ response });
    expect(message).toContain(action);
    expect(message).toContain('The last successful result is still displayed.');
    expect(message).not.toContain('private SQL');
    expect(response.bodyUsed).toBe(false);
  },
);

test('unknown color failures use generic text without reading database messages into the UI', async () => {
  const response = new Response(
    JSON.stringify({
      error_code: 'UNKNOWN_BACKEND_ERROR',
      message: 'private database and SQL details',
    }),
    { status: 500 },
  );
  expect(await getTableColorFilterErrorMessage({ response })).toBe(
    'The color filter could not be applied. The last successful result is still displayed.',
  );
});

test.each(['csv', 'json', 'xlsx'])(
  'Current View %s carries ordered row references and preserves an empty projection',
  async format => {
    jest.spyOn(SupersetClient, 'post').mockResolvedValue({
      json: { key: 'owned-draft' },
    } as never);
    const baseline = makePayload();
    await prepareTableColorFilterRequest(baseline);
    rememberTableColorFilterResponse(baseline, ready);
    for (const rowIndices of [[7, 2, 9], []]) {
      const payload = makePayload();
      payload.result_format = format;
      payload.result_type = 'results';
      await prepareTableColorFilterRequest(payload, {}, undefined, {
        snapshotId: 'snapshot-1',
        rowIndices,
      });
      expect(payload.queries[0].table_color_filter).toEqual(
        expect.objectContaining({
          snapshot_id: 'snapshot-1',
          view_rows: rowIndices,
        }),
      );
    }
    const nextInteractive = makePayload();
    await prepareTableColorFilterRequest(nextInteractive);
    expect(
      nextInteractive.queries[0].table_color_filter.view_rows,
    ).toBeUndefined();
  },
);

test('Current View cannot reuse row references after its source query changes', async () => {
  const post = jest.spyOn(SupersetClient, 'post').mockResolvedValue({
    json: { key: 'owned-draft' },
  } as never);
  const baseline = makePayload();
  await prepareTableColorFilterRequest(baseline);
  rememberTableColorFilterResponse(baseline, ready);
  const payload = makePayload();
  payload.result_format = 'xlsx';
  payload.result_type = 'results';
  payload.queries[0].filters = [{ col: 'region', op: 'IN', val: ['east'] }];
  await expect(
    prepareTableColorFilterRequest(payload, {}, undefined, {
      snapshotId: 'snapshot-1',
      rowIndices: [],
    }),
  ).rejects.toThrow('Load the current table view before exporting.');
  expect(post).toHaveBeenCalledTimes(1);
});

test('Explore waits for its owned draft and shares the draft across concurrent color requests', async () => {
  let release: (value: { json: { key: string } }) => void = () => {};
  const pendingDraft = new Promise<{ json: { key: string } }>(resolve => {
    release = resolve;
  });
  const post = jest
    .spyOn(SupersetClient, 'post')
    .mockImplementation(() => pendingDraft as never);
  const first = makePayload();
  const second = makePayload();
  const requests = [
    prepareTableColorFilterRequest(first),
    prepareTableColorFilterRequest(second),
  ];
  expect(first.queries[0].table_color_filter.form_data_key).toBeUndefined();
  expect(post).toHaveBeenCalledTimes(1);
  release({ json: { key: 'owned-draft' } });
  await Promise.all(requests);
  expect(first.queries[0].table_color_filter.form_data_key).toBe('owned-draft');
  expect(second.queries[0].table_color_filter.form_data_key).toBe(
    'owned-draft',
  );
  expect(post).toHaveBeenCalledWith(
    expect.objectContaining({
      endpoint: '/api/v1/explore/form_data',
      jsonPayload: expect.objectContaining({ datasource_id: 1, chart_id: 42 }),
    }),
  );
});

test('pagination and color changes reuse one snapshot without another draft POST', async () => {
  const post = jest
    .spyOn(SupersetClient, 'post')
    .mockResolvedValue({ json: { key: 'owned-draft' } } as never);
  const baseline = makePayload();
  await prepareTableColorFilterRequest(baseline);
  rememberTableColorFilterResponse(baseline, ready);
  for (const page of [0, 1, 2]) {
    const payload = makePayload();
    payload.queries[0].row_offset = page * 20;
    payload.queries[0].table_color_filter.selections = [
      { column: 'profit', colors: ['GREEN', 'YELLOW', 'RED'] },
    ];
    payload.form_data!.extra_form_data = {
      currentPage: page,
      alertFilter: {
        version: 2,
        selections: payload.queries[0].table_color_filter.selections,
      },
    } as unknown as QueryFormData['extra_form_data'];
    await prepareTableColorFilterRequest(payload, {
      currentPage: page,
      pageSize: 20,
    });
    expect(payload.queries[0].table_color_filter.snapshot_id).toBe(
      'snapshot-1',
    );
    expect(payload.queries[0].row_offset).toBe(page * 20);
    expect(payload.form_data?.extra_form_data).toEqual({});
  }
  expect(post).toHaveBeenCalledTimes(1);
});

test('source filters invalidate the token and reset the page without clearing colors', async () => {
  jest
    .spyOn(SupersetClient, 'post')
    .mockResolvedValue({ json: { key: 'draft' } } as never);
  const baseline = makePayload();
  await prepareTableColorFilterRequest(baseline);
  rememberTableColorFilterResponse(baseline, ready);
  const changed = makePayload();
  changed.queries[0].filters = [{ col: 'category', op: '==', val: 'new' }];
  changed.queries[0].row_offset = 40;
  changed.queries[0].table_color_filter = {
    version: 2,
    snapshot_id: 'snapshot-1',
    selections: [{ column: 'profit', colors: ['GREEN'] }],
  };
  await prepareTableColorFilterRequest(changed);
  expect(changed.queries[0].table_color_filter.snapshot_id).toBeUndefined();
  expect(changed.queries[0].table_color_filter.selections).toEqual([
    { column: 'profit', colors: ['GREEN'] },
  ]);
  expect(changed.queries[0].row_offset).toBe(0);
});

test('a changed native page size creates a fresh baseline', async () => {
  const post = jest
    .spyOn(SupersetClient, 'post')
    .mockResolvedValue({ json: { key: 'draft' } } as never);
  const baseline = makePayload();
  await prepareTableColorFilterRequest(baseline, { pageSize: 20 });
  rememberTableColorFilterResponse(baseline, ready);
  const changed = makePayload();
  changed.queries[0].row_limit = 50;
  changed.queries[0].table_color_filter.snapshot_id = 'snapshot-1';
  await prepareTableColorFilterRequest(changed, { pageSize: 50 });
  expect(changed.queries[0].table_color_filter.snapshot_id).toBeUndefined();
  expect(post).toHaveBeenCalledTimes(2);
});

test('saved Dashboard requests never create an Explore draft', async () => {
  const post = jest.spyOn(SupersetClient, 'post');
  const payload = makePayload();
  payload.form_data!.dashboardId = 3;
  payload.queries[0].table_color_filter.form_data_key = 'untrusted';
  await prepareTableColorFilterRequest(payload, {}, 'dark');
  expect(payload.queries[0].table_color_filter.form_data_key).toBeUndefined();
  expect(payload.queries[0].table_color_filter.theme_mode).toBe('dark');
  expect(post).not.toHaveBeenCalled();
});

test('exports reuse the displayed page denominator and theme, including force exports', async () => {
  window.history.replaceState({}, '', '/superset/dashboard/3/');
  const baseline = makePayload();
  baseline.form_data!.dashboardId = 3;
  await prepareTableColorFilterRequest(baseline, { pageSize: 20 }, 'dark');
  rememberTableColorFilterResponse(baseline, ready);
  const exported = makePayload();
  exported.form_data!.dashboardId = 3;
  exported.result_format = 'xlsx';
  exported.result_type = 'results';
  exported.force = true;
  exported.queries[0].row_limit = 1000;
  await prepareTableColorFilterRequest(exported, { pageSize: 20 });
  expect(exported.queries[0].table_color_filter.snapshot_id).toBe('snapshot-1');
  expect(exported.queries[0].table_color_filter.theme_mode).toBe('dark');
});

test('flag-off and ordinary Table queries make no draft request', async () => {
  const post = jest.spyOn(SupersetClient, 'post');
  window.featureFlags = {};
  await prepareTableColorFilterRequest(makePayload());
  window.featureFlags = { [FeatureFlag.TableAlertFilters]: true };
  const payload: QueryContext = makePayload();
  delete payload.queries[0].table_color_filter;
  await prepareTableColorFilterRequest(payload);
  expect(post).not.toHaveBeenCalled();
});

test('a failed draft is retriable and never leaves a fake snapshot reference', async () => {
  const post = jest
    .spyOn(SupersetClient, 'post')
    .mockRejectedValueOnce(new Error('draft unavailable'))
    .mockResolvedValueOnce({ json: { key: 'retry-draft' } } as never);
  await expect(prepareTableColorFilterRequest(makePayload())).rejects.toThrow(
    'draft unavailable',
  );
  const retry = makePayload();
  await prepareTableColorFilterRequest(retry);
  expect(retry.queries[0].table_color_filter.form_data_key).toBe('retry-draft');
  expect(retry.queries[0].table_color_filter.snapshot_id).toBeUndefined();
  expect(post).toHaveBeenCalledTimes(2);
});
