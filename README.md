# agent-news

> 完全智能体控制的新闻发布架构。把微信公众号发布的每一步拆成 **AI 可挑选执行的原子步骤**。

## 为什么有这个项目

传统自动化（包括姊妹项目 `auto-news-studio`）把发布流程写成固定脚本流水线——一步错，全盘崩。这不符合 AI agent 应用的范畴，也不具备灵活性与 AI 可操控性。

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

对比旧项目：`_apply_wechat_publish_settings` 把 4 步串成固定序列无参数；
新项目 **每一步都是独立 endpoint**，AI 决定调用顺序和是否跳过。

## 快速开始

### 环境要求

- Python 3.11+
- 可用的 Chromium / Edge 浏览器环境（用于 Playwright）

### 安装

```bash
install.bat
```

### 运行（两种方式）

**方式一：CLI 直接用（推荐，agent 友好）**

不需要手动启动服务。CLI 第一次执行时会**自动后台拉起** FastAPI 服务
（持有持久浏览器），后续命令复用同一服务实例：

```bash
python -m agent_news status              # 探测服务状态
python -m agent_news list                # 列出所有操作
python -m agent_news dashboard           # 打开公众号 + 检测登录
python -m agent_news run wechat.session  # 查看浏览器会话状态
python -m agent_news run radar.sync_sources  # 采集
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
> agent 工作流不需要显式 start。

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

## 文档

- [docs/ARCHITECTURE.md](./docs/ARCHITECTURE.md) — 五层架构详解
- [docs/ATOMIC_OPERATIONS.md](./docs/ATOMIC_OPERATIONS.md) — 所有原子操作清单
- [AGENT.md](./AGENT.md) — 给 AI 的操作手册

## 与 auto-news-studio 的关系

`auto-news-studio`（姊妹项目）是"已完结的固定脚本流水线"，针对个人使用习惯开发。
`agent-news` 是全新项目，把它从"固定流水线"重构为"原子操作注册表"，面向 AI agent 应用。

旧项目中已验证可行的代码（浏览器选择器、富文本写入策略、with_session 闭包）会被逐文件迁移并改造进新架构。

## 许可证

[MIT](./LICENSE)
