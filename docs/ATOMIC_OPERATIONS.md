# 原子操作清单

agent-news 的每一个能力都是一个**原子操作**——可独立调用、独立失败、独立重试。
完整清单可通过 `GET /api/operations` 实时查询。本文件是人工维护的参考索引。

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

每个操作返回 `OperationResult`：
- `status`: `ok` | `skipped` | `failed`
- `message`: 人类可读说明
- `state`: 该步观测/变更的状态快照
- `ok` (computed): status != failed

**失败永不异常外溢**——一个操作失败只返回 failed，不影响其他操作或 batch。

---

## 信息雷达（radar.*）

| 操作 | 参数 | 说明 |
|---|---|---|
| `radar.seed_defaults` | — | 初始化默认源（仅当无源时生效，幂等） |
| `radar.add_source` | `key`, `source_name`, `url`, `kind?`, `tags?`, `priority?` | 添加信息源 |
| `radar.remove_source` | `source_key` | 删除信息源 |
| `radar.sync_sources` | `source_key?` | 采集所有/单个源 → raw_items |
| `radar.sync_one_source` | `source_key` | 采集单个源 |
| `radar.build_events` | `merge_threshold?`, `alert_threshold?`, `watchlist?`, `clear_raw?` | 聚类+打分+物化 alerts |
| `radar.deep_dive_event` | `event_id`, `max_sources?`, `force?` | 抓全文、提取素材、附写作指南 |

读取端点（非操作）：
- `GET /api/intel/sources` / `GET /api/intel/sources/{key}`
- `GET /api/intel/raw-items`
- `GET /api/intel/events` （支持 `ignored`, `min_score` 过滤）
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

**全部参数化、全部可跳过** —— 这是相对旧项目的核心改进。

| 操作 | 参数 | 跳过条件 | 说明 |
|---|---|---|---|
| `wechat.set_original` | `enabled` (默认 True) | `enabled=False` | 原创声明 |
| `wechat.set_reward` | `enabled` (默认 True) | `enabled=False` | 赞赏 |
| `wechat.set_collection` | `name` | `name=""` | 合集（**任意名称**，不写死） |
| `wechat.set_claim_source` | `name` | `name=""` | 创作来源（**任意名称**） |
| `wechat.generate_ai_cover` | `prompt`, `wait_seconds?` | `prompt=""` | AI 封面 |
| `wechat.list_collections` | — | — | 只读：列出可选合集 |
| `wechat.list_claim_sources` | — | — | 只读：列出可选创作来源 |

**推荐流程**：先 `list_collections` 看有哪些合集 → 再 `set_collection(name=选中的)`。创作来源同理。`set_collection` / `set_claim_source` 必须回读命中目标文本才算成功，单纯点击不算。

---

## 微信保存/发表（category=save_publish）

| 操作 | 参数 | 说明 |
|---|---|---|
| `wechat.save_as_draft` | — | 存草稿箱 |
| `wechat.save_current_editor_as_draft` | — | 意图级动作 1：当前编辑页直接保存草稿箱 |
| `wechat.publish_preflight` | `require_*?` | 只读：发表前必填项校验。默认检查标题、作者、正文、封面、原创声明、合集、创作来源；赞赏默认不硬卡 |
| `wechat.click_publish` | — | 发表 step 1：点击「发表」 |
| `wechat.confirm_publish_modal` | — | 发表 step 2：二次确认 |
| `wechat.continue_publish` | `max_clicks?` | 发表 step 3：循环点击「继续发表」 |
| `wechat.wait_qrcode` | `max_checks?`, `retry_wait_ms?` | 发表 step 4：轮询二维码，出现即截图 |
| `wechat.publish_to_qrcode` | `max_continue_clicks?` | 完整发表流程（1+2+3+4），到二维码停止 |
| `wechat.publish_current_editor_to_qrcode` | `max_continue_clicks?` | 意图级动作 2：当前编辑页直接走到二维码 |
| `wechat.publish_existing_draft_to_qrcode` | `title`, `max_continue_clicks?` | 意图级动作 3：按标题打开已有草稿编辑页，再走到二维码 |
| `wechat.check_publish_done` | — | 检测是否回到首页（发表成功标志） |

**⚠️ `publish_to_qrcode` / `wait_qrcode` 返回 `requires_human_scan=True` 时，发表未完成。** 必须人工扫码确认。工作流状态进入 `pending_confirmation`，只有 `check_publish_done` 确认回到首页后才转 `published`。

发表动作默认从严：必须先通过 `wechat.publish_preflight`。缺标题、作者、正文、封面、原创声明、合集、创作来源任意一项时，`publish_to_qrcode` / `publish_current_editor_to_qrcode` / `publish_existing_draft_to_qrcode` 会直接返回 failed，不点击发表按钮。赞赏默认不硬卡；账号支持且用户要求开启时，显式传 `require_reward=true`。

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
| `wechat.analyze_publish_metrics` | `title?`, `limit?`, `max_pages?` | 基于发表记录提取全维度数据指标：阅读、点赞、分享、推荐、留言、划线、赞赏、转载 |

使用规则：

- `review_draft_box` 只确认草稿箱远端状态，不打开编辑器、不修改内容。
- `review_publish_history` 只确认发表记录远端状态；返回 `should_offer_metrics_analysis=true` 时，Agent 应主动询问用户是否继续触发 `wechat.analyze_publish_metrics`。
- `analyze_publish_metrics` 是数据闭环动作，用于量化稿件质量、受众喜爱程度和传播性。它不等于发布确认，也不会修改文章。

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

## 文章管理（REST，非操作）

| 端点 | 说明 |
|---|---|
| `POST /api/articles` | 创建文章 `{title, digest, body_markdown, author, material_id?}` |
| `GET /api/articles` | 列表（`page`, `page_size`） |
| `GET /api/articles/{id}` | 详情 |
| `PUT /api/articles/{id}` | 更新 |
| `DELETE /api/articles/{id}` | 删除 |

## 工作流（REST，非操作）

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
