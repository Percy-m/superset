/**
 * Licensed to the Apache Software Foundation (ASF) under one
 * or more contributor license agreements. See the NOTICE file
 * distributed with this work for additional information
 * regarding copyright ownership. The ASF licenses this file
 * to you under the Apache License, Version 2.0 (the
 * "License"); you may not use this file except in compliance
 * with the License. You may obtain a copy of the License at
 *
 *   http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing,
 * software distributed under the License is distributed on an
 * "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY
 * KIND, either express or implied. See the License for the
 * specific language governing permissions and limitations
 * under the License.
 */
import fs from 'fs';
import path from 'path';
import { extent, max } from 'd3-array';
import { GenericDataType } from '@apache-superset/core/common';
import { supersetTheme } from '@apache-superset/core/theme';
import {
  ColumnFilterCapability,
  DataRecord,
  TableCellPaint,
} from '@superset-ui/core';
import {
  Comparator,
  getColorFormatters,
  ObjectFormattingEnum,
} from '@superset-ui/chart-controls';
import transformProps from '../src/transformProps';
import { TableChartProps } from '../src/types';
import {
  ResolveTableCellStyleInput,
  getTableColorFilterTargets,
  resolveTableCellStyle,
  resolveTableFilterCapabilities,
  TableFormattingRule,
  tablePaintColorForScheme,
  toTableCellPaint,
} from '../src/utils/resolveTableCellStyle';
import { formatColumnValue } from '../src/utils/formatValue';
import DateWithFormatter from '../src/utils/DateWithFormatter';
import testData from './testData';

interface GoldenCase {
  name: string;
  records: DataRecord[];
  columns: string[];
  coltypes: GenericDataType[];
  source_page_size?: number;
  theme?: Partial<typeof supersetTheme>;
  form_data: Partial<TableChartProps['rawFormData']>;
  expected: {
    records?: DataRecord[];
    styles: Record<string, Partial<TableCellPaint>>[];
    catalog?: Record<string, string[]>;
    capabilities?: Record<string, ColumnFilterCapability>;
  };
}

const golden = JSON.parse(
  fs.readFileSync(
    path.resolve(
      __dirname,
      '../../../../tests/testdata/table_color_filters/styles.json',
    ),
    'utf8',
  ),
) as { cases: GoldenCase[]; default_theme?: Partial<typeof supersetTheme> };
const theme = {
  ...supersetTheme,
  colorSuccess: '#52c41a',
  colorWarning: '#faad14',
  colorError: '#ff4d4f',
  ...golden.default_theme,
};

function resolveGolden(testCase: GoldenCase) {
  const pageSize = testCase.source_page_size || testCase.records.length || 1;
  const styles: Record<string, TableCellPaint>[] = [];
  const records: DataRecord[] = [];
  let capabilities: Record<string, ColumnFilterCapability> = {};
  for (let start = 0; start < testCase.records.length; start += pageSize) {
    const passedTheme = { ...theme, ...testCase.theme };
    const transformed = transformProps({
      ...testData.raw,
      rawFormData: {
        datasource: '1__table',
        viz_type: 'table',
        ...testCase.form_data,
      },
      theme: passedTheme,
      queriesData: [
        {
          ...testData.raw.queriesData[0],
          data: testCase.records.slice(start, start + pageSize),
          colnames: testCase.columns,
          coltypes: testCase.coltypes,
        },
      ],
    });
    const visibleColumns = transformed.columns.filter(
      column => column.config?.visible !== false,
    );
    capabilities = resolveTableFilterCapabilities(
      testCase.form_data.conditional_formatting as TableFormattingRule[],
      transformed.columns,
    );
    transformed.data.forEach((row, rowIndex) => {
      const rowStyles: Record<string, TableCellPaint> = {};
      const record: DataRecord = {};
      visibleColumns.forEach(column => {
        const value = row[column.key];
        const alignPositiveNegative =
          column.config?.alignPositiveNegative ??
          transformed.alignPositiveNegative ??
          false;
        const hasBasic = Boolean(
          transformed.isUsingTimeComparison &&
          transformed.basicColorFormatters?.length,
        );
        const showCellBars =
          column.config?.showCellBars ?? transformed.showCellBars ?? true;
        const values = transformed.data
          .map(item => item[column.key])
          .filter((item): item is number => typeof item === 'number');
        const valueRange =
          !hasBasic &&
          showCellBars &&
          (column.isMetric ||
            transformed.isRawRecords ||
            column.isPercentMetric) &&
          values.length
            ? ((alignPositiveNegative
                ? [0, max(values.map(Math.abs))]
                : extent(values)) as [number, number])
            : false;
        const originKey = column.key.substring(column.label.length).trim();
        const paint = resolveTableCellStyle({
          columnKey: column.key,
          columnLabel: column.label,
          value,
          row,
          rowIndex,
          columnColorFormatters: transformed.columnColorFormatters,
          hasBasicColorFormatters: hasBasic,
          basicColorFormatter:
            transformed.basicColorFormatters?.[rowIndex]?.[originKey],
          hasBasicColorColumnFormatters: Boolean(
            transformed.basicColorColumnFormatters?.length,
          ),
          basicColorColumnFormatter:
            transformed.basicColorColumnFormatters?.[rowIndex]?.[column.key],
          comparisonMainLabel: 'Main',
          showCellBars,
          valueRange,
          alignPositiveNegative,
          colorPositiveNegative:
            column.config?.colorPositiveNegative ??
            transformed.colorPositiveNegative ??
            false,
          theme: passedTheme,
          renderHtml: Boolean(
            transformed.allowRenderHtml &&
            formatColumnValue(column, value, row)[0],
          ),
        });
        rowStyles[column.key] = toTableCellPaint(paint);
        record[column.key] =
          value instanceof DateWithFormatter ? value.input : value;
      });
      styles.push(rowStyles);
      records.push(record);
    });
  }
  const catalog = Object.fromEntries(
    Object.entries(capabilities)
      .filter(([, capability]) => capability.enabled)
      .map(([column]) => [
        column,
        (['GREEN', 'YELLOW', 'RED'] as const).filter(color =>
          styles.some(row => row[column]?.colors.includes(color)),
        ),
      ]),
  );
  return { styles, records, capabilities, catalog };
}

test.each(golden.cases)(
  'matches shared native color fixture: $name',
  testCase => {
    const result = resolveGolden(testCase);
    expect(result.styles).toHaveLength(testCase.expected.styles.length);
    testCase.expected.styles.forEach((row, index) => {
      expect(Object.keys(result.styles[index])).toEqual(Object.keys(row));
      Object.entries(row).forEach(([column, expected]) => {
        expect(result.styles[index][column]).toEqual(expected);
        expect(result.styles[index][column].colors).toEqual(expected.colors);
      });
    });
    Object.entries(testCase.expected).forEach(([field, expected]) => {
      expect(result[field as keyof typeof result]).toEqual(expected);
    });
  },
);

const input: ResolveTableCellStyleInput = {
  columnKey: 'profit',
  columnLabel: 'profit',
  value: 5,
  row: { profit: 5 },
  rowIndex: 0,
  comparisonMainLabel: 'Main',
  showCellBars: true,
  valueRange: [0, 10],
  alignPositiveNegative: true,
  colorPositiveNegative: true,
  theme,
};

test.each([
  { value: -5, color: '#ff4d4f50', colorPositiveNegative: true },
  { value: 5, color: '#52c41a50', colorPositiveNegative: true },
  { value: 5, color: theme.colorFill, colorPositiveNegative: false },
])(
  'default Cell Bar $color for $value is rendered but not indexed',
  ({ value, color, colorPositiveNegative }) => {
    const paint = resolveTableCellStyle({
      ...input,
      value,
      colorPositiveNegative,
    });
    expect(paint.cellBar).toEqual({
      color,
      width: 50,
      offset: 0,
      min: 0,
      max: 10,
    });
    expect(paint.colors).toEqual([]);
    expect(toTableCellPaint(paint)).toEqual({
      cellBar: paint.cellBar,
      colors: [],
    });
  },
);

test('does not index hidden, HTML, or zero-width conditional Cell Bars', () => {
  const conditionalInput = {
    ...input,
    columnColorFormatters: getColorFormatters(
      [
        {
          column: 'profit',
          operator: Comparator.None,
          colorScheme: 'colorSuccess',
          useGradient: false,
          objectFormatting: ObjectFormattingEnum.CELL_BAR,
        },
      ],
      [{ profit: 0 }, input.row],
      theme,
    ),
  };
  expect(
    resolveTableCellStyle({ ...conditionalInput, showCellBars: false }).colors,
  ).toEqual([]);
  expect(
    resolveTableCellStyle({ ...conditionalInput, renderHtml: true }).colors,
  ).toEqual([]);
  expect(
    resolveTableCellStyle({ ...conditionalInput, value: 0 }).colors,
  ).toEqual([]);
  expect(resolveTableCellStyle(conditionalInput).colors).toEqual(['GREEN']);
  expect(
    resolveTableCellStyle({
      ...conditionalInput,
      colorPositiveNegative: false,
    }).colors,
  ).toEqual(['GREEN']);
});

test.each([
  { value: -5, color: '#52c499', colors: ['GREEN'] },
  { value: 5, color: '#52c41a50', colors: [] },
])(
  'only a matching explicit Cell Bar is indexed for $value',
  ({ value, color, colors }) => {
    const paint = resolveTableCellStyle({
      ...input,
      value,
      columnColorFormatters: getColorFormatters(
        [
          {
            column: 'profit',
            operator: Comparator.LessThan,
            targetValue: 0,
            colorScheme: 'colorSuccess',
            useGradient: false,
            objectFormatting: ObjectFormattingEnum.CELL_BAR,
            filterable: true,
          },
        ],
        [{ profit: -5 }, { profit: 5 }],
        theme,
      ),
    });
    expect(paint.cellBar).toEqual({
      color,
      width: 50,
      offset: 0,
      min: 0,
      max: 10,
    });
    expect(paint.colors).toEqual(colors);
  },
);

test.each([-5, 5])(
  'explicit text with a default Cell Bar indexes only text for %s',
  value => {
    const paint = resolveTableCellStyle({
      ...input,
      value,
      columnColorFormatters: getColorFormatters(
        [
          {
            column: 'profit',
            operator: Comparator.None,
            colorScheme: 'colorWarning',
            useGradient: false,
            objectFormatting: ObjectFormattingEnum.TEXT_COLOR,
            filterable: true,
          },
        ],
        [{ profit: value }],
        theme,
      ),
    });
    expect(paint.cellBar?.color).toBe(value < 0 ? '#ff4d4f50' : '#52c41a50');
    expect(paint.textColor).toBe('rgb(250, 173, 20)');
    expect(paint.colors).toEqual(['YELLOW']);
  },
);

test('a background suppresses conditional Cell Bars without losing explicit text', () => {
  const rules = [
    {
      column: 'profit',
      operator: Comparator.None,
      colorScheme: 'colorSuccess',
      useGradient: false,
      objectFormatting: ObjectFormattingEnum.CELL_BAR,
    },
    {
      column: 'profit',
      operator: Comparator.None,
      colorScheme: 'colorError',
      useGradient: false,
    },
    {
      column: 'profit',
      operator: Comparator.None,
      colorScheme: 'colorWarning',
      useGradient: false,
      objectFormatting: ObjectFormattingEnum.TEXT_COLOR,
    },
  ];
  const paint = resolveTableCellStyle({
    ...input,
    columnColorFormatters: getColorFormatters(rules, [input.row], theme),
  });
  expect(paint.cellBar).toBeUndefined();
  expect(paint.colors).toEqual(['YELLOW', 'RED']);
  expect(paint.hasExplicitTextColor).toBe(true);
});

test('gradient capability uses a hidden source and conservatively blocks all visible targets', () => {
  const rules = [
    {
      column: 'source',
      operator: Comparator.None,
      colorScheme: 'colorError',
      columnFormatting: ObjectFormattingEnum.ENTIRE_ROW,
      filterable: true,
    },
  ];
  const capabilities = resolveTableFilterCapabilities(rules, [
    {
      key: 'source',
      dataType: GenericDataType.Numeric,
      config: { visible: false },
    },
    { key: 'target', dataType: GenericDataType.String },
  ]);
  expect(capabilities.source).toBeUndefined();
  expect(capabilities.target).toMatchObject({
    enabled: true,
    supported: false,
    reason: { code: 'TABLE_COLOR_FILTER_GRADIENT_UNSUPPORTED' },
  });
});

test('incomplete or invalid gradient rules do not disable a valid target', () => {
  const capabilities = resolveTableFilterCapabilities(
    [
      {
        column: 'profit',
        operator: Comparator.None,
        colorScheme: 'colorSuccess',
        useGradient: false,
        filterable: true,
      },
      {
        column: 'profit',
        operator: Comparator.GreaterThan,
        colorScheme: 'colorError',
      },
      {
        column: 'profit',
        operator: 'unknown',
        targetValue: 1,
        colorScheme: 'colorError',
      },
    ],
    [{ key: 'profit', dataType: GenericDataType.Numeric }],
  );
  expect(capabilities.profit).toEqual({ enabled: true, supported: true });
});

test('custom RGB and automatic readable text never create extra filter colors', () => {
  expect(tablePaintColorForScheme('#1677ff')).toBeUndefined();
  expect(tablePaintColorForScheme('colorSuccess')).toBe('GREEN');
  const paint = resolveTableCellStyle({
    ...input,
    columnColorFormatters: getColorFormatters(
      [
        {
          column: 'profit',
          operator: Comparator.None,
          colorScheme: '#1677ff',
          useGradient: false,
        },
      ],
      [input.row],
      theme,
    ),
  });
  expect(paint.backgroundColor).toBe('#1677ff');
  expect(paint.textColor).toBeDefined();
  expect(paint.colors).toEqual([]);
});

test('form-data targets include raw aliases, not stale aggregate fields', () => {
  const rules = ['region', 'alias', 'stale_metric'].map(column => ({
    column,
    filterable: true,
    colorScheme: 'colorSuccess',
    operator: 'None',
    useGradient: false,
  }));
  expect(
    getTableColorFilterTargets({
      query_mode: 'raw',
      all_columns: ['region', { label: 'alias', sqlExpression: 'value' }],
      metrics: ['stale_metric'],
      conditional_formatting: rules,
    }),
  ).toEqual(new Set(['region', 'alias']));
});

test('form-data targets resolve saved, adhoc, percentage and comparison output names', () => {
  const fieldNames = ['Main saved', '# SUM(amount)', '△ raw_sql', '% %percent'];
  expect(
    getTableColorFilterTargets({
      metrics: [
        'saved',
        { aggregate: 'SUM', column: { column_name: 'amount' } },
        { sqlExpression: 'raw_sql' },
      ],
      percent_metrics: [{ label: 'percent' }],
      time_compare: ['P1D'],
      conditional_formatting: fieldNames.map(column => ({
        column,
        filterable: true,
        colorScheme: 'colorSuccess',
        operator: 'None',
        useGradient: false,
      })),
    }),
  ).toEqual(new Set(fieldNames));
});

test('form-data target cleanup distinguishes active gradients from hidden stale gradient flags', () => {
  expect(
    getTableColorFilterTargets({
      query_mode: 'raw',
      all_columns: ['background', 'text', 'bar', 'string', 'hidden'],
      column_config: { hidden: { visible: false } },
      conditional_formatting: [
        ...['background', 'text', 'bar', 'hidden'].map(column => ({
          column,
          filterable: true,
          colorScheme: 'colorError',
          operator: '<',
          targetValue: 1,
          useGradient: true,
          objectFormatting:
            column === 'text'
              ? 'TEXT_COLOR'
              : column === 'bar'
                ? 'CELL_BAR'
                : 'BACKGROUND_COLOR',
        })),
        {
          column: 'string',
          filterable: true,
          colorScheme: 'colorSuccess',
          operator: 'None',
          useGradient: true,
        },
      ],
    }),
  ).toEqual(new Set(['text', 'bar', 'string']));
});
