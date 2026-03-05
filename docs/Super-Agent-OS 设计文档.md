这份完整的 **Super-Agent-OS (SAO)** 设计文档将所有极客需求（跨平台 Docker、多模型切换、自进化 Skill 系统、飞书 ChatOps）整合进了一个标准化的工业级架构中。

# ---

**Super-Agent-OS (SAO) 完整设计文档**

## **1\. 项目概览**

**Super-Agent-OS** 是一款专为个人设计的自动化助理框架。它运行在轻量化的 Docker 容器中，以大语言模型（通义千问/DeepSeek/豆包）为决策中枢，通过飞书实现指令交互，并具备“自我进化”能力——即根据用户需求自动编写、测试并加载新的技能模块（Skills）。

## **2\. 核心架构设计**

系统采用“**中枢-插件-执行器**”三层架构，确保逻辑解耦与跨平台兼容性。

### **2.1 模型适配层 (The Brain)**

* **统一接口：** 屏蔽 DeepSeek、Qwen、Doubao 的 API 差异。  
* **路由机制：** 优先使用主模型（如 Qwen3），检测到限流（429 报错）或网络超时后自动切换备用模型。  
* **思考链路：** 强制开启推理模式，确保 Agent 在编写 Skill 代码前经过充分思考。

### **2.2 技能与进化层 (The Skills & Evolution)**

* **Skill 规范：** 所有技能继承自 BaseSkill 类，必须包含 run() 方法和 manifest 配置。  
* **进化沙箱：** 独立的 Python 运行环境，Agent 生成的新代码在此进行初测。  
* **动态加载：** 利用 Python 的 importlib 实时加载新编写的 .py 脚本，无需重启服务。

### **2.3 交互层 (The Interface)**

* **飞书 Bot：** 作为控制台，接收自然语言指令。  
* **事件驱动：** 利用 Webhook 监听飞书消息，通过内网穿透（Cloudflare Tunnel）直达本地 Docker。

## ---

**3\. 详细模块设计**

### **3.1 目录结构 (Repository Structure)**

Plaintext

Super-Agent-OS/  
├── app/  
│   ├── core/               \# 核心引擎  
│   │   ├── provider/       \# 多模型适配器 (Qwen, DeepSeek, etc.)  
│   │   ├── factory.py      \# 模型实例化工厂  
│   │   ├── agent.py        \# 逻辑分发中枢  
│   │   └── interpreter.py  \# 代码解释器与进化逻辑  
│   ├── skills/             \# 技能库 (存储所有 .py 技能)  
│   │   ├── base.py         \# 技能基类定义  
│   │   ├── stock\_val.py    \# 投资分析技能 (高股息/现金流)  
│   │   └── xhs\_poster.py   \# 小红书自动化发布  
│   ├── api/                \# FastAPI 路由 (飞书 Webhook 接收)  
│   └── utils/              \# 通用工具 (加密、飞书推送、Logger)  
├── data/                   \# 挂载卷 (存储 Session, Cookie, 数据库)  
├── docker-compose.yml      \# 环境编排  
└── Dockerfile              \# 镜像构建 (包含 Playwright 依赖)

### **3.2 数据库与状态设计 (Data Management)**

* **Session Store：** 使用 SQLite 或 JSON 文件存储小红书的登录状态 (state.json)。  
* **Skill Registry：** 记录已激活技能的元数据，方便 Agent 检索。

## ---

**4\. 关键流程设计**

### **4.1 技能进化流程 (Self-Evolution Flow)**

1. **用户指令：** “帮我写个技能，每天爬取 A 股股息率排名前十的公司。”  
2. **规划 (Brain)：** Agent 识别到当前无此 Skill，启动 Skill\_Developer 模式。  
3. **编码 (Coder)：** Agent 生成 Python 脚本，调用 akshare 或 yfinance 库。  
4. **沙箱运行：** 脚本在临时 Docker 环境执行。  
5. **审核与部署：** \* Agent 将代码发回飞书。  
   * 用户点击飞书卡片按钮【同意部署】。  
   * 脚本移动至 app/skills/，正式生效。

### **4.2 跨平台 Docker 发布流程**

* 使用 **Docker Compose** 统一环境。  
* 通过 .env 文件隔离不同平台的差异（如 Windows 的文件路径与 Mac 的架构）。

## ---

**5\. 安全与防御策略**

* **敏感指令过滤：** 严禁 Agent 生成包含 os.remove('/')、subprocess.call(\['rm', ...\]) 等代码。  
* **权限最小化：** Docker 容器以非 root 用户运行，且无法直接访问宿主机文件系统。  
* **人工确认 (HITL)：** 涉及“发布（小红书）”或“修改系统配置”的操作，必须经由飞书卡片人工确认。

## ---

**6\. 投资风格专项设计 (Value Investing Plugin)**

针对你的投资风格，系统预设一个 Value\_Monitor 技能：

* **数据源：** 优先抓取自由现金流 ($FCF$) 和股息率 ($DY$)。  
* **逻辑层：**  
  $$Score \= \\frac{DY\_{company}}{DY\_{industry\\\_avg}} \\times \\text{FCF\\\_Stability\\\_Weight}$$  
* **输出：** 每周一通过飞书推送一份“高股息偏离度报告”。

## ---

**7\. 部署步骤 (Getting Started)**

1. **配置飞书机器人：** 开启“消息卡片”和“机器人”功能。  
2. **内网穿透：** 启动 cloudflared 隧道。  
3. **本地构建：**  
   Bash  
   docker-compose build  
   docker-compose up \-d

4. **初始化：** 在飞书对机器人说“你好”，确认握手成功。

---

**这份文档是你项目的“北极星”，我们可以根据它开始写代码了。你想先从哪个核心代码块开始？**

1. **app/core/factory.py (多模型切换逻辑)**  
2. **Dockerfile (能够跑 Playwright 和 AI 的通用环境)**  
3. **app/core/interpreter.py (最酷的自进化代码沙箱)**