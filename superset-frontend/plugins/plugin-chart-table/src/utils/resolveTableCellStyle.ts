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
import type { SupersetTheme } from '@apache-superset/core/theme';
import { GenericDataType } from '@apache-superset/core/common';
import { t } from '@apache-superset/core/translation';
import type {
  ColumnFilterCapability,
  DataRecord,
  DataRecordValue,
  TableCellPaint,
  TablePaintColor,
} from '@superset-ui/core';
import {
  BasicColorFormatterType,
  ColorFormatters,
  ColorSchemeEnum,
  Comparator,
  ConditionalFormattingConfig,
  getTextColorForBackground,
  MultipleValueComparators,
  ObjectFormattingEnum,
} from '@superset-ui/chart-controls';
import tinycolor from 'tinycolor2';

export type {
  ColumnFilterCapability,
  TableCellPaint,
  TablePaintColor,
} from '@superset-ui/core';

export type TableFormattingRule = Omit<
  ConditionalFormattingConfig,
  'operator'
> & {
  operator?: string;
};

export interface TableStyleSource {
  ruleId?: string;
  colorScheme?: string;
}

export interface ResolvedTableCellStyle extends TableCellPaint {
  hasExplicitTextColor: boolean;
  sources: Partial<
    Record<'background' | 'text' | 'cellBar' | 'arrow', TableStyleSource>
  >;
}

export interface ResolveTableCellStyleInput {
  columnKey: string;
  columnLabel: string;
  value: DataRecordValue;
  row: DataRecord;
  rowIndex: number;
  columnColorFormatters?: ColorFormatters;
  hasBasicColorFormatters?: boolean;
  basicColorFormatter?: BasicColorFormatterType;
  hasBasicColorColumnFormatters?: boolean;
  basicColorColumnFormatter?: BasicColorFormatterType;
  comparisonMainLabel: string;
  showCellBars: boolean;
  valueRange?: [number, number] | false | null;
  alignPositiveNegative: boolean;
  colorPositiveNegative: boolean;
  theme: SupersetTheme;
  renderHtml?: boolean;
}

/** Identify only the native three palettes and their explicit legacy aliases. */
export function tablePaintColorForScheme(
  scheme: string | undefined,
): TablePaintColor | undefined {
  // These persisted legacy identifiers are matched, never used to style the UI.
  const aliases: Record<string, TablePaintColor> = {
    colorsuccess: 'GREEN',
    colorwarning: 'YELLOW',
    colorerror: 'RED',
    // eslint-disable-next-line theme-colors/no-literal-colors -- Persisted legacy identifier.
    '#52c41a': 'GREEN',
    // eslint-disable-next-line theme-colors/no-literal-colors -- Persisted legacy identifier.
    '#00ff00': 'GREEN',
    // eslint-disable-next-line theme-colors/no-literal-colors -- Persisted legacy identifier.
    '#0f0': 'GREEN',
    // eslint-disable-next-line theme-colors/no-literal-colors -- Persisted legacy identifier.
    '#faad14': 'YELLOW',
    // eslint-disable-next-line theme-colors/no-literal-colors -- Persisted legacy identifier.
    '#ffff00': 'YELLOW',
    // eslint-disable-next-line theme-colors/no-literal-colors -- Persisted legacy identifier.
    '#ff0': 'YELLOW',
    // eslint-disable-next-line theme-colors/no-literal-colors -- Persisted legacy identifier.
    '#ff4d4f': 'RED',
    // eslint-disable-next-line theme-colors/no-literal-colors -- Persisted legacy identifier.
    '#f5222d': 'RED',
    // eslint-disable-next-line theme-colors/no-literal-colors -- Persisted legacy identifier.
    '#ff0000': 'RED',
    // eslint-disable-next-line theme-colors/no-literal-colors -- Persisted legacy identifier.
    '#f00': 'RED',
  };
  return scheme ? aliases[scheme.toLowerCase()] : undefined;
}

const visibleColor = (color: string | undefined) => {
  const parsed = tinycolor(color);
  return Boolean(color && parsed.isValid() && parsed.getAlpha() > 0);
};

/** Preserve TableChart's paint order and rendered bar/arrow visibility. */
export function resolveTableCellStyle({
  columnKey,
  columnLabel,
  value,
  row,
  rowIndex,
  columnColorFormatters = [],
  hasBasicColorFormatters = false,
  basicColorFormatter,
  hasBasicColorColumnFormatters = false,
  basicColorColumnFormatter,
  comparisonMainLabel,
  showCellBars,
  valueRange,
  alignPositiveNegative,
  colorPositiveNegative,
  theme,
  renderHtml = false,
}: ResolveTableCellStyleInput): ResolvedTableCellStyle {
  let backgroundColor: string | undefined;
  let explicitTextColor: string | undefined;
  let customBarColor: string | undefined;
  let allowBar = true;
  let arrow: string | undefined;
  let backgroundFamily: TablePaintColor | undefined;
  let textFamily: TablePaintColor | undefined;
  let barFamily: TablePaintColor | undefined;
  let arrowFamily: TablePaintColor | undefined;
  const sources: ResolvedTableCellStyle['sources'] = {};
  const comparisonFamily = (formatter?: BasicColorFormatterType) =>
    formatter?.mainArrow
      ? formatter.arrowColor === ColorSchemeEnum.Green
        ? ('GREEN' as const)
        : ('RED' as const)
      : undefined;

  if (!columnColorFormatters.length && hasBasicColorFormatters) {
    backgroundColor = basicColorFormatter?.backgroundColor;
    backgroundFamily = comparisonFamily(basicColorFormatter);
    arrow =
      columnLabel === comparisonMainLabel ? basicColorFormatter?.mainArrow : '';
    sources.background = { colorScheme: basicColorFormatter?.arrowColor };
  }

  const applyFormatter = (
    formatter: ColorFormatters[number],
    input: DataRecordValue,
  ) => {
    const color = formatter.getColorFromValue(
      input as Parameters<ColorFormatters[number]['getColorFromValue']>[0],
    );
    if (!color) return;
    const family = tablePaintColorForScheme(formatter.colorScheme);
    const source = {
      ruleId: formatter.ruleId,
      colorScheme: formatter.colorScheme,
    };
    if (
      formatter.objectFormatting === ObjectFormattingEnum.TEXT_COLOR ||
      formatter.toTextColor
    ) {
      explicitTextColor = color;
      textFamily = family;
      sources.text = source;
    } else if (formatter.objectFormatting === ObjectFormattingEnum.CELL_BAR) {
      if (showCellBars) {
        // Preserve the renderer's suffix handling, including solid colors.
        customBarColor = color.slice(0, -2);
        barFamily = family;
        sources.cellBar = source;
      }
    } else {
      backgroundColor = color;
      backgroundFamily = family;
      sources.background = source;
      allowBar = false;
    }
  };
  columnColorFormatters
    .filter(formatter =>
      formatter.columnFormatting
        ? formatter.columnFormatting === columnKey
        : formatter.column === columnKey,
    )
    .forEach(formatter =>
      applyFormatter(
        formatter,
        formatter.columnFormatting ? row[formatter.column] : value,
      ),
    );
  columnColorFormatters
    .filter(
      formatter =>
        formatter.columnFormatting === ObjectFormattingEnum.ENTIRE_ROW,
    )
    .forEach(formatter => applyFormatter(formatter, row[formatter.column]));

  if (hasBasicColorColumnFormatters) {
    if (basicColorColumnFormatter?.backgroundColor) {
      ({ backgroundColor } = basicColorColumnFormatter);
      backgroundFamily = comparisonFamily(basicColorColumnFormatter);
      sources.background = {
        colorScheme: basicColorColumnFormatter.arrowColor,
      };
    }
    arrow =
      columnLabel === comparisonMainLabel
        ? basicColorColumnFormatter?.mainArrow
        : '';
  }
  const textColor = getTextColorForBackground(
    { backgroundColor, color: explicitTextColor },
    rowIndex % 2 === 0 ? theme.colorBgLayout : theme.colorBgBase,
  );
  const result: ResolvedTableCellStyle = {
    backgroundColor,
    textColor,
    colors: [],
    hasExplicitTextColor: Boolean(explicitTextColor),
    sources,
  };
  if (
    !renderHtml &&
    showCellBars &&
    valueRange &&
    typeof value === 'number' &&
    allowBar
  ) {
    const [min, max] = valueRange;
    const positiveExtent = Math.abs(Math.max(max, 0));
    const negativeExtent = Math.abs(Math.min(min, 0));
    const extent = positiveExtent + negativeExtent;
    const width = alignPositiveNegative
      ? Math.abs(Math.round((value / max) * 100))
      : Math.round((Math.abs(value) / extent) * 100);
    const offset = alignPositiveNegative
      ? 0
      : Math.round(
          (Math.min(negativeExtent + value, negativeExtent) / extent) * 100,
        );
    const color = customBarColor
      ? `${customBarColor}99`
      : colorPositiveNegative
        ? `${value < 0 ? theme.colorError : theme.colorSuccess}50`
        : theme.colorFill;
    result.cellBar = { color, width, offset, min, max };
    if (!customBarColor)
      barFamily = colorPositiveNegative
        ? value < 0
          ? 'RED'
          : 'GREEN'
        : undefined;
  }
  if (!renderHtml && arrow) {
    const formatter = hasBasicColorColumnFormatters
      ? basicColorColumnFormatter
      : basicColorFormatter;
    arrowFamily =
      formatter?.arrowColor === ColorSchemeEnum.Green ? 'GREEN' : 'RED';
    result.arrow = {
      symbol: arrow,
      color: arrowFamily === 'GREEN' ? theme.colorSuccess : theme.colorError,
    };
    sources.arrow = { colorScheme: formatter?.arrowColor };
  }
  const families = new Set<TablePaintColor>();
  if (backgroundFamily && visibleColor(backgroundColor))
    families.add(backgroundFamily);
  if (textFamily && visibleColor(textColor)) families.add(textFamily);
  if (
    barFamily &&
    result.cellBar &&
    Number.isFinite(result.cellBar.width) &&
    result.cellBar.width > 0 &&
    visibleColor(result.cellBar.color)
  )
    families.add(barFamily);
  if (arrowFamily && result.arrow && visibleColor(result.arrow.color))
    families.add(arrowFamily);
  result.colors = (['GREEN', 'YELLOW', 'RED'] as const).filter(color =>
    families.has(color),
  );
  return result;
}

/** Project local paint to the wire format without implicit readable text or invisible bars. */
export function toTableCellPaint(
  style: ResolvedTableCellStyle,
): TableCellPaint {
  const paint: TableCellPaint = { colors: style.colors };
  if (style.backgroundColor !== undefined)
    paint.backgroundColor = style.backgroundColor;
  if (style.hasExplicitTextColor && style.textColor !== undefined)
    paint.textColor = style.textColor;
  if (
    style.cellBar &&
    Number.isFinite(style.cellBar.width) &&
    Number.isFinite(style.cellBar.offset) &&
    style.cellBar.width > 0
  ) {
    paint.cellBar = style.cellBar;
  }
  if (style.arrow) paint.arrow = style.arrow;
  return paint;
}

/** Return visible targets without interpreting the business meaning of rules. */
export function resolveTableFilterCapabilities(
  rules: TableFormattingRule[] | undefined,
  columns: {
    key: string;
    dataType: GenericDataType;
    config?: { visible?: boolean };
  }[],
): Record<string, ColumnFilterCapability> {
  const visibleColumns = columns.filter(
    column => column.config?.visible !== false,
  );
  const capabilities: Record<string, ColumnFilterCapability> =
    Object.fromEntries(
      visibleColumns.map(column => [
        column.key,
        { enabled: false, supported: true },
      ]),
    );
  rules?.forEach(rule => {
    const target = rule.columnFormatting || rule.column;
    const targets =
      target === ObjectFormattingEnum.ENTIRE_ROW
        ? visibleColumns.map(column => column.key)
        : target && capabilities[target]
          ? [target]
          : [];
    const hasRule =
      rule.column !== undefined &&
      rule.colorScheme !== undefined &&
      (rule.operator === Comparator.None ||
        (rule.operator !== undefined &&
          Object.values(Comparator).includes(rule.operator as Comparator) &&
          (MultipleValueComparators.includes(rule.operator as Comparator)
            ? rule.targetValueLeft !== undefined &&
              rule.targetValueRight !== undefined
            : rule.targetValue !== undefined)) ||
        rule.colorScheme === ColorSchemeEnum.Green ||
        rule.colorScheme === ColorSchemeEnum.Red);
    if (!hasRule) return;
    const gradient =
      rule.useGradient !== false &&
      rule.colorScheme !== ColorSchemeEnum.Green &&
      rule.colorScheme !== ColorSchemeEnum.Red &&
      !rule.toTextColor &&
      rule.objectFormatting !== ObjectFormattingEnum.TEXT_COLOR &&
      rule.objectFormatting !== ObjectFormattingEnum.CELL_BAR &&
      columns.some(
        column =>
          column.key === rule.column &&
          column.dataType === GenericDataType.Numeric,
      );
    targets.forEach(key => {
      if (rule.filterable) capabilities[key].enabled = true;
      if (gradient) {
        capabilities[key].supported = false;
        capabilities[key].reason = {
          code: 'TABLE_COLOR_FILTER_GRADIENT_UNSUPPORTED',
          message: t(
            'This column contains gradient formatting; color filtering is unavailable.',
          ),
        };
      }
    });
  });
  return capabilities;
}

const isRecord = (value: unknown): value is Record<string, unknown> =>
  typeof value === 'object' && value !== null && !Array.isArray(value);

const getFieldName = (value: unknown): string | undefined => {
  if (typeof value === 'string') return value;
  if (!isRecord(value)) return undefined;
  if (typeof value.label === 'string' && value.label) return value.label;
  if (typeof value.aggregate === 'string' && isRecord(value.column)) {
    const columnName = value.column.column_name ?? value.column.columnName;
    if (typeof columnName === 'string')
      return `${value.aggregate}(${columnName})`;
  }
  return typeof value.sqlExpression === 'string'
    ? value.sqlExpression
    : undefined;
};

const getFieldNames = (value: unknown): string[] =>
  (Array.isArray(value) ? value : [])
    .map(getFieldName)
    .filter((field): field is string => Boolean(field));

/** Resolve saved query targets before result metadata is available; the server revalidates dtypes. */
export function getTableColorFilterTargets(
  formData: Record<string, unknown>,
): Set<string> {
  const rawRules = Array.isArray(formData.conditional_formatting)
    ? formData.conditional_formatting
    : [];
  const rules: TableFormattingRule[] = rawRules.filter(isRecord).map(rule => ({
    column: typeof rule.column === 'string' ? rule.column : undefined,
    columnFormatting:
      typeof rule.columnFormatting === 'string'
        ? rule.columnFormatting
        : undefined,
    colorScheme:
      typeof rule.colorScheme === 'string' ? rule.colorScheme : undefined,
    operator: typeof rule.operator === 'string' ? rule.operator : undefined,
    targetValue:
      typeof rule.targetValue === 'number' ||
      typeof rule.targetValue === 'string'
        ? rule.targetValue
        : undefined,
    targetValueLeft:
      typeof rule.targetValueLeft === 'number'
        ? rule.targetValueLeft
        : undefined,
    targetValueRight:
      typeof rule.targetValueRight === 'number'
        ? rule.targetValueRight
        : undefined,
    objectFormatting: Object.values(ObjectFormattingEnum).includes(
      rule.objectFormatting as ObjectFormattingEnum,
    )
      ? (rule.objectFormatting as ObjectFormattingEnum)
      : undefined,
    useGradient:
      typeof rule.useGradient === 'boolean' ? rule.useGradient : undefined,
    toTextColor: rule.toTextColor === true,
    filterable: rule.filterable === true,
  }));
  const rawColumns = getFieldNames(formData.all_columns);
  const isRaw =
    formData.query_mode === 'raw' ||
    (!formData.query_mode && rawColumns.length > 0);
  const metrics = getFieldNames(formData.metrics);
  const percentMetrics = getFieldNames(formData.percent_metrics).map(
    name => `%${name}`,
  );
  const metricColumns = [...metrics, ...percentMetrics];
  const hasTimeComparison =
    Array.isArray(formData.time_compare) && formData.time_compare.length > 0;
  const displayedMetrics = hasTimeComparison
    ? metricColumns.flatMap(name =>
        ['Main', '#', '△', '%'].map(prefix => `${prefix} ${name}`),
      )
    : metricColumns;
  const columnKeys = new Set(
    isRaw
      ? rawColumns
      : [...getFieldNames(formData.groupby), ...displayedMetrics],
  );
  const columnConfig = isRecord(formData.column_config)
    ? formData.column_config
    : {};
  // A None rule on an unknown physical type must not discard valid string filters.
  // Query results and the server remain authoritative for such gradient checks.
  const columns = [...columnKeys].map(key => {
    const config = columnConfig[key];
    return {
      key,
      dataType: rules.some(
        rule =>
          rule.column === key &&
          [
            Comparator.Equal,
            Comparator.NotEqual,
            Comparator.GreaterThan,
            Comparator.GreaterOrEqual,
            Comparator.LessThan,
            Comparator.LessOrEqual,
            ...MultipleValueComparators,
          ].includes(rule.operator as Comparator) &&
          (typeof rule.targetValue === 'number' ||
            typeof rule.targetValueLeft === 'number'),
      )
        ? GenericDataType.Numeric
        : GenericDataType.String,
      config: { visible: !isRecord(config) || config.visible !== false },
    };
  });
  const validRules = rules.filter(
    rule => rule.column !== undefined && columnKeys.has(rule.column),
  );
  return new Set(
    Object.entries(resolveTableFilterCapabilities(validRules, columns))
      .filter(([, capability]) => capability.enabled && capability.supported)
      .map(([column]) => column),
  );
}
