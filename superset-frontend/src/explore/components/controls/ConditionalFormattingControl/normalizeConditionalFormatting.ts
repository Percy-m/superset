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
import { GenericDataType } from '@apache-superset/core/common';
import {
  ColorSchemeEnum,
  ObjectFormattingEnum,
} from '@superset-ui/chart-controls';
import { ConditionalFormattingConfig } from './types';

/** Mirror the numeric background gradient supported by the native renderer. */
export function hasEffectiveBackgroundGradient(
  config: ConditionalFormattingConfig,
  columnType: GenericDataType | undefined,
): boolean {
  return (
    columnType === GenericDataType.Numeric &&
    config.useGradient !== false &&
    config.colorScheme !== ColorSchemeEnum.Green &&
    config.colorScheme !== ColorSchemeEnum.Red &&
    !config.toTextColor &&
    config.objectFormatting !== ObjectFormattingEnum.TEXT_COLOR &&
    config.objectFormatting !== ObjectFormattingEnum.CELL_BAR
  );
}

/** Remove legacy independent filter semantics without changing native painting. */
export function normalizeConditionalFormattingConfig(
  config: ConditionalFormattingConfig,
): ConditionalFormattingConfig {
  const normalized: ConditionalFormattingConfig & {
    alertLevel?: unknown;
    subjectRef?: unknown;
  } = { ...config };
  delete normalized.alertLevel;
  delete normalized.subjectRef;
  return normalized;
}
