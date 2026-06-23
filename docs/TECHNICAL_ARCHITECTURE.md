# agent-news 技术架构与可行性调研

本文档基于 `docs/RADAR_DESIGN.md` 和当前代码结构，记录信息雷达、文章桥接、微信恢复、审计观测的可编码架构与当前实现状态。目标是继续保持 agent-news 的核心形态：所有能力都是可独立调用、可独立失败、可独立复核的原子操作，而不是固定工作流脚本。

## 0. 当前实现状态

本轮 Phase A-F 已落地：

- `radar.status/review_sources/review_events/review_deep_dive`
- `radar.discover_sources/validate_source/propose_source/add_validated_source/source_health_report/disable_stale_sources`
- `radar.sync_sources` 逐源返回 `source_results`、`partial`、`failed_source_count`
- `radar.build_events` 返回 `top_events` 和下一步建议
- `radar.deep_dive_event` 缓存命中和新建结果都返回复核字段
- `article.create/get/list/update/prepare_wechat_payload`
- `wechat.inspect_tabs/focus_editor_tab/close_blank_tabs/upload_cover_file`
- `audit.review_tasks`、`workflow.status`

后续项：项目内联网搜索 provider、`source_candidates/source_checks` 表、`radar.run_small_cycle`、`article.create_from_deep_dive`、更细的源 update/enable/disable/ignore 原子。

## 1. 范围

本轮覆盖五块：

1. 信息雷达观测与选题原子：`radar.status`、`radar.review_sources`、`radar.review_events`、`radar.review_deep_dive`
2. 信息源发现与治理原子：`radar.discover_sources`、`radar.validate_source`、`radar.propose_source`、`radar.add_validated_source`
3. 文章桥接原子：`article.create`、`article.get`、`article.list`、`article.update`、`article.prepare_wechat_payload`
4. 微信恢复原子：`wechat.inspect_tabs`、`wechat.focus_editor_tab`、`wechat.close_blank_tabs`、`wechat.upload_cover_file`
5. 审计与工作流观测原子：`audit.review_tasks`、`workflow.status`

不在本轮范围：

- 不引入前端。
- 不引入固定大脚本替代原子操作。
- 不让系统自动用 LLM 写正文。正文仍由外部 Agent 写作，项目只负责保存、复核、格式化和发布通道。
- 不把到达微信二维码视为发表成功。
- 不允许 Agent 联网搜索后直接污染正式源池。新增源必须经过验证、去重和评分。

## 2. 当前架构基线

当前项目已经具备一个适合 Agent 调用的底座：

```text
Agent / AI
  |
  | CLI: python -m agent_news run <op> key=value
  | HTTP: POST /api/operations/{name}/execute
  v
FastAPI 服务 agent_news.main:app
  |
  | /api/operations/*          原子操作统一入口
  | /api/intel/*               雷达读取端点
  | /api/articles              文章 REST CRUD
  | /api/workflows             工作流状态机
  | /api/publish-tasks         操作审计
  v
OPERATION_REGISTRY
  |
  | radar.*                    信息雷达
  | wechat.*                   微信浏览器自动化
  v
Domain modules
  |
  | intel/connectors.py        采集
  | intel/normalize.py         归一化
  | intel/cluster.py           聚类
  | intel/score.py             打分
  | intel/deep_dive.py         深挖
  | browser/manager.py         持久微信浏览器
  v
SQLite
  |
  | sources / raw_items / intel_events / alerts / deep_dives
  | articles / workflows / publish_tasks / publish_records
```

关键不变式：

- FastAPI 服务是主运行时，持有 `BrowserManager` 单例。
- CLI 是薄客户端，优先 HTTP；`wechat.*` 不允许本地兜底，防止浏览器 profile 冲突。
- 所有原子操作返回 `OperationResult`，失败不抛业务异常。
- 每次 operation 执行写入 `publish_tasks` 审计。
- `publish_to_qrcode` 和 `wait_qrcode` 只能推进到 `pending_confirmation`，不能直接 `published`。

## 3. 目标架构

下一阶段目标不是重构主架构，而是在现有 Operation Registry 上补齐四类能力。

```text
agent_news/
  operations/
    radar.py                  已有，短期继续承载 radar.* 原子
    articles.py               新增，承载 article.* 原子
    audit.py                  新增，承载 audit.* 原子
    workflow.py               新增，承载 workflow.* 原子
    wechat/
      navigation.py           已有
      editor.py               已有
      publish_settings.py     已有
      save_publish.py         已有
      drafts.py               已有
      history.py              已有
      tabs.py                 新增，承载标签页恢复原子
      cover_upload.py          可选，承载本地封面上传

  intel/
    review.py                 新增，雷达状态/选题/深挖复核的纯函数
    source_probe.py           新增，可选，源健康探测辅助
    source_discovery.py       新增，候选源发现、验证、去重、评分

  content/
    wechat_payload.py         新增，article -> 微信填写参数转换

  db/
    intel_repository.py       现有，必要时补 count/update/ignore 方法
    repository.py             现有，必要时补 article/material 查询辅助

  routes/
    operations.py             现有，继续统一执行和审计
    articles.py               现有 REST，可保留
```

### 为什么短期不把 `operations/radar.py` 改成包

当前 `agent_news.operations.__init__` 通过 `from . import radar` 注册雷达操作。将 `radar.py` 迁移成 `operations/radar/` 包会影响 import 路径和测试。短期更稳妥的做法是：

- 原子注册仍放在 `operations/radar.py`
- 复杂计算逻辑下沉到 `intel/review.py`、`intel/source_probe.py`
- 等 P0/P1 全部稳定后，再考虑拆包

这样能减少结构性改动，避免把“补原子”变成“重构工程”。

## 4. 文件夹规划

### 4.1 当前保留

| 路径 | 角色 | 是否改动 |
|---|---|---|
| `agent_news/operations/registry.py` | 原子操作注册和执行 | 不改 |
| `agent_news/routes/operations.py` | HTTP 执行入口、审计、workflow 推进 | 小改，仅当新增 operation state map |
| `agent_news/models/operation.py` | `OperationResult` 契约 | 不改 |
| `agent_news/db/intel_repository.py` | 雷达持久化 | 补查询/更新方法 |
| `agent_news/db/repository.py` | 文章、workflow、审计持久化 | 补 article 查询辅助即可 |
| `agent_news/browser/manager.py` | 持久浏览器、标签页管理底层 | 可补安全公开方法 |
| `agent_news/operations/wechat/*` | 微信已有原子 | 仅新增恢复模块 |

### 4.2 新增文件

| 新文件 | 目的 |
|---|---|
| `agent_news/intel/review.py` | 雷达只读复核纯函数：status、events、deep dive readiness |
| `agent_news/intel/source_probe.py` | 源探测封装，保留 per-source 失败原因 |
| `agent_news/intel/source_discovery.py` | 信息源候选发现、验证、去重、质量评分 |
| `agent_news/operations/articles.py` | `article.*` 原子操作注册 |
| `agent_news/content/wechat_payload.py` | 文章转换成微信填写载荷 |
| `agent_news/operations/audit.py` | `audit.review_tasks` |
| `agent_news/operations/workflow.py` | `workflow.status` |
| `agent_news/operations/wechat/tabs.py` | 标签页诊断、聚焦、关闭空白页 |
| `agent_news/operations/wechat/cover_upload.py` | 本地封面上传，可后置 |
| `tests/test_radar_review_operations.py` | 雷达观测原子测试 |
| `tests/test_source_discovery.py` | 信息源发现、验证、去重、添加门禁测试 |
| `tests/test_article_operations.py` | 文章桥接原子测试 |
| `tests/test_wechat_tabs.py` | 标签页恢复原子测试 |

### 4.3 注册入口

新增操作必须在 `agent_news/operations/__init__.py` 或 `agent_news/operations/wechat/__init__.py` 中 import，否则不会进入注册表。

建议：

```python
# agent_news/operations/__init__.py
from . import radar
from . import articles
from . import audit
from . import workflow
from . import wechat
```

```python
# agent_news/operations/wechat/__init__.py
from . import tabs
from . import cover_upload
```

## 5. 技术设计

### 5.1 雷达观测原子

#### `radar.status`

实现位置：

- operation：`agent_news/operations/radar.py`
- 纯函数：`agent_news/intel/review.py`

数据来源：

- `repo.list_sources()`
- `repo.list_raw_items(limit=1)`
- `repo.list_events(limit=5, ignored=False)`
- `repo.list_alerts(limit=5)`
- `repo.list_deep_dives(limit=5)`

建议补充 repository 方法：

```python
IntelRepository.count_raw_items()
IntelRepository.count_events(ignored: bool | None = False)
IntelRepository.count_alerts()
IntelRepository.count_deep_dives()
```

也可以短期用现有 list 方法拿 total，不急着加新 API。

返回策略：

- 无 source：`ok`，`suggested_next_operation="radar.seed_defaults"`
- 有 source 无 raw/event：`ok`，建议 `radar.sync_sources`
- 有 event：`ok`，建议 `radar.review_events`
- 有 deep dive：`ok`，建议 `radar.review_deep_dive`

#### `radar.review_events`

实现位置：

- operation：`agent_news/operations/radar.py`
- 纯函数：`agent_news/intel/review.py`

推荐理由只允许来自结构化字段：

- `source_count`
- `platform_count`
- `composite_score`
- `alert_state`
- `watchlisted`
- `published_at`
- `deep_dive_status`
- `worth_to_brief`
- 临时 `watchlist` 与 `title/tags/entity_names/anchor_tokens` 的命中

风险提示只允许来自真实状态：

- `deep_dive_status` 为空：尚未深挖
- `source_count <= 1`：来源偏少
- `representative_link` 为空：缺少代表链接
- `composite_score < min_score`：分数不足
- `ignored=true`：已被忽略

不允许：

- 不允许根据标题脑补事实。
- 不允许生成正文。
- 不允许用示例事件替代空结果。

#### `radar.review_deep_dive`

实现位置：

- operation：`agent_news/operations/radar.py`
- 纯函数：`agent_news/intel/review.py`

输入规则：

- `deep_dive_id` 优先。
- 没有 `deep_dive_id` 时用 `event_id` 查最近 deep dive。
- 两者都没有时返回 `failed`。
- 查不到时返回 `skipped`，建议 `radar.deep_dive_event`。

`writing_readiness` 推导：

```text
ready:
  success_count >= 2
  fact_count >= 5
  failed_count <= success_count

partial:
  success_count >= 1
  fact_count >= 2

weak:
  其他情况
```

风险提示：

- `success_count == 0`：没有成功来源
- `fact_count < 3`：事实不足
- `quote_count == 0`：缺少可引用原文
- `failed_count > 0`：存在来源抓取失败
- `status != ready`：素材包状态不是 ready

### 5.2 源健康诊断

#### `radar.review_sources`

实现位置：

- operation：`agent_news/operations/radar.py`
- 探测辅助：`agent_news/intel/source_probe.py`

`probe=false`：

- 只读 DB。
- 不访问网络。
- 返回源配置、enabled 状态、kind、tags、priority、url 是否为空。

`probe=true`：

- 对每个源单独调用 fetcher。
- 单源失败只进入该源的 `error` 字段，整体仍可 `ok`。
- 返回 `ok_count`、`failed_count`、`disabled_count`。

当前 `fetch_source()` 会吞掉异常并返回 `[]`，无法区分“真的 0 条”和“异常”。因此建议新增：

```python
ProbeResult(
  source_key: str,
  status: "ok" | "empty" | "failed" | "disabled",
  item_count: int,
  error: str | None,
)
```

不要直接改 `fetch_source()` 的旧行为，避免影响 `sync_sources`。先在 `source_probe.py` 中包一层。

### 5.3 信息源发现与治理

当前已有 `radar.add_source`，但它只是底层写库动作。Agent 联网搜索新源后，不能直接调用它写入正式源池，否则会引入搜索结果页、重复源、登录页、低质量采集站等脏数据。

新增治理链路：

```text
radar.discover_sources -> radar.validate_source -> radar.propose_source -> radar.add_validated_source -> radar.sync_one_source
```

实现位置：

- operation：`agent_news/operations/radar.py`
- 纯函数：`agent_news/intel/source_discovery.py`
- 源探测：复用 `agent_news/intel/source_probe.py`

#### `radar.discover_sources`

两种输入模式：

1. 项目内发现：传 `query/topic/kind/language/limit`，由项目内搜索 provider 返回候选。
2. 外部 Agent 发现：传 `candidates=[...]`，项目只做规范化，不自己联网搜索。

MVP 建议优先支持第二种模式，因为 Codex/外部 Agent 已经能联网搜索，项目内不必立刻绑定搜索供应商。

候选模型建议：

```python
SourceCandidate(
    url: str,
    name: str = "",
    kind: str = "rss",
    topic: str = "",
    language: str = "",
    discovered_by: str = "agent",
    evidence: list[str] = [],
)
```

边界：

- 只返回候选，不写 `sources`。
- 候选必须保留 evidence，说明为什么它像一个源。
- 搜索结果页、社交主页、登录页不能进入候选，除非 evidence 显示其有可采集 RSS/API。

#### `radar.validate_source`

验证候选源是否合格。

检查项：

- HTTP 可访问，不能是 403/404/登录页。
- RSS/Atom 可解析；HTML 源至少能提取标题和链接。
- 近期有更新，默认 30-90 天内。
- 至少有 3 条有效 item。
- item 必须有标题和链接。
- 与已有源不重复。
- 质量类型不属于垃圾聚合站、镜像站、无时间源。

返回建议：

```json
{
  "valid": true,
  "score": 86,
  "decision": "auto_add",
  "reason": "RSS 可解析，最近 7 天有 5 条更新，未与现有源重复",
  "sample_items": [],
  "dedupe": {"duplicate": false, "matched_source_key": null},
  "suggested_source": {
    "key": "example-blog",
    "name": "Example Blog",
    "kind": "rss",
    "url": "https://example.com/feed.xml",
    "tags": ["ai"],
    "priority": 70
  }
}
```

评分建议：

| 指标 | 分值 |
|---|---:|
| 可访问、可解析 | 30 |
| 最近有更新 | 20 |
| 有 3-5 条有效 item | 15 |
| 非重复源 | 15 |
| 来源质量高 | 15 |
| 元数据完整 | 5 |

决策：

- `score >= 80`：`auto_add`
- `60 <= score < 80`：`needs_confirmation`
- `score < 60`：`reject`

#### `radar.propose_source`

把验证结果转换成添加建议。短期可以不落库，直接返回 proposal；后续若需要跨会话审批，再加 `source_candidates` 表。

proposal 必须包含：

- 建议 `source_key`
- 建议 `name`
- `kind/url/tags/priority/weight`
- `score/decision/reason`
- `sample_items`
- `dedupe_info`
- `risks`

#### `radar.add_validated_source`

正式写入动作。

输入：

- 当前传 `validated_source` 完整对象；若后续增加候选源持久化表，再扩展 `candidate_id`

门禁：

- 必须有 `valid=true` 或 `decision in ("auto_add", "confirmed")`。
- 写入前再次做 key/url/domain 去重。
- `needs_confirmation` 不能自动启用，除非参数显式 `confirmed=true`。
- 写入后返回 `source_key` 和建议下一步 `radar.sync_one_source`。

内部可以复用现有 `radar.add_source` 的逻辑，但不要直接调用未验证输入。

#### `radar.source_health_report`

源池治理报告，不修改数据。

建议输出：

- 高贡献源：最近 raw item 数高。
- 低贡献源：长期 0 item。
- 失败源：最近 probe/sync 失败。
- 重复源：domain 或标题高度重叠。
- 建议动作：保留、降权、停用、人工复核。

#### `radar.disable_stale_sources`

可选写动作，谨慎后置。

约束：

- 只停用，不删除。
- 必须返回停用证据。
- 默认需要 `dry_run=true`，只有显式 `dry_run=false` 才执行。

### 5.4 现有雷达动作增强

#### `radar.sync_sources`

当前问题：只返回总数。

建议改造：

- 内部从 `collect_sources(sources)` 改成逐源调用 `fetch_source` 或 `probe_source(fetch=True)`。
- 返回 `source_results`。
- 有源失败但至少一个源成功时，状态仍为 `ok`，并设置 `partial=true`。
- 全部失败或全部 0 条时，状态可以是 `ok`，但必须说明 `raw_count=0` 和原因分布。

兼容性：

- 保留原字段 `raw_count`、`source_count`、`synced_at`。
- 新增字段不破坏现有测试。

#### `radar.build_events`

建议新增：

- `top_events`
- `suggested_next_operation="radar.review_events"`

不建议立即改变默认 `clear_raw=True`，因为这可能影响已有行为。Agent 文档推荐传 `clear_raw=false` 即可。

#### `radar.deep_dive_event`

已增强返回：

- `source_results`
- `writing_readiness`
- `suggested_next_operation="article.create"`，因为深挖后必须先由 Agent 写正文并保存成 article

缓存命中时也应返回这些字段，不能只返回 `deep_dive_id`。

### 5.5 文章桥接原子

当前文章已有 REST CRUD，但没有进入 Operation Registry。Agent 调用时需要统一原子入口，因此补 `article.*`。

实现位置：

- `agent_news/operations/articles.py`
- `agent_news/content/wechat_payload.py`

#### `article.create`

直接调用 `get_repository().create_article()`。

约束：

- `title` 必填。
- `body_markdown` 必填或允许空草稿，建议 MVP 必填。
- 不自动创建 workflow。
- 不触发微信。

#### `article.prepare_wechat_payload`

输入：`article_id`、可选 `cover_prompt`

输出：

```json
{
  "article_id": "art-...",
  "title": "...",
  "author": "...",
  "digest": "...",
  "body_markdown": "...",
  "cover_prompt": "一个和主题相关的物品类画面",
  "missing_required": [],
  "ready_for_wechat_fill": true,
  "suggested_steps": [
    {"op": "wechat.fill_editor_required", "params": {...}},
    {"op": "wechat.fill_digest", "params": {...}}
  ]
}
```

若标题、作者、正文任一缺失，operation 返回 `failed`，state 中包含 `missing_required`、`ready_for_wechat_fill=false`、`suggested_next_operation="article.update"`，且不返回可直接执行的微信填写步骤。

封面提示词规则：

- 微信 AI 封面偏物品类图片，不适合文字海报。
- 如果传入 `cover_prompt`，直接用用户提供的。
- 如果没传，基于标题生成“物品/场景类”提示，例如“一个 iPhone 放在办公桌上的产品摄影图”，不要生成“写着标题的封面图”。

不建议本轮做：

- 不在项目内调用 LLM 自动写文章。
- 不自动决定原创、合集、创作来源。

### 5.6 微信标签页恢复原子

当前 `BrowserManager.observe_page()` 已返回：

- `current_url`
- `is_editor_page`
- `page_count`
- `page_urls`

说明底层已经具备观测基础。需要补的是可调用 operation。

#### `wechat.inspect_tabs`

实现位置：

- `agent_news/operations/wechat/tabs.py`
- 必要时给 `BrowserManager` 增加更详细的 `observe_tabs()`

返回：

```json
{
  "page_count": 2,
  "tabs": [
    {"index": 0, "url": "about:blank", "is_blank": true, "is_editor": false},
    {"index": 1, "url": "https://mp.weixin.qq.com/...action=edit", "is_blank": false, "is_editor": true}
  ],
  "focused_index": 1
}
```

#### `wechat.focus_editor_tab`

逻辑：

- 在 live pages 中找到 `_is_editor_like_page(page)`。
- 设置 `BROWSER_MANAGER._page = editor_page`。
- `bring_to_front()`。
- 不新开页面。

#### `wechat.close_blank_tabs`

逻辑：

- 遍历 live pages。
- 只关闭 `about:blank` 或空 URL。
- 如果当前 `_page` 是编辑页，必须保留。
- 如果只有一个标签页，即使是 blank，也不要关闭到无页面状态。

需要注意：

- Playwright 对象必须在 worker 线程操作，所以实现必须通过 `BROWSER_MANAGER.with_session()` 或新增 manager 方法内部 `_run_in_worker()`。

### 5.7 本地封面上传

`wechat.generate_ai_cover` 已可用。本地上传是并列能力，不替代 AI 生成。

实现建议：

- 放在 `agent_news/operations/wechat/cover_upload.py`
- 复用旧项目封面选择器和当前 `cover.py` 中打开封面菜单的逻辑
- 参数 `file_path`
- 校验文件存在、后缀、大小
- 上传后读取 `_read_cover_preview_state(page)` 确认 `hasCover=true`

可后置，因为当前 AI 封面已能跑通。

### 5.8 审计与工作流观测

#### `audit.review_tasks`

实现位置：`agent_news/operations/audit.py`

数据来源：`get_repository().list_publish_tasks(limit)`

用途：

- Agent 失败后快速读最近失败步骤。
- 不需要直接查 REST `/api/publish-tasks`。

#### `workflow.status`

实现位置：`agent_news/operations/workflow.py`

输入：

- `workflow_session_id`

输出：

- 当前状态
- 文章 ID
- 合法下一步
- last_error
- settings_applied

不推进状态。

## 6. 可行性分析

### 6.1 技术可行性

整体可行，原因：

- 已实现的 Phase A-F 能力复用现有 SQLite 表和 repository，没有新增 schema。
- 雷达观测是纯读或轻量聚合，风险低。
- 信息源治理可以先做运行时验证和 proposal，不需要立刻新增候选源表。
- Agent 联网搜索可以先作为外部输入，项目只负责规范化、验证、去重、评分和入库门禁。
- 文章桥接已有 `Repository.create_article/get/list/update`，只缺 operation 包装。
- 微信标签页观测已有 `observe_page()` 基础，恢复原子只需暴露已有能力。
- Operation Registry、审计、CLI auto-start 已稳定，新增原子可以自然接入。

### 6.2 主要风险

| 风险 | 影响 | 缓解 |
|---|---|---|
| `fetch_source()` 当前吞异常 | 源健康无法区分空结果和失败 | 新增 `source_probe.py`，不直接改旧函数 |
| Agent 搜索结果质量不可控 | 搜索页、社交主页、垃圾聚合站可能混入源池 | `discover_sources` 只产候选；`validate_source` 和 `add_validated_source` 做硬门禁 |
| 自动添加源污染正式采集 | 后续新闻池噪声变大 | `score<80` 不自动添加；`needs_confirmation` 必须显式确认 |
| 候选源重复 | 同一站点多个 feed 或镜像重复采集 | 写入前做 URL/domain/key/title overlap 去重 |
| 项目内搜索 provider 不稳定 | `discover_sources` 无法统一联网搜索 | MVP 允许外部 Agent 搜索后传 `candidates`，项目先不绑定 provider |
| `radar.py` 越来越大 | 可维护性下降 | 先下沉纯函数到 `intel/review.py`，稳定后再拆包 |
| 微信标签页操作误关编辑页 | 可能丢失当前编辑上下文 | `close_blank_tabs` 只关 blank；测试覆盖保留 editor |
| 文章桥接被误解为自动写作 | Agent 可能跳过真实素材判断 | 文档和返回字段明确“不调用 LLM，不生成正文” |
| workflow 自动推进映射扩张过度 | 状态机变成隐式流程 | 只给微信发布状态映射；雷达和文章默认不自动推进发布 workflow |
| 文档漂移 | 其他 AI 误用不存在或参数不一致的操作 | 正式清单以 `GET /api/operations` 和 `docs/ATOMIC_OPERATIONS.md` 为准；后续项必须明确标注 |

### 6.3 不需要做的重工程

- 不需要引入 LangChain/LangGraph 作为项目内依赖。当前项目的“Agent 框架”是 Operation Registry + HTTP/CLI 原子接口。外部 Agent 自己负责编排。
- 不需要前端。
- 不需要任务队列。当前雷达和微信链路都是用户触发、单步可复核；后续真要定时再加 scheduler。
- 不需要新增 `source_checks` 表。先运行时返回源健康，等需要历史趋势再加表。
- 不需要立即新增 `source_candidates` 表。先让 `validate_source` / `propose_source` 返回运行时 proposal；等需要跨会话审批、批量候选管理或多人协作时再加。
- 不需要项目内立即接入搜索 API。先支持外部 Agent 搜索结果作为候选输入，避免引入额外密钥和供应商耦合。
- 不需要把 REST API 删除。REST 可以继续作为 CRUD 和读取面，operation 是 Agent 执行动作面。

## 7. 实施状态与验收口径

### Phase A：雷达 P0 只读原子（已实现）

文件：

- `agent_news/intel/review.py`
- `agent_news/operations/radar.py`
- `tests/test_radar_review_operations.py`

任务：

1. 实现 `radar.status`
2. 实现 `radar.review_events`
3. 实现 `radar.review_deep_dive`
4. 增强 `deep_dive_event` 返回 `source_results` 和 `writing_readiness`

验收：

- 空库返回真实空状态和建议。
- 有事件时按分数返回推荐。
- 没有 deep dive 时不伪造素材。
- 不触发网络。

### Phase B：源健康与 sync 增强（已实现）

文件：

- `agent_news/intel/source_probe.py`
- `agent_news/operations/radar.py`
- `tests/test_radar_source_probe.py`

任务：

1. 实现 `radar.review_sources probe=false`
2. 实现 `radar.review_sources probe=true`
3. 改造 `radar.sync_sources` 返回 `source_results`

验收：

- `probe=false` 测试中 monkeypatch fetcher 不应被调用。
- 单源失败不影响其他源。
- `sync_sources` 暴露 failed source。

### Phase C：信息源发现与治理（已实现，项目内搜索 provider 后置）

文件：

- `agent_news/intel/source_discovery.py`
- `agent_news/intel/source_probe.py`
- `agent_news/operations/radar.py`
- `tests/test_source_discovery.py`

任务：

1. 实现 `radar.validate_source`
2. 实现 `radar.propose_source`
3. 实现 `radar.add_validated_source`
4. 实现 `radar.source_health_report`
5. `radar.discover_sources` 先支持 `candidates` 输入；项目内联网搜索 provider 后置

验收：

- 有效 RSS 返回 `valid=true`、`sample_items`、`score`、`suggested_source`。
- 404、登录页、搜索结果页返回 `decision=reject`。
- 重复 URL/domain/key 被拒绝。
- 未验证候选不能进入 `sources`。
- 添加成功后建议下一步为 `radar.sync_one_source`。

### Phase D：文章桥接（已实现）

文件：

- `agent_news/operations/articles.py`
- `agent_news/content/wechat_payload.py`
- `agent_news/operations/__init__.py`
- `tests/test_article_operations.py`

任务：

1. 实现 `article.create/get/list/update`
2. 实现 `article.prepare_wechat_payload`
3. 注册 `article.*`

验收：

- 通过 operation 创建文章。
- prepare payload 可直接作为 `wechat.fill_editor_required` 参数来源。
- 不打开浏览器。

### Phase E：微信恢复（已实现）

文件：

- `agent_news/operations/wechat/tabs.py`
- `agent_news/browser/manager.py`
- `agent_news/operations/wechat/__init__.py`
- `tests/test_wechat_tabs.py`

任务：

1. 实现 `wechat.inspect_tabs`
2. 实现 `wechat.focus_editor_tab`
3. 实现 `wechat.close_blank_tabs`

验收：

- 多标签时能识别 editor。
- `about:blank` 可关闭。
- 不关闭 `action=edit` 页。

### Phase F：审计与 workflow 观测（已实现）

文件：

- `agent_news/operations/audit.py`
- `agent_news/operations/workflow.py`
- `tests/test_observability_operations.py`

任务：

1. 实现 `audit.review_tasks`
2. 实现 `workflow.status`

验收：

- 能查最近失败 operation。
- 能查 workflow 当前状态和合法下一步。
- 不修改状态。

## 8. 测试策略

### 单元测试

- repository 使用测试数据库。
- 纯函数不访问网络。
- 源探测用 monkeypatch fetcher。
- 信息源验证用 monkeypatch HTTP/fetcher，覆盖有效 RSS、空 feed、404、登录页、搜索结果页、重复源。
- `add_validated_source` 测试必须证明未验证输入无法写入正式 `sources`。
- 微信 tabs 用 fake context/page，不启动真实浏览器。

### 集成测试

- `seed_defaults -> sync_sources(stub) -> build_events -> review_events`
- `deep_dive_event(stub fetch) -> review_deep_dive`
- `discover_sources(candidates=...) -> validate_source -> propose_source -> add_validated_source -> sync_one_source(stub)`
- `article.prepare_wechat_payload -> wechat.fill_editor_required` 参数形状校验

### 真实验证

真实微信只做最后一层验证：

1. `wechat.inspect_tabs`
2. `wechat.focus_editor_tab`
3. `wechat.close_blank_tabs`
4. 确认仍在 `action=edit`

不在单元测试里启动真实微信浏览器。

## 9. 验收口径

完成后，Agent 应能稳定回答和执行：

- “现在信息雷达什么状态？”
- “哪些源失败了？”
- “这个新信息源能不能添加？为什么？”
- “帮我把这个候选 RSS 验证后加入源池。”
- “哪些已有源长期无效，可以停用？”
- “今天有哪些值得写的新闻，为什么？”
- “这条深挖素材够不够写？”
- “把这篇文章准备成微信填写参数。”
- “当前微信浏览器有几个标签页，帮我聚焦编辑页。”
- “最近哪一步失败了？”

这些回答必须来自真实 DB、真实操作审计或真实浏览器状态，不允许示例、fallback、猜测替代。
