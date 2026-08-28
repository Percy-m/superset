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
  CSSProperties,
  useCallback,
  useLayoutEffect,
  useMemo,
  useState,
  MouseEvent,
  KeyboardEvent as ReactKeyboardEvent,
  useEffect,
  useRef,
} from 'react';

import {
  ColumnInstance,
  ColumnWithLooseAccessor,
  DefaultSortTypes,
  Row,
} from 'react-table';
import { extent as d3Extent, max as d3Max } from 'd3-array';
import { FaSort } from 'react-icons/fa';
import { FaSortDown as FaSortDesc } from 'react-icons/fa';
import { FaSortUp as FaSortAsc } from 'react-icons/fa';
import cx from 'classnames';
import {
  DataRecord,
  DataRecordValue,
  DTTM_ALIAS,
  ensureIsArray,
  getSelectedText,
  getTimeFormatterForGranularity,
  BinaryQueryObjectFilterClause,
  extractTextFromHTML,
  TableCellPaint,
  TableColorSelection,
  TablePaintColor,
} from '@superset-ui/core';
import {
  styled,
  css,
  useTheme,
  SupersetTheme,
} from '@apache-superset/core/theme';
import { t, tn } from '@apache-superset/core/translation';
import { GenericDataType } from '@apache-superset/core/common';
import {
  Input,
  Space,
  RawAntdSelect as Select,
  Button,
  Dropdown,
  Icons,
  Tooltip,
} from '@superset-ui/core/components';
import {
  CheckOutlined,
  InfoCircleOutlined,
  DownOutlined,
  MinusCircleOutlined,
  PlusCircleOutlined,
  TableOutlined,
} from '@ant-design/icons';
import { isEmpty, debounce, isEqual } from 'lodash';
import { getTextColorForBackground } from '@superset-ui/chart-controls';
import {
  DataColumnMeta,
  SearchOption,
  SortByItem,
  TableChartTransformedProps,
  TableChartOwnState,
} from './types';
import DataTable, {
  DataTableProps,
  SearchInputProps,
  SelectPageSizeRendererProps,
  SizeOption,
} from './DataTable';
import Styles from './Styles';
import { formatColumnValue } from './utils/formatValue';
import { PAGE_SIZE_OPTIONS, SERVER_PAGE_SIZE_OPTIONS } from './consts';
import { updateTableOwnState } from './DataTable/utils/externalAPIs';
import getScrollBarSize from './DataTable/utils/getScrollBarSize';
import DateWithFormatter from './utils/DateWithFormatter';
import {
  resolveTableCellStyle,
  resolveTableFilterCapabilities,
} from './utils/resolveTableCellStyle';

type ValueRange = [number, number];

interface TableSize {
  width: number;
  height: number;
}

const ACTION_KEYS = {
  enter: 'Enter',
  spacebar: 'Spacebar',
  space: ' ',
};

/**
 * Return sortType based on data type
 */
function getSortTypeByDataType(dataType: GenericDataType): DefaultSortTypes {
  if (dataType === GenericDataType.Temporal) {
    return 'datetime';
  }
  if (dataType === GenericDataType.String) {
    return 'alphanumeric';
  }
  return 'basic';
}

/**
 * Sanitize a column identifier for use in HTML id attributes and CSS selectors.
 * Replaces characters that are invalid in CSS selectors with safe alternatives.
 *
 * Note: The returned value should be prefixed with a string (e.g., "header-")
 * to ensure it forms a valid HTML ID (IDs cannot start with a digit).
 *
 * Exported for testing.
 */
export function sanitizeHeaderId(columnId: string): string {
  return (
    columnId
      // Semantic replacements first: preserve meaning in IDs for readability
      // (e.g., '%pct_nice' → 'percentpct_nice' instead of '_pct_nice')
      .replace(/%/g, 'percent')
      .replace(/#/g, 'hash')
      .replace(/△/g, 'delta')
      // Generic sanitization for remaining special characters
      .replace(/\s+/g, '_')
      .replace(/[^a-zA-Z0-9_-]/g, '_')
      .replace(/_+/g, '_') // Collapse consecutive underscores
      .replace(/^_+|_+$/g, '') // Trim leading/trailing underscores
  );
}

function SortIcon<D extends object>({ column }: { column: ColumnInstance<D> }) {
  const { isSorted, isSortedDesc } = column;
  let sortIcon = <FaSort />;
  if (isSorted) {
    sortIcon = isSortedDesc ? <FaSortDesc /> : <FaSortAsc />;
  }
  return sortIcon;
}

/**
 * Label that is visually hidden but accessible
 */
const VisuallyHidden = styled.label`
  position: absolute;
  width: 1px;
  height: 1px;
  padding: 0;
  margin: -1px;
  overflow: hidden;
  clip: rect(0, 0, 0, 0);
  white-space: nowrap;
  border: 0;
`;

const AlertColorLabelContent = styled.span`
  display: inline-flex;
  align-items: center;
`;

const CellBar = styled.div<{ barPaint?: TableCellPaint['cellBar'] }>`
  position: absolute;
  height: 100%;
  display: block;
  top: 0;
  width: ${({ barPaint }) => (barPaint ? `${barPaint.width}%` : undefined)};
  left: ${({ barPaint }) => (barPaint ? `${barPaint.offset}%` : undefined)};
  background-color: ${({ barPaint }) => barPaint?.color};
`;
const ComparisonArrow = styled.span<{ arrowColor: string }>`
  color: ${({ arrowColor }) => arrowColor};
  margin-right: ${({ theme }) => theme.sizeUnit}px;
`;

const AlertColorSwatch = styled.span<{ swatchColor: string }>`
  display: inline-block;
  width: ${({ theme }) => theme.sizeUnit * 4}px;
  height: ${({ theme }) => theme.sizeUnit * 4}px;
  flex: none;
  box-sizing: border-box;
  background-color: ${({ swatchColor }) => swatchColor};
  border: 1px solid ${({ theme }) => theme.colorBorderSecondary};
  border-radius: ${({ theme }) => theme.borderRadiusXS}px;
`;

function AlertColorMenuLabel({
  disabled,
  colorKey,
}: {
  disabled: boolean;
  colorKey: TablePaintColor;
}) {
  const theme = useTheme();
  const labelRef = useRef<HTMLSpanElement>(null);
  const [isMenuItemFocused, setIsMenuItemFocused] = useState(false);
  const [isTooltipOpen, setIsTooltipOpen] = useState(false);
  const presentations = {
    RED: {
      color: theme.colorError,
      label: t('Red'),
    },
    YELLOW: {
      color: theme.colorWarning,
      label: t('Yellow'),
    },
    GREEN: {
      color: theme.colorSuccess,
      label: t('Green'),
    },
  } satisfies Record<
    TablePaintColor,
    {
      color: string;
      label: string;
    }
  >;
  const { color, label } = presentations[colorKey];

  useEffect(() => {
    const menuItem = labelRef.current?.closest<HTMLElement>(
      '[role="menuitemcheckbox"]',
    );
    if (!menuItem) {
      return undefined;
    }

    const handleFocus = () => setIsMenuItemFocused(true);
    const handleBlur = () => setIsMenuItemFocused(false);
    menuItem.addEventListener('focus', handleFocus);
    menuItem.addEventListener('blur', handleBlur);

    return () => {
      menuItem.removeEventListener('focus', handleFocus);
      menuItem.removeEventListener('blur', handleBlur);
    };
  }, []);

  return (
    <Tooltip
      open={isMenuItemFocused || isTooltipOpen}
      onOpenChange={setIsTooltipOpen}
      placement="right"
      title={label}
      trigger={['hover', 'focus']}
    >
      <AlertColorLabelContent ref={labelRef}>
        <AlertColorSwatch
          aria-hidden
          data-test={`alert-color-swatch-${colorKey.toLowerCase()}`}
          swatchColor={disabled ? theme.colorTextDisabled : color}
        />
        <VisuallyHidden as="span">{label}</VisuallyHidden>
      </AlertColorLabelContent>
    </Tooltip>
  );
}

function SearchInput({
  count,
  value,
  onChange,
  onBlur,
  inputRef,
}: SearchInputProps) {
  return (
    <Space direction="horizontal" size={4} className="dt-global-filter">
      {t('Search')}
      <Input
        aria-label={t('Search %s records', count)}
        placeholder={tn('%s record', '%s records...', count, count)}
        value={value}
        onChange={onChange}
        onBlur={onBlur}
        ref={inputRef}
      />
    </Space>
  );
}

function SelectPageSize({
  options,
  current,
  onChange,
}: SelectPageSizeRendererProps) {
  const { Option } = Select;

  return (
    <span className="dt-select-page-size">
      <VisuallyHidden htmlFor="pageSizeSelect">
        {t('Select page size')}
      </VisuallyHidden>
      {t('Show')}{' '}
      <Select<number>
        id="pageSizeSelect"
        value={current}
        onChange={value => onChange(value)}
        size="small"
        css={(theme: SupersetTheme) => css`
          width: ${theme.sizeUnit * 18}px;
        `}
        aria-label={t('Show entries per page')}
      >
        {options.map(option => {
          const [size, text] = Array.isArray(option)
            ? option
            : [option, option];
          return (
            <Option key={size} value={Number(size)}>
              {text}
            </Option>
          );
        })}
      </Select>{' '}
      {t('entries per page')}
    </span>
  );
}

const getNoResultsMessage = (filter: string) =>
  filter ? t('No matching records found') : t('No records found');

export default function TableChart<D extends DataRecord = DataRecord>(
  props: TableChartTransformedProps<D> & {
    sticky?: DataTableProps<D>['sticky'];
  },
) {
  const {
    timeGrain,
    height,
    width,
    data,
    totals,
    isRawRecords,
    rowCount = 0,
    columns: columnsMeta,
    alignPositiveNegative: defaultAlignPN = false,
    colorPositiveNegative: defaultColorPN = false,
    includeSearch = false,
    pageSize = 0,
    serverPagination = false,
    serverPaginationData,
    setDataMask,
    showCellBars = true,
    sortDesc = false,
    filters,
    sticky = true, // whether to use sticky header
    columnColorFormatters,
    alertFormattingRules = [],
    tableColorMetadata,
    tableOwnState = {},
    tableAlertFiltersEnabled = false,
    allowRearrangeColumns = false,
    allowRenderHtml = true,
    onContextMenu,
    emitCrossFilters,
    isUsingTimeComparison,
    basicColorFormatters,
    basicColorColumnFormatters,
    hasServerPageLengthChanged,
    serverPageLength,
    slice_id,
    columnLabelToNameMap = {},
  } = props;

  const comparisonColumns = useMemo(
    () => [
      { key: 'all', label: t('Display all') },
      { key: '#', label: '#' },
      { key: '△', label: '△' },
      { key: '%', label: '%' },
    ],
    [],
  );

  const timestampFormatter = useCallback(
    (value: DataRecordValue) =>
      isRawRecords
        ? String(value ?? '')
        : getTimeFormatterForGranularity(timeGrain)(
            value as number | Date | null | undefined,
          ),
    [timeGrain, isRawRecords],
  );
  const [tableSize, setTableSize] = useState<TableSize>({
    width: 0,
    height: 0,
  });
  // keep track of whether column order changed, so that column widths can too
  const [columnOrderToggle, setColumnOrderToggle] = useState(false);
  const [showComparisonDropdown, setShowComparisonDropdown] = useState(false);
  const [selectedComparisonColumns, setSelectedComparisonColumns] = useState([
    comparisonColumns[0].key,
  ]);
  const [hideComparisonKeys, setHideComparisonKeys] = useState<string[]>([]);
  // recalculated totals to display when the search filter is applied (client-side pagination)
  const [displayedTotals, setDisplayedTotals] = useState<D | undefined>(totals);
  const theme = useTheme();

  const ownStateRef = useRef(tableOwnState);
  const receivedOwnStateRef = useRef(tableOwnState);
  if (receivedOwnStateRef.current !== tableOwnState) {
    receivedOwnStateRef.current = tableOwnState;
    ownStateRef.current = tableOwnState;
  }
  const patchOwnState = useCallback(
    (patch: Partial<TableChartOwnState>) => {
      const next = { ...ownStateRef.current, ...patch };
      ownStateRef.current = next;
      updateTableOwnState(setDataMask, next);
    },
    [setDataMask],
  );
  const [pendingColors, setPendingColors] = useState<
    TableColorSelection[] | undefined
  >();
  const metadataRef = useRef(tableColorMetadata);
  metadataRef.current = tableColorMetadata;
  const fallbackCapabilities = useMemo(
    () => resolveTableFilterCapabilities(alertFormattingRules, columnsMeta),
    [alertFormattingRules, columnsMeta],
  );
  const previousRulesRef = useRef(alertFormattingRules);
  const previousCapabilitiesRef = useRef(fallbackCapabilities);
  useEffect(() => {
    // Hiding a target can invalidate its selection without editing any rule.
    if (
      isEqual(previousRulesRef.current, alertFormattingRules) &&
      isEqual(previousCapabilitiesRef.current, fallbackCapabilities)
    )
      return;
    previousRulesRef.current = alertFormattingRules;
    previousCapabilitiesRef.current = fallbackCapabilities;
    const filter = ownStateRef.current.alertFilter;
    if (!tableAlertFiltersEnabled || filter?.version !== 2) return;
    const selections = filter.selections.filter(selection => {
      const capability = fallbackCapabilities[selection.column];
      return capability?.enabled && capability.supported;
    });
    if (!isEqual(selections, filter.selections)) {
      patchOwnState({
        currentPage: 0,
        alertFilter: { version: 2, selections },
      });
    }
  }, [
    alertFormattingRules,
    fallbackCapabilities,
    patchOwnState,
    tableAlertFiltersEnabled,
  ]);
  useEffect(() => {
    setPendingColors(undefined);
    if (
      serverPagination &&
      tableColorMetadata?.status === 'ready' &&
      tableColorMetadata.row_offset !== undefined
    ) {
      const page = Math.floor(
        tableColorMetadata.row_offset /
          (ownStateRef.current.pageSize ?? pageSize),
      );
      if (page !== (ownStateRef.current.currentPage ?? 0))
        patchOwnState({ currentPage: page });
    }
    if (
      tableColorMetadata?.status === 'ready' &&
      tableColorMetadata.request_error
    ) {
      ownStateRef.current = {
        ...ownStateRef.current,
        alertFilter: {
          version: 2,
          selections: tableColorMetadata.selections ?? [],
          snapshotId: tableColorMetadata.snapshot_id,
          generation: tableColorMetadata.generation,
        },
      };
    }
  }, [tableColorMetadata, pageSize, serverPagination, patchOwnState]);

  const renderAlertFilterDropdown = useCallback(
    (columnKey: string) => {
      if (!tableAlertFiltersEnabled) {
        return null;
      }
      const localCapability = fallbackCapabilities[columnKey];
      const serverCapability = tableColorMetadata?.capabilities[columnKey];
      const capability =
        localCapability?.supported === false
          ? localCapability
          : (serverCapability ?? localCapability);
      if (localCapability?.enabled === false) return null;
      if (!capability?.enabled) {
        return null;
      }
      const ready = tableColorMetadata?.status === 'ready';
      const applied = ready
        ? (tableColorMetadata.selections ??
          tableOwnState.alertFilter?.selections ??
          [])
        : [];
      const selectedKeys =
        (pendingColors ?? applied).find(
          selection => selection.column === columnKey,
        )?.colors ?? [];
      const colorOrder: TablePaintColor[] = ['GREEN', 'YELLOW', 'RED'];
      const catalog = ready
        ? (tableColorMetadata.catalog[columnKey] ?? [])
        : [];
      const colors = colorOrder.filter(
        color => catalog.includes(color) || selectedKeys.includes(color),
      );
      const unavailableReason =
        capability.reason?.message ??
        (tableColorMetadata?.status === 'unavailable'
          ? tableColorMetadata.reason.message
          : !ready
            ? t('Load the table before filtering by color.')
            : undefined);
      const supported = ready && capability.supported;

      return (
        <Dropdown
          trigger={['click']}
          menu={{
            multiple: true,
            selectable: true,
            selectedKeys,
            items: [
              ...(!supported
                ? [{ key: 'reason', label: unavailableReason, disabled: true }]
                : colors.length === 0
                  ? [
                      {
                        key: 'empty',
                        label: t('No colored values'),
                        disabled: true,
                      },
                    ]
                  : []),
              ...colors.map(colorKey => ({
                key: colorKey,
                role: 'menuitemcheckbox',
                'aria-checked': selectedKeys.includes(colorKey),
                label: (
                  <AlertColorMenuLabel
                    disabled={!supported}
                    colorKey={colorKey}
                  />
                ),
                disabled: !supported,
                itemIcon: ({ isSelected }: { isSelected?: boolean }) =>
                  isSelected ? (
                    <Icons.CheckOutlined aria-hidden iconSize="s" />
                  ) : null,
              })),
              {
                key: 'clear',
                label: t('Clear color filter'),
                disabled: selectedKeys.length === 0,
              },
            ],
            onClick: ({ key, domEvent }) => {
              domEvent.stopPropagation();
              const metadata = metadataRef.current;
              if (metadata?.status !== 'ready') return;
              if (
                key !== 'clear' &&
                !colorOrder.includes(key as TablePaintColor)
              )
                return;
              const previous =
                ownStateRef.current.alertFilter?.selections ??
                metadata.selections ??
                [];
              const previousColors =
                previous.find(selection => selection.column === columnKey)
                  ?.colors ?? [];
              const nextColors =
                key === 'clear'
                  ? []
                  : colorOrder.filter(color =>
                      color === key
                        ? !previousColors.includes(color)
                        : previousColors.includes(color),
                    );
              const selections = previous.filter(
                selection => selection.column !== columnKey,
              );
              if (nextColors.length)
                selections.push({ column: columnKey, colors: nextColors });
              setPendingColors(selections);
              patchOwnState({
                currentPage: 0,
                alertFilter: {
                  version: 2,
                  selections,
                  snapshotId: metadata.snapshot_id,
                  generation: metadata.generation,
                },
              });
            },
          }}
        >
          <Button
            aria-label={t('Filter by color for %s', columnKey)}
            aria-busy={pendingColors !== undefined}
            buttonSize="xsmall"
            buttonStyle={selectedKeys.length ? 'primary' : 'link'}
            onClick={event => event.stopPropagation()}
          >
            <Icons.FilterOutlined iconSize="s" />
          </Button>
        </Dropdown>
      );
    },
    [
      fallbackCapabilities,
      patchOwnState,
      pendingColors,
      tableColorMetadata,
      tableAlertFiltersEnabled,
      tableOwnState,
    ],
  );

  useEffect(() => {
    setDisplayedTotals(totals);
  }, [totals]);

  // only take relevant page size options
  const pageSizeOptions = useMemo(() => {
    const getServerPagination = (n: number) => n <= rowCount;
    return (
      serverPagination ? SERVER_PAGE_SIZE_OPTIONS : PAGE_SIZE_OPTIONS
    ).filter(([n]) =>
      serverPagination ? getServerPagination(n) : n <= 2 * data.length,
    ) as SizeOption[];
  }, [data.length, rowCount, serverPagination]);

  const getValueRange = useCallback(
    function getValueRange(key: string, alignPositiveNegative: boolean) {
      const nums = data
        ?.map(row => row?.[key])
        .filter(value => typeof value === 'number') as number[];
      if (nums.length > 0) {
        return (
          alignPositiveNegative
            ? [0, d3Max(nums.map(Math.abs))]
            : d3Extent(nums)
        ) as ValueRange;
      }
      return null;
    },
    [data],
  );

  const isActiveFilterValue = useCallback(
    function isActiveFilterValue(key: string, val: DataRecordValue) {
      if (!filters || !filters[key]) return false;
      return filters[key].some(filterVal => {
        if (filterVal === val) return true;
        // DateWithFormatter extends Date — compare by time value
        // since memoization cache misses can create new instances
        if (filterVal instanceof Date && val instanceof Date) {
          return filterVal.getTime() === val.getTime();
        }
        return false;
      });
    },
    [filters],
  );

  const getCrossFilterDataMask = useCallback(
    (key: string, value: DataRecordValue) => {
      let updatedFilters = { ...filters };
      if (filters && isActiveFilterValue(key, value)) {
        updatedFilters = {};
      } else {
        updatedFilters = {
          [key]: [value],
        };
      }
      if (
        Array.isArray(updatedFilters[key]) &&
        updatedFilters[key].length === 0
      ) {
        delete updatedFilters[key];
      }

      const groupBy = Object.keys(updatedFilters);
      const groupByValues = Object.values(updatedFilters);
      const labelElements: string[] = [];
      groupBy.forEach(col => {
        const isTimestamp = col === DTTM_ALIAS;
        const filterValues = ensureIsArray(updatedFilters?.[col]);
        if (filterValues.length) {
          const valueLabels = filterValues.map(value =>
            isTimestamp ? timestampFormatter(value) : value,
          );
          labelElements.push(`${valueLabels.join(', ')}`);
        }
      });

      return {
        dataMask: {
          extraFormData: {
            filters:
              groupBy.length === 0
                ? []
                : groupBy.map(col => {
                    // Resolve adhoc column labels back to original column names
                    // so that cross-filters work on the receiving chart
                    const resolvedCol = columnLabelToNameMap[col] ?? col;
                    const val = ensureIsArray(updatedFilters?.[col]);
                    if (!val.length)
                      return {
                        col: resolvedCol,
                        op: 'IS NULL' as const,
                      };
                    return {
                      col: resolvedCol,
                      op: 'IN' as const,
                      val: val.map(el =>
                        el instanceof Date ? el.getTime() : el!,
                      ),
                      grain: resolvedCol === DTTM_ALIAS ? timeGrain : undefined,
                    };
                  }),
          },
          filterState: {
            label: labelElements.join(', '),
            value: groupByValues.length ? groupByValues : null,
            filters:
              updatedFilters && Object.keys(updatedFilters).length
                ? updatedFilters
                : null,
          },
        },
        isCurrentValueSelected: isActiveFilterValue(key, value),
      };
    },
    [
      filters,
      isActiveFilterValue,
      timestampFormatter,
      timeGrain,
      columnLabelToNameMap,
    ],
  );

  const toggleFilter = useCallback(
    function toggleFilter(key: string, val: DataRecordValue) {
      if (!emitCrossFilters) {
        return;
      }
      setDataMask(getCrossFilterDataMask(key, val).dataMask);
    },
    [emitCrossFilters, getCrossFilterDataMask, setDataMask],
  );

  const getSharedStyle = useCallback(
    (column: DataColumnMeta): CSSProperties => {
      const { isNumeric, config = {} } = column;
      const textAlign =
        config.horizontalAlign ||
        (isNumeric && !isUsingTimeComparison ? 'right' : 'left');
      return {
        textAlign,
      };
    },
    [isUsingTimeComparison],
  );

  const comparisonLabels = useMemo(() => [t('Main'), '#', '△', '%'], []);

  const filteredColumnsMeta = useMemo(() => {
    if (!isUsingTimeComparison) {
      return columnsMeta;
    }
    const allColumns = comparisonColumns[0].key;
    const main = comparisonLabels[0];
    const showAllColumns = selectedComparisonColumns.includes(allColumns);

    return columnsMeta.filter(({ label, key }) => {
      // Extract the key portion after the space, assuming the format is always "label key"
      const keyPortion = key.substring(label.length);
      const isKeyHidded = hideComparisonKeys.includes(keyPortion);
      const isLableMain = label === main;

      return (
        isLableMain ||
        (!isKeyHidded &&
          (!comparisonLabels.includes(label) ||
            showAllColumns ||
            selectedComparisonColumns.includes(label)))
      );
    });
  }, [
    columnsMeta,
    comparisonColumns,
    comparisonLabels,
    isUsingTimeComparison,
    hideComparisonKeys,
    selectedComparisonColumns,
  ]);

  const handleContextMenu = useMemo(() => {
    if (onContextMenu && !isRawRecords) {
      return (
        value: D,
        cellPoint: {
          key: string;
          value: DataRecordValue;
          isMetric?: boolean;
        },
        clientX: number,
        clientY: number,
      ) => {
        const drillToDetailFilters: BinaryQueryObjectFilterClause[] = [];
        filteredColumnsMeta.forEach(col => {
          if (!col.isMetric) {
            let dataRecordValue = value[col.key];
            dataRecordValue = extractTextFromHTML(dataRecordValue);

            drillToDetailFilters.push({
              col: col.key,
              op: '==',
              val: dataRecordValue as string | number | boolean,
              formattedVal: formatColumnValue(col, dataRecordValue)[1],
            });
          }
        });
        onContextMenu(clientX, clientY, {
          drillToDetail: drillToDetailFilters,
          crossFilter: cellPoint.isMetric
            ? undefined
            : getCrossFilterDataMask(cellPoint.key, cellPoint.value),
          drillBy: cellPoint.isMetric
            ? undefined
            : {
                filters: [
                  {
                    col: cellPoint.key,
                    op: '==',
                    val: extractTextFromHTML(cellPoint.value),
                  },
                ],
                groupbyFieldName: 'groupby',
              },
        });
      };
    }
    return undefined;
  }, [
    onContextMenu,
    isRawRecords,
    filteredColumnsMeta,
    getCrossFilterDataMask,
  ]);

  const getHeaderColumns = useCallback(
    (columnsMeta: DataColumnMeta[], enableTimeComparison?: boolean) => {
      const resultMap: Record<string, number[]> = {};

      if (!enableTimeComparison) {
        return resultMap;
      }

      columnsMeta.forEach((element, index) => {
        // Check if element's label is one of the comparison labels
        if (comparisonLabels.includes(element.label)) {
          // Extract the key portion after the space, assuming the format is always "label key"
          const keyPortion = element.key.substring(element.label.length);

          // If the key portion is not in the map, initialize it with the current index
          if (!resultMap[keyPortion]) {
            resultMap[keyPortion] = [index];
          } else {
            // Add the index to the existing array
            resultMap[keyPortion].push(index);
          }
        }
      });

      return resultMap;
    },
    [comparisonLabels],
  );

  const renderTimeComparisonDropdown = (): JSX.Element => {
    const allKey = comparisonColumns[0].key;
    const handleOnClick = (data: any) => {
      const { key } = data;
      // Toggle 'All' key selection
      if (key === allKey) {
        setSelectedComparisonColumns([allKey]);
      } else if (selectedComparisonColumns.includes(allKey)) {
        setSelectedComparisonColumns([key]);
      } else {
        // Toggle selection for other keys
        setSelectedComparisonColumns(
          selectedComparisonColumns.includes(key)
            ? selectedComparisonColumns.filter(k => k !== key) // Deselect if already selected
            : [...selectedComparisonColumns, key],
        ); // Select if not already selected
      }
    };

    const handleOnBlur = () => {
      if (selectedComparisonColumns.length === 3) {
        setSelectedComparisonColumns([comparisonColumns[0].key]);
      }
    };

    return (
      <Dropdown
        placement="bottomRight"
        open={showComparisonDropdown}
        onOpenChange={(flag: boolean) => {
          setShowComparisonDropdown(flag);
        }}
        menu={{
          multiple: true,
          onClick: handleOnClick,
          onBlur: handleOnBlur,
          selectedKeys: selectedComparisonColumns,
          items: [
            {
              key: 'all',
              label: (
                <div
                  css={css`
                    max-width: 242px;
                    padding: 0 ${theme.sizeUnit * 2}px;
                    color: ${theme.colorText};
                    font-size: ${theme.fontSizeSM}px;
                  `}
                >
                  {t(
                    'Select columns that will be displayed in the table. You can multiselect columns.',
                  )}
                </div>
              ),
              type: 'group',
              children: comparisonColumns.map(
                (column: { key: string; label: string }) => ({
                  key: column.key,
                  label: (
                    <>
                      <span
                        css={css`
                          color: ${theme.colorText};
                        `}
                      >
                        {column.label}
                      </span>
                      <span
                        css={css`
                          float: right;
                          font-size: ${theme.fontSizeSM}px;
                        `}
                      >
                        {selectedComparisonColumns.includes(column.key) && (
                          <CheckOutlined />
                        )}
                      </span>
                    </>
                  ),
                }),
              ),
            },
          ],
        }}
        trigger={['click']}
      >
        <span>
          <TableOutlined /> <DownOutlined />
        </span>
      </Dropdown>
    );
  };

  // Compute visible columns before groupHeaderColumns to ensure index consistency.
  // This filters out columns with config.visible === false.
  const visibleColumnsMeta = useMemo(
    () => filteredColumnsMeta.filter(col => col.config?.visible !== false),
    [filteredColumnsMeta],
  );

  // Use visibleColumnsMeta for groupHeaderColumns to ensure indices match the actual
  // table columns. This fixes header misalignment when columns are filtered.
  const groupHeaderColumns = useMemo(
    () => getHeaderColumns(visibleColumnsMeta, isUsingTimeComparison),
    [visibleColumnsMeta, getHeaderColumns, isUsingTimeComparison],
  );

  const renderGroupingHeaders = (): JSX.Element => {
    // TODO: Make use of ColumnGroup to render the aditional headers
    const headers: any = [];
    let currentColumnIndex = 0;

    // Sort entries by their first column index to ensure correct left-to-right order.
    // Object.entries() maintains insertion order, but when columns are filtered,
    // the first occurrence of each metric might not match the visual column order.
    const sortedEntries = Object.entries(groupHeaderColumns || {}).sort(
      (a, b) => a[1][0] - b[1][0],
    );

    sortedEntries.forEach(([key, value]) => {
      // Calculate the number of placeholder columns needed before the current header
      const startPosition = value[0];
      const colSpan = value.length;
      // Retrieve the originalLabel from the first column in this group.
      // Use visibleColumnsMeta to ensure consistent indexing with the actual table columns.
      const firstColumnInGroup = visibleColumnsMeta[startPosition];
      const originalLabel = firstColumnInGroup
        ? columnsMeta.find(col => col.key === firstColumnInGroup.key)
            ?.originalLabel || key
        : key;

      // Add placeholder <th> for columns before this header
      for (let i = currentColumnIndex; i < startPosition; i += 1) {
        headers.push(
          <th
            key={`placeholder-${i}`}
            style={{ borderBottom: 0 }}
            aria-label={`Header-${i}`}
          />,
        );
      }

      // Add the current header <th>
      headers.push(
        <th key={`header-${key}`} colSpan={colSpan} style={{ borderBottom: 0 }}>
          {originalLabel}
          <span
            css={css`
              float: right;
              & svg {
                color: ${theme.colorIcon} !important;
              }
            `}
          >
            {hideComparisonKeys.includes(key) ? (
              <PlusCircleOutlined
                onClick={() =>
                  setHideComparisonKeys(
                    hideComparisonKeys.filter(k => k !== key),
                  )
                }
              />
            ) : (
              <MinusCircleOutlined
                onClick={() =>
                  setHideComparisonKeys([...hideComparisonKeys, key])
                }
              />
            )}
          </span>
        </th>,
      );

      // Update the current column index
      currentColumnIndex = startPosition + colSpan;
    });

    return (
      <tr
        css={css`
          th {
            border-right: 1px solid ${theme.colorSplit};
          }
          th:first-child {
            border-left: none;
          }
          th:last-child {
            border-right: none;
          }
        `}
      >
        {headers}
      </tr>
    );
  };

  const getColumnConfigs = useCallback(
    (
      column: DataColumnMeta,
      i: number,
    ): ColumnWithLooseAccessor<D> & {
      columnKey: string;
    } => {
      const {
        key,
        label: originalLabel,
        dataType,
        isMetric,
        isPercentMetric,
        config = {},
        description,
      } = column;
      const label = config.customColumnName || originalLabel;
      let displayLabel = label;

      const isComparisonColumn = ['#', '△', '%', t('Main')].includes(
        column.label,
      );

      if (isComparisonColumn) {
        if (column.label === t('Main')) {
          displayLabel = config.customColumnName || column.originalLabel || '';
        } else if (config.customColumnName) {
          displayLabel =
            config.displayTypeIcon !== false
              ? `${column.label} ${config.customColumnName}`
              : config.customColumnName;
        } else if (config.displayTypeIcon === false) {
          displayLabel = '';
        }
      }

      const columnWidth = Number.isNaN(Number(config.columnWidth))
        ? config.columnWidth
        : Number(config.columnWidth);

      // inline style for both th and td cell
      const sharedStyle: CSSProperties = getSharedStyle(column);

      const alignPositiveNegative =
        config.alignPositiveNegative === undefined
          ? defaultAlignPN
          : config.alignPositiveNegative;
      const colorPositiveNegative =
        config.colorPositiveNegative === undefined
          ? defaultColorPN
          : config.colorPositiveNegative;

      const { truncateLongCells } = config;

      const hasBasicColorFormatters =
        isUsingTimeComparison &&
        Array.isArray(basicColorFormatters) &&
        basicColorFormatters.length > 0;
      const generalShowCellBars =
        config.showCellBars === undefined ? showCellBars : config.showCellBars;
      const valueRange =
        !hasBasicColorFormatters &&
        generalShowCellBars &&
        (isMetric || isRawRecords || isPercentMetric) &&
        getValueRange(key, alignPositiveNegative);

      let className = '';
      if (emitCrossFilters && !isMetric) {
        className += ' dt-is-filter';
      }

      if (!isMetric && !isPercentMetric) {
        className += ' right-border-only';
      } else if (comparisonLabels.includes(label)) {
        const groupinHeader = key.substring(label.length);
        const columnsUnderHeader = groupHeaderColumns[groupinHeader] || [];
        if (i === columnsUnderHeader[columnsUnderHeader.length - 1]) {
          className += ' right-border-only';
        }
      }

      // Cache sanitized header ID to avoid recomputing it multiple times
      const headerId = sanitizeHeaderId(column.originalLabel ?? column.key);

      return {
        id: String(i), // to allow duplicate column keys
        // must use custom accessor to allow `.` in column names
        // typing is incorrect in current version of `@types/react-table`
        // so we ask TS not to check.
        columnKey: key,
        accessor: ((datum: D) => datum[key]) as never,
        Cell: ({ value, row }: { value: DataRecordValue; row: Row<D> }) => {
          const [isHtml, text] = formatColumnValue(column, value, row.original);
          const html = isHtml && allowRenderHtml ? { __html: text } : undefined;

          const originKey = column.key.substring(column.label.length).trim();
          const resolved = resolveTableCellStyle({
            columnKey: key,
            columnLabel: column.label,
            value,
            row: row.original,
            rowIndex: row.index,
            columnColorFormatters,
            hasBasicColorFormatters,
            basicColorFormatter: basicColorFormatters?.[row.index]?.[originKey],
            hasBasicColorColumnFormatters: Boolean(
              basicColorColumnFormatters?.length,
            ),
            basicColorColumnFormatter:
              basicColorColumnFormatters?.[row.index]?.[key],
            comparisonMainLabel: comparisonLabels[0],
            showCellBars: generalShowCellBars,
            valueRange: valueRange || false,
            alignPositiveNegative,
            colorPositiveNegative,
            theme,
            renderHtml: Boolean(html),
          });
          // The snapshot owns every style dimension; a missing dimension must
          // not be recomputed against the smaller, filtered result set.
          const hasFrozenPaint =
            tableAlertFiltersEnabled && tableColorMetadata?.status === 'ready';
          const paint = hasFrozenPaint
            ? (tableColorMetadata.styles[row.index]?.[key] ?? { colors: [] })
            : resolved;
          const textColor = hasFrozenPaint
            ? getTextColorForBackground(
                {
                  backgroundColor: paint.backgroundColor,
                  color: paint.textColor,
                },
                row.index % 2 === 0 ? theme.colorBgLayout : theme.colorBgBase,
              )
            : paint.textColor;
          const arrow = paint.arrow?.symbol;
          const StyledCell = styled.td`
            text-align: ${sharedStyle.textAlign};
            white-space: ${value instanceof Date ? 'nowrap' : undefined};
            position: relative;
            font-weight: ${(
              hasFrozenPaint
                ? Boolean(paint.textColor)
                : resolved.hasExplicitTextColor
            )
              ? `${theme.fontWeightBold}`
              : `${theme.fontWeightNormal}`};
            background: ${paint.backgroundColor || undefined};
            padding-left: ${column.isChildColumn
              ? `${theme.sizeUnit * 5}px`
              : `${theme.sizeUnit}px`};
          `;

          const cellProps = {
            'aria-labelledby': `header-${headerId}`,
            role: 'cell',
            // show raw number in title in case of numeric values
            title: typeof value === 'number' ? String(value) : undefined,
            onClick:
              emitCrossFilters && !valueRange && !isMetric
                ? () => {
                    // allow selecting text in a cell
                    if (!getSelectedText()) {
                      toggleFilter(key, value);
                    }
                  }
                : undefined,
            onContextMenu: (e: MouseEvent) => {
              if (handleContextMenu) {
                e.preventDefault();
                e.stopPropagation();
                handleContextMenu(
                  row.original,
                  { key, value, isMetric },
                  e.nativeEvent.clientX,
                  e.nativeEvent.clientY,
                );
              }
            },
            className: [
              className,
              value == null ||
              (value instanceof DateWithFormatter && value.input == null)
                ? 'dt-is-null'
                : '',
              isActiveFilterValue(key, value) ? ' dt-is-active-filter' : '',
            ].join(' '),
            style: textColor
              ? ({ color: textColor } as CSSProperties)
              : undefined,
            tabIndex: 0,
          };
          if (html) {
            if (truncateLongCells) {
              return (
                <StyledCell {...cellProps}>
                  <div
                    className="dt-truncate-cell"
                    style={columnWidth ? { width: columnWidth } : undefined}
                    // Safe: HTML is sanitized via formatColumnValue
                    // eslint-disable-next-line react/no-danger
                    dangerouslySetInnerHTML={html}
                  />
                </StyledCell>
              );
            }
            // Safe: HTML is sanitized via formatColumnValue
            // eslint-disable-next-line react/no-danger
            return <StyledCell {...cellProps} dangerouslySetInnerHTML={html} />;
          }
          // If cellProps renders textContent already, then we don't have to
          // render `Cell`. This saves some time for large tables.
          return (
            <StyledCell {...cellProps}>
              {(hasFrozenPaint
                ? paint.cellBar
                : valueRange || paint.cellBar) && (
                <CellBar
                  /* The following classes are added to support custom CSS styling */
                  className={cx(
                    'cell-bar',
                    typeof value === 'number' && value < 0
                      ? 'negative'
                      : 'positive',
                  )}
                  barPaint={paint.cellBar}
                  role="presentation"
                />
              )}
              {truncateLongCells ? (
                <div
                  className="dt-truncate-cell"
                  style={columnWidth ? { width: columnWidth } : undefined}
                >
                  {arrow && paint.arrow && (
                    <ComparisonArrow arrowColor={paint.arrow.color}>
                      {arrow}
                    </ComparisonArrow>
                  )}
                  {text}
                </div>
              ) : (
                <>
                  {arrow && paint.arrow && (
                    <ComparisonArrow arrowColor={paint.arrow.color}>
                      {arrow}
                    </ComparisonArrow>
                  )}
                  {text}
                </>
              )}
            </StyledCell>
          );
        },
        Header: ({ column: col, onClick, style, onDragStart, onDrop }) => (
          <th
            id={`header-${headerId}`}
            title={
              description || t('Shift + Click to sort by multiple columns')
            }
            className={[className, col.isSorted ? 'is-sorted' : ''].join(' ')}
            style={{
              ...sharedStyle,
              ...style,
            }}
            onKeyDown={(e: ReactKeyboardEvent<HTMLElement>) => {
              // programatically sort column on keypress
              if (Object.values(ACTION_KEYS).includes(e.key)) {
                col.toggleSortBy();
              }
            }}
            role="columnheader button"
            onClick={onClick}
            data-column-name={col.id}
            {...(allowRearrangeColumns && {
              draggable: 'true',
              onDragStart,
              onDragOver: e => e.preventDefault(),
              onDragEnter: e => e.preventDefault(),
              onDrop,
            })}
            tabIndex={0}
          >
            {/* can't use `columnWidth &&` because it may also be zero */}
            {config.columnWidth ? (
              // column width hint
              <div
                style={{
                  width: columnWidth,
                  height: 0.01,
                }}
              />
            ) : null}
            <div
              data-column-name={col.id}
              css={{
                display: 'inline-flex',
                alignItems: 'flex-end',
              }}
            >
              <span data-column-name={col.id}>{displayLabel}</span>
              <SortIcon column={col} />
              {renderAlertFilterDropdown(key)}
            </div>
          </th>
        ),

        Footer: displayedTotals ? (
          i === 0 ? (
            <th key={`footer-summary-${i}`}>
              <div
                css={css`
                  display: flex;
                  align-items: center;
                  & svg {
                    margin-left: ${theme.sizeUnit}px;
                    color: ${theme.colorBorder} !important;
                  }
                `}
              >
                {t('Summary')}
                <Tooltip
                  overlay={t(
                    'Show total aggregations of selected metrics. Note that row limit does not apply to the result.',
                  )}
                >
                  <InfoCircleOutlined />
                </Tooltip>
              </div>
            </th>
          ) : (
            <td key={`footer-total-${i}`} style={sharedStyle}>
              <strong>
                {formatColumnValue(column, displayedTotals[key])[1]}
              </strong>
            </td>
          )
        ) : undefined,
        sortDescFirst: sortDesc,
        sortType: getSortTypeByDataType(dataType),
      };
    },
    [
      getSharedStyle,
      defaultAlignPN,
      defaultColorPN,
      columnColorFormatters,
      isUsingTimeComparison,
      basicColorFormatters,
      showCellBars,
      isRawRecords,
      getValueRange,
      emitCrossFilters,
      comparisonLabels,
      displayedTotals,
      theme,
      sortDesc,
      groupHeaderColumns,
      allowRenderHtml,
      basicColorColumnFormatters,
      isActiveFilterValue,
      toggleFilter,
      handleContextMenu,
      allowRearrangeColumns,
      renderAlertFilterDropdown,
      tableAlertFiltersEnabled,
      tableColorMetadata,
    ],
  );

  const columns = useMemo(
    () => visibleColumnsMeta.map(getColumnConfigs),
    [visibleColumnsMeta, getColumnConfigs],
  );

  const [searchOptions, setSearchOptions] = useState<SearchOption[]>([]);

  const handleFilteredDataChange = useCallback(
    (rows: Row<D>[], searchText?: string) => {
      if (!totals || serverPagination) {
        return;
      }

      if (!searchText?.trim()) {
        setDisplayedTotals(totals);
        return;
      }

      const updatedTotals: Record<string, DataRecordValue> = { ...totals };

      filteredColumnsMeta.forEach(column => {
        if (column.isMetric || column.isPercentMetric) {
          const aggregatedValue = rows.reduce<number>((acc, row) => {
            const rawValue = row.original?.[column.key];
            const numValue = Number(String(rawValue ?? '').replace(/,/g, ''));
            return Number.isFinite(numValue) ? acc + numValue : acc;
          }, 0);

          updatedTotals[column.key] = aggregatedValue;
        }
      });

      setDisplayedTotals(updatedTotals as D);
    },
    [filteredColumnsMeta, serverPagination, totals],
  );

  useEffect(() => {
    const options = (
      columns as unknown as ColumnWithLooseAccessor &
        {
          columnKey: string;
          sortType?: string;
        }[]
    )
      .filter(col => col?.sortType === 'alphanumeric')
      .map(column => ({
        value: column.columnKey,
        label: column.columnKey,
      }));

    if (!isEqual(options, searchOptions)) {
      setSearchOptions(options || []);
    }
  }, [columns, searchOptions]);

  const handleServerPaginationChange = useCallback(
    (pageNumber: number, pageSize: number) => {
      const previous = ownStateRef.current;
      const pageSizeChanged =
        pageSize !== (previous.pageSize ?? serverPageLength);
      patchOwnState({
        currentPage: pageNumber,
        pageSize,
        ...(pageSizeChanged && previous.alertFilter
          ? {
              alertFilter: {
                version: 2,
                selections: previous.alertFilter.selections,
              },
            }
          : {}),
      });
    },
    [patchOwnState, serverPageLength],
  );

  useEffect(() => {
    if (hasServerPageLengthChanged) {
      const previous = ownStateRef.current.alertFilter;
      patchOwnState({
        currentPage: 0,
        pageSize: serverPageLength,
        ...(previous
          ? { alertFilter: { version: 2, selections: previous.selections } }
          : {}),
      });
    }
  }, [hasServerPageLengthChanged, serverPageLength, patchOwnState]);

  const handleSizeChange = useCallback(
    ({ width, height }: { width: number; height: number }) => {
      setTableSize({ width, height });
    },
    [],
  );

  useLayoutEffect(() => {
    // After initial load the table should resize only when the new sizes
    // Are not only scrollbar updates, otherwise, the table would twitch
    const scrollBarSize = getScrollBarSize();
    const { width: tableWidth, height: tableHeight } = tableSize;
    // Table is increasing its original size
    if (
      width - tableWidth > scrollBarSize ||
      height - tableHeight > scrollBarSize
    ) {
      handleSizeChange({
        width: width - scrollBarSize,
        height: height - scrollBarSize,
      });
    } else if (
      tableWidth - width > scrollBarSize ||
      tableHeight - height > scrollBarSize
    ) {
      // Table is decreasing its original size
      handleSizeChange({
        width,
        height,
      });
    }
  }, [width, height, handleSizeChange, tableSize]);

  const { width: widthFromState, height: heightFromState } = tableSize;

  const handleSortByChange = useCallback(
    (sortBy: SortByItem[]) => {
      if (!serverPagination) return;
      if (isEqual(sortBy, ownStateRef.current.sortBy ?? [])) return;
      const previous = ownStateRef.current.alertFilter;
      patchOwnState({
        sortBy,
        currentPage: 0,
        ...(previous
          ? { alertFilter: { version: 2, selections: previous.selections } }
          : {}),
      });
    },
    [serverPagination, patchOwnState],
  );

  const handleSearch = (searchText: string) => {
    const previous = ownStateRef.current.alertFilter;
    patchOwnState({
      searchColumn: ownStateRef.current.searchColumn || searchOptions[0]?.value,
      searchText,
      currentPage: 0, // Reset to first page when searching
      ...(previous
        ? { alertFilter: { version: 2, selections: previous.selections } }
        : {}),
    });
  };

  const debouncedSearch = debounce(handleSearch, 800);

  const handleChangeSearchCol = (searchCol: string) => {
    if (!isEqual(searchCol, ownStateRef.current.searchColumn)) {
      const previous = ownStateRef.current.alertFilter;
      patchOwnState({
        searchColumn: searchCol,
        searchText: '',
        currentPage: 0,
        ...(previous
          ? { alertFilter: { version: 2, selections: previous.selections } }
          : {}),
      });
    }
  };

  // Client search/sort changes only the export projection, never the paint source.
  const clientRowsKey =
    tableColorMetadata?.status === 'ready' && tableColorMetadata.row_indices
      ? `${tableColorMetadata.snapshot_id}:${tableColorMetadata.row_indices.join(',')}`
      : undefined;
  const [clientViewData, setClientViewData] = useState<{
    rows: DataRecord[];
    indices: number[];
    sourceKey?: string;
  }>({ rows: [], indices: [] });
  const receiveClientViewRows = useCallback(
    (rows: D[], indices: number[] = []) =>
      setClientViewData({ rows, indices, sourceKey: clientRowsKey }),
    [clientRowsKey],
  );
  const clientViewRows = clientViewData.rows;

  const exportColumns = useMemo(
    () =>
      visibleColumnsMeta.map(col => ({
        key: col.key,
        label: col.config?.customColumnName || col.originalLabel || col.key,
      })),
    [visibleColumnsMeta],
  );

  // Use a ref to store previous clientViewRows and exportColumns for robust change detection
  const prevClientViewRef = useRef<{
    rows: DataRecord[];
    columns: typeof exportColumns;
  } | null>(null);
  useEffect(() => {
    if (serverPagination) return;
    // Color exports send only immutable row references, never browser row data.
    // The snapshot export path remains active when every target is hidden.
    if (
      tableAlertFiltersEnabled &&
      alertFormattingRules.some(rule => rule.filterable === true)
    ) {
      const ready =
        tableColorMetadata?.status === 'ready' ? tableColorMetadata : undefined;
      const indices = ready?.row_indices;
      if (
        !ready ||
        !indices ||
        clientViewData.sourceKey !== clientRowsKey ||
        clientViewData.indices.some(index => indices[index] === undefined)
      ) {
        if (ownStateRef.current.clientView)
          patchOwnState({ clientView: undefined });
        return;
      }
      const clientView = {
        rows: [],
        columns: exportColumns,
        count: clientViewData.indices.length,
        snapshotId: ready.snapshot_id,
        rowIndices: clientViewData.indices.map(index => indices[index]),
      };
      if (!isEqual(ownStateRef.current.clientView, clientView))
        patchOwnState({ clientView });
      return;
    }
    const prev = prevClientViewRef.current;
    const rowsChanged = !prev || !isEqual(prev.rows, clientViewRows);
    const columnsChanged = !prev || !isEqual(prev.columns, exportColumns);
    if (rowsChanged || columnsChanged) {
      prevClientViewRef.current = {
        rows: clientViewRows,
        columns: exportColumns,
      };
      patchOwnState({
        clientView: {
          rows: clientViewRows,
          columns: exportColumns,
          count: clientViewRows.length,
        },
      });
    }
  }, [
    clientViewRows,
    exportColumns,
    serverPagination,
    patchOwnState,
    tableAlertFiltersEnabled,
    alertFormattingRules,
    tableColorMetadata,
    clientViewData,
    clientRowsKey,
  ]);

  return (
    <Styles>
      <DataTable<D>
        columns={columns}
        data={data}
        rowCount={rowCount}
        tableClassName="table table-striped table-condensed"
        pageSize={pageSize}
        serverPaginationData={serverPaginationData}
        pageSizeOptions={pageSizeOptions}
        width={widthFromState}
        height={heightFromState}
        serverPagination={serverPagination}
        onServerPaginationChange={handleServerPaginationChange}
        onColumnOrderChange={() => setColumnOrderToggle(!columnOrderToggle)}
        initialSearchText={serverPaginationData?.searchText || ''}
        sortByFromParent={serverPaginationData?.sortBy || []}
        searchInputId={`${slice_id}-search`}
        // 9 page items in > 340px works well even for 100+ pages
        maxPageItemCount={width > 340 ? 9 : 7}
        noResults={getNoResultsMessage}
        searchInput={includeSearch && SearchInput}
        selectPageSize={pageSize !== null && SelectPageSize}
        // not in use in Superset, but needed for unit tests
        sticky={sticky}
        renderGroupingHeaders={
          !isEmpty(groupHeaderColumns) ? renderGroupingHeaders : undefined
        }
        renderTimeComparisonDropdown={
          isUsingTimeComparison ? renderTimeComparisonDropdown : undefined
        }
        handleSortByChange={handleSortByChange}
        onSearchColChange={handleChangeSearchCol}
        manualSearch={serverPagination}
        onSearchChange={debouncedSearch}
        searchOptions={searchOptions}
        onFilteredDataChange={handleFilteredDataChange}
        onFilteredRowsChange={receiveClientViewRows}
        filteredRowsKey={clientRowsKey}
      />
    </Styles>
  );
}
