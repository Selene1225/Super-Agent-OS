# Super-Agent-OS 完整实现规划

> 修订版 v2 | 2026-03-05

以"飞书对话跑通"为第一里程碑，之后逐步叠加多模型降级、技能系统、自进化沙箱、定时任务、浏览器自动化。共 8 个 Phase，每个 Phase 结束都有可验证的交付物。Python 3.11，Docker 化部署，Cloudflare Tunnel sidecar 暴露服务。

---

## Phase 0: 项目脚手架

搭建最小可运行的项目骨架。

1. 创建目录结构：`app/core/provider/`, `app/api/`, `app/utils/`, `data/`, `tests/`
2. 创建 `pyproject.toml` — Python 3.11，依赖分阶段安装，本阶段只装：`fastapi`, `uvicorn[standard]`, `pydantic-settings`, `python-dotenv`, `openai`, `httpx`, `cryptography`
3. 创建 `.env.example` — 本阶段所需变量：`QWEN_API_KEY`, `QWEN_BASE_URL`, `QWEN_MODEL`, `FEISHU_APP_ID`, `FEISHU_APP_SECRET`, `FEISHU_VERIFY_TOKEN`, `FEISHU_ENCRYPT_KEY`
4. 创建 `.gitignore` — `.env`, `data/`, `__pycache__/`, `.venv/` 等
5. 创建 `Dockerfile` — `python:3.11-slim`，非 root 用户 `appuser`(UID 1000)，暂不装 Playwright
6. 创建 `docker-compose.yml` — `sao` 服务（端口 8000，挂载 `./data:/app/data`）+ `tunnel` sidecar（`cloudflare/cloudflared`，共享网络栈）
7. 各包目录创建 `__init__.py`

**验证**：`docker-compose build` 成功，容器启动后 `GET /health` 返回 200

---

## Phase 1: 飞书对话跑通（第一里程碑）

打通 Webhook 接收 → 单模型调用 → 飞书回复的完整链路。

1. `app/utils/config.py` — `pydantic-settings` 的 `Settings` 类，从 `.env` 加载配置，单例
2. `app/utils/logger.py` — 统一日志，JSON 格式，级别可配
3. `app/utils/feishu.py` — 飞书 API 封装：`get_tenant_access_token()`（带缓存）、`send_text_message(chat_id, text)`、`decrypt_event()`
4. `app/core/provider/base.py` — `BaseLLMProvider` 抽象类，定义 `async chat(messages) -> str`
5. `app/core/provider/qwen.py` — `QwenProvider`，用 `openai.AsyncOpenAI` 接通义千问
6. `app/core/factory.py` — 极简版，只实例化 Qwen，直接调用
7. `app/core/agent.py` — 最小版 Agent：纯对话模式，per-`chat_id` 对话历史（内存 dict，最近 20 轮），调用 factory 获取回复
8. `app/api/main.py` — FastAPI app，`lifespan` 初始化 Settings/Agent/Factory，`GET /health`
9. `app/api/feishu_webhook.py` — `POST /feishu/event`：处理 `url_verification`（返回 challenge）、处理 `im.message.receive_v1`（提取文本 → Agent → 飞书回复）、event_id 去重（内存 TTL dict）

**验证**：飞书开放平台配置事件订阅 → URL 验证通过 → **对机器人说"你好" → 收到 AI 回复**

---

## Phase 2: 多模型切换与降级

补全 Brain 层，接入 DeepSeek + 豆包，实现自动降级。

1. `app/core/provider/deepseek.py` — `DeepSeekProvider`，OpenAI 兼容端点
2. `app/core/provider/doubao.py` — `DoubaoProvider`，火山引擎 OpenAI 兼容端点
3. 升级 `app/core/factory.py` — `ModelFactory` 完整版：
   - 根据 `PRIMARY_MODEL` 配置选择主模型
   - 429/Timeout/APIError 自动切换备用模型，最多重试 2 次
   - 降级日志记录，`available_models()` 查询接口
4. 升级 `.env.example` — 增加 `DEEPSEEK_API_KEY`, `DEEPSEEK_BASE_URL`, `DOUBAO_API_KEY`, `DOUBAO_BASE_URL`, `PRIMARY_MODEL`

**验证**：Mock 主模型返回 429 → 自动降级到备用模型 → 飞书仍然收到正常回复

---

## Phase 3: 技能基类与动态加载

定义技能契约，实现动态发现和加载。

1. `app/skills/base.py` — `BaseSkill` 抽象类：`manifest` 属性（name/description/version/schedule/required_packages）、`async run(params) -> dict`、`validate()` 校验
2. `app/skills/__init__.py` — 技能自动发现：扫描 `app/skills/*.py`，`importlib` 动态导入，注册 `BaseSkill` 子类，提供 `get_skill(name)` / `list_all_skills()`
3. `app/utils/db.py` — `aiosqlite` 封装：初始化 `data/sao.db`（WAL 模式），`skill_registry` 表，提供 `register_skill()` / `list_skills()` / `deactivate_skill()`
4. 在 `pyproject.toml` 增加依赖：`aiosqlite`

**验证**：编写一个 dummy skill（输出 "hello"），验证动态加载、registry 注册、通过名称调用全部跑通

---

## Phase 4: Agent 升级 — 技能调度

Agent 从纯聊天升级为能识别并调用技能的中枢。

1. 升级 `app/core/agent.py` — 完整版 Agent：
   - system prompt 注入当前可用技能列表（名称 + 描述）
   - 解析 LLM 返回的结构化指令 JSON：`{"action": "run_skill" | "create_skill" | "chat", ...}`
   - `run_skill` → 从 registry 取出技能并执行，结果回复飞书
   - `create_skill` → 转交 Interpreter（Phase 5）
   - `chat` → 直接返回文本

**验证**：飞书发送"查看当前技能列表" → 返回 dummy skill 信息；发送"运行 hello 技能" → 执行并返回结果

---

## Phase 5: 自进化沙箱与 HITL

Agent 能生成代码、沙箱测试、飞书卡片确认后部署。

1. `app/core/interpreter.py` — `Interpreter` 类：
   - `generate_skill(requirement)` — 构建专用 prompt，LLM 输出完整 BaseSkill 子类代码
   - `validate_code(code)` — AST 静态分析，禁止危险调用，import 白名单
   - `sandbox_run(code)` — 写入 `data/sandbox/`，`asyncio.create_subprocess_exec` 执行（超时 30s）
   - `deploy_skill(temp_path, skill_name)` — 移入 `app/skills/`，importlib 重载，写入 DB
2. `app/api/feishu_card.py` — `POST /feishu/card`：
   - 处理【同意部署】→ 调用 `interpreter.deploy_skill()`
   - 处理【拒绝】→ 丢弃沙箱代码，回复确认
3. 升级 `app/utils/feishu.py` — 增加 `send_card_message(chat_id, card_json)` 发送交互式卡片

**验证**：飞书发送"写个技能计算 1+1" → 收到代码预览卡片 → 点击【同意部署】→ 技能生效 → 可通过对话调用

---

## Phase 6: 定时任务 + 投资分析技能

第一个有实际业务价值的技能。

1. 在 `pyproject.toml` 增加依赖：`apscheduler`, `akshare`, `yfinance`, `pandas`
2. 升级 `app/api/main.py` — `lifespan` 中初始化 `APScheduler`（`AsyncIOScheduler`），扫描所有技能 `manifest.schedule`，自动注册 cron 任务
3. `app/skills/stock_val.py` — `ValueMonitorSkill(BaseSkill)`：
   - `manifest.schedule = "0 9 * * 1"`（每周一 9:00）
   - `run()` — akshare 获取 A 股股息率/FCF → 计算偏离度评分 → 生成 Markdown 报告 → 飞书卡片推送
   - 评分公式：$Score = \frac{DY_{company}}{DY_{industry\_avg}} \times FCF\_Stability\_Weight$

**验证**：手动触发 `stock_val` 技能 → 飞书收到格式化的投资分析报告；定时任务注册成功

---

## Phase 7: 小红书自动化技能

验证 Playwright 浏览器自动化，此时 Dockerfile 加装浏览器依赖。

1. 升级 `Dockerfile` — 增加 `playwright install-deps chromium && playwright install chromium`
2. 在 `pyproject.toml` 增加依赖：`playwright`
3. `app/utils/browser.py` — Playwright 浏览器管理：单例实例，Session 加载/保存（`data/xhs_state.json`）
4. `app/skills/xhs_poster.py` — `XhsPosterSkill(BaseSkill)`：
   - `run(params)` 接收标题/正文/图片
   - 加载登录态 → 填写发布表单
   - HITL：发布前截图发飞书确认，用户同意后才提交

**验证**：有 XHS 登录态环境中，端到端测试发布流程（先用测试内容）

---

## Phase 8: 安全加固与质量收尾

1. 完善 `app/core/interpreter.py` — AST 黑名单扩充、import 白名单严格化
2. `app/api/feishu_webhook.py` — 飞书事件签名验证（`X-Lark-Signature`）
3. `Dockerfile` — `/app` 只读挂载，`/app/data` 可写
4. `app/utils/error_reporter.py` — 全局异常捕获 → 错误推送飞书
5. 测试用例 `tests/`：`test_factory.py`（降级逻辑）、`test_interpreter.py`（安全检查）、`test_skill_loader.py`（动态加载）
6. 技能版本管理：旧版本归档到 `data/skills_archive/`，DB 标记 inactive

**验证**：`pytest` 全部通过；故意触发危险代码 → 被拦截；模拟异常 → 飞书收到错误通知

---

## Decisions

| 决策 | 选择 | 理由 |
|---|---|---|
| Python 版本 | 3.11 | 生态兼容性更稳定 |
| 实现优先级 | 飞书对话优先 | 先跑通端到端链路，再叠加功能 |
| 沙箱隔离 | subprocess（容器内） | 起步简单，后续可升级 Docker-in-Docker |
| LLM 协议 | 三模型统一 OpenAI 兼容 | 一套 SDK 搞定 |
| 定时调度 | APScheduler 内嵌 | 技能 manifest 声明 cron，自动注册 |
| 内网穿透 | Cloudflare Tunnel sidecar | docker-compose 独立服务，共享网络栈 |
| 技能版本 | 旧版归档 + DB 标记 | 可回滚，不丢失历史 |
