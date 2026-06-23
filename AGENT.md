# AGENT.md — agent-news 操作手册

这份文档是给外部 AI agent 的操作手册。读完它，你就能完全控制 agent-news：从信息采集、热点发现、深挖、写作，到微信草稿上传、发布前设置、最终发表。

## 铁律（不可违反）

**旧项目 `D:\python\Auto-news2\auto-news-studio` 已经成功跑通所有微信操作。直接拿来用，不要自己写代码、不要猜 DOM 结构、不要搜索前端 HTML。**

具体来说：
- 微信页面选择器、点击逻辑、导航流程：**照搬旧项目** `backend/app/publishers/wechat/` + `browser_base.py`，不要自己发明。
- 遇到微信 DOM 相关问题：**去旧项目读对应代码**，不要用截图分析、不要写 JS 探测、不要搜索网页结构。
- 唯一允许的创新是把旧项目的固定脚本拆成原子操作 + 参数化，但底层逻辑必须照搬。

## 核心理念

**agent-news 是原子操作系统，不是固定流水线。** 旧项目把发布流程写成固定脚本；这里每个操作都是独立原子，Agent 按用户目标自由编排，跳过任意步骤、改顺序、重试单步。

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

## 服务地址

本地：`http://127.0.0.1:8000`

健康检查：`GET /api/health`

**服务 auto-ensure**：不需要手动 start.bat。CLI 第一次执行时自动后台拉起服务，
持有持久 BrowserManager（微信浏览器跨命令保持）。`start.bat` 仅作人工运维用。

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
radar.review_deep_dive       只读复核素材是否足够写文章
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

文章桥接：
- `article.create/get/list/update` 是 Agent 保存成稿的统一操作面。
- `article.prepare_wechat_payload` 把文章转成微信填写参数，不打开浏览器，不自动发布。
- `article.prepare_wechat_payload` 会校验微信填写必填项：标题、作者、正文。缺字段会返回 failed 和 `missing_required`，先用 `article.update` 补齐。
- 默认封面提示词必须是物品/场景类图片描述，不生成文字海报类提示词。

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
6. **写作是你（AI）的职责。** 深挖只给素材包 + 写作指南，不生成正文。你写完存成 article。
7. **不要假设登录态。** `open_dashboard` 只负责打开首页；随后必须调用 `check_login` 校验登录态。
8. **复核是只读动作。** 保存后用 `review_draft_box` 查草稿箱；人工扫码发布后用 `review_publish_history` 查发表记录。执行发表记录复核后，先问用户是否继续触发 `analyze_publish_metrics`，不要自动开始指标分析。

## 典型完整工作流（一天的活）

```
1. POST /api/operations/radar.sync_sources/execute          采集
2. POST /api/operations/radar.build_events/execute          {"watchlist":"ai,openai"}
3. GET  /api/intel/events                                    选热点
4. POST /api/operations/radar.deep_dive_event/execute       {"event_id":"evt-xxx"}
5. GET  /api/intel/events/evt-xxx/deep-dive                  读素材 + 写作指南
6. （你写文章，POST /api/articles 存成 article）
7. POST /api/operations/wechat.open_dashboard/execute
8. POST /api/operations/wechat.open_new_editor/execute
9. POST /api/operations/batch                                 一次填完+设置
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
10. POST /api/operations/wechat.generate_ai_cover/execute   {"prompt":"一个iPhone图标"}
11. POST /api/operations/wechat.save_as_draft/execute       存草稿（或 publish_to_qrcode 发表）
12. POST /api/operations/wechat.review_draft_box/execute    {"title":"..."}   复核草稿箱
13. POST /api/operations/wechat.review_publish_history/execute {"title":"..."} 复核发表记录
    → 返回 should_offer_metrics_analysis=true 时，先询问用户是否继续
14. POST /api/operations/wechat.analyze_publish_metrics/execute {"title":"..."} 指标分析
```

## 不变式

- 不创建第二套存储。所有数据在 SQLite（articles / intel_events / deep_dives / workflows）。
- 不直接改 SQLite 文件。所有写操作走 API。
- 不把"到达二维码"当发表成功。
- 不绕过发布前设置的参数化——合集/来源名由你决定，不写死。
