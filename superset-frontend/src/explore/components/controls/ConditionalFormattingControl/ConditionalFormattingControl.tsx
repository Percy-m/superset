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
import { useEffect, useState } from 'react';
import { t } from '@apache-superset/core/translation';
import { styled, css } from '@apache-superset/core/theme';
import { Comparator } from '@superset-ui/chart-controls';
import { FeatureFlag, isFeatureEnabled } from '@superset-ui/core';
import { Button } from '@superset-ui/core/components';
import { Icons } from '@superset-ui/core/components/Icons';
import { v4 as uuidv4 } from 'uuid';
import ControlHeader from 'src/explore/components/ControlHeader';
import { FormattingPopover } from './FormattingPopover';
import { normalizeConditionalFormattingConfig } from './normalizeConditionalFormatting';
import {
  ConditionalFormattingConfig,
  ConditionalFormattingControlProps,
} from './types';
import {
  AddControlLabel,
  CaretContainer,
  Label,
  OptionControlContainer,
} from '../OptionControls';

const FormattersContainer = styled.div`
  ${({ theme }) => css`
    padding: ${theme.sizeUnit}px;
    border: solid 1px ${theme.colorBorder};
    border-radius: ${theme.borderRadius}px;
  `}
`;

export const FormatterContainer = styled(OptionControlContainer)`
  &,
  & > div {
    margin-bottom: ${({ theme }) => theme.sizeUnit}px;
    :last-child {
      margin-bottom: 0;
    }
  }
`;

export const CloseButton = styled.button`
  ${({ theme }) => css`
    background: ${theme.colorBgLayout};
    color: ${theme.colorIcon};
    height: 100%;
    width: ${theme.sizeUnit * 6}px;
    border: none;
    border-right: solid 1px ${theme.colorBorder};
    padding: 0;
    outline: none;
    border-bottom-left-radius: 3px;
    border-top-left-radius: 3px;
  `}
`;

const ConditionalFormattingControl = ({
  value,
  onChange,
  columnOptions,
  verboseMap,
  removeIrrelevantConditions,
  extraColorChoices,
  allColumns,
  supportsAlertFilter = false,
  ...props
}: ConditionalFormattingControlProps) => {
  const alertFiltersEnabled =
    supportsAlertFilter && isFeatureEnabled(FeatureFlag.TableAlertFilters);
  const [conditionalFormattingConfigs, setConditionalFormattingConfigs] =
    useState<ConditionalFormattingConfig[]>(() =>
      (value ?? []).map(normalizeConditionalFormattingConfig),
    );

  useEffect(() => {
    if (onChange) {
      onChange(conditionalFormattingConfigs);
    }
  }, [conditionalFormattingConfigs, onChange]);

  useEffect(() => {
    if (removeIrrelevantConditions) {
      // remove formatter when corresponding column is removed from controls
      const newFormattingConfigs = conditionalFormattingConfigs.filter(config =>
        columnOptions.some(option => option?.value === config?.column),
      );
      if (
        newFormattingConfigs.length !== conditionalFormattingConfigs.length &&
        removeIrrelevantConditions
      ) {
        setConditionalFormattingConfigs(newFormattingConfigs);
      }
    }
  }, [conditionalFormattingConfigs, columnOptions, removeIrrelevantConditions]);

  const onDelete = (index: number) => {
    setConditionalFormattingConfigs(prevConfigs =>
      prevConfigs.filter((_, i) => i !== index),
    );
  };

  const onSave = (config: ConditionalFormattingConfig) => {
    setConditionalFormattingConfigs(prevConfigs => [
      ...prevConfigs,
      normalizeConditionalFormattingConfig(config),
    ]);
  };

  const onEdit = (newConfig: ConditionalFormattingConfig, index: number) => {
    const newConfigs = [...conditionalFormattingConfigs];
    newConfigs.splice(
      index,
      1,
      normalizeConditionalFormattingConfig({
        ...newConfig,
        ruleId: conditionalFormattingConfigs[index].ruleId ?? newConfig.ruleId,
      }),
    );
    setConditionalFormattingConfigs(newConfigs);
  };

  const onDuplicate = (index: number) => {
    setConditionalFormattingConfigs(prevConfigs => {
      const duplicate = { ...prevConfigs[index], ruleId: uuidv4() };
      return [
        ...prevConfigs.slice(0, index + 1),
        duplicate,
        ...prevConfigs.slice(index + 1),
      ];
    });
  };

  const createLabel = ({
    column,
    operator,
    targetValue,
    targetValueLeft,
    targetValueRight,
  }: ConditionalFormattingConfig) => {
    const columnName = (column && verboseMap?.[column]) ?? column;
    switch (operator) {
      case Comparator.None:
        return `${columnName}`;
      case Comparator.Between:
        return `${targetValueLeft} ${Comparator.LessThan} ${columnName} ${Comparator.LessThan} ${targetValueRight}`;
      case Comparator.BetweenOrEqual:
        return `${targetValueLeft} ${Comparator.LessOrEqual} ${columnName} ${Comparator.LessOrEqual} ${targetValueRight}`;
      case Comparator.BetweenOrLeftEqual:
        return `${targetValueLeft} ${Comparator.LessOrEqual} ${columnName} ${Comparator.LessThan} ${targetValueRight}`;
      case Comparator.BetweenOrRightEqual:
        return `${targetValueLeft} ${Comparator.LessThan} ${columnName} ${Comparator.LessOrEqual} ${targetValueRight}`;
      case Comparator.IsTrue:
      case Comparator.IsFalse:
      case Comparator.IsNull:
      case Comparator.IsNotNull:
        return `${columnName} ${operator}`;
      default:
        return `${columnName} ${operator} ${targetValue}`;
    }
  };

  return (
    <div>
      <ControlHeader {...props} />
      <FormattersContainer>
        {conditionalFormattingConfigs.map((config, index) => (
          <FormatterContainer key={index}>
            <CloseButton onClick={() => onDelete(index)}>
              <Icons.CloseOutlined iconSize="m" />
            </CloseButton>
            {alertFiltersEnabled ? (
              <Button
                aria-label={t('Duplicate formatter')}
                buttonSize="small"
                buttonStyle="link"
                onClick={() => onDuplicate(index)}
              >
                <Icons.CopyOutlined iconSize="m" />
              </Button>
            ) : null}
            <FormattingPopover
              title={t('Edit formatter')}
              config={config}
              columns={columnOptions}
              onChange={(newConfig: ConditionalFormattingConfig) =>
                onEdit(newConfig, index)
              }
              destroyTooltipOnHide
              extraColorChoices={extraColorChoices}
              allColumns={allColumns}
              supportsAlertFilter={supportsAlertFilter}
            >
              <OptionControlContainer withCaret>
                <Label>{createLabel(config)}</Label>
                <CaretContainer>
                  <Icons.RightOutlined iconSize="m" />
                </CaretContainer>
              </OptionControlContainer>
            </FormattingPopover>
          </FormatterContainer>
        ))}
        <FormattingPopover
          title={t('Add new formatter')}
          columns={columnOptions}
          onChange={onSave}
          destroyTooltipOnHide
          extraColorChoices={extraColorChoices}
          allColumns={allColumns}
          supportsAlertFilter={supportsAlertFilter}
        >
          <AddControlLabel>
            <Icons.PlusOutlined
              iconSize="m"
              css={theme => ({
                margin: `auto ${theme.sizeUnit}px auto 0`,
                verticalAlign: 'baseline',
              })}
            />
            {t('Add new color formatter')}
          </AddControlLabel>
        </FormattingPopover>
      </FormattersContainer>
    </div>
  );
};

export default ConditionalFormattingControl;
