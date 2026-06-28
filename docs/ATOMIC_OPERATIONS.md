# 原子操作清单

agent-news 的每一个能力都是一个**原子操作**——可独立调用、独立失败、独立重试。
完整清单可通过 `GET /api/operations` 实时查询。本文件是人工维护的参考索引。

当前试生产版本注册表应返回 **80 个操作**。如本文档与实时注册表冲突，以 `GET /api/operations` 为准，并立即修正文档。

## 调用方式

```bash
# 列出所有操作
GET /api/operations

# 单步执行
POST /api/operations/{name}/execute
{"params": {...}}

# 批量执行（带失败策略）
POST /api/operations/batch
{
  "steps": [{"op": "...", "params": {...}}, ...],
  "on_error": "stop|continue|retry_once"
}
```

CLI 推荐使用项目虚拟环境：

```powershell
.\.venv\Scripts\python.exe -m agent_news list
.\.venv\Scripts\python.exe -m agent_news run radar.status
```

不要用系统 Python 作为生产入口，避免依赖缺失或本机代理导致本地服务误判。

每个操作返回 `OperationResult`：
- `status`: `ok` | `skipped` | `failed`
- `message`: 人类可读说明
- `state`: 该步观测/变更的状态快照
- `ok` (computed): status != failed

**失败永不异常外溢**——一个操作失败只返回 failed，不影响其他操作或 batch。

---

## 信息雷达（radar.*）

设计背景见 `docs/RADAR_DESIGN.md`，实施架构见 `docs/TECHNICAL_ARCHITECTURE.md`。当前已补齐状态观测、源健康诊断、候选源治理、事件复核推荐、深挖复核等原子能力。

默认源池已在本项目内置：当前默认源共 95 个。试生产库应保留 `sources=95`，运行数据表可为空。

| 操作 | 参数 | 说明 |
|---|---|---|
| `radar.status` | `include_recent?` | 只读：查看源、raw、events、alerts、deep dives 数量和建议下一步，不联网 |
| `radar.review_sources` | `probe?`, `limit_per_source?`, `source_key?` | 只读/可探测：复核源配置和健康状态，`probe=false` 不联网 |
| `radar.seed_defaults` | — | 初始化默认源（仅当无源时生效，幂等） |
| `radar.discover_sources` | `candidates?`, `query?`, `topic?`, `kind?`, `language?`, `limit?` | 候选源发现/规范化。本轮外部 Agent 传 candidates，不绑定搜索供应商 |
| `radar.validate_source` | `url`, `kind?`, `topic?`, `limit_per_source?` | 验证候选源可访问、可解析、近期有内容、未重复，并给出评分 |
| `radar.propose_source` | `validated_source` | 把验证结果整理成添加建议，不写库 |
| `radar.add_validated_source` | `validated_source`, `confirmed?` | 只添加通过验证的候选源；`needs_confirmation` 需 `confirmed=true` |
| `radar.add_source` | `key`, `source_name`, `url`, `kind?`, `tags?`, `priority?` | 底层添加信息源。Agent 自动发现新源时不要直接调用，先走验证链路 |
| `radar.sync_sources` | `source_key?` | 逐源采集所有/单个源 → raw_items，返回 `source_results` 和 partial 信息 |
| `radar.sync_one_source` | `source_key` | 采集单个源 |
| `radar.build_events` | `merge_threshold?`, `alert_threshold?`, `watchlist?`, `clear_raw?` | 聚类+打分+物化 alerts，返回 `top_events` 和下一步建议 |
| `radar.review_events` | `limit?`, `min_score?`, `include_ignored?`, `watchlist?`, `date_scope?`, `target_date?`, `timezone?` | 只读：默认只返回北京时间当天 Top events、推荐理由、风险和下一步建议；复盘历史需显式 `date_scope=all` |
| `radar.deep_dive_event` | `event_id`, `max_sources?`, `force?` | 抓全文、提取素材、附写作指南；返回 `source_results` 和 `writing_readiness` |
| `radar.review_deep_dive` | `event_id?`, `deep_dive_id?` | 只读：复核深挖素材、来源成功/失败和写作准备度，并返回 `article_writing_guide` |
| `radar.source_health_report` | — | 只读：汇总源池健康度、低贡献源、疑似重复源 |
| `radar.disable_stale_sources` | `dry_run?`, `min_raw_items?` | 停用长期无贡献源；默认 dry-run，只返回将要停用的源 |
| `radar.remove_source` | `source_key` | 删除信息源 |

### 后续雷达原子

这些尚未进入注册表，只作为后续增强项，不要在当前任务里调用。

| 操作 | 参数 | 说明 |
|---|---|---|
| `radar.review_raw_items` | `limit?`, `source_key?` | 只读：查看最近 raw items，排查采集与聚类之间的问题 |
| `radar.update_source` | `source_key`, fields | 更新源名称、URL、标签、优先级、权重、配置 |
| `radar.enable_source` / `radar.disable_source` | `source_key` | 启用/停用信息源，不删除历史数据 |
| `radar.ignore_event` / `radar.unignore_event` | `event_id` | 标记噪声事件或恢复事件 |

读取端点（非操作）：
- `GET /api/intel/sources` / `GET /api/intel/sources/{key}`
- `GET /api/intel/raw-items`
- `GET /api/intel/events` （默认只看北京时间当天；支持 `ignored`, `min_score`, `date_scope`, `target_date`, `timezone` 过滤；看历史需 `date_scope=all`）
- `GET /api/intel/events/{id}`
- `GET /api/intel/alerts`
- `GET /api/intel/deep-dives` / `GET /api/intel/deep-dives/{id}`
- `GET /api/intel/events/{id}/deep-dive`

---

## 微信导航（wechat.*，category=navigation）

| 操作 | 参数 | 说明 |
|---|---|---|
| `wechat.open_dashboard` | — | 进公众号首页（不验证登录，只导航） |
| `wechat.check_login` | — | 真实 DOM 登录检测（3 选择器 1200ms）+ 全页截图。未登录截图含二维码 |
| `wechat.session` | — | 只读会话状态：manager_alive/busy/resident_page/last_error/current_url/is_editor_page |
| `wechat.open_new_editor` | — | 从首页进空白编辑页 |
| `wechat.open_draft_box` | — | 进草稿箱列表 |
| `wechat.open_publish_history` | — | 进发表记录 |
| `wechat.open_existing_draft` | `title` | 按标题打开已有草稿编辑页 |
| `wechat.list_drafts` | `limit?` | 列出草稿箱标题（只读） |
| `wechat.review_draft_box` | `title?`, `limit?` | 只读：草稿箱复核。传标题时校验目标草稿是否已保存到远端草稿箱 |
| `wechat.inspect_tabs` | — | 只读：返回当前浏览器标签页 URL、标题、是否 blank、是否编辑页 |
| `wechat.focus_editor_tab` | — | 聚焦已有 `action=edit` 编辑页，不新开页面 |
| `wechat.close_blank_tabs` | — | 关闭重复 `about:blank` 标签，不关闭编辑页 |

### 登录流程（AI 调用顺序）

```
1. POST wechat.open_dashboard  → 打开首页
2. POST wechat.check_login     → {logged_in, screenshot, last_error}
   - logged_in=true  → 继续
   - logged_in=false → 截图含二维码，发给用户扫码 → 扫完再调 check_login
3. GET  wechat.session         → 随时查 current_url / is_editor_page / last_error
```

> CLI auto-ensure：`python -m agent_news run wechat.open_dashboard` 时，如果
> 服务没启动会自动后台拉起（等 /api/health 就绪），不需要手动 start.bat。
> 生产命令建议写成 `.\.venv\Scripts\python.exe -m agent_news ...`。

---

## 微信编辑器（category=editor）

| 操作 | 参数 | 说明 |
|---|---|---|
| `wechat.fill_editor_required` | `title`, `author`, `body_markdown`, `styled?`, `allow_platform_default?` | 一次填写编辑区必填三件套：标题、作者、正文。适合“上传文章/保存草稿/发布文章”意图，避免漏填作者 |
| `wechat.fill_title` | `text` | 填文章标题栏（空则跳过） |
| `wechat.fill_author` | `text`, `allow_platform_default?` | 填作者（空则跳过；默认严格回读校验） |
| `wechat.fill_digest` | `text` | 填摘要（空则跳过） |
| `wechat.paste_body` | `markdown`, `styled?` | 写入正文区。默认 `styled=true`：先剥离开头文章标题，再把 Markdown 转微信富文本 HTML；正文内二级/三级小标题、段落、列表、引用、加粗、字号、行距会保留；兼容命令行传入的 `\n` 转义 |
| `wechat.inspect_editor` | — | 只读：读回各字段当前值 |

**前置条件**：编辑器操作要求当前在编辑页（先 `open_new_editor` 或 `open_existing_draft`）。

**编辑区必填三件套**：标题、作者、正文。用户要求“上传文章/保存草稿/发布文章”时，优先调用 `wechat.fill_editor_required`；如果拆成单字段原子调用，也必须完整调用 `fill_title`、`fill_author`、`paste_body`。缺作者不能继续进入发布动作。

**标题/正文边界**：`fill_title` 只写微信标题编辑器；`paste_body` 只写正文编辑器。不要把文章标题放进 `paste_body.markdown`。如果外部 Agent 误传了开头 `# 文章标题`，或首行裸文本等于当前标题栏内容，`paste_body` 会自动剥离，避免标题进入正文。

---

## 微信发布前设置（category=publish_settings）

**全部参数化、全部可跳过** —— 这是相对固定流水线的核心改进。

| 操作 | 参数 | 跳过条件 | 说明 |
|---|---|---|---|
| `wechat.set_original` | `enabled` (默认 True) | `enabled=False` | 原创声明 |
| `wechat.set_original_author` | `author` | author 为空或原创弹窗计数器超限 | 修改原创声明里的作者；用于已开启原创后需要通过原创弹窗改作者的场景；以输入框右侧计数器为准，不本地估算 |
| `wechat.set_reward` | `enabled` (默认 True) | `enabled=False` | 赞赏 |
| `wechat.set_collection` | `name` | `name=""` | 合集（**任意名称**，不写死） |
| `wechat.set_claim_source` | `name` | `name=""` | 创作来源（**任意名称**） |
| `wechat.generate_ai_cover` | `prompt`, `wait_seconds?` | `prompt=""` | AI 封面 |
| `wechat.upload_cover_file` | `file_path` | 文件不存在/格式不支持 | 上传本地封面图片；找不到上传 input 或回读无封面时失败 |
| `wechat.list_collections` | — | — | 只读：列出可选合集 |
| `wechat.list_claim_sources` | — | — | 只读：列出可选创作来源 |

**推荐流程**：先 `list_collections` 看有哪些合集 → 再 `set_collection(name=选中的)`。创作来源同理。`set_collection` / `set_claim_source` 必须回读命中目标文本才算成功，单纯点击不算。作者如果已经被原创声明锁定，需要调用 `set_original_author(author=...)` 进入原创弹窗修改；该字段以输入框右侧计数器为准，动作不会静默截断。

---

## 微信保存/发表（category=save_publish）

| 操作 | 参数 | 说明 |
|---|---|---|
| `wechat.save_as_draft` | — | 存草稿箱 |
| `wechat.save_current_editor_as_draft` | — | 意图级动作 1：当前编辑页直接保存草稿箱 |
| `wechat.inspect_body_word_count` | — | 只读/门禁：读取微信底部「正文字数」计数；为 0 时保存草稿和发表都会被拦截 |
| `wechat.publish_preflight` | `require_*?` | 只读：发表前必填项校验。默认检查标题、作者、正文、封面、原创声明、合集、创作来源；赞赏默认不硬卡 |
| `wechat.click_publish` | — | 发表 step 1：点击「发表」 |
| `wechat.inspect_publish_dialog` | — | 只读：识别发表确认、未开启群发通知确认、继续发表、二维码、账号授权错误、登录态或未知弹窗 |
| `wechat.confirm_publish_modal` | — | 发表 step 2：仅当弹窗状态为 `publish_confirm` 时，点击文本精确为「发表」的按钮 |
| `wechat.confirm_publish_no_notify` | — | 发表 step 3A：仅当弹窗状态为 `publish_no_notify` 时，点击文本精确为「继续发表」的按钮 |
| `wechat.continue_publish` | `max_clicks?` | 发表 step 3：仅当弹窗状态为 `continue_publish` 或 `publish_no_notify` 时，循环点击文本精确为「继续发表」的按钮 |
| `wechat.wait_qrcode` | `max_checks?`, `retry_wait_ms?` | 发表 step 4：轮询二维码，出现即截图 |
| `wechat.publish_to_qrcode` | `max_continue_clicks?` | 完整发表流程（1+2+3+4），到二维码停止 |
| `wechat.publish_current_editor_to_qrcode` | `max_continue_clicks?` | 意图级动作 2：当前编辑页直接走到二维码 |
| `wechat.publish_existing_draft_to_qrcode` | `title`, `max_continue_clicks?` | 意图级动作 3：按标题打开已有草稿编辑页，再走到二维码 |
| `wechat.check_publish_done` | — | 检测是否回到首页（发表成功标志） |

**⚠️ `publish_to_qrcode` / `wait_qrcode` 返回 `requires_human_scan=True` 时，发表未完成。** 必须人工扫码确认。工作流状态进入 `pending_confirmation`，只有 `check_publish_done` 确认回到首页后才转 `published`。

保存/发表动作默认从严：微信底部「正文字数」计数为 0 时，`save_as_draft` / `save_current_editor_as_draft` / `click_publish` / `publish_to_qrcode` / `publish_current_editor_to_qrcode` / `publish_existing_draft_to_qrcode` 都会直接返回 failed，不点击保存草稿或发表按钮。

发表动作还必须先通过 `wechat.publish_preflight`。缺标题、作者、正文、封面、原创声明、合集、创作来源任意一项时，`publish_to_qrcode` / `publish_current_editor_to_qrcode` / `publish_existing_draft_to_qrcode` 会直接返回 failed，不点击发表按钮。赞赏默认不硬卡；账号支持且用户要求开启时，显式传 `require_reward=true`。

发表确认链路是 fail-closed：`publish_to_qrcode` 会先观察 `wechat.inspect_publish_dialog` 的分类结果，再决定下一步。只有 `publish_confirm` 才点「发表」，只有 `publish_no_notify` / `continue_publish` 才点「继续发表」，只有 `qrcode` 才返回 `reached_qrcode=true`。`publish_no_notify` 表示当天免费群发通知已用完，内容会展示在公众号主页但不群发通知，这是正常确认页。如果出现 `account_auth_error`（例如「未授权使用切换账号能力，请退出后扫码登录其他账号」）、`login_required` 或 `unknown_dialog`，动作会直接 failed，并在 `state.publish_dialog` 里保留弹窗文本、按钮列表和 `requires_relogin` 等字段，绝不猜测点击 footer 第一个按钮。

### 三种用户意图映射

- “上传草稿箱 / 保存草稿箱”：当前已在编辑页时，调用 `wechat.save_current_editor_as_draft`。
- “填好后直接发表 / 发布”：当前已在编辑页时，调用 `wechat.publish_current_editor_to_qrcode`，到二维码停止。
- “我审核/修改过草稿了，帮我发布”：调用 `wechat.publish_existing_draft_to_qrcode(title=...)`，它会先走已验证的 `open_existing_draft` 编辑入口，再到二维码停止。

---

## 微信复核与指标（category=review）

| 操作 | 参数 | 说明 |
|---|---|---|
| `wechat.review_draft_box` | `title?`, `limit?` | 草稿箱复核。保存草稿后可传标题确认目标草稿存在 |
| `wechat.review_publish_history` | `title?`, `limit?`, `max_pages?` | 发表记录复核。传标题时校验目标文章是否出现在远端发表记录 |
| `wechat.analyze_publish_metrics` | `title?`, `url?`, `limit?`, `max_pages?` | 基于发表记录提取全维度数据指标：阅读、点赞、分享、推荐、留言、划线、赞赏、转载 |
| `wechat.review_content_performance` | `title?`, `url?`, `max_pages?`, `lookback_runs?` | 只读：基于发表记录+历史快照做观察性复盘，输出强弱标签、趋势、证据等级、混杂因素和下一步建议 |
| `wechat.review_content_strategy` | `lookback_runs?` | 只读：从最近指标快照生成观察性运营策略画像，供雷达选题和文章质量门禁提示复用 |
| `wechat.pin_publish_record` | `title`, `confirmed?`, `max_pages?`, `url?/target_url?` | 发表记录更多菜单：置顶 |
| `wechat.set_publish_record_private` | `title`, `confirmed?`, `max_pages?`, `url?/target_url?` | 发表记录更多菜单：仅自己可见 |
| `wechat.delete_publish_record` | `title`, `confirmed?`, `max_pages?`, `url?/target_url?` | 危险写操作：按标题定位发表记录，同标题多篇时可传 URL 精确定位；默认只打开删除确认弹窗，只有 `confirmed=true` 才点击最终“确认” |
| `wechat.close_publish_record_recommendation` | `title`, `confirmed?`, `max_pages?`, `url?/target_url?` | 发表记录更多菜单：关闭推荐 |
| `wechat.copy_publish_record_link` | `title`, `max_pages?`, `url?/target_url?` | 发表记录更多菜单：复制链接；状态返回 `copied_url` |
| `wechat.change_publish_record_collection` | `title`, `confirmed?`, `max_pages?`, `url?/target_url?` | 发表记录更多菜单：修改合集，打开后返回弹层状态 |
| `wechat.change_publish_record_claim_source` | `title`, `confirmed?`, `max_pages?`, `url?/target_url?` | 发表记录更多菜单：声明创作来源，打开后返回弹层状态 |

使用规则：

- `review_draft_box` 只确认草稿箱远端状态，不打开编辑器、不修改内容。
- `review_publish_history` 只确认发表记录远端状态；返回 `should_offer_metrics_analysis=true` 时，Agent 应主动询问用户是否继续触发 `wechat.analyze_publish_metrics`。
- `review_content_performance` 用于复盘判断，不是新的一次抓取；它要求 `title` 或 `url` 至少一个用于定位单篇文章，并基于历史快照给出下一步建议。返回的 `performance_label` 是观察性标签，不是标题或选题的因果结论。
- `review_content_strategy` 是闭环记忆入口；它把最近 `analyze_publish_metrics` 快照转成结构化 `content_strategy_profile`，后续 `radar.review_events` 和 `article.review_quality` 会自动带上这份画像。画像包含 `evidence_level`、`causal_claim_allowed=false`、`confounders` 和 `interpretation_rules`，弱/中证据只能提示，不能触发硬规则。
- `analyze_publish_metrics` 是数据闭环动作，用于量化稿件质量、受众喜爱程度和传播性。它不等于发布确认，也不会修改文章。
- 稳定复盘结论应同步沉淀到 `docs/CONTENT_PERFORMANCE_INSIGHTS.md`，供后续 Agent 选题、写标题和写正文时读取；沉淀时必须写明样本范围和混杂因素，禁止写成“标题导致阅读高”。
- 发表记录更多菜单 7 个动作已拆成独立原子操作：置顶、仅自己可见、删除、关闭推荐、复制链接、修改合集、声明创作来源。它们都通过标题定位目标记录；同标题多篇时必须补 `url`/`target_url` 精确定位。
- `delete_publish_record` 是危险写动作：默认只打开删除确认弹窗，不会自动点最终“确认”。只有用户明确要求并传 `confirmed=true` 才执行真实删除；未知弹窗、目标不唯一、按钮不精确都必须失败停止。
- `delete_publish_record` 点击“确定”后可能进入管理员/运营者扫码验证；此时返回 `status=skipped`、`requires_human_scan=true`、`deleted=false`。二维码 URL 含安全票据，返回值只暴露 `has_qrcode=true`，不暴露二维码 `src`。
- `pin_publish_record`、`set_publish_record_private`、`close_publish_record_recommendation` 如果出现确认弹窗，默认返回 `skipped` 并停住；只有传 `confirmed=true` 才点击精确文本为“确定/确认”的按钮。

指标含义：

- `read_count`：阅读人数，衡量触达和标题/选题吸引力
- `like_count`：点赞人数，衡量认可度
- `share_count`：分享人数，衡量传播性
- `recommend_count`：推荐人数，衡量平台内推荐意愿
- `comment_count`：留言条数，衡量讨论度
- `highlight_count`：划线人数，衡量深读和摘录价值
- `tip_amount`：赞赏金额，衡量付费认可
- `reprint_count`：被转载次数，衡量外部引用和扩散

---

## 文章管理（article.*）

Agent 优先用 `article.*` 原子桥接雷达和微信，不需要混用临时参数。REST CRUD 仍保留给普通 API 调用。

写作前必须先读取 `radar.review_deep_dive` 或 deep-dive 详情中的 `article_writing_guide`。这份指南是本项目内置的公众号写作规约，约束标题策略、短讯合集结构、事实纪律和禁用 AI 味词。Agent 可以在内部推演多个标题候选，但最终只能把 1 个定稿标题写入 `article.title`，不能要求用户三选一。

| 操作 | 参数 | 说明 |
|---|---|---|
| `article.create` | `title`, `digest?`, `body_markdown`, `author?`, `material_id?` | 保存 Agent 已写好的文章，不自动发布、不创建 workflow |
| `article.get` | `article_id` | 读取文章详情 |
| `article.list` | `page?`, `page_size?` | 文章列表 |
| `article.update` | `article_id`, `fields` 或字段 kwargs | 修改标题、摘要、作者、正文、素材关联 |
| `article.review_quality` | `article_id` | 只读：按项目内置写作规范复核文章是否可进入微信填写 |
| `article.prepare_wechat_payload` | `article_id`, `cover_prompt?`, `override_quality_gate?` | 只读：转成微信填写参数，不打开浏览器 |

`article.prepare_wechat_payload` 的默认封面提示词是具象物品/场景类描述，例如芯片、手机、笔记本电脑、办公桌、实验台、合同和计算器。不要让封面提示词生成文字海报、标题海报或带字图片。

`article.review_quality` 和 `article.prepare_wechat_payload` 会执行平台前质量门禁：

- 单事件长文应通过 `material_id` 绑定 1 个 ready deep dive；ready 的最低标准是 2 个成功来源、5 条事实。
- 5 条短讯合集应通过 `material_id="dive-a,dive-b,dive-c,dive-d,dive-e"` 绑定至少 5 个 ready deep dive；每条至少 1 个成功来源和 1 条事实，总体事实不少于 5 条。
- 单事件长文默认至少 800 字；短讯合集默认 600-1000 字，并且必须用“首先/然后/接下来/再说/最后”串成 5 条平台稿。
- 平台稿不能保留 `核心事实`、`这意味着什么`、`还不确定什么`、`来源链接`、裸 URL、`## 1.` 这类本地素材格式。
- `article.prepare_wechat_payload` 默认会拦截质量不过的文章，返回 `failed`、`quality_report`、`ready_for_wechat_fill=false`，不返回可执行 `suggested_steps`。
- 只有人工确认例外时，才允许传 `override_quality_gate=true` 跳过质量门禁。

`article.prepare_wechat_payload` 仍会校验微信编辑必填项：标题、作者、正文。缺字段时返回 `failed`，state 中包含 `missing_required`、`ready_for_wechat_fill=false`、`suggested_next_operation="article.update"`。不要把缺作者或质量不过的 payload 继续传给 `wechat.fill_editor_required`。

### 文章 REST 端点

| 端点 | 说明 |
|---|---|
| `POST /api/articles` | 创建文章 `{title, digest, body_markdown, author, material_id?}` |
| `GET /api/articles` | 列表（`page`, `page_size`） |
| `GET /api/articles/{id}` | 详情 |
| `PUT /api/articles/{id}` | 更新 |
| `DELETE /api/articles/{id}` | 删除 |

### 后续文章原子

| 操作 | 参数 | 说明 |
|---|---|---|
| `article.create_from_deep_dive` | `event_id?`, `deep_dive_id?` | 可选：从 deep dive 创建文章草稿壳或素材关联；默认不自动生成正文 |

## 审计与工作流观测

| 操作 | 参数 | 说明 |
|---|---|---|
| `audit.review_tasks` | `limit?`, `status?`, `operation_prefix?` | 只读：查看最近操作审计、失败步骤和错误信息 |
| `workflow.status` | `workflow_session_id` | 只读：查看工作流当前状态、文章 ID、合法下一步、last_error、settings_applied |

## 工作流 REST

集中式状态机，非法转换返回 422。

| 端点 | 说明 |
|---|---|
| `POST /api/workflows?article_id=` | 为文章创建工作流 |
| `GET /api/workflows` | 列表 |
| `GET /api/workflows/{id}` | 详情 |
| `POST /api/workflows/{id}/transition` | `{target: "editor_open"|"content_filled"|...}` |
| `GET /api/workflows/states/allowed` | 查看合法状态图 |

状态：灵活原子编排，非固定流水线。合法转换（任一非终态可 → `failed`/`abandoned`）：
- `init → editor_open`
- `editor_open → content_filled / settings_applied / cover_ready / saved / pending_confirmation`
- `content_filled → settings_applied / cover_ready / saved / pending_confirmation`
- `settings_applied → cover_ready / saved / content_filled / pending_confirmation`
- `cover_ready → saved / settings_applied / content_filled / pending_confirmation`
- `saved → pending_confirmation`（不能直通 published）
- `pending_confirmation → published / failed / abandoned`

⚠️ `saved` 不是发布必经状态——Agent 可从任意内容/设置/封面状态直通 `pending_confirmation`（直接发布）。
⚠️ 到达二维码 = `pending_confirmation`，不是 `published`。只有 `check_publish_done` 确认回到首页才能转 `published`。
带 `workflow_session_id` 执行操作时，`status=="ok"` 的操作会自动推进 workflow（`skipped` 不推进，非法转换静默跳过）。
