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
        subjectRef: { kind: 'saved_metric', key: 'gross_revenue' },
        alertLevel: 'RED',
        filterable: true,
        column: 'gross_revenue',
        operator: '<',
        targetValue: 0,
        useGradient: false,
        objectFormatting: 'BACKGROUND_COLOR',
      },
      {
        ruleId: YELLOW_RULE_ID,
        subjectRef: { kind: 'physical_column', key: 'quantity' },
        alertLevel: 'YELLOW',
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
          alertFilters: [
            { ruleId: RED_RULE_ID.toUpperCase(), level: 'RED' },
            { ruleId: RED_RULE_ID, level: 'GREEN' },
            { ruleId: RED_RULE_ID, level: 'RED' },
          ],
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
          alertFilters: [{ ruleId: RED_RULE_ID, level: 'RED' }],
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

test('drops legacy, Cell Bar, gradient, and stale alert rules', () => {
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

  expect(result.state.dataMask).toEqual({});
  expect(result.dropped.invalidAlertFilters).toBe(2);
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
          alertFilters: [{ ruleId: RED_RULE_ID, level: 'RED' }],
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
        alertFilters: [{ ruleId: RED_RULE_ID, level: 'RED' }],
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
