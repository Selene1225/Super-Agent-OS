# Super-Agent-OS (SAO)

> 个人 AI 助理框架 —— 飞书 ChatOps + 多模型 LLM + 可扩展技能系统

Super-Agent-OS 是一个运行在本地或服务器上的个人 AI 助理，通过**飞书机器人**与你对话，底层支持多模型自动降级，并提供可插拔的技能框架，让 AI 不只是聊天，还能帮你**设提醒、查信息、自动化操作**。

---

## ✨ 已实现功能

| 功能 | 说明 |
|------|------|
| 飞书 WebSocket 长连接 | 无需公网 IP / 隧道，开箱即用 |
| 多模型支持 | 通义千问 (Qwen)、DeepSeek、豆包 (Doubao)，OpenAI 兼容协议 |
| 自动降级 | 主模型 429/超时/错误时自动切换备选模型，最多重试 2 次 |
| LLM 意图路由 | 基于大模型理解用户意图，自动分发到对应技能或普通对话 |
| 技能框架 | 可插拔的 Skill 系统，自动发现注册，支持 manifest 声明 |
| 提醒技能 | 自然语言设置提醒 → 存储到飞书多维表格 → APScheduler 定时推送 |
| 对话记忆 | per-chat 对话历史（最近 20 轮） |

## 🏗️ 架构

```
飞书用户 ←→ 飞书 WebSocket ←→ Agent (意图路由)
                                    ├→ 普通对话 → ModelFactory → LLM Provider
                                    └→ 技能调用 → Skill Framework → ReminderSkill / ...
                                                                      └→ 飞书多维表格 (Bitable)
```

## 📁 项目结构

```
app/
├── api/
│   ├── main.py              # FastAPI 入口，lifespan 初始化
│   ├── feishu_ws.py          # 飞书 WebSocket 长连接处理
│   └── feishu_webhook.py     # HTTP Webhook（备用）
├── core/
│   ├── agent.py              # 核心 Agent：LLM 意图路由 + 对话管理
│   ├── factory.py            # ModelFactory：多模型实例化 + 自动降级
│   └── provider/
│       ├── base.py           # BaseLLMProvider 抽象类
│       ├── qwen.py           # 通义千问
│       ├── deepseek.py       # DeepSeek
│       └── doubao.py         # 豆包 (火山引擎)
├── skills/
│   ├── __init__.py           # 技能自动发现与注册
│   ├── base.py               # BaseSkill / SkillManifest / SkillContext
│   └── reminder.py           # 提醒技能（飞书多维表格 + APScheduler）
└── utils/
    ├── config.py             # pydantic-settings 配置
    ├── feishu.py             # 飞书 API 封装（消息、Bitable CRUD）
    └── logger.py             # 统一日志
```

## 🚀 快速开始

### 环境要求

- Python 3.11+
- conda（推荐）或 venv

### 安装

```bash
# 克隆仓库
git clone https://github.com/<your-username>/Super-Agent-OS.git
cd Super-Agent-OS

# 创建环境
conda create -n agent python=3.11 -y
conda activate agent

# 安装依赖
pip install -e ".[dev]"
```

### 配置

```bash
cp .env.example .env
```

编辑 `.env`，填入以下必要配置：

| 变量 | 说明 |
|------|------|
| `QWEN_API_KEY` | 通义千问 API Key（[DashScope](https://dashscope.aliyun.com/)） |
| `FEISHU_APP_ID` | 飞书应用 App ID |
| `FEISHU_APP_SECRET` | 飞书应用 App Secret |
| `FEISHU_BITABLE_APP_TOKEN` | 多维表格 App Token（可选，提醒功能需要） |
| `FEISHU_BITABLE_REMINDER_TABLE_ID` | 提醒表 Table ID（可选） |

其他模型（DeepSeek、Doubao）按需配置，未配置的会自动跳过。

### 飞书应用配置

1. 在 [飞书开放平台](https://open.feishu.cn/app) 创建企业自建应用
2. 开启 **机器人** 能力
3. 权限管理中开通：
   - `im:message` / `im:message:send_as_bot` — 消息读写
   - `bitable:app` — 多维表格读写（提醒功能需要）
4. 事件订阅 → 选择 **WebSocket 模式（长连接）**，订阅 `im.message.receive_v1`
5. 创建版本并发布

### 启动

```bash
uvicorn app.api.main:app --host 0.0.0.0 --port 8000
```

启动后会自动连接飞书 WebSocket，在飞书中找到机器人即可对话。

### Docker 部署（可选）

```bash
docker-compose up -d
```

## 💬 使用示例

| 你说 | SAO 做什么 |
|------|-----------|
| 你好 | 普通对话，AI 回复 |
| 明天下午3点提醒我开会 | 解析时间 → 写入多维表格 → 定时推送 |
| 我的提醒有哪些 | 查询多维表格中的待执行提醒 |
| 帮我解释一下 Python 的 GIL | 普通对话，AI 详细解答 |

## 🗺️ Roadmap

- [x] Phase 0: 项目脚手架
- [x] Phase 1: 飞书 WebSocket 对话
- [x] Phase 2: 多模型切换与自动降级
- [x] Phase 3: 技能框架（自动发现 + manifest）
- [x] Phase 4: LLM 意图路由
- [x] 提醒技能（飞书多维表格存储）
- [ ] Phase 5: 沙箱执行 + 人工确认 (HITL)
- [ ] Phase 6: 定时任务（股票监控等）
- [ ] Phase 7: 浏览器自动化（小红书等）
- [ ] Phase 8: 安全加固与可观测性

## 📄 License

[MIT](LICENSE)
