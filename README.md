# agent-news

> 完全智能体控制的新闻发布架构。把微信公众号发布的每一步拆成 **AI 可挑选执行的原子步骤**。

## 为什么有这个项目

传统自动化常把发布流程写成固定脚本流水线——一步错，全盘崩。这不符合 AI agent 应用的范畴，也不具备灵活性与 AI 可操控性。

`agent-news` 的核心理念：

- **每个发布动作都是独立单元**（原创声明、赞赏、合集、创作来源、AI 封面、保存草稿、发布……）
- **AI 自由组合**：跳过任意步骤、改顺序、重试单步，不连锁失败
- **所有硬编码参数化**：合集名、创作来源不再写死，AI 自己识别选择
- **去前端化**：纯后端 + API + 浏览器爬虫，所有交互面向 AI

## 架构（五层）

```
Layer 5  Agent 编排层     ReAct/Reflexion loop、MCP server、CLI
Layer 4  原子操作注册表    ★ 核心创新：每步独立、可单独调用、带前后置校验
Layer 3  浏览器会话层      Playwright 持久化 context + with_session 闭包
Layer 2  状态层            SQLite + Pydantic 模型
Layer 1  接口层            FastAPI（每个原子操作 = 一个 endpoint）
```

对比固定流水线：发布设置不再被串成无参数大步骤；
本项目 **每一步都是独立 endpoint**，AI 决定调用顺序和是否跳过。

## 快速开始

### 环境要求

- Python 3.11+
- 可用的 Chromium / Edge 浏览器环境（用于 Playwright）

### 安装

```bash
install.bat
```

安装后建议使用项目虚拟环境运行 CLI，避免系统 Python 缺少依赖：

```powershell
.\.venv\Scripts\python.exe -m agent_news status
```

### 运行（两种方式）

**方式一：CLI 直接用（推荐，agent 友好）**

不需要手动启动服务。CLI 第一次执行时会**自动后台拉起** FastAPI 服务
（持有持久浏览器），后续命令复用同一服务实例：

```powershell
.\.venv\Scripts\python.exe -m agent_news status
.\.venv\Scripts\python.exe -m agent_news list
.\.venv\Scripts\python.exe -m agent_news dashboard
.\.venv\Scripts\python.exe -m agent_news run wechat.session
.\.venv\Scripts\python.exe -m agent_news run radar.status
```

**方式二：手动启动服务（人工运维）**

```bash
start.bat    # 启动常驻服务（等价于 CLI 自动拉起）
stop.bat     # 停止
doctor.bat   # 自检
```

启动后访问 `http://127.0.0.1:8000/api/health` 验证。

> 服务是常驻进程，持有 BrowserManager 单例 + Playwright worker 线程，
> 所以微信浏览器跨命令持久。CLI auto-ensure 会在服务没起时自动拉起，
> agent 工作流不需要显式 start。CLI 和启动脚本共用 `runtime/logs/backend.start.lock`
> 与 `runtime/logs/backend.pid`，避免重复后端进程。

### 停止

```bash
stop.bat
```

### 自检

```bash
doctor.bat
```

## 核心概念：原子操作

所有微信发布动作都是注册表里的独立操作。AI 有三种调用方式：

```bash
# 1. 单步执行（精细控制）
POST /api/operations/wechat.set_collection/execute
{"name": "AI新闻"}

# 2. 批量按序执行（带跳过和重试策略）
POST /api/operations/batch
{"steps": [...], "on_error": "stop|continue|retry_once"}

# 3. 先探查再决策（AI 自己识别）
POST /api/operations/wechat.list_collections/execute
→ {"items": ["AI新闻", "科技前沿", ...]}
```

完整操作清单见 `docs/ATOMIC_OPERATIONS.md`。

## 当前投产状态

当前代码已进入试生产状态：

- 操作注册表：68 个原子操作
- 默认信息源：95 个，随本项目内置分发
- 生产库初始状态：保留 `sources=95`，运行数据表清空
- 写作规约：`radar.review_deep_dive` / deep-dive 详情返回项目内置的 `article_writing_guide`，Agent 必须按它生成唯一标题和平台发布稿
- 文章质量门禁：微信 payload 前必须通过 `article.review_quality`，防止素材不足或短稿直接进草稿箱
- 5 条短讯合集要先绑定 5 个 ready deep dive，再进入平台稿；不要用 `override_quality_gate` 当日常绕过手段
- 微信发布边界：到二维码只代表 `pending_confirmation`，必须人工扫码，不能视为发布成功
- 推荐试生产路径：先跑少量源，再保存微信草稿，确认无误后再进入二维码发布链

试生产第一条链路建议：

```powershell
.\.venv\Scripts\python.exe -m agent_news run radar.status
.\.venv\Scripts\python.exe -m agent_news run radar.sync_one_source source_key=hn-frontpage
.\.venv\Scripts\python.exe -m agent_news run radar.build_events clear_raw=false
.\.venv\Scripts\python.exe -m agent_news run radar.review_events limit=5
```

## 文档

- [docs/ATOMIC_OPERATIONS.md](./docs/ATOMIC_OPERATIONS.md) — 所有原子操作清单
- [docs/RADAR_DESIGN.md](./docs/RADAR_DESIGN.md) — 信息雷达设计与使用规则
- [docs/TECHNICAL_ARCHITECTURE.md](./docs/TECHNICAL_ARCHITECTURE.md) — 技术架构、文件夹与可行性
- [AGENT.md](./AGENT.md) — 给 AI 的操作手册

## 分发边界

`agent-news` 是自包含项目。生产部署、交付给其他 AI、复制到新机器时，只需要本项目目录和安装依赖，不需要旁边存在任何历史项目。

微信选择器、富文本写入、浏览器会话、信息源和写作规约都必须维护在本项目内；如果后续发现缺口，也应在 `agent-news` 内补齐并测试。

## 许可证

[MIT](./LICENSE)
