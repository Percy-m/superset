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

import { Menu, MenuItem } from '@superset-ui/core/components/Menu';
import {
  render,
  screen,
  userEvent,
  waitFor,
} from 'spec/helpers/testing-library';
import * as copyTextToClipboard from 'src/utils/copy';
import fetchMock from 'fetch-mock';
import { ComponentProps } from 'react';
import { FeatureFlag } from '@superset-ui/core';
import { TAB_TYPE } from 'src/dashboard/util/componentTypes';
import { useShareMenuItems, ShareMenuItemProps } from '.';

const spy = jest.spyOn(copyTextToClipboard, 'default');

const DASHBOARD_ID = '26';
const createProps = () => ({
  addDangerToast: jest.fn(),
  addSuccessToast: jest.fn(),
  url: `/superset/dashboard/${DASHBOARD_ID}`,
  copyMenuItemTitle: 'Copy dashboard URL',
  emailMenuItemTitle: 'Share dashboard by email',
  emailSubject: 'Superset dashboard COVID Vaccine Dashboard',
  emailBody: 'Check out this dashboard: ',
  dashboardId: DASHBOARD_ID,
  title: 'Test Dashboard',
  submenuKey: 'share',
});

const originalLocation = window.location;

const postDashboardPermalinkMockUrl = `http://localhost/api/v1/dashboard/${DASHBOARD_ID}/permalink`;

beforeEach(() => {
  jest.clearAllMocks();
  // @ts-expect-error
  delete window.location;
  window.location = { href: '' } as any;
  fetchMock.clearHistory().removeRoutes();
  fetchMock.post(
    postDashboardPermalinkMockUrl,
    { key: '123', url: 'http://localhost/superset/dashboard/p/123/' },
    { name: postDashboardPermalinkMockUrl },
  );
});

afterEach(() => {
  window.location = originalLocation;
  window.featureFlags = {};
  fetchMock.clearHistory().removeRoutes();
});

const MenuWrapper = (
  props: ComponentProps<typeof Menu> & { shareProps: ShareMenuItemProps },
) => {
  const shareMenuItems = useShareMenuItems(props.shareProps);
  const menuItems: MenuItem[] = [shareMenuItems];
  return <Menu {...props} items={menuItems} />;
};

test('Should render menu items', () => {
  render(
    <MenuWrapper
      onClick={jest.fn()}
      selectable={false}
      data-test="main-menu"
      forceSubMenuRender
      shareProps={createProps()}
    />,
    { useRedux: true },
  );
  expect(screen.getByText('Copy dashboard URL')).toBeInTheDocument();
  expect(screen.getByText('Share dashboard by email')).toBeInTheDocument();
});

test('Click on "Copy dashboard URL" and succeed', async () => {
  spy.mockResolvedValue(undefined);
  const props = createProps();
  render(
    <MenuWrapper
      onClick={jest.fn()}
      selectable={false}
      data-test="main-menu"
      forceSubMenuRender
      shareProps={props}
    />,
    { useRedux: true },
  );

  await waitFor(() => {
    expect(spy).toHaveBeenCalledTimes(0);
    expect(props.addSuccessToast).toHaveBeenCalledTimes(0);
    expect(props.addDangerToast).toHaveBeenCalledTimes(0);
  });

  await userEvent.click(screen.getByText('Copy dashboard URL'));

  await waitFor(async () => {
    expect(spy).toHaveBeenCalledTimes(1);
    const value = await spy.mock.calls[0][0]();
    expect(value).toBe('http://localhost/superset/dashboard/p/123/');
    const body = JSON.parse(
      fetchMock.callHistory.calls(postDashboardPermalinkMockUrl)[0].options
        .body as string,
    );
    expect(body).toHaveProperty('urlParams');
    expect(props.addSuccessToast).toHaveBeenCalledTimes(1);
    expect(props.addSuccessToast).toHaveBeenCalledWith('Copied to clipboard!');
    expect(props.addDangerToast).toHaveBeenCalledTimes(0);
  });
});

test('enabled permalink sharing posts only sanitized dashboard state', async () => {
  window.featureFlags = {
    [FeatureFlag.DashboardCrossFilterPermalink]: true,
  };
  spy.mockResolvedValue(undefined);
  render(
    <MenuWrapper
      onClick={jest.fn()}
      selectable={false}
      data-test="main-menu"
      forceSubMenuRender
      shareProps={createProps()}
    />,
    {
      useRedux: true,
      initialState: {
        dataMask: {
          10: {
            id: '10',
            ownState: {
              currentPage: 7,
              searchText: 'must-not-be-shared',
              alertFilters: [
                {
                  ruleId: '772a548e-72f7-4ac8-a8ff-fdb7465b3ccd',
                  level: 'RED',
                },
              ],
            },
          },
        },
        dashboardState: {
          activeTabs: ['TAB-valid', 'TAB-deleted'],
          chartStates: { 10: { state: { rows: ['must-not-be-shared'] } } },
          sliceIds: [10],
        },
        dashboardInfo: {
          crossFiltersEnabled: true,
          metadata: { chart_configuration: { 10: {} } },
        },
        sliceEntities: {
          slices: {
            10: {
              slice_id: 10,
              form_data: {
                slice_id: 10,
                viz_type: 'table',
                groupby: ['quantity'],
                metrics: ['gross_revenue'],
                conditional_formatting: [
                  {
                    ruleId: '772a548e-72f7-4ac8-a8ff-fdb7465b3ccd',
                    subjectRef: {
                      kind: 'saved_metric',
                      key: 'gross_revenue',
                    },
                    alertLevel: 'RED',
                    filterable: true,
                    column: 'gross_revenue',
                    operator: '<',
                    targetValue: 0,
                    useGradient: false,
                  },
                ],
              },
            },
          },
        },
        nativeFilters: { filters: {} },
        dashboardLayout: {
          present: {
            'TAB-valid': { id: 'TAB-valid', type: TAB_TYPE },
            'CHART-10': {
              id: 'CHART-10',
              type: 'CHART',
              meta: { chartId: 10 },
            },
          },
          past: [],
          future: [],
        },
      },
    },
  );

  await userEvent.click(screen.getByText('Copy dashboard URL'));
  await waitFor(() => expect(spy).toHaveBeenCalledTimes(1));
  await spy.mock.calls[0][0]();
  await waitFor(() =>
    expect(
      fetchMock.callHistory.calls(postDashboardPermalinkMockUrl),
    ).toHaveLength(1),
  );
  const body = JSON.parse(
    fetchMock.callHistory.calls(postDashboardPermalinkMockUrl)[0].options
      .body as string,
  );

  expect(body).toEqual({
    activeTabs: ['TAB-valid'],
    dataMask: {
      10: {
        id: '10',
        ownState: {
          alertFilters: [
            {
              ruleId: '772a548e-72f7-4ac8-a8ff-fdb7465b3ccd',
              level: 'RED',
            },
          ],
        },
      },
    },
  });
  expect(JSON.stringify(body)).not.toContain('must-not-be-shared');
  expect(body).not.toHaveProperty('chartStates');
  expect(body).not.toHaveProperty('urlParams');
});

test('Click on "Copy dashboard URL" and fail', async () => {
  spy.mockRejectedValue(undefined);
  const props = createProps();
  render(
    <MenuWrapper
      onClick={jest.fn()}
      selectable={false}
      data-test="main-menu"
      forceSubMenuRender
      shareProps={props}
    />,
    { useRedux: true },
  );

  await waitFor(() => {
    expect(spy).toHaveBeenCalledTimes(0);
    expect(props.addSuccessToast).toHaveBeenCalledTimes(0);
    expect(props.addDangerToast).toHaveBeenCalledTimes(0);
  });

  await userEvent.click(screen.getByText('Copy dashboard URL'));

  await waitFor(async () => {
    expect(spy).toHaveBeenCalledTimes(1);
    const value = await spy.mock.calls[0][0]();
    expect(value).toBe('http://localhost/superset/dashboard/p/123/');
    expect(props.addSuccessToast).toHaveBeenCalledTimes(0);
    expect(props.addDangerToast).toHaveBeenCalledTimes(1);
    expect(props.addDangerToast).toHaveBeenCalledWith(
      'Sorry, something went wrong. Try again later.',
    );
  });
});

test('Click on "Share dashboard by email" and succeed', async () => {
  const props = createProps();
  render(
    <MenuWrapper
      onClick={jest.fn()}
      selectable={false}
      data-test="main-menu"
      forceSubMenuRender
      shareProps={props}
    />,
    { useRedux: true },
  );

  await waitFor(() => {
    expect(props.addDangerToast).toHaveBeenCalledTimes(0);
    expect(window.location.href).toBe('');
  });

  await userEvent.click(screen.getByText('Share dashboard by email'));

  await waitFor(() => {
    expect(props.addDangerToast).toHaveBeenCalledTimes(0);
    expect(window.location.href).toBe(
      'mailto:?Subject=Superset%20dashboard%20COVID%20Vaccine%20Dashboard%20&Body=Check%20out%20this%20dashboard%3A%20http%3A%2F%2Flocalhost%2Fsuperset%2Fdashboard%2Fp%2F123%2F',
    );
  });
});

test('Click on "Share dashboard by email" and fail', async () => {
  fetchMock.removeRoute(postDashboardPermalinkMockUrl);
  fetchMock.post(postDashboardPermalinkMockUrl, { status: 404 });
  const props = createProps();
  render(
    <MenuWrapper
      onClick={jest.fn()}
      selectable={false}
      data-test="main-menu"
      forceSubMenuRender
      shareProps={props}
    />,
    { useRedux: true },
  );

  await waitFor(() => {
    expect(props.addDangerToast).toHaveBeenCalledTimes(0);
    expect(window.location.href).toBe('');
  });

  await userEvent.click(screen.getByText('Share dashboard by email'));

  await waitFor(() => {
    expect(window.location.href).toBe('');
    expect(props.addDangerToast).toHaveBeenCalledTimes(1);
    expect(props.addDangerToast).toHaveBeenCalledWith(
      'Sorry, something went wrong. Try again later.',
    );
  });
});

test('Should show "Embed code" menu item when feature flag is enabled and chart has data', () => {
  window.featureFlags = {
    EMBEDDABLE_CHARTS: true,
  };
  const props = createProps();
  const propsWithFormData = {
    ...props,
    latestQueryFormData: {
      datasource: '1__table',
      viz_type: 'table',
    },
  };
  render(
    <MenuWrapper
      onClick={jest.fn()}
      selectable={false}
      data-test="main-menu"
      forceSubMenuRender
      shareProps={propsWithFormData}
    />,
    { useRedux: true },
  );
  expect(screen.getByText('Embed code')).toBeInTheDocument();
});

test('Should NOT show "Embed code" when feature flag is disabled', () => {
  window.featureFlags = {
    EMBEDDABLE_CHARTS: false,
  };
  const props = createProps();
  const propsWithFormData = {
    ...props,
    latestQueryFormData: {
      datasource: '1__table',
      viz_type: 'table',
    },
  };
  render(
    <MenuWrapper
      onClick={jest.fn()}
      selectable={false}
      data-test="main-menu"
      forceSubMenuRender
      shareProps={propsWithFormData}
    />,
    { useRedux: true },
  );
  expect(screen.queryByText('Embed code')).not.toBeInTheDocument();
});

test('Should NOT show "Embed code" when chart has no data', () => {
  window.featureFlags = {
    EMBEDDABLE_CHARTS: true,
  };
  const props = createProps();
  render(
    <MenuWrapper
      onClick={jest.fn()}
      selectable={false}
      data-test="main-menu"
      forceSubMenuRender
      shareProps={props}
    />,
    { useRedux: true },
  );
  expect(screen.queryByText('Embed code')).not.toBeInTheDocument();
});

test('Should NOT show "Embed code" when latestQueryFormData is empty object', () => {
  window.featureFlags = {
    EMBEDDABLE_CHARTS: true,
  };
  const props = createProps();
  const propsWithEmptyFormData = {
    ...props,
    latestQueryFormData: {},
  };
  render(
    <MenuWrapper
      onClick={jest.fn()}
      selectable={false}
      data-test="main-menu"
      forceSubMenuRender
      shareProps={propsWithEmptyFormData}
    />,
    { useRedux: true },
  );
  expect(screen.queryByText('Embed code')).not.toBeInTheDocument();
});

test('Should render "Embed code" with data-test attribute', () => {
  window.featureFlags = {
    EMBEDDABLE_CHARTS: true,
  };
  const props = createProps();
  const propsWithFormData = {
    ...props,
    latestQueryFormData: {
      datasource: '1__table',
      viz_type: 'table',
    },
  };
  render(
    <MenuWrapper
      onClick={jest.fn()}
      selectable={false}
      data-test="main-menu"
      forceSubMenuRender
      shareProps={propsWithFormData}
    />,
    { useRedux: true },
  );
  expect(screen.getByTestId('embed-code-button')).toBeInTheDocument();
});
