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
import { MemoryRouter } from 'react-router-dom';
import { createStore, render, waitFor } from 'spec/helpers/testing-library';
import reducerIndex from 'spec/helpers/reducerIndex';
import fetchMock from 'fetch-mock';
import { initialState } from 'src/SqlLab/fixtures';
import type { QueryEditor } from 'src/SqlLab/types';
import { Store } from 'redux';
import { RootState } from 'src/views/store';
import getBootstrapData from 'src/utils/getBootstrapData';

import PopEditorTab from '.';
import { LocationProvider } from 'src/pages/SqlLab/LocationContext';

jest.mock('src/utils/getBootstrapData', () => ({
  __esModule: true,
  applicationRoot: jest.fn(() => ''),
  default: jest.fn(() => ({
    common: {},
    requested_query: {},
    user: {},
  })),
  staticAssetsPrefix: jest.fn(() => ''),
}));

const mockGetBootstrapData = jest.mocked(getBootstrapData);

const setup = (
  url = '/sqllab',
  overridesStore?: Store,
  overridesInitialState?: RootState,
) =>
  render(
    <MemoryRouter initialEntries={[url]}>
      <LocationProvider>
        <PopEditorTab />
      </LocationProvider>
    </MemoryRouter>,
    {
      useRedux: true,
      initialState: overridesInitialState || initialState,
      ...(overridesStore && { store: overridesStore }),
    },
  );

beforeEach(() => {
  mockGetBootstrapData.mockReturnValue({
    common: {},
    requested_query: {},
    user: {},
  } as unknown as ReturnType<typeof getBootstrapData>);
  fetchMock.get('glob:*/api/v1/database/*', {});
  fetchMock.get('glob:*/api/v1/saved_query/*', {
    result: {
      id: 2,
      database: { id: 1 },
      label: 'test',
      sql: 'SELECT * FROM test_table',
    },
  });
});

afterEach(() => {
  fetchMock.clearHistory().removeRoutes();
});

let replaceState = jest.spyOn(window.history, 'replaceState');
beforeEach(() => {
  replaceState = jest.spyOn(window.history, 'replaceState');
});
afterEach(() => {
  replaceState.mockReset();
});

test('should handle id', async () => {
  const id = 1;
  fetchMock.get(`glob:*/api/v1/sqllab/permalink/kv:${id}`, {
    label: 'test permalink',
    sql: 'SELECT * FROM test_table',
    dbId: 1,
  });
  setup('/sqllab?id=1');
  await waitFor(() =>
    expect(
      fetchMock.callHistory.calls(`glob:*/api/v1/sqllab/permalink/kv:${id}`),
    ).toHaveLength(1),
  );
  expect(replaceState).toHaveBeenCalledWith(
    expect.anything(),
    expect.anything(),
    '/sqllab',
  );
  fetchMock.clearHistory().removeRoutes();
});
test('should handle permalink', async () => {
  const key = '9sadkfl';
  fetchMock.get(`glob:*/api/v1/sqllab/permalink/${key}`, {
    label: 'test permalink',
    sql: 'SELECT * FROM test_table',
    dbId: 1,
  });
  setup('/sqllab/p/9sadkfl');
  await waitFor(() =>
    expect(
      fetchMock.callHistory.calls(`glob:*/api/v1/sqllab/permalink/${key}`),
    ).toHaveLength(1),
  );
  expect(replaceState).toHaveBeenCalledWith(
    expect.anything(),
    expect.anything(),
    '/sqllab',
  );
  fetchMock.clearHistory().removeRoutes();
});
test('should handle savedQueryId', async () => {
  setup('/sqllab?savedQueryId=1');
  await waitFor(() =>
    expect(
      fetchMock.callHistory.calls('glob:*/api/v1/saved_query/1'),
    ).toHaveLength(1),
  );
  expect(replaceState).toHaveBeenCalledWith(
    expect.anything(),
    expect.anything(),
    '/sqllab',
  );
});
test('should handle sql', () => {
  setup('/sqllab?sql=1&dbid=1');
  expect(replaceState).toHaveBeenCalledWith(
    expect.anything(),
    expect.anything(),
    '/sqllab',
  );
});
test('should hydrate complete virtual dataset context from POST bootstrap', async () => {
  const sql = "SELECT '数据质量🙂'";
  mockGetBootstrapData.mockReturnValue({
    common: {},
    requested_query: {
      autorun: true,
      catalog: 'analytics',
      dbid: '7',
      isDataset: true,
      name: 'Revenue 数据集',
      schema: 'superset_quality_21_3',
      sql,
    },
    user: {},
  } as unknown as ReturnType<typeof getBootstrapData>);
  const store = createStore(initialState, reducerIndex);

  setup('/sqllab', store);

  await waitFor(() => {
    const state = store.getState() as RootState;
    const hydratedEditor = state.sqlLab.queryEditors.find(
      (queryEditor: QueryEditor) => queryEditor.sql === sql,
    );
    expect(hydratedEditor).toMatchObject({
      autorun: true,
      catalog: 'analytics',
      dbId: 7,
      isDataset: true,
      name: 'Revenue 数据集',
      schema: 'superset_quality_21_3',
      sql,
    });
  });
});
test('should normalize legacy string booleans from bootstrap', async () => {
  const sql = 'SELECT legacy_boolean_context';
  mockGetBootstrapData.mockReturnValue({
    common: {},
    requested_query: {
      autorun: 'false',
      dbid: '7',
      isDataset: 'true',
      sql,
    },
    user: {},
  } as unknown as ReturnType<typeof getBootstrapData>);
  const store = createStore(initialState, reducerIndex);

  setup('/sqllab', store);

  await waitFor(() => {
    const state = store.getState() as RootState;
    const hydratedEditor = state.sqlLab.queryEditors.find(
      (queryEditor: QueryEditor) => queryEditor.sql === sql,
    );
    expect(hydratedEditor).toMatchObject({
      autorun: false,
      dbId: 7,
      isDataset: true,
      sql,
    });
  });
});
test('should handle custom url params', () => {
  setup('/sqllab?sql=1&dbid=1&custom_value=str&extra_attr1=true');
  expect(replaceState).toHaveBeenCalledWith(
    expect.anything(),
    expect.anything(),
    '/sqllab?custom_value=str&extra_attr1=true',
  );
});
