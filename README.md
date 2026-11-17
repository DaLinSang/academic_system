# An Agentic Personal Academic Knowledge Assistant

> COMP7608B Large Language Models · Group 36 · Final Project
>
> 一个面向课程学生的学术知识助手：能自己判断「直接回答 / 查我的文档 / 多步拆解」，
> 回答带页码引用，并且每条 claim 都经过证据校验。

**当前阶段：阶段 0（接口与骨架）** · 详细排期见飞书《LLM小组项目开发规划》

---

## 1. 它做什么

| 能力 | 说明 |
|---|---|
| 文档问答 | 基于自己上传的 PDF 回答，附页码引用 |
| 多文档比较 | 沿指定维度比较几篇论文，而不是各讲一遍 |
| 研究报告 | 拆解任务、分步检索、汇总成带引用的短报告 |
| 长期记忆 | 记住用户背景 / 回答偏好 / 任务进度，跨会话生效 |
| 声明校验 | 把答案拆成 claim，逐条对证据校验，标记无依据的内容 |
| 自适应路由 | 判断这次要不要检索、要不要多步规划（RQ1 的实验对象） |

**明确不做**（避免范围失控）：复杂登录系统、云端部署、多 Agent 协作、完整大型 benchmark、
通用代码执行工具。

## 2. 30 秒跑起来

```bash
# 1) 依赖（需要 uv：https://docs.astral.sh/uv/）
uv sync --python 3.11

# 2) 环境变量
cp .env.example .env        # 选择 HKU / DeepSeek；不填 key 也能跑 Mock

# 3) 跑通阶段 0 的 Mock 全链路（无需 API key、无需数据库）
uv run python -m app.demo

# 4) 启动 Member C 的 Streamlit 页面（默认 Mock，可直接演示）
uv run streamlit run streamlit_app.py

# 5) 跑测试
uv run pytest -v
```

看到下面这样就算环境 OK：

```
── ① Router ────────
  路径      : document_retrieval
── ④ 检索证据 ──────
  [1] COMP7608B_Lec04.pdf 第 9 页  score=1.0 via=mock
── ⑦ Claim 校验 ────
  ● supported            Self-RAG 训练模型输出 reflection token...
  ✓ 无错误
```

### 启动数据库（只有成员 B 需要）

```bash
docker compose up -d          # pgvector:pg16，端口 5434
docker compose logs -f postgres
```

### 跑真实链路（需 API key + 数据库）

```bash
# 1) 起数据库
docker compose up -d

# 2) 把 PDF 放进 data/raw，然后索引进 pgvector
uv run python scripts/index_pdfs.py
uv run python scripts/index_pdfs.py --list      # 先看看有哪些文件

# 3) 用真实模块跑（LLM 路由/规划/生成 + 混合检索 + 词法校验）
uv run python -m app.demo --real
uv run python -m app.demo --real -m B2 -q "讲义里 RRF 是怎么算的？"
```

`--real` 与默认的 Mock 模式用的是**同一套 Orchestrator**，只是注入的实现不同。
没有配 key 时会自动退回 `MockLLM`，路由与规划走关键词降级，链路仍可跑通。

不同成员可以在自己的 `.env` 中独立选择 `LLM_PROVIDER=hku` 或
`LLM_PROVIDER=deepseek`，无需修改代码。HKU、DeepSeek 和 Mock 的完整配置、
认证方式与排错说明见 [`app/llm/README.md`](app/llm/README.md)。

用 5434 是为了避开本机其它项目的 5432/5433。

## 3. 仓库结构

```
app/
├── core/            公共层（A 维护，全员只读）
│   ├── types.py       7 个公共数据结构 + AgentState
│   ├── interfaces.py  5 个模块接口 Protocol ← 协作核心
│   ├── config.py      唯一配置入口（不许直接读 os.environ）
│   ├── errors.py      跨模块异常约定
│   └── mocks.py       阶段 0 的 Mock 实现
├── agent/           编排层（A）
│   ├── orchestrator.py  主流程 + B1–B4 模式
│   └── factory.py       装配工厂（Mock ↔ 真实实现互换）
├── routing/         路由（A）
├── planning/        规划（A）
├── llm/             统一模型调用层（A）
├── ingestion/       PDF 解析（B）
├── retrieval/       混合检索（B）
├── memory/          记忆存取（B）
├── verification/    claim 校验（C）
├── evaluation/      B1–B4 评测（C）
└── ui/              Streamlit 界面（C）
tests/               契约测试与各模块测试
configs/             实验配置（模型、top-k、消融开关）
data/raw/            原始 PDF（不入库）
scripts/             初始化与运维脚本
docs/                接口文档、个人贡献记录
```

## 4. 分工边界

| 成员 | 负责 | **不要碰** |
|---|---|---|
| **A** | 公共数据结构、接口、Mock、LLM 层、Router、Planner、Orchestrator、集成、日志 | PDF 解析、检索算法、记忆 SQL、claim 拆分、UI |
| **B** | PDF 解析与页码保留、切分、embedding、混合检索、MemoryStore | Orchestrator、Router、Verifier、公共结构字段 |
| **C** | Claim 拆分与校验、Streamlit、标注数据集、B1–B4 评测脚本 | 检索实现、记忆实现、编排逻辑 |

**修改 `app/core/types.py` 或 `app/core/interfaces.py` 必须三人确认。**

## 5. 四种评测模式

差异只靠开关表达，代码只有一套（见 `app/agent/orchestrator.py:MODE_PRESETS`）：

| 模式 | 路由 | 检索 | 记忆 | 校验 | 含义 |
|---|:--:|:--:|:--:|:--:|---|
| B1 | ✗ | ✗ | ✗ | ✗ | 直接 LLM |
| B2 | ✗ | ✓ | ✗ | ✗ | 普通 RAG |
| B3 | ✓ | ✓ | ✓ | ✗ | RAG + 记忆 |
| B4 | ✓ | ✓ | ✓ | ✓ | 完整 Agent |

```bash
uv run python -m app.demo -m B1 -q "比较 RAG 和 ReAct 的方法差异"
```

## 6. 开发约定

完整规范见 **[CONTRIBUTING.md](CONTRIBUTING.md)**，变更历史见 **[CHANGELOG.md](CHANGELOG.md)**。三条最容易踩的先记住：

- **不直接推 main**：一律开分支 + PR，至少一人 review
- **提交信息**：`<type>(<scope>): <subject>`，如 `feat(retrieval): 实现 RRF 混合排序`
- **绝不提交** `.env`、API key、真实数据

提交前跑一遍：

```bash
uv run pytest -q && uv run ruff check .
```

## 7. 已知限制

- **长期记忆**：真实装配使用 PostgreSQL/pgvector 存取、Ollama embedding 检索；
  写入候选仍由保守规则筛选，复杂的跨轮归纳与冲突消解尚未实现。详见 [Memory 模块说明](app/memory/README.md)
- Streamlit 已提供 PDF 上传入口，但 Real 模式导入仍要求 PostgreSQL、Ollama 正常运行
- Web 界面目前是第一版，固定 Demo 数据和 B1-B4 实验结果页仍待补充
- `web_search` 与代码执行工具未实现
- Planner 的 `tool="llm"` 步骤只说明意图，不做真实的多轮 ReAct 执行
