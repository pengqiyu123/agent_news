# 信息雷达设计文档

本文档定义 `agent-news` 信息雷达的原子能力设计。目标不是写一个固定大脚本，而是把“采集、诊断、聚类、选题、深挖、写作准备”拆成可观测、可重试、可组合的 Agent 原子能力。

## 当前落地状态

截至本轮实现，Phase A-F 已落地并进入注册表：

- 雷达观测：`radar.status`、`radar.review_sources`、`radar.review_events`、`radar.review_deep_dive`
- 源治理：`radar.discover_sources`、`radar.validate_source`、`radar.propose_source`、`radar.add_validated_source`、`radar.source_health_report`、`radar.disable_stale_sources`
- 采集透明化：`radar.sync_sources` 返回 `source_results`、`partial`、`failed_source_count`；`radar.build_events` 返回 `top_events`
- 文章桥接：`article.create/get/list/update/prepare_wechat_payload`
- 微信恢复：`wechat.inspect_tabs`、`wechat.focus_editor_tab`、`wechat.close_blank_tabs`、`wechat.upload_cover_file`
- 观测闭环：`audit.review_tasks`、`workflow.status`

仍是后续项，不要在当前任务里调用：`radar.run_small_cycle`、`radar.review_raw_items`、`radar.update_source`、`radar.enable_source/disable_source`、`radar.ignore_event/unignore_event`、`article.create_from_deep_dive`、项目内联网搜索 provider、`source_candidates/source_checks` 持久化表。

试生产基线：

- 默认源池：95 个源，全部内置在本项目中。
- 生产库干净状态：`sources=95`，`raw_items/intel_events/intel_alerts/deep_dives/articles/workflows/publish_tasks` 可为 0。
- 推荐先用 `radar.sync_one_source` 跑少量源验证，再扩大到 `radar.sync_sources`。

## 背景

微信公众号发布链路已经基本形成原子操作体系：导航、编辑、设置、保存、发布到二维码、草稿箱复核、发表记录复核、指标分析都可以单独调用。信息雷达是微信发布链路的上游，负责发现值得写的事件，并提供可靠素材。

当前已有核心原子操作：

| 操作 | 作用 |
|---|---|
| `radar.status` | 只读查看源、raw、events、alerts、deep dives 数量和建议下一步 |
| `radar.review_sources` | 源配置复核和可选健康探测 |
| `radar.seed_defaults` | 初始化默认信息源 |
| `radar.add_source` | 添加信息源 |
| `radar.discover_sources` | 规范化外部 Agent 找到的候选源 |
| `radar.validate_source` | 验证候选源并评分 |
| `radar.propose_source` | 输出候选源添加建议 |
| `radar.add_validated_source` | 通过门禁后写入正式源 |
| `radar.remove_source` | 删除信息源 |
| `radar.sync_sources` | 同步全部或单个源 |
| `radar.sync_one_source` | 同步单个源 |
| `radar.build_events` | 聚类、打分、生成事件与 alerts |
| `radar.review_events` | 只读复核事件、推荐理由、风险和 deep dive 参数 |
| `radar.deep_dive_event` | 深挖单个事件，生成事实、引文、时间线、写作指南 |
| `radar.review_deep_dive` | 只读复核深挖素材、写作准备度和文章写作指南 |
| `radar.source_health_report` | 源池健康报告 |
| `radar.disable_stale_sources` | 默认 dry-run 的低贡献源停用动作 |

这些能力已经覆盖 Agent 稳定运营的小闭环：先看状态，再采集/建事件，再复核选题和深挖素材，最后交给外部 Agent 写文章并通过 `article.*` 连接微信链路。

## 对照缺口清单

对照当前注册表，微信侧已经覆盖导航、编辑、发布前设置、保存、发布到二维码、草稿箱复核、发表记录复核和指标分析；真正缺口集中在“信息雷达 -> 写作素材 -> 成稿 -> 微信填写”的中间层。新增能力必须继续保持原子化，不允许把它们合成固定流水线。

### P0：雷达观测与选题（已实现）

| 原子 | 类型 | 作用 | 边界 |
|---|---|---|---|
| `radar.status` | 只读 | 查看源、raw、events、alerts、deep dives 数量和最近时间 | 不触发网络，不生成文章 |
| `radar.review_sources` | 只读/可探测 | 复核源配置和健康状态，暴露具体失败源 | `probe=false` 不联网；`probe=true` 单源失败不影响其他源 |
| `radar.review_events` | 只读 | 返回 Top events、推荐理由、风险和下一步建议 | 不生成文章，不编造推荐理由 |
| `radar.review_deep_dive` | 只读 | 复核已深挖素材的事实、引文、时间线、来源结果、写作准备度和 `article_writing_guide` | 不重新抓取全文；重新抓取继续用 `radar.deep_dive_event force=true` |

### P1：雷达管理增强（部分已实现）

| 原子 | 类型 | 作用 | 边界 |
|---|---|---|---|
| `radar.discover_sources` | 只读/联网 | 根据领域、关键词、语言、类型发现候选信息源 | 只返回候选，不写入正式源库 |
| `radar.validate_source` | 只读/联网 | 验证单个候选源是否可访问、可解析、近期有内容 | 只测试，不写库 |
| `radar.propose_source` | 只读/可落候选池 | 对候选源做去重、质量评分和添加建议 | 不启用正式采集 |
| `radar.add_validated_source` | 写 | 只添加通过验证的候选源 | 内部复核验证状态，再调用底层 `radar.add_source` |
| `radar.source_health_report` | 只读 | 汇总源贡献、低贡献源、疑似重复源 | 不自动停用 |
| `radar.disable_stale_sources` | 写/可选 | 停用长期失效源；默认 dry-run | 不删除源，需返回停用原因 |
| `radar.update_source` | 写 | 更新源名称、URL、标签、优先级、权重、配置 | 不做健康探测 |
| `radar.enable_source` / `radar.disable_source` | 写 | 临时启用/停用源 | 不删除源历史数据 |
| `radar.review_raw_items` | 只读 | 查看最近 raw items，排查“采到了但没聚类” | 不触发聚类 |
| `radar.ignore_event` / `radar.unignore_event` | 写 | 标记噪声事件或恢复事件 | 不删除事件 |
| `radar.mark_watchlisted` | 写/可选 | 人工标记事件为重点关注 | 不改事件分数公式，最多影响后续推荐展示 |

### P1：文章桥接原子（已实现）

文章写作仍由外部 Agent 完成，但项目需要给 Agent 一个稳定的文章存取和微信载荷准备面，避免中间靠临时参数拼接。

| 原子 | 类型 | 作用 | 边界 |
|---|---|---|---|
| `article.create` | 写 | 保存 Agent 已写好的文章 | 不调用 LLM，不自动发布 |
| `article.get` / `article.list` | 只读 | 读取文章详情和列表 | 不修改状态 |
| `article.update` | 写 | 修改标题、摘要、作者、正文、素材关联 | 不触发微信 |
| `article.create_from_deep_dive` | 写/可选 | 从 deep dive 创建文章草稿壳或素材关联 | 不自动生成正文，除非后续明确引入 LLM 写作器 |
| `article.prepare_wechat_payload` | 只读 | 将 article 转成微信填写参数：title、author、digest、body_markdown、cover_prompt | 不打开浏览器，不写微信；缺标题/作者/正文时返回 failed |

### P2：微信韧性补充（已实现）

微信主链已经接近完整，只建议补少量恢复/诊断原子，不重写发布流程。

| 原子 | 类型 | 作用 | 边界 |
|---|---|---|---|
| `wechat.inspect_tabs` | 只读 | 返回当前浏览器标签页 URL、标题、是否编辑页 | 不关闭、不切换 |
| `wechat.focus_editor_tab` | 写/导航 | 在多个标签页中聚焦 `action=edit` 编辑页 | 不新开页面 |
| `wechat.close_blank_tabs` | 写 | 关闭重复的 `about:blank` 标签页 | 不关闭编辑页和公众号有效页面 |
| `wechat.upload_cover_file` | 写 | 上传本地封面图片 | 与 `generate_ai_cover` 并列，适合已有封面文件 |

### P2：审计与工作流观测（已实现）

| 原子 | 类型 | 作用 | 边界 |
|---|---|---|---|
| `audit.review_tasks` | 只读 | 查看最近操作审计、失败步骤和错误信息 | 不重试操作 |
| `workflow.status` | 只读 | 查看当前工作流状态和合法下一步 | 不推进状态 |

## 设计原则

1. **不做固定流水线**
   信息雷达不应变成 `run_all.py` 这种黑盒脚本。Agent 应按任务目标自由组合：只看状态、只同步某个源、只重建事件、只深挖一个事件。

2. **每步可观测**
   每个动作必须返回可判断的状态：成功、跳过、失败、失败原因、数量、时间、建议下一步。

3. **失败不静默**
   单个源失败不能让整个采集失败，但必须暴露到结果里。Agent 不能把“部分成功”误解成“全部完整”。

4. **真实数据优先**
   不使用示例、fallback、假事件冒充真实结果。没有采到数据就返回空和原因。

5. **先诊断，再生成**
   文章写作应建立在 `review_events` 和 `deep_dive_event` 的真实结果上，而不是直接根据 raw feed 标题编稿。

6. **源治理优先于添加**
   `radar.add_source` 是底层写库动作，不应作为 Agent 联网发现源后的直接入口。Agent 新增信息源必须走“发现候选 -> 验证 -> 去重 -> 评分 -> 添加/拒绝/待确认”的治理链路。

## 推荐总流程

### 常规新闻发现

```text
radar.status
radar.review_sources
radar.sync_one_source source_key=hn-frontpage  # 试生产首轮建议
radar.sync_sources                             # 多源采集确认稳定后再用
radar.build_events clear_raw=false watchlist="ai,openai,anthropic,芯片"
radar.review_events
radar.deep_dive_event event_id=...
```

### 单源排障

```text
radar.review_sources
radar.sync_one_source source_key=...
radar.status
```

### 添加新信息源

```text
radar.discover_sources query="AI research official blog rss" topic="ai" kind="rss"
radar.validate_source url="https://..." kind="rss"
radar.propose_source validated_source={validate_source 返回的 state}
radar.add_validated_source validated_source={validate_source/propose_source 返回的 state}
radar.sync_one_source source_key=...
```

如果 Agent 已经通过外部联网搜索拿到候选 URL，可以跳过 `radar.discover_sources`，直接调用 `radar.validate_source`。无论候选来自哪里，都不能跳过验证直接 `radar.add_source`。

### 只看选题池

```text
radar.status
radar.review_events limit=10 min_score=50
```

### 深挖并准备写作

```text
radar.deep_dive_event event_id=... max_sources=6 force=false
radar.review_deep_dive event_id=...
```

## 新增原子动作

### 1. `radar.status`

只读状态观测，不触发网络请求。

用途：

- Agent 开始工作前判断雷达是否已有数据。
- 用户问“现在信息雷达什么状态”时直接返回。
- 避免盲目重复采集或重复聚类。

参数：

| 参数 | 默认 | 说明 |
|---|---:|---|
| `include_recent` | `true` | 是否返回最近事件/alerts 摘要 |

返回字段建议：

```json
{
  "source_count": 10,
  "enabled_source_count": 10,
  "raw_item_count": 608,
  "event_count": 34,
  "alert_count": 5,
  "deep_dive_count": 3,
  "latest_raw_collected_at": "2026-06-23T...",
  "latest_event_built_at": "2026-06-23T...",
  "recent_events": [],
  "recent_alerts": [],
  "suggested_next_operation": "radar.review_events"
}
```

成功标准：

- 无网络依赖。
- 数据为空也返回 `ok`，并给出下一步建议，例如 `radar.seed_defaults` 或 `radar.sync_sources`。

### 2. `radar.review_sources`

只读/轻量诊断动作，用于检查源配置和健康度。

用途：

- 采集前确认哪些源可用。
- 采集失败后定位具体源。
- 让 Agent 避免把源失败当成新闻不存在。

参数：

| 参数 | 默认 | 说明 |
|---|---:|---|
| `probe` | `false` | 是否实际请求每个源做健康探测 |
| `limit_per_source` | `3` | 探测时最多读取几条 |
| `source_key` | `null` | 只检查单个源 |

返回字段建议：

```json
{
  "sources": [
    {
      "key": "openai-blog",
      "name": "OpenAI Blog",
      "enabled": true,
      "kind": "rss",
      "url": "https://...",
      "tags": ["ai", "openai"],
      "priority": 90,
      "probe_status": "ok",
      "probe_count": 3,
      "error": null
    }
  ],
  "ok_count": 8,
  "failed_count": 2,
  "disabled_count": 0
}
```

成功标准：

- `probe=false` 时只读 DB，不访问网络。
- `probe=true` 时单源失败不影响其他源。
- 每个失败源要返回 `error`，不能只返回总失败。

### 3. `radar.review_events`

事件复核与选题推荐动作。

用途：

- 替代 Agent 裸读 `/api/intel/events` 后自行猜测。
- 给出 Top N 事件、推荐理由、风险提示、下一步建议。
- 支持“今天有什么值得写”的核心入口。
- 默认只看北京时间当天素材；历史复盘、补旧稿或追踪旧事件时必须显式传 `date_scope=all`。

参数：

| 参数 | 默认 | 说明 |
|---|---:|---|
| `limit` | `10` | 返回事件数量 |
| `min_score` | `0` | 最低综合分 |
| `include_ignored` | `false` | 是否包含 ignored 事件 |
| `watchlist` | `""` | 临时关注词，逗号分隔 |
| `date_scope` | `today` | `today` 只看当天素材；`all` 才看历史事件 |
| `target_date` | 北京时间今天 | 指定 `YYYY-MM-DD` 复核某一天 |
| `timezone` | `Asia/Shanghai` | 日期过滤时区 |

返回字段建议：

```json
{
  "events": [
    {
      "id": "evt-...",
      "title": "...",
      "composite_score": 82.5,
      "alert_state": "hot",
      "source_count": 4,
      "published_at": "...",
      "representative_link": "https://...",
      "why_recommended": [
        "4 个来源同时覆盖",
        "命中 watchlist: openai",
        "发布时间新"
      ],
      "risks": [
        "尚未深挖全文",
        "来源主要来自英文媒体"
      ],
      "suggested_next_operation": "radar.deep_dive_event",
      "suggested_params": {"event_id": "evt-..."}
    }
  ],
  "count": 10,
  "suggested_top_event_id": "evt-..."
}
```

成功标准：

- 即使没有事件也返回 `ok`，并建议 `radar.sync_sources` 或 `radar.build_events`。
- 推荐理由必须来自已有结构化字段，不能编造。
- 不直接生成文章。

### 4. `radar.review_deep_dive`

已深挖素材复核动作，不触发网络请求。

用途：

- 深挖后判断素材是否足够写文章。
- 用户问“这条能不能写”时返回可解释依据。
- 暴露每个来源抓取/抽取是否成功，避免 Agent 把 partial 当成 ready。

参数：

| 参数 | 默认 | 说明 |
|---|---:|---|
| `event_id` | `null` | 按事件查最近 deep dive |
| `deep_dive_id` | `null` | 直接查指定 deep dive |

返回字段建议：

```json
{
  "deep_dive_id": "dive-...",
  "event_id": "evt-...",
  "status": "partial",
  "writing_readiness": "partial",
  "fact_count": 6,
  "quote_count": 2,
  "timeline_count": 1,
  "source_results": [
    {"source_key": "openai-blog", "status": "success", "word_count": 1200, "error": null},
    {"source_key": "media-x", "status": "failed", "word_count": 0, "error": "extract_empty"}
  ],
  "article_writing_guide": "# 公众号文章写作指南\n...",
  "risks": [
    "仅 1 个来源抓取成功",
    "缺少可引用原文"
  ],
  "suggested_next_operation": "radar.deep_dive_event"
}
```

成功标准：

- 只读 DB，不重新抓取网页。
- `writing_readiness` 只能由真实字段推导：成功来源数、事实数、引文数、失败数。
- 必须返回 `article_writing_guide`，供外部 Agent 按本项目内置公众号风格生成唯一标题和平台发布稿；不能让用户从多个标题里选择。
- 只有 `writing_readiness="ready"` 才建议 `article.create`；`partial/weak` 必须继续深挖、补源或改选题。
- 没有 deep dive 时返回 `skipped` 或 `ok` + 建议 `radar.deep_dive_event`，不能伪造素材。

### 5. `radar.run_small_cycle`（后续项，当前未注册）

可选的薄组合动作，不是固定主流程。

用途：

- 给用户一句话“跑一下信息雷达”时使用。
- 它只是原子操作的安全编排：状态 -> 源检查 -> 同步 -> 聚类 -> 事件复核。

参数：

| 参数 | 默认 | 说明 |
|---|---:|---|
| `watchlist` | `""` | 关注词 |
| `clear_raw` | `false` | 是否聚类后清 raw |
| `review_limit` | `10` | 复核事件数量 |
| `probe_sources` | `false` | 是否探测源 |

返回字段建议：

```json
{
  "steps": [
    {"op": "radar.status", "status": "ok"},
    {"op": "radar.review_sources", "status": "ok"},
    {"op": "radar.sync_sources", "status": "ok"},
    {"op": "radar.build_events", "status": "ok"},
    {"op": "radar.review_events", "status": "ok"}
  ],
  "top_events": [],
  "suggested_next_operation": "radar.deep_dive_event"
}
```

设计约束：

- 它不能替代底层原子动作。
- 每个子步骤的结果必须完整返回。
- 任一步失败时不要吞掉，应返回 `failed_step` 和已完成步骤。

## 信息源发现与治理

信息源治理的目标是让 Agent 能主动扩展源池，但不污染正式采集源。现有 `radar.add_source` 只负责写库，它不判断来源质量，也不做联网验证，因此不能作为自动发现后的第一入口。

### 治理流程

```text
候选发现 -> 有效性验证 -> 去重 -> 质量评分 -> 添加/拒绝/待人工确认 -> 首次同步复核
```

### `radar.discover_sources`

候选发现动作，可联网搜索，也可接收外部 Agent 搜索后的候选列表。

参数：

| 参数 | 默认 | 说明 |
|---|---:|---|
| `query` | `""` | 搜索关键词 |
| `topic` | `""` | 主题标签，如 `ai`、`chip`、`startup` |
| `kind` | `rss` | 候选源类型：`rss`、`html`、`api` |
| `language` | `""` | 语言偏好 |
| `limit` | `10` | 最多返回候选数 |
| `candidates` | `[]` | 外部 Agent 已发现的候选 URL，可直接传入 |

返回字段建议：

```json
{
  "candidates": [
    {
      "url": "https://example.com/feed.xml",
      "name": "Example Blog",
      "kind": "rss",
      "topic": "ai",
      "discovered_by": "web_search",
      "evidence": ["页面声明 RSS", "最近文章列表可见"]
    }
  ],
  "count": 1,
  "suggested_next_operation": "radar.validate_source"
}
```

边界：

- 只返回候选，不写入 `sources`。
- 不把搜索结果页、社交主页、登录页直接当源。
- 如果项目内没有搜索 provider，则允许外部 Agent 先联网搜索，再把候选 URL 传入 `candidates`。

### `radar.validate_source`

验证单个候选源是否能进入源池。

参数：

| 参数 | 默认 | 说明 |
|---|---:|---|
| `url` | 必填 | 候选源 URL |
| `kind` | `rss` | 源类型 |
| `topic` | `""` | 主题标签 |
| `limit_per_source` | `5` | 验证时最多读取条目 |

验证标准：

- URL 可访问，不能是 403、404、登录页或搜索结果页。
- RSS/Atom 能解析；HTML 源至少能提取标题、链接、时间中的两项。
- 最近 30-90 天内有更新。
- 至少返回 3-5 条有效 item。
- item 必须有标题和链接。
- 与已有源 URL、domain、标题重叠度不过高。
- 来源优先级：官方博客、研究机构、主流媒体、垂直科技媒体优先。
- 拒绝聚合垃圾站、镜像站、明显采集站、无更新时间的站点。

返回字段建议：

```json
{
  "valid": true,
  "score": 86,
  "decision": "auto_add|needs_confirmation|reject",
  "reason": "RSS 可解析，最近 7 天有 5 条更新，未与现有源重复",
  "sample_items": [
    {"title": "...", "link": "...", "published_at": "..."}
  ],
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

### `radar.propose_source`

将验证结果整理成添加建议。当前不落库，只返回 proposal；后续如果需要多人/多 Agent 协作，再增加 `source_candidates` 表。

决策规则建议：

| 分数 | 决策 | 行为 |
|---:|---|---|
| `>= 80` | `auto_add` | 可调用 `radar.add_validated_source` 自动添加并启用 |
| `60-79` | `needs_confirmation` | 返回候选，等待用户或更强 Agent 确认 |
| `< 60` | `reject` | 不添加，返回拒绝原因 |

### `radar.add_validated_source`

正式添加动作，只接受已通过验证的候选。

约束：

- 当前必须传完整 `validated_source`。
- 添加前再次检查 URL/key/domain 是否重复。
- 添加后建议立即执行 `radar.sync_one_source source_key=...` 做首次同步复核。
- 默认 `enabled=true` 只适用于 `auto_add`；`needs_confirmation` 必须显式确认。
- 不允许绕过验证直接批量导入搜索结果。

### `radar.source_health_report`

定期源池治理动作。

输出建议：

- 高贡献源：最近 N 次采集贡献 raw items 多。
- 低贡献源：长期 0 item。
- 失败源：最近连续失败。
- 重复源：domain 或标题重叠过高。
- 建议操作：保留、降权、停用、人工复核。

### `radar.disable_stale_sources`

可选写动作，只停用，不删除。

约束：

- 必须基于健康报告。
- 返回每个停用源的证据：连续失败次数、最后成功时间、最近 item 数。
- 不删除源配置，避免误杀后无法恢复。

## 现有动作优化

### `radar.sync_sources`

当前问题：

- 返回总数，但缺少每个源的成功/失败明细。

建议改造：

```json
{
  "raw_count": 608,
  "source_count": 10,
  "source_results": [
    {"source_key": "openai-blog", "status": "ok", "raw_count": 12},
    {"source_key": "x-feed", "status": "failed", "raw_count": 0, "error": "timeout"}
  ],
  "partial": true,
  "failed_source_count": 1
}
```

### `radar.build_events`

当前建议：

- Agent 实操默认传 `clear_raw=false`，方便调试。
- 返回 Top events 摘要，减少调用方必须再查一次列表。

建议新增返回：

```json
{
  "top_events": [
    {"id": "evt-...", "title": "...", "score": 83.2}
  ],
  "suggested_next_operation": "radar.review_events"
}
```

### `radar.deep_dive_event`

当前建议：

- 返回 `source_results`，暴露哪些 URL 抓取成功/失败。
- 返回 `writing_readiness`，让 Agent 判断是否足够写文章。

建议新增返回：

```json
{
  "writing_readiness": "ready|partial|weak",
  "source_results": [
    {"url": "...", "status": "success", "word_count": 1200},
    {"url": "...", "status": "failed", "error": "extract_empty"}
  ],
  "suggested_next_operation": "article.create | radar.deep_dive_event"
}
```

只有 `writing_readiness="ready"` 时才建议 `article.create`。`partial/weak` 代表素材仍不足，Agent 应继续补源、强制重挖或改选题。

平台短讯合集的 `material_id` 可以是逗号分隔的 5 个 deep dive ID，例如 `dive-a,dive-b,dive-c,dive-d,dive-e`。不要只拿单个 deep dive 去写 5 条合集，也不要用 `override_quality_gate` 绕过素材数量门槛。

## 数据模型建议

短期不新增表，优先使用已有表和运行时聚合：

- `sources`
- `raw_items`
- `intel_events`
- `alerts`
- `deep_dives`
- `publish_tasks`

如果后续需要持久化源健康历史，再新增 `source_checks` 表：

```text
source_checks
- id
- source_key
- status
- item_count
- error
- checked_at
- duration_ms
```

当前阶段不建议先加表，避免过早扩展 schema。

如果后续需要持久化候选源审批，再新增 `source_candidates` 表：

```text
source_candidates
- id
- url
- kind
- name
- topic
- discovered_by
- validation_status
- score
- decision
- reason
- sample_items
- dedupe_info
- suggested_source
- created_at
- reviewed_at
```

当前阶段建议先不加表，用 `radar.validate_source` / `radar.propose_source` 的运行时结果驱动添加；等出现多人协作、跨会话审批或批量候选管理需求，再持久化。

## Agent 使用规则

当用户说“找今天 AI 新闻”：

```text
1. radar.status
2. 若 source_count=0 -> radar.seed_defaults
3. radar.review_sources probe=false
4. radar.sync_sources
5. radar.build_events clear_raw=false watchlist=...
6. radar.review_events limit=10
7. 选择一个事件后 radar.deep_dive_event
8. radar.review_deep_dive event_id=...
9. 读 deep-dive，写文章
```

当用户说“为什么今天没新闻”：

```text
1. radar.status
2. radar.review_sources probe=true
3. 如果源失败，报告具体源失败
4. 如果源正常但 raw=0，说明真实没有采到
5. 不允许用示例新闻代替
```

当用户说“帮我增加一些 AI 信息源”：

```text
1. radar.discover_sources query="AI official blog RSS" topic=ai kind=rss
2. 对候选逐个 radar.validate_source
3. radar.propose_source 汇总分数和风险
4. score>=80 且无重复 -> radar.add_validated_source
5. score=60-79 -> 询问用户是否确认添加
6. score<60 -> 拒绝并说明原因
7. 添加后 radar.sync_one_source 复核真实采集
```

当用户说“只深挖这条”：

```text
1. radar.deep_dive_event event_id=...
2. radar.review_deep_dive event_id=...
```

当用户说“这篇文章帮我上传草稿箱”：

```text
1. article.prepare_wechat_payload（若 missing_required 非空，先 article.update 补齐）
2. wechat.open_dashboard
3. wechat.check_login
4. wechat.open_new_editor
5. wechat.fill_editor_required
6. 按用户要求设置原创/合集/创作来源/封面
7. wechat.save_current_editor_as_draft
8. wechat.review_draft_box title=...
```

## API 与 CLI 示例

CLI：

```powershell
.\.venv\Scripts\python.exe -m agent_news run radar.status
.\.venv\Scripts\python.exe -m agent_news run radar.review_sources probe=true
.\.venv\Scripts\python.exe -m agent_news run radar.sync_one_source source_key=hn-frontpage
.\.venv\Scripts\python.exe -m agent_news run radar.sync_sources
.\.venv\Scripts\python.exe -m agent_news run radar.build_events watchlist=ai,openai clear_raw=false
.\.venv\Scripts\python.exe -m agent_news run radar.review_events limit=10 min_score=50
.\.venv\Scripts\python.exe -m agent_news run radar.deep_dive_event event_id=evt-xxx
.\.venv\Scripts\python.exe -m agent_news run radar.review_deep_dive event_id=evt-xxx
.\.venv\Scripts\python.exe -m agent_news run radar.validate_source url=https://example.com/feed.xml kind=rss topic=ai
```

HTTP：

```http
POST /api/operations/radar.review_events/execute
Content-Type: application/json

{
  "params": {
    "limit": 10,
    "min_score": 50,
    "watchlist": "ai,openai,anthropic"
  }
}
```

## 测试计划

### 单元测试

- `radar.status` 空库时返回 ok 和建议。
- `radar.status` 有 sources/raw/events/alerts 时数量正确。
- `radar.review_sources probe=false` 不触发网络。
- `radar.review_sources probe=true` 单源失败不影响其他源。
- `radar.validate_source` 有效 RSS 返回 sample_items 和 score。
- `radar.validate_source` 对 404/登录页/搜索结果页返回 reject。
- `radar.add_validated_source` 拒绝未验证候选。
- `radar.add_validated_source` 添加前执行重复检查。
- `radar.review_events` 无事件时返回 ok 和建议。
- `radar.review_events` 能按 score 排序，并生成推荐理由。
- `radar.review_deep_dive` 不触发网络，能按真实素材生成 writing_readiness。
- `radar.review_deep_dive` 没有 deep dive 时建议 `radar.deep_dive_event`。
- `article.prepare_wechat_payload` 不打开浏览器；必填字段齐全时返回微信填写参数，缺字段时返回 failed 和 missing_required。
- `wechat.close_blank_tabs` 不关闭 `action=edit` 编辑页。

### 集成测试

- seed -> sync(stub) -> build -> review_events。
- review_events 选 top event -> deep_dive_event(stub fetch) -> review_deep_dive。
- article.prepare_wechat_payload ready_for_wechat_fill=true -> wechat.fill_editor_required 参数可直接衔接。

后续项：

- `radar.run_small_cycle` 若实现，再补“某一步失败时返回 failed_step 和已完成步骤”的组合测试。

### 回归测试

- 不能用 sample/fallback 数据冒充真实采集结果。
- `build_events clear_raw=false` 后 raw_items 不被清空。
- `sync_sources` 单源失败时整体可 partial success，但必须暴露失败源。
- 自动发现候选源不能直接写入 `sources`。
- 新源添加后必须能用 `sync_one_source` 采到真实 raw items，或返回明确失败。

## 实施状态

### Phase 1：只读观测（已实现）

1. 实现 `radar.status`
2. 实现 `radar.review_events`
3. 实现 `radar.review_deep_dive`
4. 更新 `AGENT.md` 和 `docs/ATOMIC_OPERATIONS.md`
5. 补测试

### Phase 2：源健康诊断（已实现）

1. 实现 `radar.review_sources probe=false`
2. 实现 `probe=true` 单源探测
3. 优化 `sync_sources` 返回 `source_results`
4. 补测试

### Phase 3：信息源发现与治理（已实现，项目内搜索 provider 后置）

1. 实现 `radar.validate_source`
2. 实现 `radar.propose_source`
3. 实现 `radar.add_validated_source`
4. 视搜索 provider 决定是否实现项目内 `radar.discover_sources`；否则允许外部 Agent 搜索后传候选
5. 实现 `radar.source_health_report`
6. 补测试

### Phase 4：小闭环组合（后续项）

1. 实现 `radar.run_small_cycle`
2. 加失败中断与审计
3. 真实跑一次信息雷达闭环

### Phase 5：写作桥接（已实现；`article.create_from_deep_dive` 后置）

1. 实现 `article.create` / `article.get` / `article.list` / `article.update`
2. 实现 `article.prepare_wechat_payload`
3. 视产品决定是否实现 `article.create_from_deep_dive`；默认不让系统自动生成正文
4. 明确文章结构：短讯合集、单篇深稿、快讯
5. 与微信 `fill_editor_required` 对接

### Phase 6：微信恢复与审计观测（已实现）

1. 实现 `wechat.inspect_tabs`
2. 实现 `wechat.focus_editor_tab`
3. 实现 `wechat.close_blank_tabs`
4. 实现 `wechat.upload_cover_file`
5. 实现 `audit.review_tasks` / `workflow.status`

## 验收标准

完成 Phase 1-2 后，Agent 应能稳定回答：

- 当前有多少信息源？
- 哪些源失败了？为什么？
- 新发现的信息源是否有效？为什么能/不能添加？
- 新源是否与已有源重复？
- 今天采到了多少 raw items？
- 聚类出了多少事件？
- 哪些事件最值得写？为什么？
- 某事件是否值得深挖？
- 深挖素材是否足够写文章？

完成 Phase 3 后，用户可以说：

```text
跑一下今天的信息雷达，找 5 条值得写的 AI 新闻
```

Agent 应能执行：

```text
status -> review_sources -> sync_sources -> build_events -> review_events
```

并返回真实 Top events，而不是直接进入微信发布或生成假文章。
