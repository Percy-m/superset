<!--
Licensed to the Apache Software Foundation (ASF) under one
or more contributor license agreements.  See the NOTICE file
distributed with this work for additional information
regarding copyright ownership.  The ASF licenses this file
to you under the Apache License, Version 2.0 (the
"License"); you may not use this file except in compliance
with the License.  You may obtain a copy of the License at

  http://www.apache.org/licenses/LICENSE-2.0

Unless required by applicable law or agreed to in writing,
software distributed under the License is distributed on an
"AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY
KIND, either express or implied.  See the License for the
specific language governing permissions and limitations
under the License.
-->

# FR-02 颜色驱动筛选：实施与本机验收记录

| 项目     | 内容                                                               |
| -------- | ------------------------------------------------------------------ |
| 日期     | 2026-08-28～2026-08-29，Asia/Shanghai                              |
| 设计     | [重设计 V1.2 与高保真原型](./fr02-color-driven-filter-redesign.md) |
| 代码起点 | `dev/6.1`，`9ff15ca9f85beaddfea15283c511022cfa5dd45d`              |
| 设计提交 | `52b1794cf3`、`4477c94038`、`59d4f8b3e6`，均独立于实现             |
| 实现提交 | `a04e266124`，功能实现、单测和可复跑服务验收脚本                   |
| 验收性质 | 本机开发服务、真实 ClickHouse 数据及 Redis；不等同生产部署验收     |

## 1. 实际实现

1. 每条 formatter 内只保留一个 `Enable alert filter` 扩展开关，删除独立
   `Alert level` 配置和 `subjectRef` 准入。旧字段在读取/保存规范化时清理，
   不再根据独立等级生成 SQL 条件。
2. 以原生最终染色为参照，按目标列的绿色、黄色、红色保留行。同列多色 OR，
   跨列 AND；未开启该开关的其他规则仍参与最终颜色合成。
3. 纯色背景、显式文字、实际可见 Cell Bar、跨列/整行、保存/临时指标、
   字符串、百分比和时间对比派生列均进入相同颜色求值链路。
   原生规则顺序、覆盖关系、分页分母和 Cell Bar 范围不因筛选重新计算。
4. 有效数值背景渐变保留染色能力，但目标列暂不支持颜色筛选。
   开关不消失：禁用、未勾选、显示原因；Apply 正常可用。
   同列混入渐变时保留可打开的漏斗和解释，不返回只覆盖部分规则的假结果。
5. 后端通过保存 Slice 或当前用户拥有的 Explore 草稿读取规则和可信上下文。
   新协议为 `queries[0].table_color_filter.version=2`，选择值只有列和三类颜色；
   请求不能提交新的阈值、SQL、样式或数据结果。
6. 使用独立的完整结果快照，默认上限 **1,000 行**。首次取基础结果并执行
   原生后加工，再冻结值、样式和行索引；后续颜色切换/翻页/导出复用该快照。
   服务端模式不是循环按页重跑数据库 SQL。
7. 数据、分页、总数、数值汇总和 XLSX 使用相同行集。
   Current View 导出额外传递客户端可见行索引及顺序，后端验证每个索引属于
   已授权且匹配颜色的行集；不接受客户端提交行值，空视图也不回落为全量导出。
8. Permalink 只保存有效颜色选择，不保存快照 token、结果或客户端搜索/排序投影。
   当前用户、Dataset/RLS、Dashboard、规则、主题和查询上下文都参与快照校验。

实现涉及经典 Table、格式器公共控件的 capability、Chart 查询状态、Explore、
Dashboard 分享/导出和后端 QueryContext。非经典 Table 不展示新开关。
未修改 ClickHouse dialect、数据库驱动、FR-01/FR-05 功能或数据库 schema。

## 2. 本机环境与数据

- 后端：`http://127.0.0.1:8088`；前端：`http://localhost:9001`。
  本轮已启动/重启服务并用最终代码检查，不以静态原型替代服务测试。
- ClickHouse：`21.3.20.1`；共享结果缓存：本机 Redis。
- Python：本机 venv 为 3.11；Node.js：22。代码另做 Python 3.10 语法和
  `typing` 导入审查，本次 34 个 Python 文件通过；未在真正 Python 3.10
  解释器下执行此轮测试。
- `NO_PROXY` / `no_proxy` 包含 loopback。连接凭据、令牌、SQL 正文不写入本文。
- 现有八张测试表满足 10k 量级要求：`fact_sales` 20,000 行、
  `fact_events` 12,000 行，其余 `fact_inventory`、`dim_customer`、
  `dim_product`、`drill_wide`、`export_edge_cases`、`complex_sql_cases`
  各 10,000 行。既有 `validate.sql` 的 25 项数据校验通过。
- 未重建或覆盖已有物理表；新边界由已有数据上的测试查询、受控草稿和纯函数
  fixtures 构造。真实 HTTP 验收创建的 16 个临时草稿已删除，保存的 Slice 1/3
  未改动；浏览器只 Apply 未保存的 Explore 草稿，不点击 Save。

两个必须区分的原生环境语义：

1. `vq_fact_sales_alerts` 中现有日期字段 `is_dttm=False`，因此时间对比使用已有
   客户数据集、有效 `signup_date` 和保存 Chart 1，不通过改 Dataset 元数据伪造通过。
2. 原生 Decimal `contribution` 会受到 object dtype 总计列选择的影响。本实现
   保持其原有逐页百分比结果；Float64 另有严格 `all_records` 全量分母断言。
   不能把 Decimal 原生行为描述为本次修复或声称它已经改成另一种分母。

## 3. 测试层次和结果

| 层次                   | 实际结果                                                                   | 能证明什么                                                    |
| ---------------------- | -------------------------------------------------------------------------- | ------------------------------------------------------------- |
| 后端定向联合单测       | 14 个文件，402 passed；最终一次 4.69 秒                                    | 求值、缓存、预算、权限边界、查询、schema、API、导出和兼容分支 |
| 前端定向联合测试       | 25 suites，533 passed；最终一次 76.189 秒                                  | 原生 Table、格式器、真实 Redux 父子状态链路、菜单、分享和导出 |
| TypeScript             | 根 `tsc --noEmit` 通过                                                     | 当前前端项目类型检查，不等同生产 bundle 构建                  |
| 真实 HTTP 接口         | 22 项通过、141 次请求；该次约 4.76 秒                                      | 运行中的 Flask、ClickHouse、Redis、草稿、分页/过滤/导出       |
| 真实 Flask test-client | 21 项通过、138 次请求、55 次测试源 SELECT                                  | 同一真实数据库的执行次数观测，不是 mock SQL                   |
| 跨进程快照             | HTTP worker 首建，独立 Flask app/Redis 读取；125 行值/色一致，源 SELECT=0  | 快照不是进程内私有字典                                        |
| Dashboard XLSX         | 2 项通过、6 次 HTTP 请求；3 个 Sheet，目标表 24 行；失效快照 HTTP 410 JSON | 真实嵌套 Tab、行值/顺序/颜色一致及原子失败                    |
| 实际页面               | 见下节                                                                     | 最终 React UI 能点击、Apply，且真实返回结果与显示一致         |

上述数量是各自一次完整执行结果，不把重跑累计为更多测试。
接口总耗时仅针对本机小结果 fixture，不能解释为生产复杂 SQL 的性能承诺。

### 3.1 实际页面操作

页面使用保存 Chart 3/4 和 Dashboard 3，不是 `9012` 的设计原型。

| 操作                           | 实际观察                                                                             |
| ------------------------------ | ------------------------------------------------------------------------------------ |
| 服务端分页，gross_revenue 点绿 | 97 行基准变为 24 行；全部可见收入单元格为原绿色                                      |
| 同列再选黄、红                 | 24 → 72 → 97 行；第三个颜色正常加入，前两个选择保留                                  |
| 叠加 quantity 绿色             | 变为 45 行，当前页 quantity 单元格均为绿色；可独立清除此列                           |
| 非服务端分页                   | 绿色筛选得到 24 行；重新载入最终 bundle 后顶部 `24 rows` 与 `Search 24 records` 一致 |
| 当前未保存格式器启用渐变       | Enable 开关仍可见、禁用且未选中；提示不支持，并说明 Apply 将关闭该规则筛选           |
| 渐变 Apply                     | 正常应用并显示原渐变；该列漏斗可查看不支持原因，颜色项禁用；其他纯色列仍可筛选       |
| 客户端搜索无匹配               | 出现 `No matching records found`；表头、漏斗保留；清空搜索可恢复，不靠刷新退出       |
| 两张 Dashboard Table 独立选择  | Chart 3 绿色 24 行、Chart 4 红色 25 行，互不覆盖                                     |
| 再应用 Native Filter：AMER     | 两表分别 6 行绿色/6 行红色，区域均为 AMER；后续请求未冲掉颜色选择                    |
| 超过快照 TTL 后继续点击        | 明确提示快照过期、要求重载；保留上次成功结果，没有把失败显示为已应用                 |

### 3.2 重点边界覆盖

| 用例组       | 已执行覆盖                                                                                 | 层次                                |
| ------------ | ------------------------------------------------------------------------------------------ | ----------------------------------- |
| 配置         | Table capability、开关开关/删除/复制、旧字段清理、无 Alert level、渐变开关/Apply           | 前端单测、实际 UI                   |
| 原生染色     | 全数值区间、字符串大小写、布尔/空值、覆盖顺序、整行/跨列、文字、HTML、Cell Bar 零宽/被覆盖 | 前后端共享 golden、独立边界单测     |
| 字段来源     | raw、分组、保存指标、临时指标、字符串、百分比                                              | 单测、真实 ClickHouse               |
| 时间对比     | Main、历史值、差额、变化率；37 个分组、两个原始页；筛选行数 37/37/8/8，原生值完全一致      | 单测、真实 ClickHouse               |
| 分页         | client/server、最后一页回零、同列三色、跨列、多列、清除、空结果                            | 单测、真实 HTTP；常用操作有 UI 验证 |
| 容量         | 999/1,000/1,001 行，宽度/字节/单值边界、发布元数据加入后超限、普通页长/offset 大于 K       | 单测；行边界有真实 HTTP             |
| 状态         | 首次请求不额外触发空 ownState 查询、乱序返回、失败保留旧结果、只更新 clientView 不重查     | Redux/Chart action 单测             |
| 快照         | 失效/不存在、跨用户、RLS/权限/主题/规则变化、共享存储失败、并发构建占用、force refresh     | 单测；跨进程读取/过期另有真实验证   |
| Current View | 重排/搜索/页范围投影、空投影、重复/越界/不属于筛选集、旧 token、pending 阻断               | 前后端单测、真实 XLSX               |
| 导出         | CSV/JSON/XLSX；样式冻结、长整数、公式防护、非法控制字符、Tab 顺序/原子失败                 | 单测；XLSX/Tab 有真实 HTTP          |
| 分享         | v2 选择白名单、剔除 token/数据、失效状态清洗、不保存 clientView                            | 前后端单测                          |
| 功能关闭     | flag 关闭、无 filterable、其他图表类型走原路径                                             | 前后端定向回归                      |

### 3.3 查询次数与资源结论

执行次数观测使用原执行方法的透传包装，仍运行真实 ClickHouse SQL，不用
返回预制结果的 mock。普通冷快照基础查询一次；时间对比基础和历史查询各一次。
同一有效快照的后续选择、翻页和导出均为 **0 次测试源 SELECT**。
原有 `all_records` 总计等必要辅助查询按原语义执行，不承诺所有图表只产生一条 SQL。

1,000 指颜色筛选前的**图表结果行数**，不是 ClickHouse 扫描行数。
`LIMIT 1001` 可以约束结果搬运，不能保证复杂 JOIN/GROUP BY 只扫描 1,001 条。
超限时普通 Table 保持原有浏览数据，颜色过滤明确不可用，不截断成“前 1,000 行
里的绿色结果”。完整快照的字节预算也不是进程 RSS 硬限制。

## 4. 自审和复审中修正的问题

- Current View 必须导出当前可见行及顺序，不能仅按所有颜色匹配行导出；空投影
  不能退化为全量。API 增加受校验的 `view_rows`，响应带 `row_indices`。
- 单独更新 clientView 不应触发 Chart 请求；真实 Redux 父组件测试复现并修正
  undefined → 空 ownState 和伪 Cross Filter 导致的额外刷新。
- 预算检查改为增量累计，避免每页重复扫描前面所有行；超限普通页长/高 offset
  仍返回正确普通结果。加入 token 等发布元数据后再超限也走显式降级。
- 客户端筛选后的 Explore 顶部计数和 CSV 阈值使用 `filtered_rowcount`，
  保留零值；源 `sql_rowcount` 的诊断含义保持不变。
- 跨列导出必须按选择集合比较，不能因用户点击列顺序与后端规范排序不同而阻止
  Current View/Tab 导出。列/颜色逆序、清空/移除但响应未完成、失败回滚的回归已通过。
  实际页面先选 quantity、再选 gross_revenue 得到 15 行，Current View Excel
  操作成功发出请求并返回 HTTP 200，没有误报需要重新加载。
- 隐藏已选目标列时，即使 formatter 规则未变也清理失效选择；隐藏最后一个目标
  仍保留快照行索引投影，不退回客户端原始行值。client/server 与等价 rerender
  四项回归通过，其中两项隐藏测试已先在修复前复现失败。
- 校验发现的测试类型标注、脚本重复模块导入和格式问题在本次文件内解决。
  旧配色 HEX 只作为持久化标识匹配，不用于硬编码新 UI；映射属性带有局部原因说明。

## 5. 可复跑命令

在仓库根目录激活项目 venv，前端使用已安装的 Node.js 22。
真实服务脚本只允许 loopback URL；必须先启动加载此提交代码的 Superset、
ClickHouse 和共享 Redis，并设置本机配置路径。不要把本机配置或密码提交。

```bash
curl --noproxy '*' -f http://127.0.0.1:8088/health

NO_PROXY=127.0.0.1,localhost no_proxy=127.0.0.1,localhost \
SUPERSET_CONFIG_PATH="$PWD/superset_config.py" \
venv/bin/python scripts/tests/validate_table_color_filters.py --transport http

NO_PROXY=127.0.0.1,localhost no_proxy=127.0.0.1,localhost \
SUPERSET_CONFIG_PATH="$PWD/superset_config.py" \
venv/bin/python scripts/tests/validate_table_color_filters.py --transport test-client

NO_PROXY=127.0.0.1,localhost no_proxy=127.0.0.1,localhost \
SUPERSET_CONFIG_PATH="$PWD/superset_config.py" \
venv/bin/python scripts/tests/validate_table_color_dashboard_export.py

venv/bin/python -m pytest \
  tests/unit_tests/common/test_table_color_query.py \
  tests/unit_tests/common/test_table_color_snapshot.py \
  tests/unit_tests/common/test_table_color_context.py \
  tests/unit_tests/common/test_table_color_styles.py \
  tests/unit_tests/common/test_table_color_export_precision.py \
  tests/unit_tests/common/test_query_context_factory.py \
  tests/unit_tests/common/test_query_context_processor.py \
  tests/unit_tests/common/test_table_alerts.py \
  tests/unit_tests/commands/dashboard/export_xlsx_test.py \
  tests/unit_tests/commands/dashboard/table_color_export_test.py \
  tests/unit_tests/charts/data_api_test.py \
  tests/unit_tests/charts/test_schemas.py \
  tests/unit_tests/utils/styled_excel_test.py \
  tests/unit_tests/utils/json_tests.py -q --disable-warnings
```

前端在 `superset-frontend` 目录执行下列精确去重测试集：

```bash
NODE_ENV=test NODE_OPTIONS=--max-old-space-size=4096 \
node node_modules/jest/bin/jest.js --runInBand --runTestsByPath \
  packages/superset-ui-chart-controls/test/utils/getColorFormatters.test.ts \
  plugins/plugin-chart-table/test/TableChart.test.tsx \
  plugins/plugin-chart-table/test/buildQuery.test.ts \
  plugins/plugin-chart-table/test/controlPanel.test.ts \
  plugins/plugin-chart-table/test/controlPanel.test.tsx \
  plugins/plugin-chart-table/test/resolveTableCellStyle.test.ts \
  plugins/plugin-chart-table/test/sortAlphanumericCaseInsensitive.test.ts \
  plugins/plugin-chart-table/test/utils/formatValue.test.ts \
  src/components/Chart/Chart.test.tsx \
  src/components/Chart/ChartRenderer.test.tsx \
  src/components/Chart/chartAction.samples.test.ts \
  src/components/Chart/chartActions.test.ts \
  src/components/Chart/tableColorChartActions.test.ts \
  src/components/Chart/tableColorFilterRequest.test.ts \
  src/dashboard/components/Dashboard.test.tsx \
  src/dashboard/components/menu/DownloadMenuItems/DownloadMenuItems.test.tsx \
  src/dashboard/permalink/sanitizeShareableState.test.ts \
  src/dashboard/util/activeAllDashboardFilters.test.ts \
  src/explore/actions/saveModalActions.test.ts \
  src/explore/components/ChartPills.test.tsx \
  src/explore/components/ExploreChartHeader/ExploreChartHeader.test.tsx \
  src/explore/components/ExploreViewContainer/ExploreViewContainer.test.tsx \
  src/explore/components/controls/ConditionalFormattingControl/ConditionalFormattingControl.test.tsx \
  src/explore/components/controls/ConditionalFormattingControl/FormattingPopoverContent.test.tsx \
  src/explore/exploreUtils/exportChart.test.ts --silent

node node_modules/typescript/bin/tsc --noEmit --pretty false -p tsconfig.json
```

## 6. 门禁、部署和未验证范围

最终 86 个实现/测试文件的定向 pre-commit 全部通过，使用
`CI=1 GITHUB_BASE_REF=dev/6.1`，不修改钩子或检查项。代码提交后又单独运行
Pylint 钩子，确认按此开发基线实际覆盖新提交并通过；`git diff --cached --check`
通过。默认 `master` 基线 Pylint 扫描发现四个未修改模块的
既有 `consider-using-transaction` 提示：`commands/report/execute.py`、
`db_engine_specs/base.py`、`migrations/shared/utils.py`、`utils/log.py`。
没有删除或关闭该 checker，没有混入这些模块的修改。本次另外直接对全部变更
生产 Python 文件运行 Pylint，结果 10.00/10；`dev/6.1` 基线定向门禁另行记录。
不能据此宣称默认全仓检查已经通过。推送前仍必须执行 `pre-commit run --all-files`。

生产上线前必须同时部署新版前后端；在现有 `FEATURE_FLAGS` 中合并启用
`TABLE_ALERT_FILTERS`，其默认值仍为 false。每条规则是否启用仍由 formatter
中的唯一开关决定，不因部署 flag 打开而批量改动规则。

| 配置                                       | 默认值 / 部署要求                                                       |
| ------------------------------------------ | ----------------------------------------------------------------------- |
| `DATA_CACHE_CONFIG`                        | 配置可用的共享 Redis/Memcached；默认 NullCache 不支持跨 worker 颜色快照 |
| `TABLE_ALERT_FILTER_MAX_ROWS`              | 1,000；不覆盖全局 ROW_LIMIT 或单页大小                                  |
| `TABLE_ALERT_FILTER_MAX_CELLS`             | 1,000,000                                                               |
| `TABLE_ALERT_FILTER_MAX_BYTES`             | 64 MiB，完整序列化快照预算                                              |
| `TABLE_ALERT_FILTER_MAX_CELL_BYTES`        | 1 MiB                                                                   |
| `TABLE_ALERT_FILTER_TIMEOUT`               | 30 秒，且不扩大已有 Superset timeout；不等同改造数据库取消机制          |
| `TABLE_ALERT_FILTER_SNAPSHOT_TTL`          | 300 秒                                                                  |
| `TABLE_ALERT_FILTER_ALLOW_IN_MEMORY_CACHE` | false；生产不能以进程内缓存替代共享存储                                 |

以下未执行，不标记通过：真正 Python 3.10 运行时测试；MySQL/PostgreSQL 真实
连接差分；生产复杂 SQL 基准、峰值 RSS 和多用户压力测试；真实用户权限撤销/
Guest Token 浏览器会话；完整 Permalink 首屏浏览器恢复；窄屏和完整键盘/
屏幕阅读器验收；所有 Superset 测试与生产打包。已有相关定向单测不能替代这些验收。

本次未推送，也未覆盖远端历史。回退后的本地分支与远端分叉，后续推送需要先
明确历史处理方式。设计、功能实现和本文验证记录分别提交；本地配置与
`.codex/`、`.npm-cache/`、`superset_home/` 不纳入提交。
