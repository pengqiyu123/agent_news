# AGENT.md — agent-news 操作手册

这份文档是给外部 AI agent 的操作手册。读完它，你就能完全控制 agent-news：从信息采集、热点发现、深挖、写作，到微信草稿上传、发布前设置、最终发表。

## 铁律（不可违反）

**agent-news 必须自包含运行。分发、接手和生产执行时只依赖本项目目录，不依赖 `auto-news-studio` 或任何项目外源码。**

具体来说：
- 微信页面选择器、点击逻辑、导航流程已经内置在 `agent_news/browser/` 和 `agent_news/operations/wechat/`。其他 AI 只能调用这些原子操作或维护本项目内代码，不能要求读取外部项目。
- 遇到微信 DOM 相关问题，先用本项目的 `wechat.session`、`wechat.inspect_tabs`、`wechat.review_draft_box`、`wechat.publish_preflight` 和已有 selector profile 定位问题；不要猜 DOM，也不要搜索前端 HTML。
- 允许改造的方向是：把本项目已有稳定实现继续拆成原子操作、参数化、可观测、可测试。禁止把流程退回成固定大脚本。

## 核心理念

**agent-news 是原子操作系统，不是固定流水线。** 这里每个操作都是独立原子，Agent 按用户目标自由编排，跳过任意步骤、改顺序、重试单步。

两条合法终态路径（saved 非必经）：
- **保存草稿**：`open_dashboard → open_new_editor/open_existing_draft → fill_editor_required 或 fill_title+fill_author+paste_body → 可选 settings/cover → save_as_draft`
- **直接发布**：`open_dashboard → open_new_editor/open_existing_draft → fill_editor_required 或 fill_title+fill_author+paste_body → settings/cover → publish_to_qrcode → pending_confirmation`

三类用户口令对应的保存/发表意图：
- 用户说“上传草稿箱/保存草稿箱”：当前编辑页内容填好后调用 `wechat.save_current_editor_as_draft`（或底层 `wechat.save_as_draft`），随后可用 `wechat.review_draft_box(title=...)` 复核远端草稿箱。
- 用户说“填完后直接发表/发布”：当前编辑页内容填好后调用 `wechat.publish_current_editor_to_qrcode`（或底层 `wechat.publish_to_qrcode`），到二维码停止。
- 用户说“我审核过草稿了，帮我发布”：调用 `wechat.publish_existing_draft_to_qrcode(title=...)`，它会按标题打开草稿编辑页，再到二维码停止。人工扫码后可用 `wechat.review_publish_history(title=...)` 复核发表记录。

⚠️ 到达二维码只代表 `pending_confirmation`，不是 `published`。只有 `check_publish_done` 真实确认后才能进入 `published`。

编辑区必填项：标题、作者、正文。优先用 `wechat.fill_editor_required` 一次填完；若拆成单字段调用，必须显式调用 `fill_title`、`fill_author`、`paste_body` 三步，不能只填标题和正文。

发表前默认必填项：标题、作者、正文、封面、原创声明、合集、创作来源。发表动作会先执行 `wechat.publish_preflight` 门禁；缺项时返回 failed 和 missing 列表，不允许裸点发表。赞赏是可选项，账号支持且用户要求开启时才设置 `require_reward=true`。

保存草稿和发表都必须以微信底部「正文字数」为硬门禁：调用 `wechat.inspect_body_word_count` 可只读查看；计数为 0 时，保存草稿和发表原子会直接 failed，不点击按钮。

## 服务地址

本地：`http://127.0.0.1:8000`

健康检查：`GET /api/health`

**服务 auto-ensure**：不需要手动 start.bat。CLI 第一次执行时自动后台拉起服务，
持有持久 BrowserManager（微信浏览器跨命令保持）。`start.bat` 仅作人工运维用。

**CLI 入口必须使用项目虚拟环境**：

```powershell
.\.venv\Scripts\python.exe -m agent_news status
.\.venv\Scripts\python.exe -m agent_news run radar.status
```

不要用系统 `python -m agent_news ...` 作为生产入口。系统 Python 可能缺少 `feedparser` 等依赖，导致 RSS 探测失败或服务状态误判。

CLI、`start.bat`、`stop.bat` 共用 `runtime/logs/backend.start.lock` 和 `runtime/logs/backend.pid`。如果遇到服务状态异常，先执行 `stop.bat`，再重试 CLI 命令。

所有操作通过统一入口：
- 列出所有操作：`GET /api/operations`
- 单步执行：`POST /api/operations/{name}/execute`（每次执行写入 publish_tasks 审计）
- 批量执行：`POST /api/operations/batch`（带 `on_error: stop|continue|retry_once`）
- 会话状态：`POST wechat.session` → {current_url, is_editor_page, last_error, manager_alive, busy}

## 两条主链

### 链 A：信息雷达（状态 → 源治理 → 采集 → 热点 → 深挖 → 写作素材）

详细设计见 `docs/RADAR_DESIGN.md`；实施架构见 `docs/TECHNICAL_ARCHITECTURE.md`。雷达也坚持原子操作，不使用固定大脚本；状态观测、源健康、候选源验证、事件复核、深挖复核都已经注册成 `radar.*` 原子。

```
radar.status                  只读观测：源/raw/events/alerts/deep dives 数量和建议下一步
radar.review_sources          只读/可探测：源配置、健康、单源失败原因
radar.seed_defaults          初始化默认源（首次）
radar.discover_sources        外部候选 URL 规范化，不写库
radar.validate_source         验证候选源：可访问、可解析、去重、评分
radar.propose_source          生成添加建议，不写库
radar.add_validated_source    通过门禁后写入正式源
radar.sync_sources            采集所有/单个源 → raw_items，返回 source_results
radar.build_events            聚类 + 打分 + 物化 alerts → intel_events
radar.review_events           只读复核 Top events、推荐理由、风险、deep dive 参数
radar.deep_dive_event        抓全文、提取事实/引文/时间线 + 写作指南
radar.review_deep_dive       只读复核素材是否足够写文章，并返回 article_writing_guide
（你根据深挖结果自己写文章，存成 article）
```

读取入口：
- `GET /api/intel/events` — 事件列表（按 composite_score 排序，默认排除 ignored）
- `GET /api/intel/events/{id}` — 单事件详情
- `GET /api/intel/alerts` — 高分预警
- `GET /api/intel/events/{id}/deep-dive` — 深挖结果（含 article_writing_guide）

源治理规则：
- 外部 Agent 可以联网搜索候选 URL，但不能直接 `radar.add_source` 污染正式源池。
- 新源必须走 `discover_sources -> validate_source -> propose_source -> add_validated_source -> sync_one_source`。
- `score>=80` 可自动添加；`60-79` 必须 `confirmed=true`；`<60` 拒绝。
- 当前生产源池为 95 个内置默认源，已经随本项目分发，不需要外部项目参与。

文章桥接：
- `article.create/get/list/update` 是 Agent 保存成稿的统一操作面。
- 写文章前必须读取 `radar.review_deep_dive` 或 deep-dive 详情里的 `article_writing_guide`。这份规约已经内置在本项目，包含标题策略、短讯合集结构、禁用 AI 味词和事实纪律。
- 标题可以在 Agent 内部推演 2-3 个候选，但最终只能保存 1 个定稿标题到 `article.title`；不要把标题选择题抛给用户，也不要把多个候选写进文章正文。
- `article.review_quality` 是平台执行前的独立 Critique 原子；不通过时只允许修改文章或继续深挖，不能进入微信填写。
- `article.prepare_wechat_payload` 把文章转成微信填写参数，不打开浏览器，不自动发布。
- `article.prepare_wechat_payload` 会校验微信填写必填项和文章质量门禁。缺字段会返回 failed 和 `missing_required`；质量不过会返回 failed 和 `quality_report`。
- 默认质量门禁要求：单事件长文绑定 1 个 ready deep dive（至少 2 个成功来源、5 条事实）；5 条短讯合集通过 `material_id="dive-a,dive-b,dive-c,dive-d,dive-e"` 绑定至少 5 个 ready deep dive，每条至少 1 个成功来源和 1 条事实。
- `override_quality_gate=true` 只允许人工明确确认的例外，不可作为日常绕过素材不足的办法。
- 默认封面提示词必须是具象物品/场景类图片描述，例如芯片、手机、办公桌、实验台、合同和计算器，不生成文字海报类提示词。

### 链 B：微信发布（导航 → 编辑 → 设置 → 保存/发表）

```
wechat.open_dashboard        进公众号首页（只导航）
wechat.open_new_editor       进空白编辑页
wechat.fill_editor_required  一次填写标题、作者、正文（上传/保存/发布文章时优先用）
wechat.fill_title            填文章标题（标题栏）
wechat.fill_author           填作者
wechat.fill_digest           填摘要
wechat.paste_body            写入正文（会剥离开头文章标题；默认 Markdown -> 微信富文本 HTML）
── 发布前设置（全部可跳过，全部参数化）──
wechat.set_original(enabled=?)
wechat.set_reward(enabled=?)
wechat.set_collection(name=?)      ← 先调 list_collections 看有哪些
wechat.set_claim_source(name=?)    ← 先调 list_claim_sources 看有哪些
wechat.generate_ai_cover(prompt=?) ← 空则跳过
── 终点 ──
wechat.save_as_draft         存草稿
wechat.save_current_editor_as_draft  意图级：当前编辑页保存草稿
wechat.inspect_body_word_count 只读：读取底部正文字数；0 时保存/发表被拦截
wechat.review_draft_box      只读：草稿箱复核（可传标题）
# 或
wechat.publish_preflight     只读：发表前必填项校验
wechat.publish_to_qrcode     走发表到二维码（⚠️ 到二维码≠发表成功，需人工扫码）
wechat.publish_current_editor_to_qrcode  意图级：当前编辑页直接到二维码
wechat.publish_existing_draft_to_qrcode  意图级：打开已有草稿再到二维码
wechat.review_publish_history 只读：发表记录复核（可传标题）
wechat.analyze_publish_metrics 只读：发表记录全维度指标分析
```

## 关键规则

1. **发布前设置不是固定序列。** 你决定开哪些、跳哪些、用哪个合集名。先 `list_collections` / `list_claim_sources` 看选项，再 `set_*`。
2. **到二维码不是发表成功。** `publish_to_qrcode` 返回 `requires_human_scan=True` 时，必须人工扫码确认。绝不谎报发表成功。
3. **每步独立失败可重试。** 用 batch 的 `on_error=continue` 让失败步不影响后续；或单步重试。
4. **标题、作者、正文缺一不可。** 用户说“上传文章/保存草稿/发布文章”时，优先调用 `fill_editor_required`。如果为了重试拆成原子字段，也必须完整调用 `fill_title`、`fill_author`、`paste_body`。
5. **标题和正文必须分开。** `fill_title` 只写标题栏；`paste_body` 只写正文区。正文 Markdown 不要带文章标题；如果开头误带 `# 文章标题` 或首行等于当前标题，`paste_body` 会先剥离，再保留正文里的二级/三级小标题、段落、列表、引用、加粗、字号和行距。
6. **正文字数 0 不能保存/发表。** 微信底部 `js_word_count` 是硬门禁，不是建议值。`save_as_draft`、`save_current_editor_as_draft`、`click_publish`、`publish_to_qrcode` 会先检查它；为 0 时必须回到正文写入问题排查。
7. **写作是你（AI）的职责。** 深挖只给素材包 + 写作指南，不生成正文。你必须按 `article_writing_guide` 写成平台发布稿，再存成 article。
8. **不要假设登录态。** `open_dashboard` 只负责打开首页；随后必须调用 `check_login` 校验登录态。
9. **复核是只读动作。** 保存后用 `review_draft_box` 查草稿箱；人工扫码发布后用 `review_publish_history` 查发表记录。执行发表记录复核后，先问用户是否继续触发 `analyze_publish_metrics`，不要自动开始指标分析。

## 典型完整工作流（一天的活）

```
1. radar.status                                               看源池与运行数据
2. radar.sync_sources 或 radar.sync_one_source                  采集
3. radar.build_events clear_raw=false watchlist="ai,openai"    聚类
4. radar.review_events limit=10                                选热点
5. radar.deep_dive_event event_id=evt-xxx                      深挖
6. radar.review_deep_dive event_id=evt-xxx                     复核素材；读取 article_writing_guide；ready 才继续写
7. article.create                                               按写作指南写成 1 个标题 + 平台发布稿后保存
8. article.review_quality article_id=art-xxx                   Critique；通过后再进平台
9. article.prepare_wechat_payload                               生成微信填写参数
10. wechat.open_dashboard
11. wechat.check_login
12. wechat.open_new_editor
13. POST /api/operations/batch                                  一次填完+设置
   {
     "steps": [
       {"op":"wechat.fill_editor_required","params":{"title":"...","author":"...","body_markdown":"..."}},
       {"op":"wechat.fill_digest","params":{"text":"..."}},
       {"op":"wechat.set_original","params":{"enabled":true}},
       {"op":"wechat.set_reward","params":{"enabled":false}},
       {"op":"wechat.set_collection","params":{"name":"AI新闻"}},
       {"op":"wechat.set_claim_source","params":{"name":"个人观点，仅供参考"}}
     ],
     "on_error":"stop"
   }
14. wechat.generate_ai_cover prompt="一个iPhone图标"
15. wechat.inspect_body_word_count                            复核正文字数不为 0
16. wechat.save_current_editor_as_draft                       存草稿（或 publish_current_editor_to_qrcode）
17. wechat.review_draft_box title="..."                       复核草稿箱
18. wechat.review_publish_history title="..."                 人工扫码发布后复核发表记录
    → 返回 should_offer_metrics_analysis=true 时，先询问用户是否继续
19. wechat.analyze_publish_metrics title="..."                用户确认后做指标分析
```

## 试生产前检查

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m agent_news list
.\.venv\Scripts\python.exe -m agent_news status
.\.venv\Scripts\python.exe -m agent_news run radar.status
```

期望：

- 测试通过。
- `list` 返回 68 个操作。
- `status.server_running=true`。
- `radar.status` 至少返回 `source_count=95`。
- 新生产库首次运行时，`raw_item_count/event_count/alert_count/deep_dive_count` 可以为 0。

## 不变式

- 不创建第二套存储。所有数据在 SQLite（articles / intel_events / deep_dives / workflows）。
- 不直接改 SQLite 文件。所有写操作走 API。
- 不把"到达二维码"当发表成功。
- 不绕过发布前设置的参数化——合集/来源名由你决定，不写死。
