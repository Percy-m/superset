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
  DataMask,
  FeatureFlag,
  JsonObject,
  QueryFormData,
  SupersetClient,
  TableColorSelection,
} from '@superset-ui/core';
import { waitFor } from 'spec/helpers/testing-library';
import {
  ChartThunkDispatch,
  exploreJSON,
  RootState,
  CHART_UPDATE_FAILED,
  CHART_UPDATE_STARTED,
  CHART_UPDATE_SUCCEEDED,
} from './chartAction';
import { clearTableColorFilterRequestCache } from './tableColorFilterRequest';

const formData: QueryFormData = {
  datasource: '1__table',
  viz_type: 'table',
  slice_id: 42,
  conditional_formatting: [
    { column: 'profit', filterable: true, useGradient: false },
  ],
};
const ready = {
  status: 'ready',
  snapshot_id: 'snapshot-1',
  generation: 'generation-1',
  baseline_rowcount: 3,
  filtered_rowcount: 1,
  source_page_size: 20,
  catalog: { profit: ['GREEN', 'YELLOW', 'RED'] },
  capabilities: { profit: { enabled: true, supported: true } },
  styles: [],
  expires_in: 300,
  selections: [{ column: 'profit', colors: ['GREEN'] }],
};

function stateHarness() {
  const state = {
    charts: {
      42: {
        queriesResponse: [
          { data: [{ profit: 1 }], table_color_metadata: ready },
        ],
      },
    },
    common: { conf: {} },
    dataMask: {
      42: {
        ownState: {
          currentPage: 0,
          pageSize: 20,
          alertFilter: {
            version: 2,
            selections: [
              { column: 'profit', colors: ['GREEN', 'YELLOW', 'RED'] },
            ],
          },
        },
      },
    },
  } as unknown as RootState;
  const sink = jest.fn(
    (action: {
      type: string;
      queryController?: AbortController;
      dataMask?: DataMask;
    }) => {
      if (action.type === CHART_UPDATE_STARTED)
        state.charts[42].queryController = action.queryController ?? null;
      if (action.type === 'UPDATE_DATA_MASK' && action.dataMask) {
        state.dataMask[42] = { ...state.dataMask[42], ...action.dataMask };
      }
      return action;
    },
  );
  return { state, sink, dispatch: sink as unknown as ChartThunkDispatch };
}

beforeEach(() => {
  window.featureFlags = { [FeatureFlag.TableAlertFilters]: true };
  clearTableColorFilterRequestCache();
});
afterEach(() => {
  jest.restoreAllMocks();
  window.featureFlags = {};
  clearTableColorFilterRequestCache();
});

test('a failed color request restores the applied selection and keeps the last result', async () => {
  jest
    .spyOn(SupersetClient, 'post')
    .mockRejectedValue(new Error('private database detail'));
  const { state, sink, dispatch } = stateHarness();
  await exploreJSON(
    formData,
    false,
    60,
    42,
    3,
    state.dataMask[42].ownState,
  )(dispatch, () => state, undefined);
  expect(
    sink.mock.calls.some(([action]) => action.type === CHART_UPDATE_FAILED),
  ).toBe(false);
  const success = sink.mock.calls.find(
    ([action]) => action.type === CHART_UPDATE_SUCCEEDED,
  )?.[0] as unknown as { queriesResponse: JsonObject[] };
  expect(success.queriesResponse[0].data).toEqual([{ profit: 1 }]);
  expect(success.queriesResponse[0].table_color_metadata.request_error).toMatch(
    /last successful result/,
  );
  expect(JSON.stringify(success)).not.toContain('private database detail');
  expect(state.dataMask[42].ownState?.alertFilter).toEqual({
    version: 2,
    selections: ready.selections,
    snapshotId: 'snapshot-1',
    generation: 'generation-1',
  });
});

test.each([undefined, 'snapshotId', 'generation'] as const)(
  'failure recovery ignores selection order but still validates identity %s',
  async staleField => {
    jest
      .spyOn(SupersetClient, 'post')
      .mockRejectedValue(new Error('Request failed'));
    const { state, sink, dispatch } = stateHarness();
    const appliedSelections: TableColorSelection[] = [
      { column: 'error_count', colors: ['RED'] },
      { column: 'gross_profit', colors: ['GREEN', 'YELLOW'] },
    ];
    state.charts[42].queriesResponse = [
      {
        data: [{ profit: 1 }],
        table_color_metadata: { ...ready, selections: appliedSelections },
      },
    ];
    state.dataMask[42].ownState = {
      ...state.dataMask[42].ownState,
      alertFilter: {
        version: 2,
        snapshotId: ready.snapshot_id,
        generation: ready.generation,
        ...(staleField ? { [staleField]: 'stale-identity' } : {}),
        selections: [
          { column: 'gross_profit', colors: ['YELLOW', 'GREEN'] },
          { column: 'error_count', colors: ['RED'] },
        ],
      },
    };

    await exploreJSON(
      formData,
      false,
      60,
      42,
      3,
      state.dataMask[42].ownState,
    )(dispatch, () => state, undefined);

    const updates = sink.mock.calls.filter(
      ([action]) => action.type === 'UPDATE_DATA_MASK',
    );
    expect(updates).toHaveLength(staleField ? 1 : 0);
    expect(
      sink.mock.calls.some(([action]) => action.type === CHART_UPDATE_FAILED),
    ).toBe(false);
    if (staleField) {
      expect(state.dataMask[42].ownState?.alertFilter).toEqual({
        version: 2,
        selections: appliedSelections,
        snapshotId: ready.snapshot_id,
        generation: ready.generation,
      });
    }
  },
);

test('an expired snapshot preserves the last result and tells the user to reload without leaking error bodies', async () => {
  jest.spyOn(SupersetClient, 'post').mockRejectedValue({
    response: new Response(
      JSON.stringify({
        error_code: 'TABLE_COLOR_FILTER_SNAPSHOT_EXPIRED',
        message: 'private query and filter values',
      }),
      { status: 410 },
    ),
  });
  const { state, sink, dispatch } = stateHarness();
  await exploreJSON(
    formData,
    false,
    60,
    42,
    3,
    state.dataMask[42].ownState,
  )(dispatch, () => state, undefined);
  const success = sink.mock.calls.find(
    ([action]) => action.type === CHART_UPDATE_SUCCEEDED,
  )?.[0] as unknown as { queriesResponse: JsonObject[] };
  expect(
    success.queriesResponse[0].table_color_metadata.request_error,
  ).toContain('Reload the table and try again.');
  expect(success.queriesResponse[0].data).toEqual([{ profit: 1 }]);
  expect(JSON.stringify(sink.mock.calls)).not.toContain('private query');
});

test('an initial prepare failure leaves the ordinary table visible with an unavailable reason', async () => {
  jest
    .spyOn(SupersetClient, 'post')
    .mockRejectedValue(new Error('draft unavailable'));
  const { state, sink, dispatch } = stateHarness();
  state.charts[42].queriesResponse = [{ data: [{ profit: 7 }] }];
  await exploreJSON(
    formData,
    false,
    60,
    42,
    3,
  )(dispatch, () => state, undefined);
  const success = sink.mock.calls.find(
    ([action]) => action.type === CHART_UPDATE_SUCCEEDED,
  )?.[0] as unknown as { queriesResponse: JsonObject[] };
  expect(success.queriesResponse[0].data).toEqual([{ profit: 7 }]);
  expect(success.queriesResponse[0].table_color_metadata.status).toBe(
    'unavailable',
  );
  expect(
    sink.mock.calls.some(([action]) => action.type === CHART_UPDATE_FAILED),
  ).toBe(false);
});

test('a late response cannot replace the newest color result', async () => {
  const resolvers: Array<(result: unknown) => void> = [];
  const post = jest.spyOn(SupersetClient, 'post').mockImplementation(
    () =>
      new Promise(resolve => {
        resolvers.push(resolve);
      }) as never,
  );
  const { state, sink, dispatch } = stateHarness();
  const first = exploreJSON(
    formData,
    false,
    60,
    42,
    3,
  )(dispatch, () => state, undefined);
  await waitFor(() => expect(post).toHaveBeenCalledTimes(1));
  const second = exploreJSON(
    formData,
    false,
    60,
    42,
    3,
  )(dispatch, () => state, undefined);
  await waitFor(() => expect(post).toHaveBeenCalledTimes(2));
  resolvers[1]({
    response: { status: 200 },
    json: { result: [{ data: [{ profit: 2 }], table_color_metadata: ready }] },
  });
  await second;
  resolvers[0]({
    response: { status: 200 },
    json: { result: [{ data: [{ profit: 1 }], table_color_metadata: ready }] },
  });
  await first;
  const successes = sink.mock.calls.filter(
    ([action]) => action.type === CHART_UPDATE_SUCCEEDED,
  );
  expect(successes).toHaveLength(1);
  expect(
    (successes[0][0] as unknown as { queriesResponse: JsonObject[] })
      .queriesResponse[0].data,
  ).toEqual([{ profit: 2 }]);
});
