# SAO 后续实施计划 (Phase 5-10)

> 更新时间：2026-03-06
> 当前进度：Phase 0-4 已完成

---

## 已完成回顾（Phase 0-4）

- ✅ **Phase 0**：项目脚手架（pyproject.toml、目录结构、配置系统）
- ✅ **Phase 1**：飞书 WebSocket 长连接对话
- ✅ **Phase 2**：多模型适配（通义千问/DeepSeek/豆包）+ 自动降级
- ✅ **Phase 3**：技能框架（BaseSkill ABC + pkgutil 自动发现）
- ✅ **Phase 4**：提醒技能 v0.4（设置/查看/修改/取消 + Bitable 存储 + APScheduler）

---

## Phase 5：斜杠命令 + Session 管理 + 记忆系统

**目标**：省 token、提升操控感（借鉴 OpenClaw）、结构化记忆管理

**预估工时**：5-6h | **优先级**：🔴 立即

### 5.1 斜杠命令

| 命令 | 功能 |
|---|---|
| `/status` | 显示当前模型、已加载技能数、scheduler 状态、记忆统计 |
| `/skills` | 列出所有已安装技能及版本 |
| `/new` | 重置当前对话历史 |
| `/compact` | 用 LLM 总结历史 → 压缩 context |
| `/doctor` | 检查：模型连通性、飞书 token、Bitable 权限、scheduler |
| `/model <name>` | 切换主模型（qwen/deepseek/doubao） |
| `/memory` | 查看长期记忆摘要 |
| `/help` | 显示所有可用命令 |

**技术方案**：在 `Agent.process()` 入口加前缀解析，`/` 开头直接执行，不走 LLM 路由。

### 5.2 记忆系统

记忆作为**独立模块** `app/core/memory/` 管理，代码和数据分离、分层存储。

#### 代码结构

```
app/core/memory/
├── __init__.py          # 公共 API：save_message, get_history, compact, remember, recall
├── store.py             # 对话历史持久化（SQLite）
├── compactor.py         # LLM 总结压缩 context
├── long_term.py         # 长期记忆管理（MEMORY.md 读写 + 向量搜索预留）
└── models.py            # 数据模型（Message, Session, MemoryEntry）
```

#### 数据结构

```
data/
├── memory.db            # SQLite — 对话历史 + 会话元数据 + 记忆索引
└── memory/
    ├── MEMORY.md        # 长期记忆（用户偏好、关键决定、持久事实）
    └── daily/
        └── YYYY-MM-DD.md  # 每日摘要（Agent 自动追加）
```

#### 数据模型 (`models.py`)

```python
@dataclass
class Message:
    id: str               # UUID
    session_id: str       # 会话标识（open_id）
    role: str             # "user" | "assistant" | "system"
    content: str
    created_at: datetime
    metadata: dict        # 额外信息（skill_name, action 等）

@dataclass
class Session:
    id: str               # open_id
    created_at: datetime
    last_active: datetime
    message_count: int
    compact_summary: str | None   # /compact 生成的摘要
    model: str            # 当前使用的模型

@dataclass
class MemoryEntry:
    id: str
    content: str          # 记忆内容
    category: str         # "preference" | "fact" | "decision" | "context"
    source: str           # "user_explicit" | "agent_inferred" | "compact"
    created_at: datetime
    expires_at: datetime | None  # 可选过期时间
```

#### SQLite 表设计 (`store.py`)

```sql
-- 对话消息
CREATE TABLE IF NOT EXISTS messages (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    metadata TEXT  -- JSON
);
CREATE INDEX IF NOT EXISTS idx_messages_session ON messages(session_id, created_at);

-- 会话元数据
CREATE TABLE IF NOT EXISTS sessions (
    id TEXT PRIMARY KEY,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    last_active TIMESTAMP,
    message_count INTEGER DEFAULT 0,
    compact_summary TEXT,
    model TEXT
);

-- 长期记忆索引（MEMORY.md 为人类可读源文件，此表为结构化索引）
CREATE TABLE IF NOT EXISTS memory_entries (
    id TEXT PRIMARY KEY,
    content TEXT NOT NULL,
    category TEXT NOT NULL,  -- preference / fact / decision / context
    source TEXT NOT NULL,    -- user_explicit / agent_inferred / compact
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    expires_at TIMESTAMP
);
```

#### 公共 API (`__init__.py`)

```python
# 对话历史
async def save_message(session_id, role, content, metadata=None) -> Message
async def get_history(session_id, limit=20) -> list[Message]
async def clear_history(session_id) -> None

# Context 压缩
async def compact_session(session_id, factory) -> str  # 返回摘要

# 长期记忆
async def remember(content, category, source) -> MemoryEntry  # 写入 MEMORY.md + SQLite
async def recall(query, limit=5) -> list[MemoryEntry]         # 当前：关键词匹配；远期：向量搜索
async def get_memory_stats() -> dict                           # 统计数据

# 生命周期
async def init_memory() -> None   # 建表 + 创建目录
```

#### 与 Agent 集成

```python
# agent.py 改造
class Agent:
    async def process(self, user_message, chat_id):
        # 1. 斜杠命令拦截
        if user_message.startswith("/"):
            return await self._handle_command(user_message, chat_id)

        # 2. 保存用户消息到 SQLite
        await memory.save_message(chat_id, "user", user_message)

        # 3. 获取历史（从 SQLite 而非内存 dict）
        history = await memory.get_history(chat_id, limit=20)

        # 4. 获取长期记忆作为 system context
        relevant = await memory.recall(user_message, limit=3)

        # 5. 意图路由 + 技能/对话（带 history + relevant memory）

        # 6. 保存 AI 回复到 SQLite
        await memory.save_message(chat_id, "assistant", reply)

        # 7. Agent 自主判断是否需要 remember
        return reply
```

#### 记忆写入时机

| 时机 | 行为 |
|---|---|
| 用户明确说"记住xxx" | Agent 调用 `remember()` 写入 |
| `/compact` | 摘要存入 `sessions.compact_summary`，重要事实提取到 MEMORY.md |
| 对话接近 context 上限 | 自动触发 compact，先 flush 重要信息到 MEMORY.md |
| 技能执行后 | 关键结果存入日记 `daily/YYYY-MM-DD.md` |

#### 远期扩展预留

- **向量搜索**：`recall()` 当前用关键词匹配，后续可接入 embedding（SQLite-vec 或远程 API）
- **MEMORY.md 同步 Bitable**：结构化记忆双写，飞书端也能查看/编辑
- **记忆过期清理**：`expires_at` 字段 + APScheduler 定时清理
- **跨设备同步**：SQLite → 远程数据库迁移路径预留

---

## Phase 6：技能市场 — 搜索 + 安装已有技能

**目标**：通过飞书对话，让 Agent 搜索网上已有的技能包并安装

**预估工时**：4-6h | **优先级**：🟠 紧接

### 用户交互流程

```
用户："帮我装一个天气查询的技能"
  ↓
Agent → 搜索技能源（GitHub/PyPI/SAO Registry）
  ↓
Agent → "找到 3 个匹配的技能包：
  1. sao-skill-weather v1.2 ⭐23 — 支持全球天气查询
  2. sao-skill-weather-cn v0.8 — 中国城市天气
  3. sao-skill-aqi v0.5 — 空气质量指数"
  ↓
用户："装第一个"
  ↓
Agent → pip install + 复制到 skills/ → 飞书卡片确认
  ↓
用户点「确认安装」→ 热加载激活
```

### 技术方案

- **技能包规范**：定义 `sao-skill-*` 的 PyPI 命名约定，每个包必须包含 `BaseSkill` 子类
- **搜索源**：先支持 PyPI 搜索（PyPI API），后续加 GitHub topic 搜索
- **安装隔离**：`pip install --target=app/skills/_vendor/` 到独立目录
- **飞书卡片确认**：安装前发送交互卡片，用户点确认后才激活（依赖 Phase 8）
- **热加载**：`importlib.import_module()` + 注册到 `_registry`，无需重启

---

## Phase 7：技能自进化 — Agent 自己写代码生成新技能

**目标**：用户描述需求 → Agent 利用 Copilot/Gemini 订阅写代码 → 测试 → 部署

**预估工时**：6-8h | **优先级**：🟠 紧接（核心差异化功能）

### 用户交互流程

```
用户："帮我写个技能，每天获取 A 股股息率前十"
  ↓
Agent 分析：现有技能无法满足 → 启动「技能开发」模式
  ↓
Agent → 生成需求描述 + 技能骨架
  ↓
调用 IDE Adapter（可配置）:
  · VS Code:     code chat -m agent "根据以下需求创建SAO技能..."
  · Copilot CLI:  copilot -p "..." --allow-all-tools
  · Cursor:       cursor 打开项目 + MCP 调用
  · 内置 LLM:    直接用 qwen/deepseek 生成（无需 IDE）
  ↓
代码写入 app/skills/<new_skill>/
  ↓
Subprocess 沙箱测试（AST 黑名单 + 超时 + 内存限制）
  ↓
飞书卡片：展示代码 + 测试结果 + 【部署】【拒绝】按钮
  ↓
用户点「部署」→ 热加载激活
```

### IDE Adapter 架构（可配置选择）

```python
# app/core/ide_adapters/base.py
class BaseIDEAdapter(ABC):
    async def generate_code(self, prompt: str, context: dict) -> str: ...

# app/core/ide_adapters/vscode.py      — 调用 code chat
# app/core/ide_adapters/copilot_cli.py — 调用 copilot -p
# app/core/ide_adapters/builtin_llm.py — 直接用自带模型生成
```

`.env` 配置：
```
IDE_ADAPTER=copilot_cli   # 或 vscode / builtin_llm
```

### 沙箱方案（subprocess，当前 conda 环境非 Docker）

- `subprocess.run()` 在新进程执行，`timeout=30s`
- AST 静态检查：禁止 `os.remove`、`subprocess.call`、`shutil.rmtree` 等危险 API
- 测试通过 = 导入成功 + `manifest` 存在 + `run()` 不抛异常

---

## Phase 8：飞书交互卡片（HITL 确认系统）

**目标**：高风险操作统一走飞书卡片确认

**预估工时**：3-4h | **优先级**：🔴 立即（Phase 6/7 的前置依赖）

### 覆盖场景

- 安装新技能 → 卡片展示技能信息 + 确认按钮
- Agent 生成新代码 → 卡片展示代码 + 测试结果 + 部署/拒绝
- 未来：定时任务创建、敏感操作（发帖、转账等）

### 技术方案

- 使用飞书消息卡片（Interactive Card）的 Action 回调
- WS 模式下通过 card action handler 接收用户点击事件
- 内部维护 `pending_actions` 队列：`action_id → callback`

---

## Phase 9：定时任务 + 周期性技能

**目标**：技能可以按 cron 表达式定时执行

**预估工时**：2-3h | **优先级**：🟡 后续

### 方案

- `SkillManifest.schedule` 已预留字段
- APScheduler CronTrigger 执行，结果推送飞书
- 用户可通过对话管理："每天早上9点运行股息监控"

---

## Phase 10：SAO 作为 MCP Server

**目标**：让 VS Code/Cursor/Claude Code 里也能直接调用 SAO 的技能

**预估工时**：4-6h | **优先级**：🟢 远期

### 方案

- SAO 暴露 MCP Server（stdio 或 HTTP）
- 技能自动映射为 MCP Tools
- 在 IDE 里说 "帮我设个提醒" → Copilot 调用 SAO 的 reminder tool
- 双向打通：飞书对话 ↔ IDE 里的 Copilot
- 使用 MCP Python SDK：`pip install mcp`

---

## 执行优先级总览

| 优先级 | Phase | 预估工时 | 核心价值 |
|---|---|---|---|
| 🔴 立即 | **5：斜杠命令 + 记忆系统** | 5-6h | 省 token + 操控感 + 持久记忆 |
| 🔴 立即 | **8：飞书卡片确认** | 3-4h | Phase 6/7 的前置依赖 |
| 🟠 紧接 | **6：技能市场搜索安装** | 4-6h | 快速扩展能力 |
| 🟠 紧接 | **7：技能自进化** | 6-8h | 核心差异化功能 |
| 🟡 后续 | **9：定时任务** | 2-3h | 复用已有 APScheduler |
| 🟢 远期 | **10：MCP Server** | 4-6h | 生态打通 |

### 推荐执行顺序

```
Phase 5（斜杠命令）→ Phase 8（飞书卡片）→ Phase 6（技能市场）→ Phase 7（自进化）→ Phase 9（定时任务）→ Phase 10（MCP）
```

---

## 借鉴 OpenClaw 的设计点

| 来源 | 借鉴内容 | 对应 Phase |
|---|---|---|
| Chat Commands | 斜杆命令：`/status` `/new` `/compact` `/doctor` `/memory` | Phase 5 |
| Doctor 命令 | 自检：模型连通性、token 有效性、权限检查 | Phase 5 |
| Session 模型 | 对话持久化 + `/compact` 压缩上下文 | Phase 5 |
| Memory 系统 | 3 层记忆：对话历史 + MEMORY.md + 向量搜索（预留） | Phase 5 |
| SKILL.md 声明式 | 技能包含自己的 prompt 模板（`prompts.py` 已在做） | 已有 |
| Sandbox per-session | 每次 Agent 生成新代码在隔离环境测试 | Phase 7 |
| 多 Agent 路由 | Agent 间通信协调（研究+代码协作） | 远期 |
| ClawHub 注册中心 | 技能市场搜索、安装、管理 | Phase 6 |
