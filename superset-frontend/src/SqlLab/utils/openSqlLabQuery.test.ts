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
import { logging } from '@apache-superset/core/utils';
import { SupersetClient } from '@superset-ui/core';
import { openSqlLabQuery } from './openSqlLabQuery';

jest.mock('@superset-ui/core', () => ({
  ...jest.requireActual('@superset-ui/core'),
  SupersetClient: {
    postForm: jest.fn(),
  },
}));

const mockPostForm = SupersetClient.postForm as jest.MockedFunction<
  typeof SupersetClient.postForm
>;

const longSql = `${Array.from(
  { length: 300 },
  (_, index) =>
    `-- line ${index.toString().padStart(3, '0')} 测试🙂 "quoted" 'single' \\path\t${'x'.repeat(20)}\r\n`,
).join('')}SELECT count() FROM complex_sql_cases;`;

beforeEach(() => {
  jest.clearAllMocks();
  mockPostForm.mockResolvedValue(undefined);
});

test('same-tab navigation keeps the complete query in router state', async () => {
  const navigate = jest.fn();
  const requestedQuery = { datasourceKey: '7__table', sql: longSql };

  await openSqlLabQuery({ requestedQuery, target: 'same-tab', navigate });

  expect(navigate).toHaveBeenCalledWith({
    pathname: '/sqllab',
    state: { requestedQuery },
  });
  expect(mockPostForm).not.toHaveBeenCalled();
});

test('new-tab navigation posts a lossless 300-line Unicode query', async () => {
  const requestedQuery = { datasourceKey: '7__table', sql: longSql };

  await openSqlLabQuery({ requestedQuery, target: 'new-tab' });

  expect(longSql.split('\r\n')).toHaveLength(301);
  expect(Array.from(longSql).length).toBeGreaterThanOrEqual(12000);
  expect(new TextEncoder().encode(longSql).byteLength).toBeGreaterThan(12000);
  expect(mockPostForm).toHaveBeenCalledWith('/sqllab/', {
    form_data: JSON.stringify(requestedQuery),
  });
  const serialized = mockPostForm.mock.calls[0][1].form_data;
  expect(JSON.parse(serialized)).toEqual(requestedQuery);
  expect(JSON.parse(serialized).sql).toBe(longSql);
  expect(window.location.href).not.toContain('sql=');
});

test('new-tab failure logs only diagnostics and never falls back to a URL', async () => {
  const secretSql = `${longSql}\nSELECT 'secret-marker';`;
  mockPostForm.mockRejectedValue(new Error(secretSql));
  const logSpy = jest.spyOn(logging, 'info');
  const openSpy = jest.spyOn(window, 'open');

  await expect(
    openSqlLabQuery({
      requestedQuery: { datasourceKey: '7__table', sql: secretSql },
      target: 'new-tab',
    }),
  ).rejects.toThrow('SQL Lab query navigation failed');

  expect(openSpy).not.toHaveBeenCalled();
  expect(JSON.stringify(logSpy.mock.calls)).not.toContain('secret-marker');
  expect(logSpy).toHaveBeenLastCalledWith(
    'SQL Lab query navigation',
    expect.objectContaining({
      status: 'failed',
      characterCount: Array.from(secretSql).length,
      utf8ByteCount: new TextEncoder().encode(secretSql).byteLength,
      sha256Prefix: expect.stringMatching(/^(?:[0-9a-f]{12}|unavailable)$/),
    }),
  );
  logSpy.mockRestore();
  openSpy.mockRestore();
});
