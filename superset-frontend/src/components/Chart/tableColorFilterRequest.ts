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
  FeatureFlag,
  isFeatureEnabled,
  JsonObject,
  QueryContext,
  SupersetClient,
  TableColorMetadata,
  TableColorFilterRequest,
  TableColorSelection,
  TablePaintColor,
} from '@superset-ui/core';
import { t } from '@apache-superset/core/translation';

interface RequestContext {
  sourceKey: string;
  draft?: Promise<string>;
  metadata?: Extract<TableColorMetadata, { status: 'ready' }>;
  themeMode?: 'default' | 'dark';
}

const contexts = new Map<string, RequestContext>();
const requests = new WeakMap<QueryContext, RequestContext>();
const MAX_CONTEXTS = 50;

/** A browser-only ordered projection; the server validates every row reference. */
export interface TableColorExportView {
  snapshotId: string;
  rowIndices: number[];
}

/** Match the server's column/color set semantics without changing caller state. */
export function normalizeTableColorSelections(
  selections: readonly TableColorSelection[] = [],
): TableColorSelection[] {
  const byColumn = new Map<string, Set<TablePaintColor>>();
  selections.forEach(({ column, colors }) => {
    const selected = byColumn.get(column) ?? new Set<TablePaintColor>();
    colors.forEach(color => selected.add(color));
    byColumn.set(column, selected);
  });
  return [...byColumn]
    .sort(([left], [right]) => (left < right ? -1 : left > right ? 1 : 0))
    .map(([column, colors]) => ({ column, colors: [...colors].sort() }));
}

/** Click order is not part of the applied color-filter identity. */
export function areTableColorSelectionsEqual(
  left: readonly TableColorSelection[] = [],
  right: readonly TableColorSelection[] = [],
): boolean {
  return (
    JSON.stringify(normalizeTableColorSelections(left)) ===
    JSON.stringify(normalizeTableColorSelections(right))
  );
}

/** Translate only known error codes; never display a database response body. */
export async function getTableColorFilterErrorMessage(
  response: unknown,
): Promise<string> {
  const record =
    response && typeof response === 'object'
      ? (response as Record<string, unknown>)
      : {};
  let code = record.error_code;
  const rawResponse = response instanceof Response ? response : record.response;
  if (!code && rawResponse instanceof Response && !rawResponse.bodyUsed) {
    try {
      const body: unknown = await rawResponse.clone().json();
      if (body && typeof body === 'object')
        code = (body as Record<string, unknown>).error_code;
    } catch {
      // A proxy/HTML response is not trusted to supply user-facing detail.
    }
  }
  if (!code && record.statusText === 'timeout')
    code = 'TABLE_COLOR_FILTER_TIMEOUT';
  const remediations: Record<string, string> = {
    TABLE_COLOR_FILTER_SNAPSHOT_EXPIRED: t(
      'The color-filter snapshot has expired. Reload the table and try again.',
    ),
    TABLE_COLOR_FILTER_CONTEXT_CHANGED: t(
      'The table query or formatting changed. Refresh the table before filtering.',
    ),
    TABLE_COLOR_FILTER_LIMIT_EXCEEDED: t(
      'The complete color-filter result exceeds the row or data-size limit (1,000 rows by default). Narrow the query and reload the table.',
    ),
    TABLE_COLOR_FILTER_CACHE_UNAVAILABLE: t(
      'The color-filter cache is unavailable. Try again after the service recovers.',
    ),
    TABLE_COLOR_FILTER_BUILD_IN_PROGRESS: t(
      'The color-filter result is still being prepared. Wait for it to finish and try again.',
    ),
    TABLE_COLOR_FILTER_TIMEOUT: t(
      'Preparing the color-filter result timed out. Narrow the query and reload the table.',
    ),
  };
  const remediation =
    typeof code === 'string' && Object.hasOwn(remediations, code)
      ? remediations[code]
      : undefined;
  return remediation
    ? `${remediation} ${t('The last successful result is still displayed.')}`
    : t(
        'The color filter could not be applied. The last successful result is still displayed.',
      );
}

/** Stable, private comparison key; never persisted, logged, or sent to clients. */
const serialize = (value: unknown) =>
  JSON.stringify(value, (_key: string, entry: unknown) => {
    if (entry && typeof entry === 'object' && !Array.isArray(entry)) {
      const object = entry as Record<string, unknown>;
      return Object.fromEntries(
        Object.keys(object)
          .sort()
          .map(key => [key, object[key]]),
      );
    }
    return entry;
  });

/** Prepare an owned Explore draft before executing its first color query. */
export async function prepareTableColorFilterRequest(
  payload: QueryContext,
  ownState: JsonObject = {},
  themeMode?: 'default' | 'dark',
  currentView?: TableColorExportView,
): Promise<void> {
  const main = payload.queries?.[0];
  const formData = payload.form_data;
  if (
    !main ||
    !formData ||
    formData.viz_type !== 'table' ||
    !isFeatureEnabled(FeatureFlag.TableAlertFilters) ||
    !main.table_color_filter
  ) {
    if (currentView)
      throw new Error('Load the current table view before exporting.');
    return;
  }
  const request: TableColorFilterRequest = main.table_color_filter;
  const {
    force: _force,
    result_format: _format,
    result_type: _type,
    ...sourceFormData
  } = formData;
  // Explore historically also puts ownState into extra_form_data. Pagination
  // and color selection are request state, not part of the baseline identity.
  {
    const transient = new Set([
      'alertFilter',
      'alertFilters',
      'clientView',
      'currentPage',
      'pageSize',
      'sortBy',
      'searchText',
      'searchColumn',
    ]);
    sourceFormData.extra_form_data = Object.fromEntries(
      Object.entries(sourceFormData.extra_form_data ?? {}).filter(
        ([field]) => !transient.has(field),
      ),
    );
  }
  payload.form_data = {
    ...formData,
    extra_form_data: sourceFormData.extra_form_data,
  };
  const {
    row_offset: _offset,
    row_limit: _limit,
    table_color_filter: _filter,
    ...sourceQuery
  } = main;
  const isExplore =
    !formData.dashboardId && /(^|\/)explore\/?$/.test(window.location.pathname);
  const key = `${isExplore ? 'explore' : 'saved'}:${formData.dashboardId ?? ''}:${formData.slice_id ?? 'new'}:${formData.datasource}`;
  const previous = contexts.get(key);
  const resolvedThemeMode = themeMode ?? previous?.themeMode ?? 'default';
  request.theme_mode = resolvedThemeMode;
  const sourceKey = serialize({
    formData: sourceFormData,
    query: sourceQuery,
    sourcePageSize: formData.server_pagination
      ? (ownState.pageSize ?? formData.server_page_length ?? main.row_limit)
      : undefined,
    themeMode: resolvedThemeMode,
  });
  const changed = previous?.sourceKey !== sourceKey;
  const refresh =
    payload.force &&
    payload.result_format === 'json' &&
    payload.result_type === 'full';
  const context =
    changed || refresh
      ? { sourceKey, themeMode: resolvedThemeMode }
      : (previous as RequestContext);
  contexts.set(key, context);
  if (contexts.size > MAX_CONTEXTS) {
    const firstKey = contexts.keys().next().value;
    if (firstKey !== undefined) contexts.delete(firstKey);
  }

  // Query changes invalidate the token, but do not reinterpret the selected colors.
  if (refresh || (previous && changed)) delete request.snapshot_id;
  if (!request.snapshot_id && context.metadata && !changed) {
    request.snapshot_id = context.metadata.snapshot_id;
  }
  if (!request.snapshot_id && previous && changed) main.row_offset = 0;

  if (currentView) {
    if (
      !['csv', 'xlsx'].includes(payload.result_format ?? '') &&
      payload.result_type !== 'results'
    ) {
      throw new Error('A color view projection is only valid for export.');
    }
    if (
      (previous && changed) ||
      (request.snapshot_id && request.snapshot_id !== currentView.snapshotId)
    ) {
      throw new Error('Load the current table view before exporting.');
    }
    request.snapshot_id = currentView.snapshotId;
    request.view_rows = [...currentView.rowIndices];
  }

  if (isExplore) {
    if (!context.draft) {
      context.draft = SupersetClient.post({
        endpoint: '/api/v1/explore/form_data',
        jsonPayload: {
          datasource_id: payload.datasource.id,
          datasource_type: payload.datasource.type,
          chart_id:
            Number(formData.slice_id) > 0 ? formData.slice_id : undefined,
          form_data: JSON.stringify(sourceFormData),
        },
      }).then(({ json }) => {
        if (typeof json.key !== 'string' || !json.key) {
          throw new Error('Unable to prepare the table color filter draft.');
        }
        return json.key;
      });
    }
    try {
      request.form_data_key = await context.draft;
    } catch (error) {
      context.draft = undefined;
      throw error;
    }
  } else {
    delete request.form_data_key;
  }
  requests.set(payload, context);
}

/** Reuse only a response belonging to the prepared source query. */
export function rememberTableColorFilterResponse(
  payload: QueryContext,
  metadata?: TableColorMetadata,
): void {
  const context = requests.get(payload);
  if (context && metadata?.status === 'ready') context.metadata = metadata;
}

/** Release private request state, also used when testing independent sessions. */
export function clearTableColorFilterRequestCache(): void {
  contexts.clear();
}
