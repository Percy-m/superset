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
import { MouseEvent, useEffect, useMemo, useState } from 'react';
import { logging } from '@apache-superset/core/utils';
import { t } from '@apache-superset/core/translation';
import {
  DataMaskStateWithId,
  FeatureFlag,
  isFeatureEnabled,
  QueryData,
  SupersetClient,
  TableColorFilterState,
  TableColorMetadata,
} from '@superset-ui/core';
import { Checkbox, Flex, Modal } from '@superset-ui/core/components';
import contentDisposition from 'content-disposition';
import { useSelector } from 'react-redux';
import { useToasts } from 'src/components/MessageToasts/withToasts';
import { areTableColorSelectionsEqual } from 'src/components/Chart/tableColorFilterRequest';
import { DashboardLayout, RootState } from 'src/dashboard/types';
import { DASHBOARD_ROOT_ID } from 'src/dashboard/util/constants';
import { CHART_TYPE, TAB_TYPE } from 'src/dashboard/util/componentTypes';

type ColorSnapshots = Record<
  string,
  { snapshot_id: string; generation: string; theme_mode: 'default' | 'dark' }
>;

/** Keep permission-bound tokens ephemeral and scoped to the selected layout. */
export const getDashboardColorExportState = (
  layout: DashboardLayout,
  tabIds: string[],
  dataMask: DataMaskStateWithId,
  charts: Record<string, { queriesResponse?: QueryData[] | null }>,
): { dataMask: DataMaskStateWithId; colorSnapshots?: ColorSnapshots } => {
  if (!isFeatureEnabled(FeatureFlag.TableAlertFilters)) return { dataMask };
  const sanitized = Object.fromEntries(
    Object.entries(dataMask).map(([id, mask]) => {
      const filter = mask.ownState?.alertFilter as
        | TableColorFilterState
        | undefined;
      if (filter?.version !== 2 && !mask.ownState?.clientView)
        return [id, mask];
      const ownState = { ...mask.ownState };
      delete ownState.clientView;
      if (filter?.version === 2)
        ownState.alertFilter = { version: 2, selections: filter.selections };
      return [
        id,
        {
          ...mask,
          ownState,
        },
      ];
    }),
  );
  const snapshots: ColorSnapshots = {};
  const visited = new Set<string>();
  const visit = (id: string) => {
    if (visited.has(id)) return;
    visited.add(id);
    const component = layout[id];
    if (!component) return;
    if (component.type === CHART_TYPE && component.meta?.chartId) {
      const chartId = String(component.meta.chartId);
      const filter = dataMask[chartId]?.ownState?.alertFilter as
        | TableColorFilterState
        | undefined;
      const metadata = charts[chartId]?.queriesResponse?.[0]
        ?.table_color_metadata as TableColorMetadata | undefined;
      const selections = filter?.version === 2 ? filter.selections : [];
      if (
        metadata?.status === 'ready'
          ? !areTableColorSelectionsEqual(selections, metadata.selections)
          : selections.length > 0
      ) {
        throw new Error(
          'Load the selected color-filtered table before exporting.',
        );
      }
      if (metadata?.status === 'ready') {
        snapshots[chartId] = {
          snapshot_id: metadata.snapshot_id,
          generation: metadata.generation,
          theme_mode: metadata.theme_mode ?? 'default',
        };
      }
    }
    component.children?.forEach(visit);
  };
  tabIds.forEach(visit);
  return {
    dataMask: sanitized,
    ...(Object.keys(snapshots).length ? { colorSnapshots: snapshots } : {}),
  };
};

type DashboardTabOption = {
  id: string;
  label: string;
};

const getOrderedDashboardTabs = (
  layout: DashboardLayout,
): DashboardTabOption[] => {
  const options: DashboardTabOption[] = [];
  const visited = new Set<string>();

  const visit = (componentId: string) => {
    if (visited.has(componentId)) return;
    visited.add(componentId);
    const component = layout[componentId];
    if (!component) return;
    if (component.type === TAB_TYPE) {
      options.push({
        id: component.id,
        label: component.meta?.text || t('Untitled tab'),
      });
    }
    component.children?.forEach(visit);
  };

  if (layout[DASHBOARD_ROOT_ID]) visit(DASHBOARD_ROOT_ID);
  Object.values(layout).forEach(component => visit(component.id));
  if (options.length === 0 && layout[DASHBOARD_ROOT_ID]) {
    options.push({
      id: DASHBOARD_ROOT_ID,
      label: t('Entire dashboard'),
    });
  }
  return options;
};

const getDefaultTabSelection = (
  tabs: DashboardTabOption[],
  activeTabs: string[],
) => {
  const availableIds = new Set(tabs.map(tab => tab.id));
  const selectedActiveTabs = activeTabs.filter(tabId =>
    availableIds.has(tabId),
  );
  return selectedActiveTabs.length > 0
    ? selectedActiveTabs
    : tabs.slice(0, 1).map(tab => tab.id);
};

const downloadWorkbook = async (
  dashboardId: number,
  tabIds: string[],
  dataMask: DataMaskStateWithId,
  colorSnapshots?: ColorSnapshots,
) => {
  const response = await SupersetClient.post({
    endpoint: `/api/v1/dashboard/${dashboardId}/export_xlsx/`,
    headers: {
      Accept:
        'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
      'Content-Type': 'application/json',
    },
    body: JSON.stringify({
      tabIds,
      dataMask,
      ...(colorSnapshots ? { colorSnapshots } : {}),
    }),
    parseMethod: 'raw',
  });
  const disposition = response.headers.get('Content-Disposition');
  let filename = `dashboard_${dashboardId}.xlsx`;
  if (disposition) {
    try {
      const { filename: parsedFilename } =
        contentDisposition.parse(disposition).parameters;
      filename = parsedFilename;
    } catch (error) {
      logging.warn('Failed to parse Dashboard XLSX filename', error);
    }
  }
  const objectUrl = window.URL.createObjectURL(await response.blob());
  try {
    const anchor = document.createElement('a');
    anchor.href = objectUrl;
    anchor.download = filename;
    anchor.style.display = 'none';
    document.body.appendChild(anchor);
    anchor.click();
    anchor.remove();
  } finally {
    window.URL.revokeObjectURL(objectUrl);
  }
};

export interface DashboardTabXlsxExportProps {
  dashboardId: number;
}

export const DashboardTabXlsxExport = ({
  dashboardId,
}: DashboardTabXlsxExportProps) => {
  const layout = useSelector(
    (state: RootState) => state.dashboardLayout.present,
  );
  const activeTabs = useSelector(
    (state: RootState) => state.dashboardState.activeTabs,
  );
  const dataMask = useSelector((state: RootState) => state.dataMask);
  const charts = useSelector((state: RootState) => state.charts);
  const { addDangerToast, addSuccessToast } = useToasts();
  const tabs = useMemo(() => getOrderedDashboardTabs(layout), [layout]);
  const [show, setShow] = useState(false);
  const [selectedTabIds, setSelectedTabIds] = useState<string[]>([]);
  const [selectionInitialized, setSelectionInitialized] = useState(false);
  const [loading, setLoading] = useState(false);

  const open = (event: MouseEvent<HTMLSpanElement>) => {
    event.stopPropagation();
    setSelectedTabIds(getDefaultTabSelection(tabs, activeTabs));
    setSelectionInitialized(tabs.length > 0);
    setShow(true);
  };

  useEffect(() => {
    if (!show || selectionInitialized || tabs.length === 0) return;
    setSelectedTabIds(getDefaultTabSelection(tabs, activeTabs));
    setSelectionInitialized(true);
  }, [activeTabs, selectionInitialized, show, tabs]);

  const close = () => {
    setShow(false);
    setSelectionInitialized(false);
  };

  const exportTabs = async () => {
    const selected = new Set(selectedTabIds);
    const orderedSelection = tabs
      .filter(tab => selected.has(tab.id))
      .map(tab => tab.id);
    setLoading(true);
    try {
      const exportState = getDashboardColorExportState(
        layout,
        orderedSelection,
        dataMask,
        charts ?? {},
      );
      await downloadWorkbook(
        dashboardId,
        orderedSelection,
        exportState.dataMask,
        exportState.colorSnapshots,
      );
      close();
      addSuccessToast(t('Dashboard tabs exported successfully'));
    } catch (error) {
      // Snapshot errors must not leak response bodies or filter values into logs.
      logging.warn('Dashboard XLSX export did not complete');
      addDangerToast(
        error instanceof Error &&
          error.message ===
            'Load the selected color-filtered table before exporting.'
          ? t(
              'Wait for the selected color-filtered table to finish loading before exporting.',
            )
          : t('Sorry, something went wrong. Try again later.'),
      );
    } finally {
      setLoading(false);
    }
  };

  return (
    <>
      <span onClick={open}>{t('Export tabs to Excel')}</span>
      <Modal
        show={show}
        title={t('Export dashboard tabs to Excel')}
        onHide={close}
        onHandledPrimaryAction={exportTabs}
        primaryButtonName={t('Export')}
        primaryButtonLoading={loading}
        disablePrimaryButton={selectedTabIds.length === 0 || loading}
        destroyOnHidden
      >
        <Flex vertical gap="small">
          <span>
            {t(
              'Select one or more tabs. Nested Table charts are exported in dashboard order.',
            )}
          </span>
          {tabs.map(tab => (
            <Checkbox
              key={tab.id}
              checked={selectedTabIds.includes(tab.id)}
              onChange={event =>
                setSelectedTabIds(current =>
                  event.target.checked
                    ? [...current, tab.id]
                    : current.filter(tabId => tabId !== tab.id),
                )
              }
            >
              {tab.label}
            </Checkbox>
          ))}
        </Flex>
      </Modal>
    </>
  );
};
