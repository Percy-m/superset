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

import { Table as AntTable } from 'antd';
import {
  TablePaginationConfig,
  TableProps as AntTableProps,
} from 'antd/es/table';
import classNames from 'classnames';
import { useResizeDetector } from 'react-resize-detector';
import {
  CSSProperties,
  createContext,
  forwardRef,
  HTMLAttributes,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  useCallback,
} from 'react';
import { VariableSizeGrid as Grid } from 'react-window';
import { safeHtmlSpan } from '@superset-ui/core';
import { useTheme, styled } from '@apache-superset/core/theme';

import { TableSize, ETableAction } from './index';

export interface VirtualTableProps<
  RecordType,
> extends AntTableProps<RecordType> {
  height?: number;
  allowHTML?: boolean;
}

type VirtualColumns<RecordType> = NonNullable<
  AntTableProps<RecordType>['columns']
>;

export interface VirtualColumnLayout<RecordType> {
  columns: VirtualColumns<RecordType>;
  contentWidth: number;
  estimatedColumnWidth: number;
}

const DEFAULT_MIN_COLUMN_WIDTH = 50;

const isFiniteNumber = (value: unknown): value is number =>
  typeof value === 'number' && Number.isFinite(value);

/**
 * Resolve one immutable column layout shared by the AntD header and virtual body.
 */
export function resolveVirtualColumnLayout<RecordType extends object>(
  columns: VirtualColumns<RecordType> | undefined,
  containerWidth: number,
  defaultColumnWidth: number,
  minimumColumnWidth = DEFAULT_MIN_COLUMN_WIDTH,
): VirtualColumnLayout<RecordType> {
  if (!columns?.length) {
    return {
      columns: [],
      contentWidth: 0,
      estimatedColumnWidth: 0,
    };
  }

  const minWidth =
    isFiniteNumber(minimumColumnWidth) && minimumColumnWidth > 0
      ? minimumColumnWidth
      : DEFAULT_MIN_COLUMN_WIDTH;
  const fallbackWidth =
    isFiniteNumber(defaultColumnWidth) && defaultColumnWidth > 0
      ? Math.max(defaultColumnWidth, minWidth)
      : minWidth;
  const availableContainerWidth =
    isFiniteNumber(containerWidth) && containerWidth > 0 ? containerWidth : 0;
  const explicitWidths = columns.map(({ width }) =>
    isFiniteNumber(width) ? Math.max(width, minWidth) : undefined,
  );
  const fixedWidth = explicitWidths.reduce<number>(
    (total, width) => total + (width ?? 0),
    0,
  );
  const missingWidthCount = explicitWidths.filter(
    width => width === undefined,
  ).length;
  const distributedWidth =
    missingWidthCount > 0 && availableContainerWidth > 0
      ? Math.max(
          Math.floor(
            (availableContainerWidth - fixedWidth) / missingWidthCount,
          ),
          minWidth,
        )
      : fallbackWidth;

  const resolvedColumns = columns.map((column, index) => ({
    ...column,
    width: explicitWidths[index] ?? distributedWidth,
  })) as VirtualColumns<RecordType>;
  let contentWidth = resolvedColumns.reduce(
    (total, { width }) => total + (width as number),
    0,
  );

  if (contentWidth < availableContainerWidth) {
    const lastColumnIndex = resolvedColumns.length - 1;
    const lastColumn = resolvedColumns[lastColumnIndex];
    resolvedColumns[lastColumnIndex] = {
      ...lastColumn,
      width:
        (lastColumn.width as number) + (availableContainerWidth - contentWidth),
    };
    contentWidth = availableContainerWidth;
  }

  return {
    columns: resolvedColumns,
    contentWidth,
    estimatedColumnWidth: contentWidth / resolvedColumns.length,
  };
}

const StyledCell = styled('div')(
  ({ theme }) => `
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  padding-inline: ${theme.paddingXS}px;
  border-bottom: 1px solid ${theme.colorSplit};
  transition: background 0.3s;
  line-height: ${theme.lineHeight};
  box-sizing: border-box;
`,
);

const VirtualGridContentWidthContext = createContext(0);

const VirtualGridInner = forwardRef<
  HTMLDivElement,
  HTMLAttributes<HTMLDivElement>
>(({ style, ...rest }, innerRef) => {
  const contentWidth = useContext(VirtualGridContentWidthContext);

  return (
    <div
      {...rest}
      ref={innerRef}
      style={{
        ...style,
        width: contentWidth,
      }}
    />
  );
});

VirtualGridInner.displayName = 'VirtualGridInner';

const StyledTable = styled(AntTable)(
  ({ theme }) => `
    th.ant-table-cell {
      font-weight: ${theme.fontWeightStrong};
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
    }

    .ant-spin-nested-loading .ant-spin .ant-spin-dot {
      width: ${theme.sizeUnit * 12}px;
      height: unset;
    }
`,
) as unknown as typeof AntTable;

const SMALL = 39;
const MIDDLE = 47;

const VirtualTable = <RecordType extends object>(
  props: VirtualTableProps<RecordType>,
) => {
  const {
    columns,
    pagination,
    onChange,
    height,
    scroll,
    size,
    allowHTML = false,
  } = props;
  const [tableWidth, setTableWidth] = useState<number>(0);
  const onResize = useCallback((width: number) => {
    setTableWidth(width);
  }, []);
  const { ref } = useResizeDetector({ onResize });
  const theme = useTheme();

  // If a column definition has no width, react-window will use this as the default column width
  const DEFAULT_COL_WIDTH = theme?.sizeUnit * 37 || 150;
  const {
    columns: mergedColumns,
    contentWidth,
    estimatedColumnWidth,
  } = useMemo(
    () => resolveVirtualColumnLayout(columns, tableWidth, DEFAULT_COL_WIDTH),
    [columns, tableWidth, DEFAULT_COL_WIDTH],
  );
  const columnWidthSignature = mergedColumns
    .map(({ width }) => width)
    .join(',');

  const gridRef = useRef<any>();
  const [connectObject] = useState<any>(() => {
    const obj = {};
    Object.defineProperty(obj, 'scrollLeft', {
      get: () => {
        if (gridRef.current) {
          return gridRef.current?.state?.scrollLeft;
        }
        return 0;
      },
      set: (scrollLeft: number) => {
        if (gridRef.current) {
          gridRef.current.scrollTo({ scrollLeft });
        }
      },
    });

    return obj;
  });

  useEffect(() => {
    const grid = gridRef.current;
    if (!grid) {
      return;
    }

    grid.resetAfterIndices({
      columnIndex: 0,
      rowIndex: 0,
      shouldForceUpdate: true,
    });
  }, [columnWidthSignature, size, tableWidth]);

  /*
   * antd Table has a runtime error when it tries to fire the onChange event triggered from a pageChange
   * when the table body is overridden with the virtualized table.  This function capture the page change event
   * from within the pagination controls and proxies the onChange event payload
   */
  const onPageChange = (page: number, size: number) => {
    /**
     * This resets vertical scroll position to 0 (top) when page changes
     * We intentionally leave horizontal scroll where it was so user can focus on
     * specific range of columns as they page through data
     */
    gridRef.current?.scrollTo?.({ scrollTop: 0 });

    onChange?.(
      {
        ...pagination,
        current: page,
        pageSize: size,
      } as TablePaginationConfig,
      {},
      {},
      {
        action: ETableAction.Paginate,
        currentDataSource: [],
      },
    );
  };

  const renderVirtualList = (
    rawData: readonly object[],
    { ref, onScroll }: any,
  ) => {
    // eslint-disable-next-line no-param-reassign
    ref.current = connectObject;
    const cellSize = size === TableSize.Middle ? MIDDLE : SMALL;
    return (
      <VirtualGridContentWidthContext.Provider value={contentWidth}>
        <Grid
          ref={gridRef}
          className="virtual-grid"
          columnCount={mergedColumns.length}
          estimatedColumnWidth={estimatedColumnWidth}
          columnWidth={(index: number) => {
            const { width = DEFAULT_COL_WIDTH } = mergedColumns[index];
            return width as number;
          }}
          height={height || (scroll!.y as number)}
          innerElementType={VirtualGridInner}
          rowCount={rawData.length}
          rowHeight={() => cellSize}
          width={tableWidth}
          onScroll={({ scrollLeft }: { scrollLeft: number }) => {
            onScroll({ scrollLeft });
          }}
        >
          {({
            columnIndex,
            rowIndex,
            style,
          }: {
            columnIndex: number;
            rowIndex: number;
            style: CSSProperties;
          }) => {
            const data: any = rawData?.[rowIndex];
            // Set default content
            let content =
              data?.[(mergedColumns as any)?.[columnIndex]?.dataIndex];
            // Check if the column has a render function
            const render = mergedColumns[columnIndex]?.render;
            if (typeof render === 'function') {
              // Use render function to generate formatted content using column's render function
              content = render(content, data, rowIndex);
            }

            if (allowHTML && typeof content === 'string') {
              content = safeHtmlSpan(content);
            }

            return (
              <StyledCell
                className={classNames('virtual-table-cell', {
                  'virtual-table-cell-last':
                    columnIndex === mergedColumns.length - 1,
                })}
                style={{
                  ...style,
                  paddingBlock:
                    size === TableSize.Middle
                      ? theme.paddingSM
                      : theme.paddingXS,
                }}
                title={typeof content === 'string' ? content : undefined}
              >
                {content}
              </StyledCell>
            );
          }}
        </Grid>
      </VirtualGridContentWidthContext.Provider>
    );
  };

  const modifiedPagination = {
    ...pagination,
    onChange: onPageChange,
  };

  return (
    <div ref={ref}>
      <StyledTable
        {...props}
        sticky={false}
        className="virtual-table"
        components={{
          body: renderVirtualList,
        }}
        pagination={pagination ? modifiedPagination : false}
        scroll={{ ...scroll, x: contentWidth }}
        columns={mergedColumns}
      />
    </div>
  );
};

export default VirtualTable;
