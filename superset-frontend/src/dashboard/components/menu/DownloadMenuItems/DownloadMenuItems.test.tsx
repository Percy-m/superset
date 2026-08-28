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
import React from 'react';
import {
  act,
  createStore,
  render,
  screen,
  userEvent,
  waitFor,
} from 'spec/helpers/testing-library';
import { Menu, MenuItem } from '@superset-ui/core/components/Menu';
import {
  DataMaskStateWithId,
  FeatureFlag,
  SupersetClient,
  TableColorSelection,
} from '@superset-ui/core';
import { DashboardLayout } from 'src/dashboard/types';
import { DASHBOARD_ROOT_ID } from 'src/dashboard/util/constants';
import {
  CHART_TYPE,
  TAB_TYPE,
  TABS_TYPE,
} from 'src/dashboard/util/componentTypes';
import {
  DashboardTabXlsxExport,
  getDashboardColorExportState,
} from './DashboardTabXlsxExport';
import { useDownloadMenuItems } from '.';

const mockAddSuccessToast = jest.fn();
const mockAddDangerToast = jest.fn();

jest.mock('src/components/MessageToasts/withToasts', () => ({
  __esModule: true,
  default: (Component: React.ComponentType) => Component,
  useToasts: () => ({
    addSuccessToast: mockAddSuccessToast,
    addDangerToast: mockAddDangerToast,
  }),
}));

jest.mock('@superset-ui/core', () => ({
  ...jest.requireActual('@superset-ui/core'),
  SupersetClient: {
    get: jest.fn(),
    post: jest.fn(),
  },
}));

const mockSupersetClient = SupersetClient as jest.Mocked<typeof SupersetClient>;

const createProps = () => ({
  pdfMenuItemTitle: 'Export to PDF',
  imageMenuItemTitle: 'Download as Image',
  dashboardTitle: 'Test Dashboard',
  logEvent: jest.fn(),
  dashboardId: 123,
  title: 'Download',
  submenuKey: 'download',
  userCanExport: true,
});

const MenuWrapper = () => {
  const downloadMenuItem = useDownloadMenuItems(createProps());
  const menuItems: MenuItem[] = [downloadMenuItem];
  return <Menu forceSubMenuRender items={menuItems} />;
};

const originalCreateObjectURL = window.URL.createObjectURL;
const originalRevokeObjectURL = window.URL.revokeObjectURL;

const colorLayout: DashboardLayout = {
  'TAB-a': {
    id: 'TAB-a',
    type: TAB_TYPE,
    meta: {},
    children: ['CHART-3', 'TAB-child'],
  },
  'TAB-child': {
    id: 'TAB-child',
    type: TAB_TYPE,
    meta: {},
    children: ['CHART-3'],
  },
  'CHART-3': {
    id: 'CHART-3',
    type: CHART_TYPE,
    meta: { chartId: 3 },
    children: [],
  },
  'CHART-4': {
    id: 'CHART-4',
    type: CHART_TYPE,
    meta: { chartId: 4 },
    children: [],
  },
};
const colorMask: DataMaskStateWithId = {
  3: {
    id: '3',
    ownState: {
      alertFilter: {
        version: 2,
        selections: [{ column: 'profit', colors: ['GREEN'] }],
        snapshotId: 'snapshot-3',
        generation: 'generation-3',
      },
    },
  },
};
const colorCharts = {
  3: {
    queriesResponse: [
      {
        table_color_metadata: {
          status: 'ready',
          snapshot_id: 'snapshot-3',
          generation: 'generation-3',
          theme_mode: 'dark',
          selections: [{ column: 'profit', colors: ['GREEN'] }],
        },
      },
    ],
  },
  4: {
    queriesResponse: [
      {
        table_color_metadata: {
          status: 'ready',
          snapshot_id: 'snapshot-4',
          generation: 'generation-4',
        },
      },
    ],
  },
};

test('Tab export sends selected snapshots separately and does not mutate the data mask', () => {
  window.featureFlags = { [FeatureFlag.TableAlertFilters]: true };
  const exported = getDashboardColorExportState(
    colorLayout,
    ['TAB-a', 'TAB-child'],
    colorMask,
    colorCharts,
  );
  expect(exported.colorSnapshots).toEqual({
    3: {
      snapshot_id: 'snapshot-3',
      generation: 'generation-3',
      theme_mode: 'dark',
    },
  });
  expect(exported.dataMask[3].ownState?.alertFilter).toEqual({
    version: 2,
    selections: [{ column: 'profit', colors: ['GREEN'] }],
  });
  expect(colorMask[3].ownState?.alertFilter.snapshotId).toBe('snapshot-3');
  expect(JSON.stringify(exported.dataMask)).not.toContain('snapshot-3');
});

test('Tab export refuses an uncompleted or unprepared color selection', () => {
  window.featureFlags = { [FeatureFlag.TableAlertFilters]: true };
  expect(() =>
    getDashboardColorExportState(colorLayout, ['TAB-a'], colorMask, {}),
  ).toThrow('Load the selected color-filtered table');
  const pendingMask = {
    3: {
      id: '3',
      ownState: {
        alertFilter: {
          version: 2,
          selections: [{ column: 'profit', colors: ['RED'] }],
        },
      },
    },
  };
  expect(() =>
    getDashboardColorExportState(
      colorLayout,
      ['TAB-a'],
      pendingMask,
      colorCharts,
    ),
  ).toThrow('Load the selected color-filtered table');
});

test.each<{
  name: string;
  selections: TableColorSelection[];
  accepted: boolean;
}>([
  {
    name: 'reversed columns',
    selections: [
      { column: 'gross_profit', colors: ['GREEN', 'YELLOW'] },
      { column: 'error_count', colors: ['RED'] },
    ],
    accepted: true,
  },
  {
    name: 'reversed colors',
    selections: [
      { column: 'error_count', colors: ['RED'] },
      { column: 'gross_profit', colors: ['YELLOW', 'GREEN'] },
    ],
    accepted: true,
  },
  {
    name: 'reversed columns and colors',
    selections: [
      { column: 'gross_profit', colors: ['YELLOW', 'GREEN'] },
      { column: 'error_count', colors: ['RED'] },
    ],
    accepted: true,
  },
  {
    name: 'a different color',
    selections: [
      { column: 'gross_profit', colors: ['GREEN', 'RED'] },
      { column: 'error_count', colors: ['RED'] },
    ],
    accepted: false,
  },
  {
    name: 'a missing column',
    selections: [{ column: 'gross_profit', colors: ['GREEN', 'YELLOW'] }],
    accepted: false,
  },
  { name: 'a pending clear', selections: [], accepted: false },
])('Tab export handles $name', ({ selections, accepted }) => {
  window.featureFlags = { [FeatureFlag.TableAlertFilters]: true };
  const mask: DataMaskStateWithId = {
    3: {
      ...colorMask[3],
      ownState: { alertFilter: { version: 2, selections } },
    },
  };
  const charts = {
    3: {
      queriesResponse: [
        {
          table_color_metadata: {
            ...colorCharts[3].queriesResponse[0].table_color_metadata,
            selections: [
              { column: 'error_count', colors: ['RED'] },
              { column: 'gross_profit', colors: ['GREEN', 'YELLOW'] },
            ],
          },
        },
      ],
    },
  };
  const getState = () =>
    getDashboardColorExportState(colorLayout, ['TAB-a'], mask, charts);
  if (accepted) {
    expect(getState().colorSnapshots?.[3].snapshot_id).toBe('snapshot-3');
    expect(getState().dataMask[3].ownState?.alertFilter.selections).toEqual(
      selections,
    );
  } else {
    expect(getState).toThrow('Load the selected color-filtered table');
  }
});

test.each<DataMaskStateWithId>([{ 3: { id: '3', ownState: {} } }, {}])(
  'Tab export rejects a removed filter while its response still contains colors: %j',
  mask => {
    window.featureFlags = { [FeatureFlag.TableAlertFilters]: true };
    expect(() =>
      getDashboardColorExportState(colorLayout, ['TAB-a'], mask, colorCharts),
    ).toThrow('Load the selected color-filtered table');
  },
);

test('Tab export with the flag off has no new snapshot parameters', () => {
  window.featureFlags = {};
  expect(
    getDashboardColorExportState(
      colorLayout,
      ['TAB-a'],
      colorMask,
      colorCharts,
    ),
  ).toEqual({ dataMask: colorMask });
});

beforeEach(() => {
  jest.clearAllMocks();
  window.featureFlags = {};
});

afterEach(() => {
  window.URL.createObjectURL = originalCreateObjectURL;
  window.URL.revokeObjectURL = originalRevokeObjectURL;
  window.featureFlags = {};
});

test('Should render all menu items', () => {
  render(<MenuWrapper />, {
    useRedux: true,
  });

  // Screenshot options
  expect(screen.getByText('Export to PDF')).toBeInTheDocument();
  expect(screen.getByText('Download as Image')).toBeInTheDocument();

  // Export options
  expect(screen.getByText('Export YAML')).toBeInTheDocument();
  expect(screen.getByText('Export as Example')).toBeInTheDocument();
});

test('Export as Example calls SupersetClient.get with correct endpoint', async () => {
  const mockBlob = new Blob(['test'], { type: 'application/zip' });
  const mockResponse: Pick<Response, 'blob' | 'headers'> = {
    blob: jest.fn().mockResolvedValue(mockBlob),
    headers: new Headers({
      'Content-Disposition': 'attachment; filename="dashboard_123_example.zip"',
    }),
  };
  mockSupersetClient.get.mockResolvedValue(mockResponse as unknown as Response);

  // Mock URL.createObjectURL / revokeObjectURL since jsdom doesn't support them
  const createObjectURL = jest.fn(() => 'blob:http://localhost/fake');
  const revokeObjectURL = jest.fn();
  window.URL.createObjectURL = createObjectURL;
  window.URL.revokeObjectURL = revokeObjectURL;

  render(<MenuWrapper />, { useRedux: true });

  await userEvent.click(screen.getByText('Export as Example'));

  await waitFor(() => {
    expect(mockSupersetClient.get).toHaveBeenCalledWith({
      endpoint: '/api/v1/dashboard/123/export_as_example/',
      headers: { Accept: 'application/zip' },
      parseMethod: 'raw',
    });
    expect(mockAddSuccessToast).toHaveBeenCalledWith(
      'Dashboard exported as example successfully',
    );
  });
});

test('Export as Example shows error toast on failure', async () => {
  mockSupersetClient.get.mockRejectedValue(new Error('Network error'));

  render(<MenuWrapper />, { useRedux: true });

  await userEvent.click(screen.getByText('Export as Example'));

  await waitFor(() => {
    expect(mockAddDangerToast).toHaveBeenCalledWith(
      'Sorry, something went wrong. Try again later.',
    );
  });
});

test('styled Tab XLSX export posts selected tabs and dashboard state', async () => {
  window.featureFlags = {
    [FeatureFlag.StyledXlsxExport]: true,
    [FeatureFlag.DashboardTabXlsxExport]: true,
  };
  const mockBlob = new Blob(['xlsx'], {
    type: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
  });
  mockSupersetClient.post.mockResolvedValue({
    blob: jest.fn().mockResolvedValue(mockBlob),
    headers: new Headers({
      'Content-Disposition': 'attachment; filename="dashboard_123.xlsx"',
    }),
  } as unknown as Response);
  const createObjectURL = jest.fn(() => 'blob:http://localhost/xlsx');
  const revokeObjectURL = jest.fn();
  window.URL.createObjectURL = createObjectURL;
  window.URL.revokeObjectURL = revokeObjectURL;

  render(<MenuWrapper />, {
    useRedux: true,
    initialState: {
      dashboardLayout: {
        past: [],
        future: [],
        present: {
          [DASHBOARD_ROOT_ID]: {
            id: DASHBOARD_ROOT_ID,
            type: 'ROOT',
            meta: {},
            children: ['TABS-main'],
          },
          'TABS-main': {
            id: 'TABS-main',
            type: TABS_TYPE,
            meta: {},
            children: ['TAB-a', 'TAB-b'],
          },
          'TAB-a': {
            id: 'TAB-a',
            type: TAB_TYPE,
            meta: { text: 'Quality Overview' },
            children: [],
          },
          'TAB-b': {
            id: 'TAB-b',
            type: TAB_TYPE,
            meta: { text: 'Exceptions' },
            children: [],
          },
        },
      },
      dashboardState: { activeTabs: ['TAB-a'] },
      dataMask: { 3: { id: '3', ownState: { alertFilters: [] } } },
    },
  });

  await userEvent.click(screen.getByText('Export tabs to Excel'));
  expect(
    screen.getByText('Export dashboard tabs to Excel'),
  ).toBeInTheDocument();
  expect(screen.getByLabelText('Quality Overview')).toBeChecked();
  await userEvent.click(screen.getByLabelText('Exceptions'));
  await userEvent.click(screen.getByRole('button', { name: 'Export' }));

  await waitFor(() => {
    expect(mockSupersetClient.post).toHaveBeenCalledWith({
      endpoint: '/api/v1/dashboard/123/export_xlsx/',
      headers: {
        Accept:
          'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({
        tabIds: ['TAB-a', 'TAB-b'],
        dataMask: { 3: { id: '3', ownState: { alertFilters: [] } } },
      }),
      parseMethod: 'raw',
    });
    expect(mockAddSuccessToast).toHaveBeenCalledWith(
      'Dashboard tabs exported successfully',
    );
  });
  expect(createObjectURL).toHaveBeenCalledWith(mockBlob);
  expect(revokeObjectURL).toHaveBeenCalledWith('blob:http://localhost/xlsx');
});

test('XLSX export selects the entire dashboard when layout has no tabs', async () => {
  const mockBlob = new Blob(['xlsx'], {
    type: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
  });
  mockSupersetClient.post.mockResolvedValue({
    blob: jest.fn().mockResolvedValue(mockBlob),
    headers: new Headers(),
  } as unknown as Response);
  window.URL.createObjectURL = jest.fn(() => 'blob:http://localhost/xlsx');
  window.URL.revokeObjectURL = jest.fn();

  render(<DashboardTabXlsxExport dashboardId={1} />, {
    useRedux: true,
    initialState: {
      dashboardLayout: {
        past: [],
        future: [],
        present: {
          [DASHBOARD_ROOT_ID]: {
            id: DASHBOARD_ROOT_ID,
            type: 'ROOT',
            meta: {},
            children: ['GRID_ID'],
          },
          GRID_ID: {
            id: 'GRID_ID',
            type: 'GRID',
            meta: {},
            children: ['ROW-FR01'],
          },
          'ROW-FR01': {
            id: 'ROW-FR01',
            type: 'ROW',
            meta: {},
            children: ['CHART-FR01'],
          },
          'CHART-FR01': {
            id: 'CHART-FR01',
            type: 'CHART',
            meta: {
              chartId: 1,
              sliceName: 'FR-01 ClickHouse Drill Detail',
            },
            children: [],
          },
        },
      },
      dashboardState: { activeTabs: [] },
      dataMask: {},
    },
  });

  await userEvent.click(screen.getByText('Export tabs to Excel'));

  expect(screen.getByLabelText('Entire dashboard')).toBeChecked();
  expect(screen.getByRole('button', { name: 'Export' })).toBeEnabled();
  await userEvent.click(screen.getByRole('button', { name: 'Export' }));

  await waitFor(() => {
    expect(mockSupersetClient.post).toHaveBeenCalledWith(
      expect.objectContaining({
        endpoint: '/api/v1/dashboard/1/export_xlsx/',
        body: JSON.stringify({ tabIds: [DASHBOARD_ROOT_ID], dataMask: {} }),
      }),
    );
  });
});

test('Tab XLSX export initializes nested tabs after layout hydration', async () => {
  const emptyLayout = { past: [], future: [], present: {} };
  const nestedLayout = {
    [DASHBOARD_ROOT_ID]: {
      id: DASHBOARD_ROOT_ID,
      type: 'ROOT',
      meta: {},
      children: ['TABS-outer'],
    },
    'TABS-outer': {
      id: 'TABS-outer',
      type: TABS_TYPE,
      meta: {},
      children: ['TAB-outer', 'TAB-other'],
    },
    'TAB-outer': {
      id: 'TAB-outer',
      type: TAB_TYPE,
      meta: { text: 'Outer' },
      children: ['TABS-inner'],
    },
    'TABS-inner': {
      id: 'TABS-inner',
      type: TABS_TYPE,
      meta: {},
      children: ['TAB-inner'],
    },
    'TAB-inner': {
      id: 'TAB-inner',
      type: TAB_TYPE,
      meta: { text: 'Inner' },
      children: [],
    },
    'TAB-other': {
      id: 'TAB-other',
      type: TAB_TYPE,
      meta: { text: 'Other' },
      children: [],
    },
  };
  const dashboardLayout = (
    state = emptyLayout,
    action: { type: string; payload?: typeof nestedLayout },
  ) =>
    action.type === 'test/layoutHydrated'
      ? { ...state, present: action.payload ?? {} }
      : state;
  const store = createStore(
    {
      dashboardLayout: emptyLayout,
      dashboardState: { activeTabs: ['TAB-inner'] },
      dataMask: {},
    },
    {
      dashboardLayout,
      dashboardState: (state = { activeTabs: ['TAB-inner'] }) => state,
      dataMask: (state = {}) => state,
    },
  );

  render(<DashboardTabXlsxExport dashboardId={123} />, { store });
  await userEvent.click(screen.getByText('Export tabs to Excel'));

  expect(screen.getByRole('button', { name: 'Export' })).toBeDisabled();

  act(() => {
    store.dispatch({ type: 'test/layoutHydrated', payload: nestedLayout });
  });

  await waitFor(() => {
    expect(screen.getByLabelText('Inner')).toBeChecked();
    expect(screen.getByRole('button', { name: 'Export' })).toBeEnabled();
  });
  const orderedCheckboxes = screen.getAllByRole('checkbox');
  expect(orderedCheckboxes).toEqual([
    screen.getByLabelText('Outer'),
    screen.getByLabelText('Inner'),
    screen.getByLabelText('Other'),
  ]);

  await userEvent.click(screen.getByLabelText('Inner'));
  expect(screen.getByRole('button', { name: 'Export' })).toBeDisabled();
  await userEvent.click(screen.getByLabelText('Outer'));
  expect(screen.getByRole('button', { name: 'Export' })).toBeEnabled();
});
