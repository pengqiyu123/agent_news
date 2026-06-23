# agent-news 框架补齐计划

## Summary

已只读检查 `D:\python\Auto-news2\agent-news`。项目已有 FastAPI、SQLite、操作注册表、信息雷达、微信浏览器管理器雏形，不需要重写；当前主要问题是“文档宣称的 Agent 原子操作能力”与实际代码未完全闭合。

本轮实现目标：把 `agent-news` 收口成无前端、Agent 工具驱动的本地服务。唯一主入口是 FastAPI Tool Server，CLI 作为薄客户端调用它；微信浏览器由常驻服务里的 `BrowserManager` 单例持有，保证又快又准复用登录态和当前编辑页。

## Key Changes

- 修复微信原子操作注册与导入断点：
  - `wechat.__init__` 导入 `navigation / drafts / editor / publish_settings / cover / save_publish`。
  - 补齐缺失的 `browser.nav`、`browser.profiles` 兼容模块，或把引用统一改为现有 `browser.dom/selectors`。
  - 修复 `save_publish.py` 截断语法（open_preview 已移除，不再实现）。
  - 统一 `OperationResult` 写法，所有状态都放进 `state`，截图同时进入 `artifacts`。

- 明确运行时架构：
  - 保留 `start.bat`/FastAPI 常驻服务为主运行方式。
  - CLI 的 `list/run/dashboard/status` 默认请求 `http://127.0.0.1:8000`，不再启动一次性浏览器。
  - 移除或降级现有缺失的 `daemon.py` 依赖；如保留 `daemon` 命令，仅作为服务状态/启动脚本兼容入口，不另起第二套浏览器 sidecar。

- 强化浏览器实时状态与微信打开路径：
  - 新增或统一 `wechat.session` / `/api/browser/wechat/status` 返回：`manager_alive`、`busy`、`current_url`、`resident_page`、`is_editor_page`、`logged_in`、`last_reset_reason`、`last_error`。
  - `wechat.open_dashboard(wait_login=false)` 负责打开公众号后台并快速判断登录态；未登录时返回二维码截图路径和 `requires_login_scan=true`。
  - `with_session` 兼容 `with_session(action_fn)` 与 `with_session(channel, action_fn=...)`，并继续保持单标签页、优先复用 `action=edit` 编辑页。

- 补齐 Agent 工作流契约：
  - `POST /api/operations/{name}/execute` 和 batch 增加可选 `article_id`、`workflow_session_id`。
  - 路由层记录每次微信操作到 `publish_tasks`，注册表保持纯执行。
  - 工作流新增 `pending_confirmation` 状态：`publish_to_qrcode` 只能进入该状态，不能标记 `published`。
  - 只有未来的真实平台确认操作才能把状态改为 `published`。
  - 文章创建不强制 `event_id`；支持 `primary_event_id` 和 `included_event_ids`，解决多事件短讯合集不知道绑定哪个事件的问题。

- 更新文档给其他 AI 使用：
  - `README.md` 写清“无前端、FastAPI Tool Server、CLI 薄客户端”。
  - `AGENT.md` 按真实顺序写：启动服务 → 查状态 → 信息雷达 → 写文章 → 创建工作流 → 打开微信 → 单步填充 → 发布设置 → 封面 → 草稿或二维码。
  - `docs/ATOMIC_OPERATIONS.md` 由实际注册操作生成或人工同步，避免文档列出但代码未注册。

## Test Plan

- 第一阶段只测注册和导入：
  - `python -m agent_news list` 必须列出 radar + 全部 wechat 操作。
  - `pytest tests/test_wechat_operations.py tests/test_publish_settings.py -q` 先通过注册、skip、无浏览器 graceful failure。

- 第二阶段测服务与 CLI：
  - `start.bat` 后 `GET /api/health` 返回 ok。
  - `python -m agent_news status` 能返回浏览器状态。
  - `python -m agent_news run wechat.session` 不启动第二套浏览器。

- 第三阶段测工作流与审计：
  - 执行单步和 batch 后，`publish_tasks` 产生记录。
  - `publish_to_qrcode` 成功时 workflow 进入 `pending_confirmation`，不能进入 `published`。
  - 非法状态转换返回 422。

- 第四阶段可选真实微信验证：
  - 启动服务。
  - `wechat.open_dashboard` 打开 Edge 持久化 profile。
  - `wechat.list_drafts` 读取真实草稿。
  - `wechat.open_existing_draft(title=...)` 必须到达 `action=edit`。
  - 后续仅走到二维码，停止等待人工扫码。

## Assumptions

- 不做前端。
- 不删除 `data/`、`runtime/`、浏览器 profile 或现有真实微信登录态。
- 不把“到达二维码”称为发表成功。
- 以现有 `agent-news` 骨架为基础补齐，不迁移回 `auto-news-studio` 的固定流水线。
