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
  JsonResponse,
  SupersetClient,
} from '@superset-ui/core';
import { getDatasourceSamples } from './chartAction';

afterEach(() => {
  jest.restoreAllMocks();
});

test('getDatasourceSamples sends typed drill mode and search outside the URL body', async () => {
  const controller = new AbortController();
  const post = jest.spyOn(SupersetClient, 'post').mockResolvedValue({
    json: {
      result: {
        data: [],
        colnames: [],
        coltypes: [],
        total_count: 0,
        rowcount: 0,
      },
    },
  } as unknown as JsonResponse);

  await getDatasourceSamples(
    DatasourceType.Table,
    7,
    false,
    { filters: [] },
    25,
    2,
    3,
    'server',
    { column: 'customer_name', value: 'Acme' },
    controller.signal,
  );

  expect(post).toHaveBeenCalledWith({
    endpoint: '/datasource/samples',
    jsonPayload: {
      filters: [],
      search: { column: 'customer_name', value: 'Acme' },
    },
    searchParams: {
      force: false,
      datasource_type: DatasourceType.Table,
      datasource_id: 7,
      dashboard_id: 3,
      per_page: 25,
      page: 2,
      detail_mode: 'server',
    },
    parseMethod: 'json-bigint',
    signal: controller.signal,
  });
});
