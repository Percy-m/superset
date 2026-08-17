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
import { logging } from '@apache-superset/core/utils';
import { SupersetClient } from '@superset-ui/core';
import { ensureAppRoot } from 'src/utils/pathUtils';
import { safeStringify } from 'src/utils/safeStringify';

export interface SqlLabRequestedQuery {
  datasourceKey?: string;
  sql: string;
}

export interface SqlLabLocation {
  pathname: '/sqllab';
  state: {
    requestedQuery: SqlLabRequestedQuery;
  };
}

type SameTabOptions = {
  requestedQuery: SqlLabRequestedQuery;
  target: 'same-tab';
  navigate: (location: SqlLabLocation) => void;
};

type NewTabOptions = {
  requestedQuery: SqlLabRequestedQuery;
  target: 'new-tab';
};

export type OpenSqlLabQueryOptions = SameTabOptions | NewTabOptions;

type NavigationStatus = 'success' | 'failed';

const hashPrefix = async (bytes: Uint8Array): Promise<string> => {
  if (!globalThis.crypto?.subtle) return 'unavailable';
  try {
    const digest = await globalThis.crypto.subtle.digest('SHA-256', bytes);
    return Array.from(new Uint8Array(digest))
      .map(value => value.toString(16).padStart(2, '0'))
      .join('')
      .slice(0, 12);
  } catch {
    return 'unavailable';
  }
};

const logNavigation = async (
  requestedQuery: SqlLabRequestedQuery,
  target: OpenSqlLabQueryOptions['target'],
  status: NavigationStatus,
) => {
  try {
    const bytes = new TextEncoder().encode(requestedQuery.sql);
    logging.info('SQL Lab query navigation', {
      target,
      status,
      characterCount: Array.from(requestedQuery.sql).length,
      utf8ByteCount: bytes.byteLength,
      sha256Prefix: await hashPrefix(bytes),
    });
  } catch {
    // Diagnostics must never alter navigation or expose the query through errors.
  }
};

export async function openSqlLabQuery(
  options: OpenSqlLabQueryOptions,
): Promise<void> {
  const { requestedQuery, target } = options;
  if (target === 'same-tab') {
    options.navigate({
      pathname: '/sqllab',
      state: { requestedQuery },
    });
    await logNavigation(requestedQuery, target, 'success');
    return;
  }

  try {
    await SupersetClient.postForm(ensureAppRoot('/sqllab/'), {
      form_data: safeStringify(requestedQuery),
    });
    await logNavigation(requestedQuery, target, 'success');
  } catch {
    await logNavigation(requestedQuery, target, 'failed');
    throw new Error('SQL Lab query navigation failed');
  }
}
