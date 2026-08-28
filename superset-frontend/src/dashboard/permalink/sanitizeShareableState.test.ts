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
import { TAB_TYPE } from 'src/dashboard/util/componentTypes';
import {
  logDashboardStateDrops,
  sanitizeShareableDashboardState,
} from './sanitizeShareableState';

const RED_RULE_ID = '772a548e-72f7-4ac8-a8ff-fdb7465b3ccd';
const YELLOW_RULE_ID = '99ab3f13-81cb-433b-8615-1290f4ddf6cc';

const tableChart = {
  slice_id: 10,
  form_data: {
    slice_id: 10,
    viz_type: 'table',
    groupby: ['quantity'],
    metrics: ['gross_revenue'],
    conditional_formatting: [
      {
        ruleId: RED_RULE_ID,
        colorScheme: 'colorError',
        filterable: true,
        column: 'gross_revenue',
        operator: '<',
        targetValue: 0,
        useGradient: false,
        objectFormatting: 'BACKGROUND_COLOR',
      },
      {
        ruleId: YELLOW_RULE_ID,
        colorScheme: 'colorWarning',
        filterable: true,
        column: 'quantity',
        operator: '≤ x <',
        targetValueLeft: 1,
        targetValueRight: 4,
        useGradient: false,
        objectFormatting: 'TEXT_COLOR',
      },
    ],
  },
};

const chartLayout = {
  'CHART-10': { id: 'CHART-10', type: 'CHART', meta: { chartId: 10 } },
};

test('sanitizes native, cross, alert, and tab state with a strict whitelist', () => {
  const result = sanitizeShareableDashboardState({
    dataMask: {
      'NATIVE_FILTER-1': {
        id: 'NATIVE_FILTER-1',
        extraFormData: {
          filters: [{ col: 'region', op: 'IN', val: ['APAC'] }],
        },
        filterState: { value: ['APAC'], label: 'APAC' },
        ownState: { internalSearch: 'must-not-be-shared' },
      },
      'NATIVE_FILTER-empty': {
        id: 'NATIVE_FILTER-empty',
        filterState: { value: null, excludeFilterValues: true },
      },
      10: {
        id: '10',
        extraFormData: {
          filters: [{ col: 'channel', op: 'IN', val: ['Online'] }],
          time_range: 'must-not-override-cross-filter-time',
        },
        filterState: {
          value: ['Online'],
          transientSearch: 'must-not-be-shared',
        },
        ownState: {
          currentPage: 8,
          pageSize: 200,
          searchText: 'private transient search',
          clientView: { rows: ['must-not-be-shared'] },
          alertFilter: {
            version: 2,
            selections: [
              { column: 'gross_revenue', colors: ['RED'] },
              { column: 'gross_revenue', colors: ['BLUE'] },
              { column: 'deleted_column', colors: ['RED'] },
            ],
            snapshotId: 'must-not-be-shared',
            generation: 8,
            form_data_key: 'must-not-be-shared',
          },
        },
        queryResult: ['must-not-be-shared'],
      },
      20: {
        id: '20',
        extraFormData: { time_range: 'Last week' },
        filterState: { value: 'Last week' },
      },
      999: {
        id: '999',
        extraFormData: { filters: [{ col: 'secret', op: '==', val: 'x' }] },
      },
      'CHART_CUSTOMIZATION-1': {
        id: 'CHART_CUSTOMIZATION-1',
        filterState: { value: ['not-shareable'] },
      },
    },
    activeTabs: ['TAB-valid-inner', 'TAB-valid', 'TAB-deleted', 'TAB-valid'],
    nativeFilterConfiguration: [
      { id: 'NATIVE_FILTER-1' },
      { id: 'NATIVE_FILTER-empty' },
    ],
    charts: [tableChart, { slice_id: 20, form_data: { viz_type: 'pie' } }],
    chartConfiguration: { 10: {}, 20: {} },
    layout: {
      'TAB-valid': {
        id: 'TAB-valid',
        type: TAB_TYPE,
        parents: ['ROOT_ID', 'GRID_ID', 'TABS-outer'],
      },
      'TAB-valid-inner': {
        id: 'TAB-valid-inner',
        type: TAB_TYPE,
        parents: [
          'ROOT_ID',
          'GRID_ID',
          'TABS-outer',
          'TAB-valid',
          'TABS-inner',
        ],
      },
      'CHART-10': {
        id: 'CHART-10',
        type: 'CHART',
        meta: { chartId: 10 },
      },
      'CHART-20': {
        id: 'CHART-20',
        type: 'CHART',
        meta: { chartId: 20 },
      },
    },
    anchor: 'CHART-10',
  });

  expect(result.state).toEqual({
    dataMask: {
      'NATIVE_FILTER-1': {
        id: 'NATIVE_FILTER-1',
        extraFormData: {
          filters: [{ col: 'region', op: 'IN', val: ['APAC'] }],
        },
        filterState: { value: ['APAC'], label: 'APAC' },
      },
      '10': {
        id: '10',
        extraFormData: {
          filters: [{ col: 'channel', op: 'IN', val: ['Online'] }],
        },
        filterState: { value: ['Online'] },
        ownState: {
          alertFilter: {
            version: 2,
            selections: [{ column: 'gross_revenue', colors: ['RED'] }],
          },
        },
      },
    },
    activeTabs: ['TAB-valid', 'TAB-valid-inner'],
  });
  expect(result.anchor).toBe('CHART-10');
  expect(result.dropped.invalidDataMasks).toBe(2);
  expect(result.dropped.invalidAlertFilters).toBe(2);
  expect(result.dropped.invalidTabs).toBe(2);
  expect(JSON.stringify(result.state)).not.toContain('must-not-be-shared');
});

test('retains Cell Bar filters but drops effective gradients and legacy rule references', () => {
  const invalidRulesChart = {
    ...tableChart,
    form_data: {
      ...tableChart.form_data,
      conditional_formatting: [
        { column: 'gross_revenue', operator: '<', targetValue: 0 },
        {
          ...tableChart.form_data.conditional_formatting[0],
          ruleId: RED_RULE_ID,
          objectFormatting: 'CELL_BAR',
        },
        {
          ...tableChart.form_data.conditional_formatting[1],
          ruleId: YELLOW_RULE_ID,
          objectFormatting: 'BACKGROUND_COLOR',
          useGradient: true,
        },
      ],
    },
  };

  const result = sanitizeShareableDashboardState({
    dataMask: {
      10: {
        id: '10',
        ownState: {
          alertFilters: [
            { ruleId: RED_RULE_ID, level: 'RED' },
            { ruleId: YELLOW_RULE_ID, level: 'YELLOW' },
          ],
          alertFilter: {
            version: 2,
            selections: [
              { column: 'gross_revenue', colors: ['RED'] },
              { column: 'quantity', colors: ['YELLOW'] },
            ],
          },
        },
      },
    },
    charts: [invalidRulesChart],
    layout: {
      'CHART-10': {
        id: 'CHART-10',
        type: 'CHART',
        meta: { chartId: 10 },
      },
    },
  });

  expect(result.state.dataMask).toEqual({
    10: {
      id: '10',
      ownState: {
        alertFilter: {
          version: 2,
          selections: [{ column: 'gross_revenue', colors: ['RED'] }],
        },
      },
    },
  });
  expect(result.dropped.invalidAlertFilters).toBe(3);
});

test('drops cross-filter state from a source outside effective chart configuration', () => {
  const result = sanitizeShareableDashboardState({
    dataMask: {
      20: {
        id: '20',
        extraFormData: {
          filters: [{ col: 'region', op: 'IN', val: ['APAC'] }],
          time_range: 'No filter',
        },
        filterState: { value: ['APAC'], transientSearch: 'not-shareable' },
      },
    },
    charts: [
      { slice_id: 20, form_data: { viz_type: 'non_interactive_test_chart' } },
    ],
    chartConfiguration: { 10: {} },
    layout: {
      'CHART-20': {
        id: 'CHART-20',
        type: 'CHART',
        meta: { chartId: 20 },
      },
    },
  });

  expect(result.state.dataMask).toEqual({});
  expect(result.dropped.invalidDataMasks).toBe(1);
});

test('keeps alert state but drops cross filters when dashboard cross filters are disabled', () => {
  const result = sanitizeShareableDashboardState({
    dataMask: {
      10: {
        id: '10',
        extraFormData: {
          filters: [{ col: 'region', op: 'IN', val: ['APAC'] }],
        },
        filterState: { value: ['APAC'] },
        ownState: {
          alertFilter: {
            version: 2,
            selections: [{ column: 'gross_revenue', colors: ['RED'] }],
          },
        },
      },
    },
    charts: [tableChart],
    chartConfiguration: { 10: {} },
    crossFiltersEnabled: false,
    layout: {
      'CHART-10': {
        id: 'CHART-10',
        type: 'CHART',
        meta: { chartId: 10 },
      },
    },
  });

  expect(result.state.dataMask).toEqual({
    10: {
      id: '10',
      ownState: {
        alertFilter: {
          version: 2,
          selections: [{ column: 'gross_revenue', colors: ['RED'] }],
        },
      },
    },
  });
  expect(result.dropped.invalidDataMasks).toBe(1);
});

test('diagnostic logging includes only phase and counters', () => {
  const infoSpy = jest.spyOn(logging, 'info').mockImplementation();
  const dropped = {
    invalidDataMasks: 2,
    invalidAlertFilters: 1,
    transientChartFields: 3,
    invalidTabs: 4,
    invalidAnchors: 1,
  };

  logDashboardStateDrops('restore', dropped);

  expect(infoSpy).toHaveBeenCalledWith('Dashboard permalink state sanitized', {
    phase: 'restore',
    ...dropped,
  });
  expect(JSON.stringify(infoSpy.mock.calls)).not.toContain('APAC');
  infoSpy.mockRestore();
});

test('shares all three colors independently of enabling rule palette and deduplicates by column', () => {
  const result = sanitizeShareableDashboardState({
    charts: [tableChart],
    layout: chartLayout,
    dataMask: {
      10: {
        ownState: {
          alertFilter: {
            version: 2,
            snapshotId: 'private-snapshot',
            generation: 7,
            selections: [
              {
                column: 'gross_revenue',
                colors: ['RED', 'GREEN'],
                ruleId: 'discard',
              },
              { column: 'gross_revenue', colors: ['YELLOW', 'RED'] },
              { column: 'quantity', colors: ['YELLOW'] },
            ],
          },
        },
      },
    },
  });

  expect(result.state.dataMask[10].ownState).toEqual({
    alertFilter: {
      version: 2,
      selections: [
        { column: 'gross_revenue', colors: ['GREEN', 'YELLOW', 'RED'] },
        { column: 'quantity', colors: ['YELLOW'] },
      ],
    },
  });
  expect(result.dropped.transientChartFields).toBe(3);
  expect(JSON.stringify(result.state)).not.toContain('private-snapshot');
});

test.each([
  undefined,
  null,
  [],
  { version: 1, selections: [{ column: 'gross_revenue', colors: ['RED'] }] },
  { version: 2, selections: null },
  { version: 2, selections: [] },
  { version: 2, selections: [{ column: 'gross_revenue', colors: [] }] },
  { version: 2, selections: [{ column: 'gross_revenue', colors: ['red'] }] },
  {
    version: 2,
    selections: [{ column: 'gross_revenue', colors: ['RED', '#ff0000'] }],
  },
])('does not share malformed or empty color state: %j', alertFilter => {
  const result = sanitizeShareableDashboardState({
    charts: [tableChart],
    layout: chartLayout,
    dataMask: { 10: { ownState: { alertFilter } } },
  });
  expect(result.state.dataMask).toEqual({});
});

test('does not share disabled, hidden, removed-source, or foreign-chart targets', () => {
  const makeChart = (formData: Record<string, unknown>) => ({
    slice_id: 10,
    form_data: { ...tableChart.form_data, ...formData },
  });
  const cases = [
    makeChart({ viz_type: 'pivot_table_v2' }),
    makeChart({ column_config: { gross_revenue: { visible: false } } }),
    makeChart({
      conditional_formatting: [
        {
          ...tableChart.form_data.conditional_formatting[0],
          filterable: false,
        },
      ],
    }),
    makeChart({
      conditional_formatting: [
        {
          ...tableChart.form_data.conditional_formatting[0],
          column: 'deleted',
          columnFormatting: 'gross_revenue',
        },
      ],
    }),
  ];
  cases.forEach(chart => {
    const result = sanitizeShareableDashboardState({
      charts: [chart],
      layout: chartLayout,
      dataMask: {
        10: {
          ownState: {
            alertFilter: {
              version: 2,
              selections: [{ column: 'gross_revenue', colors: ['RED'] }],
            },
          },
        },
      },
    });
    expect(result.state.dataMask).toEqual({});
    expect(result.dropped.invalidAlertFilters).toBe(1);
  });
});

test('shares raw string, adhoc, percent and comparison targets without subject references', () => {
  const examples = [
    {
      formData: { query_mode: 'raw', all_columns: ['status'] },
      column: 'status',
    },
    {
      formData: {
        metrics: [{ label: 'SUM(amount)', sqlExpression: 'SUM(amount)' }],
      },
      column: 'SUM(amount)',
    },
    { formData: { percent_metrics: ['amount'] }, column: '%amount' },
    {
      formData: { metrics: ['amount'], time_compare: ['1 year ago'] },
      column: '△ amount',
    },
  ];
  examples.forEach(({ formData, column }) => {
    const result = sanitizeShareableDashboardState({
      charts: [
        {
          slice_id: 10,
          form_data: {
            viz_type: 'table',
            ...formData,
            conditional_formatting: [
              {
                column,
                colorScheme: 'colorSuccess',
                operator: 'None',
                useGradient: false,
                filterable: true,
              },
            ],
          },
        },
      ],
      layout: chartLayout,
      dataMask: {
        10: {
          ownState: {
            alertFilter: {
              version: 2,
              selections: [{ column, colors: ['GREEN'] }],
            },
          },
        },
      },
    });
    expect(result.state.dataMask[10].ownState).toEqual({
      alertFilter: { version: 2, selections: [{ column, colors: ['GREEN'] }] },
    });
  });
});

test('a hidden numeric source gradient disables another enabled visible target', () => {
  const result = sanitizeShareableDashboardState({
    charts: [
      {
        ...tableChart,
        form_data: {
          ...tableChart.form_data,
          column_config: { quantity: { visible: false } },
          conditional_formatting: [
            tableChart.form_data.conditional_formatting[0],
            {
              ...tableChart.form_data.conditional_formatting[1],
              columnFormatting: 'gross_revenue',
              objectFormatting: 'BACKGROUND_COLOR',
              useGradient: true,
              filterable: false,
            },
          ],
        },
      },
    ],
    layout: chartLayout,
    dataMask: {
      10: {
        ownState: {
          alertFilter: {
            version: 2,
            selections: [{ column: 'gross_revenue', colors: ['RED'] }],
          },
        },
      },
    },
  });
  expect(result.state.dataMask).toEqual({});
  expect(result.dropped.invalidAlertFilters).toBe(1);
});

test('limits shared colors to 100 enabled targets without retaining runtime state', () => {
  const columns = Array.from({ length: 101 }, (_, index) => `column_${index}`);
  const result = sanitizeShareableDashboardState({
    charts: [
      {
        slice_id: 10,
        form_data: {
          viz_type: 'table',
          query_mode: 'raw',
          all_columns: columns,
          conditional_formatting: [
            {
              column: columns[0],
              columnFormatting: 'ENTIRE_ROW',
              operator: 'None',
              colorScheme: 'colorSuccess',
              useGradient: false,
              filterable: true,
            },
          ],
        },
      },
    ],
    layout: chartLayout,
    dataMask: {
      10: {
        ownState: {
          alertFilter: {
            version: 2,
            selections: columns.map(column => ({ column, colors: ['GREEN'] })),
          },
        },
      },
    },
  });
  expect(result.state.dataMask[10].ownState).toEqual({
    alertFilter: {
      version: 2,
      selections: columns
        .slice(0, 100)
        .map(column => ({ column, colors: ['GREEN'] })),
    },
  });
  expect(result.dropped.invalidAlertFilters).toBe(1);
});
