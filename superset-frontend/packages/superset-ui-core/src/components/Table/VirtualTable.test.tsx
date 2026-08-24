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
import { act, render, screen, waitFor } from '@superset-ui/core/spec';
import type { ColumnsType } from 'antd/es/table';
import * as resizeDetector from 'react-resize-detector';

import { TableSize } from './index';
import VirtualTable, { resolveVirtualColumnLayout } from './VirtualTable';

interface RowData {
  [key: string]: string;
}

const createColumns = (count: number, width?: number): ColumnsType<RowData> =>
  Array.from({ length: count }, (_, index) => ({
    dataIndex: `column${index}`,
    key: `column${index}`,
    title: `Column ${index}`,
    width,
  }));

test('resolves the initial nine-column Drill Detail layout exactly', () => {
  const columns = createColumns(9, 150);
  const originalColumns = columns.map(column => ({ ...column }));

  const layout = resolveVirtualColumnLayout(columns, 828, 150);

  expect(layout.contentWidth).toBe(1350);
  expect(layout.estimatedColumnWidth).toBe(150);
  expect(layout.columns.map(({ width }) => width)).toEqual(Array(9).fill(150));
  expect(columns).toEqual(originalColumns);
  expect(layout.columns).not.toBe(columns);
});

test('stretches only the last column when fixed columns are narrower than the container', () => {
  const layout = resolveVirtualColumnLayout(createColumns(3, 150), 828, 150);

  expect(layout.columns.map(({ width }) => width)).toEqual([150, 150, 528]);
  expect(layout.contentWidth).toBe(828);
  expect(layout.estimatedColumnWidth).toBe(276);
});

test('resolves mixed, invalid, empty, and wide column layouts safely', () => {
  const mixedColumns: ColumnsType<RowData> = [
    { dataIndex: 'fixed', width: 200 },
    { dataIndex: 'missing' },
    { dataIndex: 'zero', width: 0 },
    { dataIndex: 'invalid', width: Number.NaN },
  ];
  const mixedLayout = resolveVirtualColumnLayout(mixedColumns, 828, 150);
  const emptyLayout = resolveVirtualColumnLayout<RowData>([], 828, 150);
  const singleLayout = resolveVirtualColumnLayout(createColumns(1), 0, 150);
  const wideLayout = resolveVirtualColumnLayout(createColumns(52), 828, 150);

  expect(mixedLayout.columns.map(({ width }) => width)).toEqual([
    200, 289, 50, 289,
  ]);
  expect(mixedLayout.contentWidth).toBe(828);
  expect(emptyLayout).toEqual({
    columns: [],
    contentWidth: 0,
    estimatedColumnWidth: 0,
  });
  expect(singleLayout.columns[0].width).toBe(150);
  expect(singleLayout.contentWidth).toBe(150);
  expect(wideLayout.contentWidth).toBe(2600);
  expect(
    wideLayout.columns.every(
      ({ width }) =>
        typeof width === 'number' && Number.isFinite(width) && width >= 50,
    ),
  ).toBe(true);
});

test('normalizes nonnumeric widths and distributes rounding remainder exactly', () => {
  const columns: ColumnsType<RowData> = [
    { dataIndex: 'percentage', width: '20%' },
    { dataIndex: 'negative', width: -10 },
    { dataIndex: 'infinite', width: Number.POSITIVE_INFINITY },
  ];
  const originalColumns = columns.map(column => ({ ...column }));
  const layout = resolveVirtualColumnLayout(columns, 829, 150);
  const invalidContainerLayout = resolveVirtualColumnLayout(
    columns,
    Number.NaN,
    150,
  );
  const infiniteContainerLayout = resolveVirtualColumnLayout(
    columns,
    Number.POSITIVE_INFINITY,
    150,
  );

  expect(layout.columns.map(({ width }) => width)).toEqual([389, 50, 390]);
  expect(layout.contentWidth).toBe(829);
  expect(layout.estimatedColumnWidth).toBeCloseTo(829 / 3);
  expect(
    layout.columns.every(
      ({ width }) => typeof width === 'number' && Number.isFinite(width),
    ),
  ).toBe(true);
  expect(invalidContainerLayout.columns.map(({ width }) => width)).toEqual([
    150, 50, 150,
  ]);
  expect(infiniteContainerLayout.contentWidth).toBe(350);
  expect(columns).toEqual(originalColumns);
});

test('shares one resolved layout with the header and virtual body after resize', async () => {
  let onResize: ((width?: number, height?: number) => void) | undefined;
  const resizeSpy = jest
    .spyOn(resizeDetector, 'useResizeDetector')
    .mockImplementation(props => {
      onResize = props?.onResize;
      return { ref: { current: null } };
    });
  const columns = createColumns(9, 150);
  const data = [
    Object.fromEntries(
      columns.map((_, index) => [`column${index}`, `Value ${index}`]),
    ),
  ];

  const { container, rerender } = render(
    <VirtualTable
      columns={columns}
      dataSource={data}
      pagination={false}
      scroll={{ y: 300 }}
      size={TableSize.Small}
    />,
  );

  act(() => onResize?.(828, 300));

  await waitFor(() => expect(screen.getByText('Value 0')).toBeInTheDocument());
  const grid = container.querySelector<HTMLElement>('.virtual-grid');
  const gridInner = grid?.firstElementChild as HTMLElement | null;
  const headerTable = container.querySelector<HTMLTableElement>(
    '.ant-table-header table',
  );

  expect(grid).toHaveStyle({ width: '828px' });
  expect(gridInner?.style.width).toBe('1350px');
  expect(headerTable?.style.width).toBe('1350px');

  rerender(
    <VirtualTable
      columns={createColumns(9, 200)}
      dataSource={data}
      pagination={false}
      scroll={{ y: 300 }}
      size={TableSize.Small}
    />,
  );

  await waitFor(() =>
    expect(screen.getByText('Value 1')).toHaveStyle({ left: '200px' }),
  );
  expect(gridInner?.style.width).toBe('1800px');
  expect(headerTable?.style.width).toBe('1800px');

  resizeSpy.mockRestore();
});

test('uses AntD small and middle padding tokens without changing row heights', async () => {
  const resizeSpy = jest
    .spyOn(resizeDetector, 'useResizeDetector')
    .mockReturnValue({ ref: { current: null }, width: 150 });
  const columns = createColumns(1, 150);
  const data = [{ column0: 'Small value' }];
  const { rerender } = render(
    <VirtualTable
      columns={columns}
      dataSource={data}
      pagination={false}
      scroll={{ y: 300 }}
      size={TableSize.Small}
    />,
  );

  const smallCell = await screen.findByText('Small value');
  expect(smallCell).toHaveStyle({
    height: '39px',
    paddingBlock: '8px',
    paddingInline: '8px',
  });

  rerender(
    <VirtualTable
      columns={columns}
      dataSource={[{ column0: 'Middle value' }]}
      pagination={false}
      scroll={{ y: 300 }}
      size={TableSize.Middle}
    />,
  );

  const middleCell = await screen.findByText('Middle value');
  expect(middleCell).toHaveStyle({
    height: '47px',
    paddingBlock: '12px',
    paddingInline: '8px',
  });

  resizeSpy.mockRestore();
});
