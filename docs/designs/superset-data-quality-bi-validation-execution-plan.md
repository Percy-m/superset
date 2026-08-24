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

# Superset 数据质量 BI 增强补漏与验证执行计划

| 属性         | 值                                                                                                  |
| ------------ | --------------------------------------------------------------------------------------------------- |
| 文档版本     | V1.9                                                                                                |
| 文档状态     | Executing                                                                                           |
| 日期         | 2026-08-24                                                                                          |
| 代码分支     | `dev/6.1`                                                                                           |
| 起始基线     | `4b2b732ecf`                                                                                        |
| 关联设计     | [详细设计](./superset-data-quality-bi-detailed-design.md)                                           |
| 关联计划     | [FR-01～FR-05 实施计划](./superset-data-quality-bi-implementation-plan.md)                          |
| 关联补充设计 | [ClickHouse 21.3 与 Drill Detail 对齐](./superset-clickhouse-21-3-drill-detail-alignment-design.md) |
| 执行原则     | 先关闭 FR-05 明确漏项，再按驱动、执行链、权限/数据一致性、浏览器和跨平台顺序逐项验证                |

## 1. 结论与范围

本计划处理两类事项：

1. 修复 FR5-05 遗漏的虚拟数据集编辑器 `Open in SQL Lab` 新窗口入口；
2. 对已经实现但尚未形成发布级证据的场景逐项验证。`PENDING` 只表示尚未执行；开始执行
   后，完成态只能记录为 `PASS`、`FAIL` 或 `BLOCKED`，不得以单元测试替代真实数据库、
   浏览器或权限链路。

已确认仅为显示问题的经典 Table 服务端分页页长 `0` 不在本计划范围内。FR-01～FR-04
不重新设计功能；验证发现缺陷时，建立独立修复步骤和提交，不借验证进行无关重构。

## 2. 严格执行顺序

```mermaid
flowchart TD
  A["V0：记录环境与数据基线"] --> B["V1：修复 DatasourceEditor FR5-05 漏迁移"]
  B --> C{"FR-05 定向门禁通过？"}
  C -- "否" --> B
  C -- "是" --> D["V2：ClickHouse Connect 回到声明支持版本"]
  D --> E["V3：ClickHouse 21.3 双 connector 与同步/异步执行链"]
  E --> F["V4：FR-01～FR-04 权限、RLS 与 Guest Token"]
  F --> G["V5：Permalink 冷启动首请求"]
  G --> H["V6：真实 Dashboard XLSX 全栈与原子失败"]
  H --> I["V7：FR-05 浏览器新窗口 POST 与 ClickHouse 执行"]
  I --> J["V8：Drill Detail 跨平台 Playwright"]
  J --> K["V9：Flag 组合、全仓门禁与证据归档"]
```

每个阶段必须先记录环境和命令，再执行测试，最后更新本文状态。前一阶段出现数据错误、
权限绕过、敏感 SQL 泄露或无法解释的回归时，停止后续阶段；纯环境缺失记录为
`BLOCKED`，不把它写成功能失败。

## 3. V1：FR5-05 DatasourceEditor 补漏方案

### 3.1 现状和影响

`DatasourceEditor.getSQLLabUrl()` 把虚拟数据集的 `datasource.sql` 放入查询字符串；顶部
新窗口按钮和结果/错误提示中的文本链接复用该 URL。即使
`LONG_SQL_POST_NAVIGATION=true`，该入口仍可能遇到 URL 长度限制，并让 SQL 进入浏览器
历史或 Referer。这是原 6.1 路径在 FR5-05 迁移时的遗漏，不是分页、告警颜色或 Drill
Detail 修复引入的回归。

### 3.2 实现设计

1. 扩展 `SqlLabRequestedQuery` 类型，除 `datasourceKey/sql` 外允许虚拟数据集入口需要的
   `dbid`、`name`、`catalog`、`schema`、`autorun` 和 `isDataset`；不使用 `any`。
2. `DatasourceEditor` 增加唯一的 requested-query 构造方法，按钮和文本链接不得各自拼装。
3. Flag 关闭：保留现有 `URLSearchParams`、`window.open` 和 `<a href>` 兼容行为。
4. Flag 开启：两类入口都调用 `openSqlLabQuery({target: 'new-tab'})`，由
   `SupersetClient.postForm('/sqllab/', {form_data})` 打开新窗口；链接的 `href` 只能是
   不含 SQL 的 `/sqllab/` 安全回退地址。
5. 隐藏表单构造、认证或 `submit()` 抛错时调用既有 danger toast；禁止回退为携带 SQL 的
   GET，也禁止在 toast 或日志中带出 SQL 正文。`postForm` resolve 不代表目标页 HTTP
   成功，403/500 必须在新标签页单独验证。
6. SQL Lab bootstrap 必须恢复数据库、catalog、schema、名称、SQL、autorun 和 dataset
   上下文；不得只验证请求已发出。
7. `PopEditorTab` 将 POST boolean 和旧 URL string 规范化后写入 `QueryEditor.autorun` 与
   `QueryEditor.isDataset`，确保虚拟数据集提示和编辑器布局不丢失。
8. helper 直接向 `SupersetClient.postForm` 传 `/sqllab/`，由客户端统一追加一次
   `appRoot`；禁止入口先调用 `ensureAppRoot`。
9. `SqllabView.root` 的 POST 事件日志中，由 request 派生的 payload 使用端点级白名单，
   只保留 path、载荷状态、字符数、UTF-8 字节数和 SHA-256 前 12 位；日志框架仍可加入
   `object_ref` 及 action/user/duration/referrer envelope，但不得记录 SQL、`form_data`、
   CSRF/Guest Token 或额外 form/query 字段。其他端点和 SQL Lab GET 不改变。

```mermaid
sequenceDiagram
  actor User as 用户
  participant Editor as DatasourceEditor
  participant Flag as LONG_SQL_POST_NAVIGATION
  participant Helper as openSqlLabQuery
  participant Client as SupersetClient.postForm
  participant View as SqllabView.root
  participant Lab as SQL Lab

  User->>Editor: 点击按钮或文本链接
  Editor->>Flag: 检查开关
  alt Flag 关闭
    Editor->>Lab: GET /sqllab/?dbid=...&sql=...
  else Flag 开启
    Editor->>Editor: 构造完整 requestedQuery
    Editor->>Helper: target=new-tab
    Helper->>Client: POST /sqllab/ form_data
    Client->>View: SQL 仅位于 request body
    View->>View: request payload 仅保留白名单诊断
    View-->>Lab: bootstrap requested_query
    Lab->>Lab: PopEditorTab 恢复完整 QueryEditor
    Lab-->>User: 新窗口恢复虚拟数据集查询
  end
```

### 3.3 测试门禁

| ID       | 层级     | 场景                                    | 通过标准                                                                 |
| -------- | -------- | --------------------------------------- | ------------------------------------------------------------------------ |
| `V1-T01` | Jest/RTL | Flag 开启，顶部按钮                     | helper 收到完整 dataset payload；`window.open` 未调用                    |
| `V1-T02` | Jest/RTL | Flag 开启，文本链接                     | `preventDefault`；POST payload 完整；href 不含 SQL                       |
| `V1-T03` | Jest/RTL | Flag 关闭，按钮和链接                   | 原 URL 参数、目标窗口和交互保持兼容                                      |
| `V1-T04` | Jest/RTL | 300 行、至少 12,000 Unicode 字符        | CJK、emoji、换行逐字节一致；URL 不含 SQL                                 |
| `V1-T05` | Jest/RTL | 表单构造、认证或 submit reject          | danger toast；无 GET/URL 降级；toast 不含 SQL                            |
| `V1-T06` | Flask    | `/sqllab/` 接收 dataset requested query | bootstrap JSON 保留全部字段                                              |
| `V1-T07` | Jest/RTL | `PopEditorTab` 消费 POST bootstrap      | 完整 `QueryEditor` 含 `isDataset=true`                                   |
| `V1-T08` | Python   | SQL Lab POST 事件日志                   | request payload 仅含白名单诊断；框架 envelope 可存在；SQL/Token 均不存在 |
| `V1-T09` | Jest     | `APPLICATION_ROOT=/prefix` 契约         | 最终 form action 只有一个 `/prefix`                                      |
| `V1-T10` | 浏览器   | 本地虚拟数据集编辑器按钮和文本链接      | 新窗口 200；地址栏、Referer 与事件日志无 SQL；完整上下文及结果恢复       |

定向命令：

```bash
cd superset-frontend
npm run test -- src/SqlLab/utils/openSqlLabQuery.test.ts
npm run test -- src/SqlLab/components/PopEditorTab/PopEditorTab.test.tsx
npm run test -- src/components/Datasource/components/DatasourceEditor/tests/DatasourceEditor.test.tsx
npm run test -- packages/superset-ui-core/test/connection/SupersetClientClass.test.ts
npm run type
cd ..
pytest tests/unit_tests/utils/log_tests.py
pytest tests/integration_tests/sqllab_tests.py \
  -k test_sqllab_post_preserves_long_unicode_requested_query
git diff --check
```

V1 的阻断门禁是上述新增/受影响测试、类型检查和浏览器恢复链路。完整
`tests/integration_tests/sqllab_tests.py` 另作为本机基线债务执行：若失败只来自已经定位的
integration metadata/权限 fixture，记录 `V1-BASELINE BLOCKED` 后可进入 V2，但必须在 V9
前初始化标准 test metadata 并重跑通过；如果失败触及本次 POST、bootstrap 或日志分支，则
视为 V1 失败并停止。环境债务获准继续不等于发布级通过。

## 4. V2～V3：ClickHouse 21.3 驱动与执行链

### 4.1 环境转换策略

仓库声明 `clickhouse-connect>=0.13.0,<1.0`，而起始本机 venv 为 `1.3.0`，其结果不能作为
声明支持范围的发布证据。执行时先记录完整 freeze，再按以下顺序转换：

1. 在 `/private/tmp` 独立 `--target` 目录安装 `0.15.1`，通过 `PYTHONPATH` 验证导入、
   EngineSpec、SQLGlot、自有 dialect 和真实 21.3 查询，主 venv 暂不修改；
2. 先把 `validate.sql` 扩展为恰好 26 项并修正 seed gate：输出协议固定为
   `TabSeparatedRaw` 四列 `check_order,check_name,observed,status`。解析器必须断言恰好
   26 行、顺序严格为 1..26、名称非空且唯一、每行恰好四列且 status 全为 `PASS`，不能只
   依赖 SQL 客户端退出码；第 26 项覆盖 minute/hour/day/week/month/quarter/year 七粒度，
   week 对照 `toMonday`，每个 mismatch 均为 0；
3. shadow `0.15.1` 通过后，主 venv 固定到 `0.15.1`，执行 `pip check` 并重启 Flask；
4. 执行 26/26 数据门禁、真实 connector、SQL Lab、Chart/Data API 和浏览器链路；
5. 在第二个 shadow 目录安装下界 `0.13.0`，重复 connector/dialect 最小矩阵；不得把下界
   留在主 venv；
6. legacy `clickhouse-sqlalchemy==0.2.9` 只在第三个一次性 target/venv 中验证，以约束文件
   固定 `SQLAlchemy==1.4.54`，先 dry-run 并保存 freeze；禁止其传递依赖改写主 venv 的
   SQLAlchemy。`0.3.2` 要求 SQLAlchemy 2，不属于本仓库 1.4.54 基线；
7. 结束时主 venv 恢复 `0.15.1`，执行 `pip check`、重启、`/health` 和版本断言。

shadow 环境命令模板如下；两个版本必须使用不同目录，代理变量全部清除，本机地址显式进入
`NO_PROXY`：

```bash
CH0151_SHADOW="$(mktemp -d /private/tmp/superset-clickhouse-connect-0.15.1.XXXXXX)"
env -u HTTP_PROXY -u HTTPS_PROXY -u ALL_PROXY \
  -u http_proxy -u https_proxy -u all_proxy \
  NO_PROXY=127.0.0.1,localhost no_proxy=127.0.0.1,localhost \
  venv/bin/python -m pip install --disable-pip-version-check --no-cache-dir \
  --no-deps --target "$CH0151_SHADOW" 'clickhouse-connect==0.15.1'
PYTHONPATH="$CH0151_SHADOW" venv/bin/python -c \
  'from importlib.metadata import version; assert version("clickhouse-connect") == "0.15.1"'

CH0130_SHADOW="$(mktemp -d /private/tmp/superset-clickhouse-connect-0.13.0.XXXXXX)"
env -u HTTP_PROXY -u HTTPS_PROXY -u ALL_PROXY \
  -u http_proxy -u https_proxy -u all_proxy \
  NO_PROXY=127.0.0.1,localhost no_proxy=127.0.0.1,localhost \
  venv/bin/python -m pip install --disable-pip-version-check --no-cache-dir \
  --no-deps --target "$CH0130_SHADOW" 'clickhouse-connect==0.13.0'

LEGACY_SHADOW="$(mktemp -d /private/tmp/superset-clickhouse-sqlalchemy-0.2.9.XXXXXX)"
env -u HTTP_PROXY -u HTTPS_PROXY -u ALL_PROXY \
  -u http_proxy -u https_proxy -u all_proxy \
  venv/bin/python -m pip install --dry-run --ignore-installed \
  'SQLAlchemy==1.4.54' 'clickhouse-sqlalchemy==0.2.9'
env -u HTTP_PROXY -u HTTPS_PROXY -u ALL_PROXY \
  -u http_proxy -u https_proxy -u all_proxy \
  venv/bin/python -m pip install --disable-pip-version-check --no-cache-dir \
  --target "$LEGACY_SHADOW" \
  'SQLAlchemy==1.4.54' 'clickhouse-sqlalchemy==0.2.9'
PYTHONPATH="$LEGACY_SHADOW" venv/bin/python -c \
  'import sqlalchemy; assert sqlalchemy.__version__ == "1.4.54"'
venv/bin/python - <<'PY'
import socket
with socket.create_connection(("127.0.0.1", 9000), timeout=2):
    pass
PY
PYTHONPATH="$LEGACY_SHADOW" venv/bin/python - <<'PY'
from sqlalchemy import create_engine, text
engine = create_engine(
    "clickhouse+native://default:@127.0.0.1:9000/superset_quality_21_3"
)
with engine.connect() as connection:
    assert str(connection.execute(text("SELECT version()")).scalar()).startswith("21.3.")
PY

# 只有 shadow 0.15.1 与 26/26 gate 通过后才允许执行主 venv 回退。
env -u HTTP_PROXY -u HTTPS_PROXY -u ALL_PROXY \
  -u http_proxy -u https_proxy -u all_proxy \
  venv/bin/python -m pip install --disable-pip-version-check --no-cache-dir \
  --no-deps --force-reinstall 'clickhouse-connect==0.15.1'
```

`requirements/base.txt` 含 editable `-e ./superset-core`，pip 不允许把这类 requirements
文件直接作为 constraint；legacy shadow 因此在 dry-run 和实际安装中显式固定
`SQLAlchemy==1.4.54`。安装后同时检查 shadow freeze 和主 venv 版本，不能用忽略 constraint
错误的命令作为验证证据。

版本转换后必须重启 Flask；仅执行 `pip show` 不算验证。前端 dev server无需因 Python
driver 变更重启。legacy 真实连接固定为
`clickhouse+native://default:@127.0.0.1:9000/superset_quality_21_3`；若 native 9000 未暴露，
`V3-T01` 记为 `BLOCKED`，并在环境/证据列记录 `reason=ENVIRONMENT`，不得用 HTTP
connector 代替。安装前后都要断言主 venv 的 SQLAlchemy 仍为 1.4.54，并保存 shadow
freeze。

### 4.2 Seed、异步环境与失败语义

`scripts/tests/seed_clickhouse_21_3.sh` 必须把结果解析拆成可从 stdin 或文件注入的函数。
脚本单测分别输入 25 行、27 行、任一 `FAIL`、重复/乱序 order、重复/空名称、三列或五列，
均应非零退出；只允许满足上述四列协议的 26 行通过。`--validate-only` 只执行版本检查和
`validate.sql`：执行前后采集目标表的 `database,name,uuid,metadata_modification_time`，并
断言完全一致，以证明没有运行 DROP/CREATE。第 26 项在恰好 12,000 行 `fact_events` 上
逐粒度输出 `mismatch=0`，不得把七个结果压成无法定位的单一布尔值。

SQL Lab 的 SQL 正文允许存在于经过授权的 `Query.sql`、`Query.executed_sql`、SQL Lab 成功
响应的 `sql/executedSql` 字段及显式配置的 `QUERY_LOGGER` 审计目标；这些是产品执行和审计
契约，不应误报为泄露。禁止面是 URL/history/Referer、DB event log、access/application INFO
日志、API 错误字段、`Query.error_message/extra.errors` 和 Celery 错误 payload。验证使用仅存于
内存的 canary，并只归档 SQL 长度、UTF-8 字节数、SHA-256 前缀和命中计数，不归档正文。
运行时固定 `DEBUG=false`、`QUERY_LOGGER=None`；如果要求 DEBUG 或显式审计目标也不记录 SQL，
属于不同的日志契约变更，不能由本门禁替代。

`/api/v1/sqllab/execute/` 的 request-derived event payload 必须端点级白名单化：仅保留 path、
database ID、sync/async、query limit、载荷状态、字符数、UTF-8 字节数和哈希前缀，排除
`sql`、`templateParams`、catalog/schema、client/tab 标识、Token 及 query string。raw Code 36
负例必须先使用存在的表和列，断言 code 恰为 36；不存在列导致的 Code 47 可回显完整 SQL，
不能作为大小写兼容或脱敏成功证据。错误检查针对 exact submitted SQL、SQL canary 和 filter
canary，不使用含义过宽的 `SELECT` 字符串匹配。白名单保留 EventLogger 已有的 `runAsync`、
`queryLimit`、`select_as_cta` 键名；孤立 surrogate 输入只产生 `invalid_sql_encoding` 状态，
不能让事件采集覆盖业务响应。Code 36 只匹配 `[SQL: ...]` 前的 `DB::Exception` 错误段，SQL
正文或注释中的同名短语不能触发重分类。

真实异步链路需要临时 Redis 或预先为空的专用 Redis DB、`RESULTS_BACKEND`、Celery
broker/result backend、worker，以及测试 Database 的 `allow_run_async=1`。每次执行使用唯一
`RUN_ID`，以该 ID 命名容器或 Redis key prefix、队列和临时 Superset config；共享 Redis 时
必须先断言专用 DB 为空，只按 before/after key delta 精确清理，禁止 `FLUSHDB`。启动 worker
后先用 ping 验证，再提交单/多 statement 异步 SQL，并比对 cursor SQL 与
`executed_sql`；驱动自动附加的 wire-format suffix 单独分类，不误报为产品 SQL 改写。结束
时仅按 RUN_ID 停止 worker、删除专属 key/容器和临时配置，并恢复 `allow_run_async`。如果
Docker/worker 无法启动，真实异步项记
为 `BLOCKED`，并在环境/证据列记录 `reason=ENVIRONMENT`；Mock/单元测试可单独 PASS，但
不得替代真实项。该环境阻塞允许继续 V4～V8，V9 必须标记“非发布级完成”，不得宣称全
通过。

### 4.3 验证矩阵

| ID       | 范围                         | 关键检查                                                                  |
| -------- | ---------------------------- | ------------------------------------------------------------------------- |
| `V2-T01` | 服务与数据基线               | Superset health、CH `21.3.x`、8 表+1 view、92k 行、26/26 门禁             |
| `V2-T02` | Connect `0.15.1`             | 七种 dateTrunc 小写、真实查询、Chart/Data API                             |
| `V2-T03` | Connect `0.13.0`             | 最低声明版本可导入、连接、执行七粒度                                      |
| `V2-T04` | MySQL/PostgreSQL/YQL 回归    | dialect 映射和 SQL 输出不变                                               |
| `V2-T05` | seed gate 自检               | 四列协议、26 行/顺序/唯一名称/PASS；负例非零；validate-only 元数据不变    |
| `V2-T06` | relation 分类与安装边界      | connect 0.13/0.15 均归一化为 8 table+1 view；API/UI 正确；metadata `<1.0` |
| `V3-T01` | legacy clickhouse-sqlalchemy | `0.2.9` + SQLAlchemy `1.4.54`；native 9000；真实 21.3 查询与错误分类      |
| `V3-T02` | SQL Lab 同步                 | cursor SQL 和 `executed_sql` 都使用小写静态粒度                           |
| `V3-T03` | SQL Lab 异步/Celery runtime  | 唯一 Redis/Celery runtime；单/多 statement 解析与最终 SQL 一致；精确清理  |
| `V3-T04` | raw connector Code 36        | 直接发送大写 unit 时 21.3 受控失败                                        |
| `V3-T05` | Superset 正常链路            | 静态 unit 被转小写并成功；`executed_sql` 与 cursor 一致                   |
| `V3-T06` | 受控错误脱敏                 | 授权 SQL 面保留契约；禁止面不含 exact SQL/canary/filter value             |

## 5. V4～V8：功能与浏览器验证

### 5.1 共用前置条件与回滚

- V2 的 26/26 数据门禁必须为 PASS；ClickHouse 连接固定为
  `clickhousedb+connect://default:@127.0.0.1:8123/superset_quality_21_3`，证据中不复制密码。
- 本机 metadata 变更前创建
  `RUN_ID="$(date +%Y%m%dT%H%M%S)-$$"` 和
  `EVIDENCE_DIR="$(mktemp -d /private/tmp/superset-dq-validation-${RUN_ID}.XXXXXX)"`；使用
  `sqlite3 superset_home/superset.db ".backup '$EVIDENCE_DIR/superset.db'"` 取得在线一致快照，
  对备份执行 `PRAGMA integrity_check` 后 `chmod 400`。把源文件及备份 SHA-256、起始提交和
  环境版本写入 manifest。所有新建对象使用 `dqv_${RUN_ID}_` 前缀，并把 Dashboard、Slice、
  Dataset、RLS、Role 和 Permalink 精确 ID 写入同一 run 的 `manifest.json`，不得覆盖历史
  run 的备份或证据。
- 身份固定为 admin、仅获指定 Dataset/Dashboard 的 `dqv_${RUN_ID}_gamma`、
  `dqv_${RUN_ID}_apac`、`dqv_${RUN_ID}_emea` RLS 用户和受限 Guest Token。每个 deny case
  使用独立身份，不复用 admin session。
- 每阶段完成后只按 manifest 中精确 ID 删除本阶段对象；不得按通配符删除。失败时保留备份和
  脱敏证据，不用备份覆盖当前 metadata，除非用户另行确认恢复。
- 自动化优先使用 pytest/Jest 的事务 fixture；只有浏览器特有行为才使用本机 8088/9001。

### 5.2 V4：权限、RLS 与数据一致性

| ID       | 操作/命令                                                                                                       | 断言                                                                                                                           | 清理/证据                             |
| -------- | --------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------ | ------------------------------------- |
| `V4-T01` | 先运行 Samples/排序/容量 unit 与精确 API/RLS 回归，再在真实 21.3 执行分页、搜索、四项容量和复杂类型矩阵         | FR-01 server/bounded、稳定排序、权限；Array-only 精确 422 且不创建 page/count QueryContext                                     | 临时 Dataset 按精确 ID 删除；脱敏摘要 |
| `V4-T02` | Table alert/QueryContext/schema/resolver pytest，加 Table `buildQuery`、`TableChart` Jest；真实关系留给 T03/T05 | 同 subject OR、跨 subject AND、WHERE/HAVING、伪造规则、缓存指纹、totals/export 一致                                            | 无 metadata；测试日志                 |
| `V4-T03` | 使用本次 RUN_ID 的 Gamma 和 APAC/EMEA 身份调用 Chart Data、Samples、Dashboard XLSX API                          | 有对象权限且命中 RLS 的身份返回 200，但行集仅含授权区域；无 Dataset、Dashboard 或 `can_csv` 权限的独立身份返回 403；错误无 SQL | manifest + 脱敏响应摘要               |
| `V4-T04` | 使用受限 Guest Token 重复 Native/Cross/alert 组合查询                                                           | Guest 只看到授权 Dashboard/Dataset 与 RLS 行；不继承链接创建者权限                                                             | 撤销 token；记录行数哈希              |
| `V4-T05` | 对同一筛选依次取页面数据、total count、totals、单表 XLSX 和 Tab XLSX                                            | 五个消费者基于同一过滤关系；行数、聚合总计和导出数据一致                                                                       | 删除导出临时文件                      |

本机集成基线初始化后必须检查 `Public` Role。测试配置的 `PUBLIC_ROLE_LIKE=Gamma` 会在执行
`superset init`/`load-test-users` 时把 `can_get_drill_info` 复制给 Public，FAB 随后把接口视为
公开资源，使“移除临时用户权限应返回 403”的用例失真。隔离测试库需清空 Public 的测试权限并
断言数量为 0；只允许修改临时 metadata，不修改产品配置或主 metadata。

`V4-T01` 必须按以下子阶段执行，任一失败立即停止：

1. unit、Samples、embedded、`drill_info` 与通用 RLS 精确节点；
2. 26/26 ClickHouse fixture 门禁；
3. 真实 server 第 1/2/50 页、Unicode 前缀搜索、1,000 行/50,000 cells/8 MiB/1 MiB
   边界；
4. 物理和虚拟 Array-only、`row_id + metrics` 混合投影及 Array structured search；
5. 前四步全部通过后，才执行独立 Gamma/APAC/EMEA 的 RLS count/data 验证。

若第 4 步发现复杂列因 `type_generic=STRING` 绕过标量门禁，记录 `V4-T01-H=FAIL`，创建
`V4-FIX-02`，完成 EngineSpec scoped 修复、双 ClickHouse key 与 MySQL/PostgreSQL 隔离测试、
真实 H/混合投影定向验证后，从第 1 步完整重跑为 `V4-T01B`。只有完整重跑通过，才能执行
独立代码与文档审查、`pre-commit run --all-files`、commit 和 push；不得跳过到 T02 或 V5。
追踪关系固定为：设计 `CH-14` = 实施 `FR1-FIX-01` = 验证/提交 `V4-FIX-02`。

### 5.3 V5：Permalink 冷启动

| ID       | 操作/命令                                                                                                                | 断言                                                                                                                                                          | 清理/证据                |
| -------- | ------------------------------------------------------------------------------------------------------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------ |
| `V5-T01` | `npm run test -- src/dashboard/containers/DashboardPage.test.tsx src/dashboard/permalink/sanitizeShareableState.test.ts` | hydrate gate 在状态清洗和注入前阻止 Chart 请求；Redux 恢复 Chart-ID mask                                                                                      | Jest 日志                |
| `V5-T02` | 浏览器新建含 Native、Cross、alert、active Tab 的 permalink，使用无缓存新标签页打开并捕获首批 Chart Data 请求             | Native、Cross 与 alert 已进入相应首个 Chart Data 请求；`activeTabs` 已在请求调度前恢复并只触发活动 Tab 下的首批 Chart；没有无筛选请求、二次闪烁或数据结果复用 | 截图 + 脱敏 request keys |
| `V5-T03` | 删除副本中的一个 Chart、Filter、Tab 和告警规则后再次访问旧链接                                                           | stale 项静默丢弃；计数日志无值；其余状态仍生效                                                                                                                | 删除副本与 permalink key |
| `V5-T04` | 以受限 Gamma/Guest 打开 admin 创建的链接并回归旧 Native-only permalink                                                   | 访问者实时 Dashboard/Dataset/RLS 权限生效；旧链接无额外请求                                                                                                   | session 隔离截图/日志    |

### 5.4 V6：真实 Dashboard XLSX

| ID       | 操作/命令                                                                                                                                                         | 断言                                                                         | 清理/证据              |
| -------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------- | ---------------------- |
| `V6-T01` | `pytest tests/unit_tests/commands/dashboard/export_xlsx_test.py tests/unit_tests/utils/styled_excel_test.py tests/integration_tests/dashboards/xlsx_api_tests.py` | 样式、Data Bar、Sheet 名、公式注入、长整数、10 表限制、权限和原子失败        | pytest 回滚            |
| `V6-T02` | 在本机 Dashboard 副本选择父/子嵌套 Tab，下载真实 ClickHouse workbook；用 `openpyxl` 只读检查                                                                      | Sheet 深度优先顺序、Slice 去重、背景/文字色、Data Bar、屏幕/导出筛选数据一致 | 保存脱敏结构摘要后删除 |
| `V6-T03` | 分别导出 10 个和 11 个经典 Table，并混入非 Table Chart                                                                                                            | 10 个成功；11 个整体 422 且不截断；非 Table 写入 `_导出说明`                 | 删除临时 Dashboard     |
| `V6-T04` | 在第 N 个 Chart 注入受控查询失败和 Workbook close 失败                                                                                                            | 返回 JSON 错误，不发送 XLSX 前缀；临时文件计数回到基线；错误无 SQL/筛选值    | 解除 fault injection   |

### 5.5 V7：FR-05 浏览器与 ClickHouse 执行

| ID       | 操作/命令                                                                                           | 断言                                                                                                                                           | 清理/证据          |
| -------- | --------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------- | ------------------ |
| `V7-T01` | 在虚拟数据集编辑器填入 300 行、至少 12,000 Unicode 字符，分别点击顶部按钮和结果文本链接             | 两次均以隐藏 form POST 打开新窗口；action 正确追加一次 appRoot；地址栏/history 无 SQL                                                          | 关闭新标签页；截图 |
| `V7-T02` | 在新 SQL Lab 检查数据库、catalog、schema、名称、SQL、autorun、dataset warning，并执行可运行 fixture | 字节/换行/CJK/emoji 完整；`QueryEditor.isDataset=true`；ClickHouse 21.3 query success                                                          | 删除 Query 记录    |
| `V7-T03` | 查询浏览器 Referer、Flask access/event log 与 metadata `Log.json`                                   | request-derived payload 仅含 path/status/counts/hash；框架 `object_ref` 与列 envelope 可存在；SQL marker、form_data、CSRF/Guest Token 均不存在 | 归档脱敏字段清单   |
| `V7-T04` | Flag 关闭后重复按钮和链接；再恢复开启                                                               | 关闭态保持旧兼容 URL；开启态恢复 POST；切换不改变其他 SQL Lab 入口                                                                             | 恢复 config 并重启 |

### 5.6 V8：Drill Detail 跨平台几何

| ID       | 操作/命令                                                                                                                                        | 断言                                                                                      | 清理/证据              |
| -------- | ------------------------------------------------------------------------------------------------------------------------------------------------ | ----------------------------------------------------------------------------------------- | ---------------------- |
| `V8-T01` | `npm run test -- packages/superset-ui-core/src/components/Table/VirtualTable.test.tsx src/components/Chart/DrillDetail/DrillDetailPane.test.tsx` | 唯一列布局驱动 header/body；分页可达；1/3/52 列与首次滚动宽度一致                         | Jest 日志              |
| `V8-T02` | Playwright 在本机 overlay scrollbar 下测试 100%/200% zoom、窄/宽 pane、dark mode、横纵滚动、键盘焦点                                             | 每列 header/body 左边界误差不超过 1 px；最后一行和分页控件可见；无 `100vw`/padding 偏差   | 截图和几何 JSON        |
| `V8-T03` | 使用参数化 scrollbar occupied width 做组件几何测试；请求 Windows/Linux classic-scrollbar runner                                                  | 组件断言通过；若本机无法产生 classic scrollbar，仅外部 runner 项记 `BLOCKED`，不误报 PASS | 保留 runner 请求与日志 |

V4～V8 不新增 Cypress。任何阶段产生新功能修复时立即停止后续验证，先完成定向测试，再从该阶段
第一项完整重跑；重跑通过后才能完成代码审查、全仓门禁、独立 commit 和 push。

## 6. V9：组合回归、提交与证据格式

组合顺序为全 Flag 关闭、逐项开启、累积开启，最后执行
`alert + Permalink + Tab XLSX + APAC RLS`。V1 功能修复结束形成一个 code review → tests →
commit → push 边界；V2/V3 环境和数据门禁、V4～V8 缺陷修复及最终证据各自形成独立边界。
**每一次 push 前**都必须完成以下闭环；`<allowlist...>` 只能列出本次提交所属文件：

```bash
git diff --check
git add -- <allowlist...>
git diff --cached --name-only
# 断言 staged names 不包含 .gitignore、.codex/、superset_home/ 或非本提交文件
pre-commit run --all-files
# 如 hook 自动修改文件：只重新 stage allowlist，commit/amend 后再运行全仓门禁
git add -- <allowlist...>
git diff --cached --check
pre-commit run --all-files
git diff --cached --check
git diff --check
```

最后一次 `pre-commit run --all-files` 必须返回 0 且不再产生修改；若在它之后执行
`git commit --amend` 纳入 autofix，必须再跑一次相同门禁，然后才能 push。push 前再次检查
`git diff --cached --name-only`（提交前）或 `git show --name-only`（提交后）的 allowlist。

每项结果按以下格式追加到第 7 节：

```text
ID | PASS/FAIL/BLOCKED | 环境/版本 | 命令或操作 | 关键断言 | 日志/截图/文件位置
```

测试日志不得包含 SQL 正文、筛选值、阈值、凭据或数据库异常详情。失败产生功能修复时，代码
和测试同一提交；纯证据归档使用单独文档提交。不得触碰用户已有 `.gitignore`、`.codex/`
和 `superset_home/`。

## 7. 执行记录

| ID                      | 状态        | 环境/版本                                              | 证据                                                                                                                                                                                                                                                                                                                                                                    |
| ----------------------- | ----------- | ------------------------------------------------------ | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `V0-T01`                | PASS        | Superset `8088`、frontend `9001`，18:07 CST            | `curl /health=200`；浏览器 Dataset 编辑器可交互；Flask session `35214`                                                                                                                                                                                                                                                                                                  |
| `V0-T02`                | PASS        | Git `dev/6.1@4b2b732ecf`，2026-08-24                   | `git rev-parse HEAD`；`git status --short --branch`，用户路径未纳入改动                                                                                                                                                                                                                                                                                                 |
| `V1-T01～T05`           | PASS        | Jest，Node 22                                          | DatasourceEditor 与 helper 正向、负向和关闭态兼容用例通过                                                                                                                                                                                                                                                                                                               |
| `V1-T06～T08`           | PASS        | Jest + pytest，Node 22 / Python 3.11                   | bootstrap、PopEditor hydration、事件日志白名单用例通过                                                                                                                                                                                                                                                                                                                  |
| `V1-T09`                | PASS        | Jest，`appRoot=''` 与 `/prefix`                        | SupersetClient form action 契约通过                                                                                                                                                                                                                                                                                                                                     |
| `V1-T10`                | PASS        | 本机浏览器 + ClickHouse 21.3                           | 按钮/文本链接均打开 clean `/sqllab`；dataset 提示、schema、查询和结果恢复；`Log.referrer` 为不含 SQL 的 Dataset list URL                                                                                                                                                                                                                                                |
| `V1-LOG`                | PASS        | metadata `Log.id=3643`                                 | request payload 仅含 path/accepted/长度/字节/哈希和框架 `object_ref`；SQL、form_data、Token 命中均为 0                                                                                                                                                                                                                                                                  |
| `V1-BASELINE`           | PASS        | 唯一 `/private/tmp` SQLite + 标准 test init            | 先确认旧 integration metadata 因缺 `can_read SQLLab`、`can_execute_sql_query SQLLab` 与 `can_post TabStateView` 得到 13 passed/12 skipped/5 failed；隔离执行 db upgrade → init → load-test-users 后为 18 passed/12 skipped/0 failed，未修改现有 metadata                                                                                                                |
| `V2-T01`                | PASS        | ClickHouse `21.3.20.1`                                 | 8 张物理表 + 1 个 View、92,000 行；严格四列 26/26 gate PASS；第 26 项 12,000 行且七粒度 mismatch 全 0                                                                                                                                                                                                                                                                   |
| `V2-T02`                | PASS        | shadow + active Connect `0.15.1`                       | 120 个 dialect/EngineSpec/seed 测试通过；真实 21.3 连接、七粒度、12,000 行和 View 10,000 行通过；主 venv `pip check`；18:58 CST 重开 Dashboard 2 后 Chart Data POST 200，Table 渲染 4 个 segment、各 250 行；SQL Lab execute 200 并显示 1K 行结果                                                                                                                       |
| `V2-T03`                | PASS        | shadow Connect `0.13.0`                                | 120 个测试、真实 21.3 连接、七粒度和 12,000 行通过；raw inspector 9/0，Superset EngineSpec 归一化为 8/1，View 10,000 行查询通过                                                                                                                                                                                                                                         |
| `V2-T04`                | PASS        | SQLGlot `28.10.0`                                      | 双 ClickHouse key 使用自有 dialect；MySQL/PostgreSQL/YQL 与 stock ClickHouse 生成结果保持不变                                                                                                                                                                                                                                                                           |
| `V2-T05`                | PASS        | seed gate unit + 真实容器                              | 12 个 parser/validate-only 测试通过；25/27/FAIL/乱序 order/重复 order/重复 name/空 name/列数错误均拒绝；真实 validate-only 前后均为同一组 9 个对象，按同次运行的 UUID/mtime 序列化结果逐字节一致                                                                                                                                                                        |
| `V2-T06`                | PASS        | active 0.15.1 + Flask/API/Browser                      | tables API `200/count=9`、8 table+1 view、无重复；SQL Lab 用 function 图标展示 `drill_wide_flat` 并成功展开 52 列；5 处 metadata、`version_requirements` 与 pyproject 均锁定 `<1.0`                                                                                                                                                                                     |
| `V2-ENV`                | PASS        | active Connect `0.15.1`                                | 起始 freeze 285 包、SHA-256 `71513b5276ff...`; 清除失效代理并设置 localhost NO_PROXY 后 schema/function API 与 SQL Lab 正常；主 venv 保持 SQLAlchemy 1.4.54                                                                                                                                                                                                             |
| `V3-T01`                | PASS        | legacy `0.2.9` shadow + native 9000                    | SQLAlchemy 1.4.54；目标矩阵通过；真实 21.3 七粒度、8 table+1 view、10,000 行 View 与 Code 36 通过；主 venv 未安装 legacy                                                                                                                                                                                                                                                |
| `V3-T02`                | PASS        | SQL Lab sync + 两个 engine key                         | 跨层单测断言 cursor bytes=`Query.executed_sql` 且 month 小写；源 metadata 同步 Query 18 成功返回 7 行，执行 SQL 仅含小写 unit                                                                                                                                                                                                                                           |
| `V3-T03`                | PASS        | RUN_ID `v3-20260824T111751Z-9016fe9b`                  | 隔离 metadata/8089/Celery prefork=1/Redis DB12～14；single 七粒度和 multi 均 202→success、progress=100、results 200；multi 的 2 个 statement 均 QueryFinish/exception=0 且哈希不同；`Query.executed_sql` 按既有单字段语义保留最后 block，cursor 主体一致且仅多 driver `FORMAT Native` suffix；8089/worker/key/temp 均精确清理，DBSIZE=0；证据 SHA-256 `2bc35a6ecb84...` |
| `V3-T04`                | PASS        | Connect `0.13.0`/`0.15.1` + legacy `0.2.9`             | 使用存在表/列的 raw 大写 datepart 均得到 Code 36；SQL/filter canary 均未进入异常；Code 47 不作为本项证据                                                                                                                                                                                                                                                                |
| `V3-T05`                | PASS        | Superset/真实 ClickHouse 21.3                          | 标准 `DATE_TRUNC` 生成小写后 connect/legacy 均成功；源表 12,000 行；同步/异步与 Query audit 字段一致                                                                                                                                                                                                                                                                    |
| `V3-T06`                | PASS        | Flask DEBUG=false、`QUERY_LOGGER=None`                 | execute event log 仅含端点白名单，SQL/模板/Token 命中 0；既有 curated 键名保持；孤立 surrogate 不抛错；Code 36 API、`Query.error_message/extra` 和 INFO app log 使用固定安全消息，无表达式/server/version/URL/canary；SQL 正文短语与非目标错误保持原映射                                                                                                                |
| `V3-ENV`                | PASS        | active Connect `0.15.1`                                | 主 venv SQLAlchemy 1.4.54、SQLGlot 28.10.0、legacy absent、`pip check` 通过；源 DB `allow_run_async=0` 且无 RUN_ID/client ID；并行人工验证产生非 V3 Query，因此只声明隔离运行未写源 metadata，不声明源库逐字节静止                                                                                                                                                      |
| `V4-T01A`               | PASS        | Python unit + 隔离 SQLite/Redis                        | Drill Detail 排序、容量和查询 action `18/18`；精确 Samples/embedded/`drill_info` `16/16`；通用 RLS 数据限制与 cache key `2/2`。真实 ClickHouse Samples count/data 与边界矩阵仍归 T01 后续子阶段，不据此关闭 T01。                                                                                                                                                       |
| `V4-T01-PRE-H`          | PASS        | Python 3.11 + 隔离 SQLite/SimpleCache + CH `21.3.20.1` | 仅子阶段 1～3：原 FR-01 unit `18/18`、Samples/embedded/`drill_info` `16/16`、通用 RLS `2/2`、fixture 26/26 及真实 A～G 通过；不关闭 T01。                                                                                                                                                                                                                               |
| `V4-T01-ENV`            | BLOCKED     | 隔离 SQLite + Redis DB9～11，sandbox                   | 首次 server search/bounded rerun 在读取 `127.0.0.1:6379` 时被环境拒绝；改用保持 Samples 语义的 SimpleCache 后通过。不是产品失败，不覆盖 PRE-H 结果。                                                                                                                                                                                                                    |
| `V4-T01-H`              | FAIL        | 真实 ClickHouse `21.3.20.1`，10k 虚拟 Dataset          | `Array(Int32)` 被反射为 generic STRING；Array-only Server 实际 `200/50 rows`，预期 422。A～G 的 12k 稳定页、Unicode 搜索与四项容量边界均通过；临时 Dataset 已按 ID 删除并确认 404，后续阶段已停止。                                                                                                                                                                     |
| `V4-FIX-02`             | PASS        | unit + API + 真实 ClickHouse 21.3                      | EngineSpec 原始类型标量能力、双 key 复杂类型否决、QueryContext 前 early-fail、Array search 400、MySQL/PostgreSQL 与 legacy Samples 隔离均通过；独立代码审查无 P0～P3。                                                                                                                                                                                                  |
| `V4-T01B`               | PASS        | Python 3.11 + 隔离 SQLite + CH `21.3.20.1`             | 自动化 158 passed、fixture 26/26、A～G 全重跑、物理/虚拟 Array-only、混合投影及双 connector 反射通过；三身份 page 1/25 均为 5,000 total/200 rows 且只含授权区域，无 Dataset 权限为 403；临时对象全部精确清理。                                                                                                                                                          |
| `FR1-ORDER-FOLLOWUP-01` | PENDING     | 独立架构增强，不阻断 `V4-T01B`                         | 评估 PK/unique 元数据、显式稳定键、EngineSpec 行身份和 keyset pagination；覆盖重复标量、复杂值不同及并发写入。不得用未证明唯一的 hash 静默冒充稳定行身份。                                                                                                                                                                                                              |
| `V4-T02A`               | PASS        | pytest + Jest/Node 22                                  | Table alert/QueryContext 后端原套件 `56/56`；schema 与 Dashboard resolver 补充节点通过；Table `buildQuery`/`TableChart` `94/94`。真实 WHERE/HAVING、五消费者关系仍归 T03/T05，不据此关闭 T02。                                                                                                                                                                          |
| `V4-FIX-01`             | PASS        | unit + 真实 Guest/ClickHouse 21.3                      | Dashboard XLSX QueryContext 用服务端 Dashboard ID 覆盖保存值且不原地修改；unit `6/6`。受限 Guest Role 仅含 Dashboard read 与 `can_csv`、无任一数据权限，Slice 保存值和 `dataMask` 均伪造为其他 Dashboard 时，授权 Dashboard 仍导出 2 个数据 Sheet；错误 Dashboard 返回 404。                                                                                            |
| `V4-FIX-01-CI`          | PASS        | 独立 SQLite metadata/examples + pytest                 | 新增真实 Guest API 集成测试；先清空 Public 的 98 个测试权限并断言为 0，捕获并修复未绑定临时 `GUEST_ROLE_NAME` 的测试假阳性；最终精确节点 `1/1`、整个 XLSX API 文件 `5/5`。测试验证 dataset-scoped RLS 仅保留 `girl`、服务端 Dashboard ID 抵抗 Slice/dataMask 伪造、错误 Dashboard 404 且非 XLSX，并完整清理 Role/Dashboard/EmbeddedDashboard。                          |
| `V4-T04A`               | PASS        | 隔离 metadata + Guest Token + CH `21.3.20.1`           | APAC dataset-scoped Guest RLS 的 2 个 Sheet 均仅含 APAC；无 RLS token 的同两 Sheet 均含 AMER/APAC/EMEA/LATAM；每次查询都确认 Guest RLS 解析计数。Native/Cross/alert 组合仍待 T04 后续，不据此关闭 T04。                                                                                                                                                                 |
| `V4-ENV`                | PASS        | 临时 SQLite baseline + Redis DB9～11                   | 定位并隔离 `Public=Gamma` 导致的公开接口假阳性；清空临时 Public 权限后精确权限节点全部通过。ClickHouse 门禁重新确认 `21.3.20.1`、9 个对象、92,000 行；不修改主 metadata 或 ClickHouse 数据。                                                                                                                                                                            |
| `V4-T02B`               | PASS        | RUN_ID `v4-20260824T142959Z-428ec56d` + CH `21.3.20.1` | RED/GREEN 同 subject OR 与 quantity 跨 subject AND 得到 30 个分组；WHERE/HAVING、6,645,643.00 revenue、5,108,189.00 profit、8,996 count 与直连 oracle 一致；引用重排/去重缓存键相同，选择变化缓存键不同；stale、level mismatch、Cell Bar 均 400；只归档 SQL 哈希。                                                                                                 |
| `V4-T03`                | PASS        | 隔离 metadata + 真实 ClickHouse                        | Gamma/APAC/EMEA 的 Chart Data、Samples、Dashboard XLSX 均 200；Samples total 为 20,000/5,000/5,000 且 RLS 区域正确。无 Dataset 的三个入口均 403；无 Dashboard 的 XLSX 403；无 `can_csv` 的单图与 Dashboard XLSX 均 403，JSON/Samples 阳性对照 200；错误无 SQL/连接详情，临时 workbook 增量 0。                                                                     |
| `V4-FIX-03`             | PASS        | unit + 真实 Guest/ClickHouse 21.3                      | Table 告警规则配置读取原先只使用 Dataset ACL `ChartFilter`，导致通过授权 Dashboard 获得 Dataset 访问的 Guest 提前 400。保留正常 base filter；Guest 或具备实际 Dashboard roles 的 RBAC grant 才可走“Dashboard 已鉴权 + 非 Native Filter + Slice 成员 + Dataset 一致”的受限 fallback；最终 QueryContext/RLS 校验不变。Schema→Factory 异步缓存回读用规范化引用重新解析规则，main/totals QueryObject cache key 逐项一致。 |
| `V4-T04B`               | PASS        | 真实 Guest Token + CH `21.3.20.1`                      | Native region + Cross channel + YELLOW alert 联合状态下，APAC Guest 为 4 个分组、无 RLS Guest/管理员均为 8 个；Guest Chart Data 与 Dashboard XLSX 哈希同为 `0f98ef8c...`，RLS 缓存键隔离且 JSON 不回传 SQL；错误资源 Chart/Dashboard 为 400/404，伪造规则 400。同 Dataset 但不属于授权 Dashboard 的 Native Filter Slice 与不存在 Slice 返回相同 400 状态和响应体，不形成对象存在性旁路。 |
| `V4-T05-PRE`            | FAIL        | 真实前端请求形状 + styled XLSX                         | 页面、total count、totals、单查询 XLSX 与 Tab XLSX 的 30 行关系和样式均一致，但 `show_totals=true` 使单图下载返回外层 ZIP `query_1.xlsx/query_2.xlsx`，不满足单 Table styled XLSX 的直接下载契约；确认是数据库无关的响应编排缺口。                                                                                                                               |
| `V4-FIX-04`             | PASS        | Python unit + Jest + 真实 CH/XLSX                      | styled 单图下载显式使用 `xlsx_primary_query_only` 响应投影；完整 QueryContext 仍执行，服务端仅在 Flag、XLSX、styled、保存经典 Table 全部准入后返回主 workbook。未传选项的未知多查询仍为 ZIP，CSV/Flag 关闭及非法组合不截断或返回 400；相关 Python `92 passed`、Jest `109 passed`，6 个既有 DuckDB fixture 节点按原注释跳过，真实 MIME/文件名回归通过。 |
| `V4-T05`                | PASS        | 真实 CH `21.3.20.1` + openpyxl                         | 页面 10 行、total count 30、过滤后 totals、完整 JSON、单表 XLSX、两个 Dashboard 数据 Sheet 与真实 oracle 共用关系哈希 `65936551...`；红/绿 revenue 各 15、quantity 绿 30、profit 蓝 15、原生 Data Bar 存在；父子 Tab 同选去重后总计 3 Sheet；单图下载是标准 XLSX 而非外层 ZIP。                                                                              |
| `V4-CLEANUP`            | PASS        | 精确 ID/UUID 清理                                      | 隔离服务停止后删除 2 个 RLS、2 个 EmbeddedDashboard、7 个 User、7 个 Role、同 Dataset 隐藏 Slice 及临时 Dashboard owner 关系；逐模型与 RUN_ID 前缀计数全部为 0。源 metadata 与 ClickHouse fixture 未修改。                                                                                                                                                                  |
| `V4-*`                  | PASS        | Python 3.11 / Node 22 / CH `21.3.20.1`                 | T01～T05 的自动化、真实数据、权限/RLS、Guest、五消费者和 XLSX 下载契约全部关闭；可进入 V5。`FR1-ORDER-FOLLOWUP-01` 是不阻断本阶段的独立架构增强。                                                                                                                                                                                                                         |
| `V5-*`                  | PENDING     | —                                                      | —                                                                                                                                                                                                                                                                                                                                                                       |
| `V6-*`                  | PENDING     | —                                                      | —                                                                                                                                                                                                                                                                                                                                                                       |
| `V7-*`                  | PENDING     | —                                                      | —                                                                                                                                                                                                                                                                                                                                                                       |
| `V8-*`                  | PENDING     | —                                                      | —                                                                                                                                                                                                                                                                                                                                                                       |
| `V9-*`                  | PENDING     | —                                                      | —                                                                                                                                                                                                                                                                                                                                                                       |
