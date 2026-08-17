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
import { ComponentProps } from 'react';
import fetchMock from 'fetch-mock';
import {
  FeatureFlag,
  QueryFormData,
  QueryMode,
  SupersetClient,
} from '@superset-ui/core';
import { GenericDataType } from '@apache-superset/core/common';
import {
  render,
  screen,
  userEvent,
  waitFor,
} from 'spec/helpers/testing-library';
import { getMockStoreWithNativeFilters } from 'spec/fixtures/mockStore';
import chartQueries, { sliceId } from 'spec/fixtures/mockChartQueries';
import { supersetGetCache } from 'src/utils/cachedSupersetGet';
import DrillDetailPane from './DrillDetailPane';

const chart = chartQueries[sliceId];
type DrillDetailPaneProps = ComponentProps<typeof DrillDetailPane>;

const setup = (overrides: Partial<DrillDetailPaneProps> = {}) => {
  const store = getMockStoreWithNativeFilters();
  const props = {
    initialFilters: [],
    formData: chart.form_data as unknown as QueryFormData,
    ...overrides,
  };
  return render(<DrillDetailPane {...props} />, {
    useRedux: true,
    store,
  });
};

const waitForRender = (overrides: Partial<DrillDetailPaneProps> = {}) =>
  waitFor(() => setup(overrides));

const SAMPLES_ENDPOINT =
  'end:/datasource/samples?force=false&datasource_type=table&datasource_id=7&per_page=50&page=1';

const DATASET_ENDPOINT = 'glob:*/api/v1/dataset/*';

const MOCKED_DATASET = {
  changed_on_humanized: '2 days ago',
  created_on_humanized: 'a week ago',
  description: 'Simple description',
  table_name: 'test_table',
  changed_by: {
    first_name: 'John',
    last_name: 'Doe',
  },
  created_by: {
    first_name: 'John',
    last_name: 'Doe',
  },
  owners: [
    {
      first_name: 'John',
      last_name: 'Doe',
    },
  ],
};

const setupDatasetEndpoint = () => {
  fetchMock.get(DATASET_ENDPOINT, {
    status: 'complete',
    result: MOCKED_DATASET,
  });
};

const fetchWithNoData = () => {
  setupDatasetEndpoint();
  fetchMock.post(SAMPLES_ENDPOINT, {
    result: {
      total_count: 0,
      data: [],
      colnames: [],
      coltypes: [],
    },
  });
};

const fetchWithData = () => {
  setupDatasetEndpoint();
  fetchMock.post(SAMPLES_ENDPOINT, {
    result: {
      total_count: 3,
      data: [
        {
          year: 1996,
          na_sales: 11.27,
          eu_sales: 8.89,
        },
        {
          year: 1989,
          na_sales: 23.2,
          eu_sales: 2.26,
        },
        {
          year: 1999,
          na_sales: 9,
          eu_sales: 6.18,
        },
      ],
      colnames: ['year', 'na_sales', 'eu_sales'],
      coltypes: [0, 0, 0],
    },
  });
};

afterEach(() => {
  fetchMock.clearHistory().removeRoutes();
  supersetGetCache.clear();
  window.featureFlags = {};
});

test('should render', async () => {
  fetchWithNoData();
  const { container } = await waitForRender();
  expect(container).toBeInTheDocument();
});

test('should render loading indicator', async () => {
  fetchWithData();
  setup();
  await waitFor(() =>
    expect(screen.getByLabelText('Loading')).toBeInTheDocument(),
  );
});

test('should render the table with results', async () => {
  fetchWithData();
  await waitForRender();
  expect(screen.getByRole('table')).toBeInTheDocument();
  expect(screen.getByText('1996')).toBeInTheDocument();
  expect(screen.getByText('11.27')).toBeInTheDocument();
  expect(screen.getByText('1989')).toBeInTheDocument();
  expect(screen.getByText('23.2')).toBeInTheDocument();
  expect(screen.getByText('1999')).toBeInTheDocument();
  expect(screen.getByText('9')).toBeInTheDocument();
  expect(
    screen.getByRole('columnheader', { name: 'year' }),
  ).toBeInTheDocument();
  expect(
    screen.getByRole('columnheader', { name: 'na_sales' }),
  ).toBeInTheDocument();
  expect(
    screen.getByRole('columnheader', { name: 'eu_sales' }),
  ).toBeInTheDocument();
});

test('should render the "No results" components', async () => {
  fetchWithNoData();
  setup();
  expect(
    await screen.findByText('No rows were returned for this dataset'),
  ).toBeInTheDocument();
});

test('should render the metadata bar', async () => {
  fetchWithNoData();
  setup({ dataset: MOCKED_DATASET });
  expect(
    await screen.findByText(MOCKED_DATASET.table_name),
  ).toBeInTheDocument();
  expect(
    await screen.findByText(MOCKED_DATASET.description),
  ).toBeInTheDocument();
  expect(
    await screen.findByText(
      `${MOCKED_DATASET.created_by.first_name} ${MOCKED_DATASET.created_by.last_name}`,
    ),
  ).toBeInTheDocument();
  expect(
    await screen.findByText(MOCKED_DATASET.changed_on_humanized),
  ).toBeInTheDocument();
});

test('should render the error', async () => {
  jest
    .spyOn(SupersetClient, 'post')
    .mockRejectedValue(new Error('Something went wrong'));
  await waitForRender();
  expect(screen.getByText('Error: Something went wrong')).toBeInTheDocument();
});

test('should use verbose_map for column headers when available', async () => {
  jest.restoreAllMocks();

  const datasetWithVerboseMap = {
    ...MOCKED_DATASET,
    verbose_map: {
      year: 'Year of Release',
      na_sales: 'North America Sales',
    },
  };

  fetchMock.post(SAMPLES_ENDPOINT, {
    result: {
      total_count: 1,
      data: [
        {
          year: 1996,
          na_sales: 11.27,
          eu_sales: 8.89,
        },
      ],
      colnames: ['year', 'na_sales', 'eu_sales'],
      coltypes: [0, 0, 0],
    },
  });

  await waitForRender({ dataset: datasetWithVerboseMap });

  expect(
    screen.getByRole('columnheader', { name: 'Year of Release' }),
  ).toBeInTheDocument();
  expect(
    screen.getByRole('columnheader', { name: 'North America Sales' }),
  ).toBeInTheDocument();
  expect(
    screen.queryByRole('columnheader', { name: 'eu_sales' }),
  ).toBeInTheDocument();

  expect(
    screen.queryByRole('columnheader', { name: 'year' }),
  ).not.toBeInTheDocument();
  expect(
    screen.queryByRole('columnheader', { name: 'na_sales' }),
  ).not.toBeInTheDocument();
});

test('does not render configurable search while the feature flag is disabled', async () => {
  fetchWithNoData();
  await waitForRender({
    formData: {
      ...chart.form_data,
      viz_type: 'table',
      query_mode: QueryMode.Aggregate,
      drill_detail_include_search: true,
    } as unknown as QueryFormData,
  });

  expect(
    screen.queryByRole('textbox', { name: 'Drill detail search' }),
  ).not.toBeInTheDocument();
});

test('server mode sends selected-column prefix search and configured page length', async () => {
  window.featureFlags = {
    [FeatureFlag.DrillDetailConfigurableTable]: true,
  };
  setupDatasetEndpoint();
  fetchMock.post('glob:*/datasource/samples*detail_mode=server*', {
    result: {
      total_count: 1,
      rowcount: 1,
      data: [{ customer_name: 'Acme 北京' }],
      colnames: ['customer_name'],
      coltypes: [GenericDataType.String],
    },
  });
  const formData = {
    ...chart.form_data,
    viz_type: 'table',
    query_mode: QueryMode.Aggregate,
    metrics: ['sum__num'],
    drill_detail_server_pagination: true,
    drill_detail_server_page_length: 25,
    drill_detail_include_search: true,
  } as unknown as QueryFormData;
  const dataset = {
    ...MOCKED_DATASET,
    columns: [
      {
        column_name: 'customer_name',
        type_generic: GenericDataType.String,
        filterable: true,
      },
    ],
  };

  setup({ formData, dataset });
  const search = await screen.findByRole('textbox', {
    name: 'Drill detail search',
  });
  await waitFor(() =>
    expect(
      fetchMock.callHistory.calls(
        'glob:*/datasource/samples*detail_mode=server*',
      ),
    ).toHaveLength(1),
  );
  userEvent.type(search, 'Acme');

  await waitFor(() => {
    const calls = fetchMock.callHistory.calls(
      'glob:*/datasource/samples*detail_mode=server*',
    );
    expect(calls.length).toBeGreaterThan(1);
    expect(calls.at(-1)?.options.body).toEqual(
      expect.stringContaining(
        '"search":{"column":"customer_name","value":"Acme"}',
      ),
    );
    expect(calls.at(-1)?.url).toContain('per_page=25');
    expect(calls.at(-1)?.url).toContain('page=1');
  });
});

test('bounded-client mode searches all loaded columns without another request', async () => {
  window.featureFlags = {
    [FeatureFlag.DrillDetailConfigurableTable]: true,
  };
  setupDatasetEndpoint();
  fetchMock.post('glob:*/datasource/samples*detail_mode=bounded_client*', {
    result: {
      total_count: 2,
      rowcount: 2,
      data: [
        { customer_name: 'Acme', amount: 42 },
        { customer_name: 'Beta', amount: 7 },
      ],
      colnames: ['customer_name', 'amount'],
      coltypes: [GenericDataType.String, GenericDataType.Numeric],
    },
  });
  const formData = {
    ...chart.form_data,
    viz_type: 'table',
    query_mode: QueryMode.Aggregate,
    metrics: ['sum__num'],
    drill_detail_server_pagination: false,
    drill_detail_client_page_length: 25,
    drill_detail_include_search: true,
  } as unknown as QueryFormData;

  setup({ formData });
  expect(await screen.findByText('2 rows')).toBeInTheDocument();
  const search = screen.getByRole('textbox', { name: 'Drill detail search' });
  userEvent.type(search, 'Beta');

  expect(await screen.findByText('1 row')).toBeInTheDocument();
  expect(
    fetchMock.callHistory.calls(
      'glob:*/datasource/samples*detail_mode=bounded_client*',
    ),
  ).toHaveLength(1);
});
