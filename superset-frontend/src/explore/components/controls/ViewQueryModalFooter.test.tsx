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
import { fireEvent, render, screen } from 'spec/helpers/testing-library';
import { FeatureFlag, SupersetClient } from '@superset-ui/core';
import { openSqlLabQuery } from 'src/SqlLab/utils/openSqlLabQuery';
import ViewQueryModalFooter from './ViewQueryModalFooter';

const mockHistoryPush = jest.fn();
const mockAddDangerToast = jest.fn();

jest.mock('react-router-dom', () => ({
  ...jest.requireActual('react-router-dom'),
  useHistory: () => ({ push: mockHistoryPush }),
}));
jest.mock('src/SqlLab/utils/openSqlLabQuery', () => ({
  openSqlLabQuery: jest.fn(),
}));
jest.mock('src/components/MessageToasts/withToasts', () => ({
  useToasts: () => ({ addDangerToast: mockAddDangerToast }),
}));

const mockOpenSqlLabQuery = openSqlLabQuery as jest.MockedFunction<
  typeof openSqlLabQuery
>;
const requestedQuery = {
  datasourceKey: '7__table',
  sql: 'SELECT 测试🙂',
};

const setup = () =>
  render(
    <ViewQueryModalFooter
      closeModal={jest.fn()}
      changeDatasource={jest.fn()}
      datasource={{ id: '7', type: 'table', sql: requestedQuery.sql }}
    />,
    { useRouter: true, useRedux: true },
  );

beforeEach(() => {
  jest.clearAllMocks();
  window.featureFlags = {};
  mockOpenSqlLabQuery.mockResolvedValue(undefined);
});

afterEach(() => {
  window.featureFlags = {};
});

test('same-tab action uses the shared router-state navigation', () => {
  setup();

  fireEvent.click(screen.getByText('Open in SQL Lab'));

  expect(mockOpenSqlLabQuery).toHaveBeenCalledWith(
    expect.objectContaining({
      requestedQuery,
      target: 'same-tab',
      navigate: expect.any(Function),
    }),
  );
});

test('feature-enabled modifier action uses POST form navigation', () => {
  window.featureFlags = { [FeatureFlag.LongSqlPostNavigation]: true };
  setup();

  fireEvent.click(screen.getByText('Open in SQL Lab'), { ctrlKey: true });

  expect(mockOpenSqlLabQuery).toHaveBeenCalledWith({
    requestedQuery,
    target: 'new-tab',
  });
});

test('feature-disabled modifier action preserves the legacy form payload', () => {
  const postFormSpy = jest
    .spyOn(SupersetClient, 'postForm')
    .mockResolvedValue(undefined);
  setup();

  fireEvent.click(screen.getByText('Open in SQL Lab'), { metaKey: true });

  expect(postFormSpy).toHaveBeenCalledWith('/sqllab/', requestedQuery);
  expect(mockOpenSqlLabQuery).not.toHaveBeenCalled();
});
