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
  cloneElement,
  ReactElement,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from 'react';
import { useSelector } from 'react-redux';
import { t } from '@apache-superset/core/translation';
import {
  BinaryQueryObjectFilterClause,
  DataRecord,
  DatasourceType,
  ensureIsArray,
  FeatureFlag,
  isFeatureEnabled,
  JsonObject,
  QueryFormData,
  QueryMode,
} from '@superset-ui/core';
import { css, useTheme } from '@apache-superset/core/theme';
import { GenericDataType } from '@apache-superset/core/common';
import { useResizeDetector } from 'react-resize-detector';
import BooleanCell from '@superset-ui/core/components/Table/cell-renderers/BooleanCell';
import NullCell from '@superset-ui/core/components/Table/cell-renderers/NullCell';
import TimeCell from '@superset-ui/core/components/Table/cell-renderers/TimeCell';
import {
  EmptyState,
  Input,
  Loading,
  Select,
} from '@superset-ui/core/components';
import {
  DrillDetailMode,
  DrillDetailSearch,
  getDatasourceSamples,
} from 'src/components/Chart/chartAction';
import Table, {
  ColumnsType,
  TableSize,
} from '@superset-ui/core/components/Table';
import { RootState } from 'src/dashboard/types';
import HeaderWithRadioGroup from '@superset-ui/core/components/Table/header-renderers/HeaderWithRadioGroup';
import { useDatasetMetadataBar } from 'src/features/datasets/metadataBar/useDatasetMetadataBar';
import { Dataset } from '../types';
import TableControls from './DrillDetailTableControls';
import {
  filterBoundedClientRows,
  getDrillPayload,
  normalizeDrillPageLength,
  validateBoundedClientResult,
} from './utils';
import { ResultsPage } from './types';

const LEGACY_PAGE_SIZE = 50;

type ConfigurableDrillFormData = QueryFormData & {
  drill_detail_server_pagination?: boolean;
  drill_detail_server_page_length?: number;
  drill_detail_client_page_length?: number;
  drill_detail_include_search?: boolean;
};

// Must be outside of the main component due to problems in
// react-resize-detector with conditional rendering
// https://github.com/maslianok/react-resize-detector/issues/178
function Resizable({ children }: { children: ReactElement }) {
  const { ref, height } = useResizeDetector();
  return (
    <div ref={ref} css={{ flex: 1 }}>
      {cloneElement(children, { height })}
    </div>
  );
}

enum TimeFormatting {
  Original,
  Formatted,
}

export default function DrillDetailPane({
  formData,
  initialFilters,
  dataset,
}: {
  formData: QueryFormData;
  initialFilters: BinaryQueryObjectFilterClause[];
  dataset?: Dataset;
}) {
  const theme = useTheme();
  const drillFormData = formData as ConfigurableDrillFormData;
  const configurableDetailEnabled =
    isFeatureEnabled(FeatureFlag.DrillDetailConfigurableTable) &&
    formData.viz_type === 'table' &&
    formData.query_mode !== QueryMode.Raw &&
    ensureIsArray(formData.metrics).length > 0;
  const serverPagination =
    !configurableDetailEnabled ||
    drillFormData.drill_detail_server_pagination !== false;
  const detailMode: DrillDetailMode = serverPagination
    ? 'server'
    : 'bounded_client';
  const pageLength = configurableDetailEnabled
    ? normalizeDrillPageLength(
        serverPagination
          ? drillFormData.drill_detail_server_page_length
          : drillFormData.drill_detail_client_page_length,
      )
    : LEGACY_PAGE_SIZE;
  const includeSearch =
    configurableDetailEnabled &&
    drillFormData.drill_detail_include_search === true;
  const textSearchColumns = useMemo(
    () =>
      ensureIsArray(dataset?.columns).filter(
        column =>
          column.type_generic === GenericDataType.String &&
          column.filterable !== false &&
          column.is_active !== false &&
          column.is_physical !== false &&
          !column.expression,
      ),
    [dataset?.columns],
  );

  const [pageIndex, setPageIndex] = useState(0);
  const lastPageIndex = useRef(pageIndex);
  const [filters, setFilters] = useState(initialFilters);
  const [isLoading, setIsLoading] = useState(false);
  const [responseError, setResponseError] = useState('');
  const [resultsPages, setResultsPages] = useState<Map<number, ResultsPage>>(
    new Map(),
  );
  const [timeFormatting, setTimeFormatting] = useState<
    Record<string, TimeFormatting>
  >({});
  const [searchValue, setSearchValue] = useState('');
  const [searchColumn, setSearchColumn] = useState(
    textSearchColumns[0]?.column_name ?? '',
  );
  const requestController = useRef<AbortController>();

  const dashboardId = useSelector<RootState, number>(
    ({ dashboardInfo }) => dashboardInfo.id,
  );

  const samplesRowLimit = Number(
    useSelector(
      (state: { common: { conf: JsonObject } }) =>
        state.common.conf.SAMPLES_ROW_LIMIT,
    ) ?? 1000,
  );

  const [datasourceId, datasourceType] = useMemo(
    () => formData.datasource.split('__'),
    [formData.datasource],
  );

  const { metadataBar: metadataBarComponent } = useDatasetMetadataBar({
    dataset,
  });

  const resetResults = useCallback(() => {
    requestController.current?.abort();
    setResponseError('');
    setResultsPages(new Map());
    setPageIndex(0);
  }, []);

  useEffect(() => {
    resetResults();
  }, [detailMode, pageLength, resetResults]);

  useEffect(() => {
    if (
      detailMode === 'server' &&
      !textSearchColumns.some(column => column.column_name === searchColumn)
    ) {
      setSearchColumn(textSearchColumns[0]?.column_name ?? '');
      setSearchValue('');
      resetResults();
    }
  }, [detailMode, resetResults, searchColumn, textSearchColumns]);

  const resultsPageIndex = detailMode === 'server' ? pageIndex : 0;
  const resultsPage = useMemo(() => {
    const nextResultsPage = resultsPages.get(resultsPageIndex);
    if (nextResultsPage) {
      lastPageIndex.current = resultsPageIndex;
      return nextResultsPage;
    }
    return resultsPages.get(lastPageIndex.current);
  }, [resultsPageIndex, resultsPages]);

  const mappedColumns: ColumnsType<DataRecord> = useMemo(
    () =>
      resultsPage?.colNames.map((column, index) => ({
        key: column,
        dataIndex: column,
        title:
          resultsPage?.colTypes[index] === GenericDataType.Temporal ? (
            <HeaderWithRadioGroup
              headerTitle={dataset?.verbose_map?.[column] || column}
              groupTitle={t('Formatting')}
              groupOptions={[
                { label: t('Original value'), value: TimeFormatting.Original },
                {
                  label: t('Formatted value'),
                  value: TimeFormatting.Formatted,
                },
              ]}
              value={
                timeFormatting[column] === TimeFormatting.Original
                  ? TimeFormatting.Original
                  : TimeFormatting.Formatted
              }
              onChange={value =>
                setTimeFormatting(state => ({
                  ...state,
                  [column]: parseInt(value, 10) as TimeFormatting,
                }))
              }
            />
          ) : (
            dataset?.verbose_map?.[column] || column
          ),
        render: value => {
          if (value === true || value === false) {
            return <BooleanCell value={value} />;
          }
          if (value === null) {
            return <NullCell />;
          }
          if (
            resultsPage?.colTypes[index] === GenericDataType.Temporal &&
            timeFormatting[column] !== TimeFormatting.Original &&
            (typeof value === 'number' || value instanceof Date)
          ) {
            return <TimeCell value={value} />;
          }
          return String(value);
        },
        width: 150,
      })) || [],
    [
      resultsPage?.colNames,
      resultsPage?.colTypes,
      timeFormatting,
      dataset?.verbose_map,
    ],
  );

  const visibleRows = useMemo(
    () =>
      detailMode === 'bounded_client'
        ? filterBoundedClientRows(resultsPage?.data ?? [], searchValue)
        : (resultsPage?.data ?? []),
    [detailMode, resultsPage?.data, searchValue],
  );
  const data: DataRecord[] = useMemo(
    () =>
      visibleRows.map((row, index) => ({
        ...row,
        key: `${resultsPageIndex}-${index}`,
      })),
    [resultsPageIndex, visibleRows],
  );
  const visibleTotal =
    detailMode === 'bounded_client' ? data.length : resultsPage?.total;

  const handleReload = useCallback(() => {
    resetResults();
  }, [resetResults]);

  const handleFiltersChange = useCallback(
    (nextFilters: BinaryQueryObjectFilterClause[]) => {
      setFilters(nextFilters);
      resetResults();
    },
    [resetResults],
  );

  const handleSearchValueChange = useCallback(
    (value: string) => {
      setSearchValue(value);
      setPageIndex(0);
      if (detailMode === 'server') {
        resetResults();
      }
    },
    [detailMode, resetResults],
  );

  const handleSearchColumnChange = useCallback(
    (value: string) => {
      setSearchColumn(value);
      setPageIndex(0);
      resetResults();
    },
    [resetResults],
  );

  const requestSearch: DrillDetailSearch | undefined = useMemo(() => {
    const trimmedSearchValue = searchValue.trim();
    return includeSearch &&
      detailMode === 'server' &&
      searchColumn &&
      trimmedSearchValue
      ? { column: searchColumn, value: trimmedSearchValue }
      : undefined;
  }, [detailMode, includeSearch, searchColumn, searchValue]);
  const hasCachedPage = resultsPages.has(resultsPageIndex);

  useEffect(() => {
    if (responseError || hasCachedPage) {
      return undefined;
    }
    const controller = new AbortController();
    requestController.current?.abort();
    requestController.current = controller;
    setIsLoading(true);
    const jsonPayload = getDrillPayload(formData, filters) ?? {};
    const cachePageLimit = Math.max(1, Math.ceil(samplesRowLimit / pageLength));
    getDatasourceSamples(
      datasourceType as DatasourceType,
      Number(datasourceId),
      false,
      jsonPayload,
      pageLength,
      resultsPageIndex + 1,
      dashboardId,
      configurableDetailEnabled ? detailMode : undefined,
      requestSearch,
      controller.signal,
    )
      .then(response => {
        if (controller.signal.aborted) {
          return;
        }
        const responseData = ensureIsArray(response.data);
        const columnNames = ensureIsArray(response.colnames);
        if (detailMode === 'bounded_client') {
          validateBoundedClientResult(responseData, columnNames);
        }
        setResultsPages(currentResultsPages => {
          const retainedEntries =
            cachePageLimit > 1
              ? [...currentResultsPages.entries()].slice(-(cachePageLimit - 1))
              : [];
          return new Map([
            ...retainedEntries,
            [
              resultsPageIndex,
              {
                total: response.total_count,
                data: responseData,
                colNames: columnNames,
                colTypes: ensureIsArray(response.coltypes),
              },
            ] as [number, ResultsPage],
          ]);
        });
        setResponseError('');
      })
      .catch(error => {
        if (!controller.signal.aborted) {
          setResponseError(`${error.name}: ${error.message}`);
        }
      })
      .finally(() => {
        if (requestController.current === controller) {
          setIsLoading(false);
        }
      });
    return () => controller.abort();
  }, [
    configurableDetailEnabled,
    dashboardId,
    datasourceId,
    datasourceType,
    detailMode,
    filters,
    formData,
    hasCachedPage,
    pageLength,
    requestSearch,
    responseError,
    resultsPageIndex,
    samplesRowLimit,
  ]);

  const bootstrapping = !responseError && !resultsPages.size;
  const allowHTML = formData.allow_render_html ?? true;

  let tableContent = null;
  if (responseError) {
    tableContent = (
      <pre
        css={css`
          margin-top: ${theme.sizeUnit * 4}px;
        `}
      >
        {responseError}
      </pre>
    );
  } else if (bootstrapping) {
    tableContent = <Loading />;
  } else if (visibleTotal === 0) {
    tableContent = (
      <EmptyState
        image="document.svg"
        title={t('No rows were returned for this dataset')}
      />
    );
  } else {
    tableContent = (
      <Resizable>
        <Table
          key={`${detailMode}-${pageLength}-${
            detailMode === 'bounded_client' ? searchValue : ''
          }`}
          data={data}
          columns={mappedColumns}
          size={TableSize.Small}
          defaultPageSize={pageLength}
          recordCount={detailMode === 'server' ? resultsPage?.total : undefined}
          usePagination
          loading={isLoading}
          onChange={
            detailMode === 'server'
              ? pagination =>
                  setPageIndex(pagination.current ? pagination.current - 1 : 0)
              : undefined
          }
          resizable
          virtualize
          allowHTML={allowHTML}
        />
      </Resizable>
    );
  }

  return (
    <>
      {includeSearch && (
        <div
          css={css`
            display: flex;
            gap: ${theme.sizeUnit * 2}px;
            margin-bottom: ${theme.sizeUnit * 2}px;
          `}
        >
          {detailMode === 'server' && (
            <Select
              ariaLabel={t('Drill detail search column')}
              value={searchColumn || undefined}
              options={textSearchColumns.map(column => ({
                label:
                  dataset?.verbose_map?.[column.column_name] ||
                  column.column_name,
                value: column.column_name,
              }))}
              onChange={handleSearchColumnChange}
              disabled={!textSearchColumns.length}
              css={css`
                min-width: 180px;
              `}
            />
          )}
          <Input
            aria-label={t('Drill detail search')}
            allowClear
            disabled={detailMode === 'server' && !searchColumn}
            placeholder={
              detailMode === 'server'
                ? t('Search by prefix')
                : t('Search all columns')
            }
            value={searchValue}
            onChange={event => handleSearchValueChange(event.target.value)}
          />
        </div>
      )}
      {!bootstrapping && metadataBarComponent}
      {!bootstrapping && (
        <TableControls
          filters={filters}
          setFilters={handleFiltersChange}
          totalCount={visibleTotal}
          loading={isLoading}
          onReload={handleReload}
        />
      )}
      {tableContent}
    </>
  );
}
