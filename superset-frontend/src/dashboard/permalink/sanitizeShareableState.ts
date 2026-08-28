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
import {
  Behavior,
  getChartMetadataRegistry,
  type DataMaskStateWithId,
  type DataMaskWithId,
  type ExtraFormData,
  type FilterState,
  type JsonObject,
  type TablePaintColor,
} from '@superset-ui/core';
import { CHART_TYPE, TAB_TYPE } from 'src/dashboard/util/componentTypes';
import { getTableColorFilterTargets } from '../../../plugins/plugin-chart-table/src/utils/resolveTableCellStyle';

const tablePaintColors: TablePaintColor[] = ['GREEN', 'YELLOW', 'RED'];

type UnknownRecord = Record<string, unknown>;

export interface DashboardStateChart {
  id?: string | number;
  slice_id?: string | number;
  viz_type?: string;
  form_data?: unknown;
}

export interface DashboardStateLayoutItem {
  id?: string;
  type?: string;
  parents?: string[];
  meta?: {
    chartId?: string | number;
  };
}

export interface DashboardStateNativeFilter {
  id?: unknown;
}

export interface DashboardStateDropCounts {
  invalidDataMasks: number;
  invalidAlertFilters: number;
  transientChartFields: number;
  invalidTabs: number;
  invalidAnchors: number;
}

export interface ShareableDashboardState {
  dataMask: DataMaskStateWithId;
  activeTabs: string[];
}

export interface SanitizedDashboardState {
  state: ShareableDashboardState;
  anchor?: string;
  dropped: DashboardStateDropCounts;
}

export interface SanitizeShareableStateInput {
  dataMask?: unknown;
  activeTabs?: readonly string[] | null;
  nativeFilterConfiguration?:
    | readonly DashboardStateNativeFilter[]
    | Record<string, DashboardStateNativeFilter | undefined>
    | null;
  charts?:
    | readonly DashboardStateChart[]
    | Record<string, DashboardStateChart | undefined>
    | null;
  chartConfiguration?: Record<string | number, unknown> | null;
  crossFiltersEnabled?: boolean;
  layout?: Record<string, DashboardStateLayoutItem | undefined> | null;
  anchor?: string;
}

const isRecord = (value: unknown): value is UnknownRecord =>
  typeof value === 'object' && value !== null && !Array.isArray(value);

const hasMeaningfulValue = (value: unknown): boolean => {
  if (value === null || value === undefined) {
    return false;
  }
  if (Array.isArray(value)) {
    return value.some(hasMeaningfulValue);
  }
  if (isRecord(value)) {
    return Object.values(value).some(hasMeaningfulValue);
  }
  return true;
};

const asValues = <T>(
  value: readonly T[] | Record<string, T | undefined> | null | undefined,
): T[] =>
  (Array.isArray(value) ? value : Object.values(value ?? {})).filter(
    (item): item is T => item !== undefined,
  );

const getChartId = (chart: DashboardStateChart): string | undefined => {
  const formData = isRecord(chart.form_data) ? chart.form_data : {};
  const candidateIds = [formData.slice_id, chart.slice_id, chart.id];
  const id = candidateIds.find(candidate => {
    const numericId = Number(candidate);
    return Number.isInteger(numericId) && numericId > 0;
  });
  return id === undefined ? undefined : String(Number(id));
};

const getValidColorFilterTargets = (
  chart: DashboardStateChart,
): Set<string> => {
  const formData = isRecord(chart.form_data) ? chart.form_data : {};
  const vizType = formData.viz_type ?? chart.viz_type;
  if (vizType !== 'table') {
    return new Set();
  }
  return getTableColorFilterTargets(formData);
};

const sanitizeColorFilter = (
  ownState: UnknownRecord,
  validTargets: Set<string>,
  dropped: DashboardStateDropCounts,
): JsonObject | undefined => {
  const rawColorFilter = ownState.alertFilter;
  dropped.transientChartFields += Object.keys(ownState).filter(
    key => key !== 'alertFilter' && key !== 'alertFilters',
  ).length;
  if (ownState.alertFilters !== undefined) {
    dropped.invalidAlertFilters += Array.isArray(ownState.alertFilters)
      ? ownState.alertFilters.length
      : 1;
  }
  if (rawColorFilter === undefined) return undefined;
  if (
    !isRecord(rawColorFilter) ||
    rawColorFilter.version !== 2 ||
    !Array.isArray(rawColorFilter.selections)
  ) {
    dropped.invalidAlertFilters += 1;
    return undefined;
  }
  dropped.transientChartFields += Object.keys(rawColorFilter).filter(
    key => key !== 'version' && key !== 'selections',
  ).length;
  const selected = new Map<string, Set<TablePaintColor>>();
  rawColorFilter.selections.forEach(selection => {
    if (
      !isRecord(selection) ||
      typeof selection.column !== 'string' ||
      !validTargets.has(selection.column) ||
      !Array.isArray(selection.colors) ||
      selection.colors.length === 0 ||
      selection.colors.length > 3 ||
      !selection.colors.every(color => tablePaintColors.includes(color)) ||
      (!selected.has(selection.column) && selected.size >= 100)
    ) {
      dropped.invalidAlertFilters += 1;
      return;
    }
    dropped.transientChartFields += Object.keys(selection).filter(
      key => key !== 'column' && key !== 'colors',
    ).length;
    const colors = selected.get(selection.column) ?? new Set<TablePaintColor>();
    selection.colors.forEach(color => colors.add(color));
    selected.set(selection.column, colors);
  });
  const selections = [...selected].map(([column, colors]) => ({
    column,
    colors: tablePaintColors.filter(color => colors.has(color)),
  }));
  return selections.length
    ? { alertFilter: { version: 2, selections } }
    : undefined;
};

const copyNativeFilterState = (
  mask: UnknownRecord,
  output: DataMaskWithId,
): boolean => {
  let hasFilterState = false;
  if (isRecord(mask.extraFormData) && hasMeaningfulValue(mask.extraFormData)) {
    output.extraFormData = mask.extraFormData as ExtraFormData;
    hasFilterState = true;
  }
  if (
    hasFilterState &&
    isRecord(mask.filterState) &&
    hasMeaningfulValue(mask.filterState)
  ) {
    output.filterState = mask.filterState as FilterState;
  }
  return hasFilterState;
};

const crossFilterStateFields = new Set([
  'value',
  'label',
  'filters',
  'selectedValues',
  'selectedFilters',
  'customColumnLabel',
]);

const copyCrossFilterState = (
  mask: UnknownRecord,
  output: DataMaskWithId,
  dropped: DashboardStateDropCounts,
): boolean => {
  let hasCrossFilterFilters = false;
  if (isRecord(mask.extraFormData)) {
    dropped.transientChartFields += Object.keys(mask.extraFormData).filter(
      key => key !== 'filters',
    ).length;
    if (
      Array.isArray(mask.extraFormData.filters) &&
      hasMeaningfulValue(mask.extraFormData.filters)
    ) {
      output.extraFormData = {
        filters: mask.extraFormData.filters,
      } as ExtraFormData;
      hasCrossFilterFilters = true;
    }
  } else if (mask.extraFormData !== undefined) {
    dropped.transientChartFields += 1;
  }

  if (hasCrossFilterFilters && isRecord(mask.filterState)) {
    const filterState = Object.fromEntries(
      Object.entries(mask.filterState).filter(([key, value]) => {
        const allowed = crossFilterStateFields.has(key);
        if (!allowed) {
          dropped.transientChartFields += 1;
        }
        return allowed && hasMeaningfulValue(value);
      }),
    );
    if (Object.keys(filterState).length > 0) {
      output.filterState = filterState as FilterState;
    }
  } else if (mask.filterState !== undefined) {
    dropped.transientChartFields += 1;
  }
  return hasCrossFilterFilters;
};

export const sanitizeShareableDashboardState = ({
  dataMask,
  activeTabs,
  nativeFilterConfiguration,
  charts,
  chartConfiguration,
  crossFiltersEnabled = true,
  layout,
  anchor,
}: SanitizeShareableStateInput): SanitizedDashboardState => {
  const dropped: DashboardStateDropCounts = {
    invalidDataMasks: 0,
    invalidAlertFilters: 0,
    transientChartFields: 0,
    invalidTabs: 0,
    invalidAnchors: 0,
  };
  const nativeFilterIds = new Set(
    asValues(nativeFilterConfiguration)
      .map(filter => filter.id)
      .filter((id): id is string => typeof id === 'string'),
  );
  const chartIdsInLayout = new Set(
    Object.values(layout ?? {})
      .filter(item => item?.type === CHART_TYPE)
      .map(item => Number(item?.meta?.chartId))
      .filter(id => Number.isInteger(id) && id > 0)
      .map(String),
  );
  const configuredCrossFilterIds = new Set(
    Object.keys(chartConfiguration ?? {}).map(id => String(Number(id))),
  );
  const chartTargets = new Map<string, Set<string>>();
  const crossFilterChartIds = new Set<string>();
  asValues(charts).forEach(chart => {
    const chartId = getChartId(chart);
    if (chartId && chartIdsInLayout.has(chartId)) {
      chartTargets.set(chartId, getValidColorFilterTargets(chart));
      const formData = isRecord(chart.form_data) ? chart.form_data : {};
      const vizType = formData.viz_type ?? chart.viz_type;
      const supportsCrossFilters =
        configuredCrossFilterIds.has(chartId) ||
        (typeof vizType === 'string' &&
          getChartMetadataRegistry()
            .get(vizType)
            ?.behaviors?.includes(Behavior.InteractiveChart));
      if (crossFiltersEnabled && supportsCrossFilters) {
        crossFilterChartIds.add(chartId);
      }
    }
  });

  const sanitizedDataMask: DataMaskStateWithId = {};
  if (dataMask !== null && dataMask !== undefined && !isRecord(dataMask)) {
    dropped.invalidDataMasks += 1;
  }
  Object.entries(isRecord(dataMask) ? dataMask : {}).forEach(
    ([id, rawMask]) => {
      if (!isRecord(rawMask)) {
        dropped.invalidDataMasks += 1;
        return;
      }
      const sanitizedMask = { id } as DataMaskWithId;
      const unsupportedFields = Object.keys(rawMask).filter(
        key =>
          !['id', 'extraFormData', 'filterState', 'ownState'].includes(key),
      );
      dropped.transientChartFields += unsupportedFields.length;

      if (nativeFilterIds.has(id)) {
        copyNativeFilterState(rawMask, sanitizedMask);
        if (isRecord(rawMask.ownState)) {
          dropped.transientChartFields += Object.keys(rawMask.ownState).length;
        }
        if (sanitizedMask.extraFormData || sanitizedMask.filterState) {
          sanitizedDataMask[id] = sanitizedMask;
        }
        return;
      }

      const chartId = String(Number(id));
      const validTargets = chartTargets.get(chartId);
      if (!validTargets || !Number.isInteger(Number(id))) {
        dropped.invalidDataMasks += 1;
        return;
      }

      sanitizedMask.id = chartId;
      const hasCrossFilterInput =
        hasMeaningfulValue(rawMask.extraFormData) ||
        hasMeaningfulValue(rawMask.filterState);
      const hasCrossFilterState = crossFilterChartIds.has(chartId)
        ? copyCrossFilterState(rawMask, sanitizedMask, dropped)
        : false;
      if (hasCrossFilterInput && !crossFilterChartIds.has(chartId)) {
        dropped.invalidDataMasks += 1;
      }
      if (isRecord(rawMask.ownState)) {
        sanitizedMask.ownState = sanitizeColorFilter(
          rawMask.ownState,
          validTargets,
          dropped,
        );
      } else if (rawMask.ownState !== undefined) {
        dropped.transientChartFields += 1;
      }
      if (!sanitizedMask.ownState) {
        delete sanitizedMask.ownState;
      }
      if (hasCrossFilterState || sanitizedMask.ownState) {
        sanitizedDataMask[chartId] = sanitizedMask;
      }
    },
  );

  const validTabIds = new Set(
    Object.values(layout ?? {})
      .filter(item => item?.type === TAB_TYPE && typeof item.id === 'string')
      .map(item => item!.id!),
  );
  const tabDepths = new Map(
    Object.values(layout ?? {})
      .filter(item => item?.type === TAB_TYPE && typeof item.id === 'string')
      .map(item => [item!.id!, item!.parents?.length ?? 0]),
  );
  const sanitizedTabs: string[] = [];
  const seenTabs = new Set<string>();
  if (Array.isArray(activeTabs)) {
    activeTabs.forEach(tabId => {
      if (
        typeof tabId !== 'string' ||
        !validTabIds.has(tabId) ||
        seenTabs.has(tabId)
      ) {
        dropped.invalidTabs += 1;
        return;
      }
      seenTabs.add(tabId);
      sanitizedTabs.push(tabId);
    });
  } else if (activeTabs !== null && activeTabs !== undefined) {
    dropped.invalidTabs += 1;
  }
  const originalTabOrder = new Map(
    sanitizedTabs.map((tabId, index) => [tabId, index]),
  );
  sanitizedTabs.sort(
    (left, right) =>
      (tabDepths.get(left) ?? 0) - (tabDepths.get(right) ?? 0) ||
      (originalTabOrder.get(left) ?? 0) - (originalTabOrder.get(right) ?? 0),
  );

  const sanitizedAnchor =
    typeof anchor === 'string' && layout?.[anchor] ? anchor : undefined;
  if (anchor && !sanitizedAnchor) {
    dropped.invalidAnchors += 1;
  }

  return {
    state: { dataMask: sanitizedDataMask, activeTabs: sanitizedTabs },
    ...(sanitizedAnchor ? { anchor: sanitizedAnchor } : {}),
    dropped,
  };
};

export const logDashboardStateDrops = (
  phase: 'save' | 'restore',
  dropped: DashboardStateDropCounts,
) => {
  if (Object.values(dropped).some(count => count > 0)) {
    logging.info('Dashboard permalink state sanitized', {
      phase,
      ...dropped,
    });
  }
};
