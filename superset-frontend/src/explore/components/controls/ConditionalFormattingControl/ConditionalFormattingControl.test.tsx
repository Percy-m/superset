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
  fireEvent,
  render,
  screen,
  waitFor,
} from 'spec/helpers/testing-library';
import { Comparator } from '@superset-ui/chart-controls';
import { GenericDataType } from '@apache-superset/core/common';
import { FeatureFlag } from '@superset-ui/core';
import ConditionalFormattingControl from './ConditionalFormattingControl';
import { ConditionalFormattingConfig } from './types';

const columnOptions = [
  { label: 'My Column', value: 'my_col', dataType: GenericDataType.Boolean },
];

const defaultProps = {
  columnOptions,
  verboseMap: {} as Record<string, string>,
  removeIrrelevantConditions: false,
  label: 'Conditional Formatting',
  description: 'Test',
  name: 'conditional_formatting',
  onChange: jest.fn(),
};

test('renders "is false" operator label without trailing undefined', () => {
  const value: ConditionalFormattingConfig[] = [
    {
      column: 'my_col',
      operator: Comparator.IsFalse,
      colorScheme: 'colorSuccess',
    },
  ];
  render(<ConditionalFormattingControl {...defaultProps} value={value} />);
  expect(screen.getByText('my_col is false')).toBeInTheDocument();
});

test('renders "is true" operator label without trailing undefined', () => {
  const value: ConditionalFormattingConfig[] = [
    {
      column: 'my_col',
      operator: Comparator.IsTrue,
      colorScheme: 'colorSuccess',
    },
  ];
  render(<ConditionalFormattingControl {...defaultProps} value={value} />);
  expect(screen.getByText('my_col is true')).toBeInTheDocument();
});

test('renders "is null" operator label without trailing undefined', () => {
  const value: ConditionalFormattingConfig[] = [
    {
      column: 'my_col',
      operator: Comparator.IsNull,
      colorScheme: 'colorSuccess',
    },
  ];
  render(<ConditionalFormattingControl {...defaultProps} value={value} />);
  expect(screen.getByText('my_col is null')).toBeInTheDocument();
});

test('renders "is not null" operator label without trailing undefined', () => {
  const value: ConditionalFormattingConfig[] = [
    {
      column: 'my_col',
      operator: Comparator.IsNotNull,
      colorScheme: 'colorSuccess',
    },
  ];
  render(<ConditionalFormattingControl {...defaultProps} value={value} />);
  expect(screen.getByText('my_col is not null')).toBeInTheDocument();
});

test('renders verbose column name when available', () => {
  const value: ConditionalFormattingConfig[] = [
    {
      column: 'my_col',
      operator: Comparator.IsFalse,
      colorScheme: 'colorSuccess',
    },
  ];
  render(
    <ConditionalFormattingControl
      {...defaultProps}
      verboseMap={{ my_col: 'My Column' }}
      value={value}
    />,
  );
  expect(screen.getByText('My Column is false')).toBeInTheDocument();
});

test('duplicating a formatter assigns a new stable rule UUID', async () => {
  const previousFlags = window.featureFlags;
  window.featureFlags = { [FeatureFlag.TableAlertFilters]: true };
  const onChange = jest.fn();
  const value: ConditionalFormattingConfig[] = [
    {
      ruleId: '772a548e-72f7-4ac8-a8ff-fdb7465b3ccd',
      column: 'my_col',
      operator: Comparator.IsFalse,
      colorScheme: 'colorSuccess',
    },
  ];
  try {
    render(
      <ConditionalFormattingControl
        {...defaultProps}
        onChange={onChange}
        value={value}
      />,
    );

    fireEvent.click(screen.getByLabelText('Duplicate formatter'));

    await waitFor(() => {
      const latestValue = onChange.mock.calls.at(-1)?.[0];
      expect(latestValue).toHaveLength(2);
      expect(latestValue[0].ruleId).toBe(value[0].ruleId);
      expect(latestValue[1].ruleId).not.toBe(value[0].ruleId);
      expect(latestValue[1].ruleId).toMatch(
        /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/,
      );
    });
  } finally {
    window.featureFlags = previousFlags;
  }
});
