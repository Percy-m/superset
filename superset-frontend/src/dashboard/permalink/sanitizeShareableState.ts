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
} from '@superset-ui/core';
import { CHART_TYPE, TAB_TYPE } from 'src/dashboard/util/componentTypes';

const alertLevels = new Set(['RED', 'YELLOW', 'GREEN']);
const numericComparators = new Set([
  '=',
  '≠',
  '<',
  '>',
  '≤',
  '≥',
  '< x <',
  '≤ x ≤',
  '≤ x <',
  '< x ≤',
]);
const rangeComparators = new Set(['< x <', '≤ x ≤', '≤ x <', '< x ≤']);
const uuid4Pattern =
  /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;

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

const isFiniteNumericTarget = (value: unknown): boolean => {
  if (typeof value === 'number') {
    return Number.isFinite(value);
  }
  return (
    typeof value === 'string' &&
    value.trim().length > 0 &&
    Number.isFinite(Number(value))
  );
};

const getValidAlertRules = (chart: DashboardStateChart) => {
  const formData = isRecord(chart.form_data) ? chart.form_data : {};
  const vizType = formData.viz_type ?? chart.viz_type;
  if (vizType !== 'table') {
    return new Map<string, string>();
  }

  const groupby = new Set(
    (Array.isArray(formData.groupby) ? formData.groupby : []).filter(
      (item): item is string => typeof item === 'string',
    ),
  );
  const metrics = new Set(
    (Array.isArray(formData.metrics) ? formData.metrics : []).filter(
      (item): item is string => typeof item === 'string',
    ),
  );
  const rules = Array.isArray(formData.conditional_formatting)
    ? formData.conditional_formatting
    : [];
  const validRules = new Map<string, string>();
  const seenRuleIds = new Set<string>();
  const duplicateRuleIds = new Set<string>();

  rules.forEach(candidate => {
    if (!isRecord(candidate) || typeof candidate.ruleId !== 'string') {
      return;
    }
    const ruleId = candidate.ruleId.toLowerCase();
    if (!uuid4Pattern.test(ruleId)) {
      return;
    }
    if (seenRuleIds.has(ruleId)) {
      duplicateRuleIds.add(ruleId);
      validRules.delete(ruleId);
      return;
    }
    seenRuleIds.add(ruleId);

    const subject = candidate.subjectRef;
    if (!isRecord(subject)) {
      return;
    }
    const subjectKind = subject.kind;
    const subjectKey = subject.key;
    const subjectIsValid =
      typeof subjectKey === 'string' &&
      candidate.column === subjectKey &&
      ((subjectKind === 'physical_column' && groupby.has(subjectKey)) ||
        (subjectKind === 'saved_metric' && metrics.has(subjectKey)));
    const { operator } = candidate;
    const targetsAreValid = rangeComparators.has(String(operator))
      ? isFiniteNumericTarget(candidate.targetValueLeft) &&
        isFiniteNumericTarget(candidate.targetValueRight) &&
        Number(candidate.targetValueLeft) < Number(candidate.targetValueRight)
      : isFiniteNumericTarget(candidate.targetValue);

    if (
      candidate.filterable !== true ||
      typeof candidate.alertLevel !== 'string' ||
      !alertLevels.has(candidate.alertLevel) ||
      typeof operator !== 'string' ||
      !numericComparators.has(operator) ||
      !targetsAreValid ||
      !subjectIsValid ||
      candidate.useGradient === true ||
      candidate.objectFormatting === 'CELL_BAR'
    ) {
      return;
    }
    validRules.set(ruleId, candidate.alertLevel);
  });

  duplicateRuleIds.forEach(ruleId => validRules.delete(ruleId));
  return validRules;
};

const sanitizeAlertFilters = (
  ownState: UnknownRecord,
  validRules: Map<string, string>,
  dropped: DashboardStateDropCounts,
): JsonObject | undefined => {
  const rawAlertFilters = ownState.alertFilters;
  dropped.transientChartFields += Object.keys(ownState).filter(
    key => key !== 'alertFilters',
  ).length;
  if (!Array.isArray(rawAlertFilters)) {
    if (rawAlertFilters !== undefined) {
      dropped.invalidAlertFilters += 1;
    }
    return undefined;
  }

  const seenReferences = new Set<string>();
  const alertFilters: JsonObject[] = [];
  rawAlertFilters.forEach(reference => {
    if (!isRecord(reference)) {
      dropped.invalidAlertFilters += 1;
      return;
    }
    const ruleId =
      typeof reference.ruleId === 'string'
        ? reference.ruleId.toLowerCase()
        : '';
    const { level } = reference;
    const referenceKey = `${ruleId}:${String(level)}`;
    if (
      !uuid4Pattern.test(ruleId) ||
      typeof level !== 'string' ||
      validRules.get(ruleId) !== level ||
      seenReferences.has(referenceKey) ||
      alertFilters.length >= 50
    ) {
      dropped.invalidAlertFilters += 1;
      return;
    }
    seenReferences.add(referenceKey);
    alertFilters.push({ ruleId, level });
  });

  return alertFilters.length > 0 ? { alertFilters } : undefined;
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
  const chartRules = new Map<string, Map<string, string>>();
  const crossFilterChartIds = new Set<string>();
  asValues(charts).forEach(chart => {
    const chartId = getChartId(chart);
    if (chartId && chartIdsInLayout.has(chartId)) {
      chartRules.set(chartId, getValidAlertRules(chart));
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
      const validRules = chartRules.get(chartId);
      if (!validRules || !Number.isInteger(Number(id))) {
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
        sanitizedMask.ownState = sanitizeAlertFilters(
          rawMask.ownState,
          validRules,
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
