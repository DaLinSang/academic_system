# 协作开发规范

> COMP7608B Large Language Models · Group 36 · Final Project
>
> **谁该读这份文档**：A、B、C 三个人全部。开工前读一遍，之后每次开分支、写提交、提 PR 前对一遍。
>
> **这份文档解决什么问题**：三个人并行改同一个仓库，最容易崩的不是技术，是协作。分支乱建、提交信息看不懂、改接口不通知、实验结果对不上——这些都会在最后一周集中爆炸。下面每一条规则，都是为了把这些雷提前排掉。

---

## 目录

1. [三分钟上手](#1-三分钟上手)
2. [项目简介](#2-项目简介)
3. [团队分工与模块所有权](#3-团队分工与模块所有权)
4. [环境与依赖](#4-环境与依赖)
5. [分支规范](#5-分支规范)
6. [提交规范](#6-提交规范)
7. [版本规范](#7-版本规范)
8. [PR 流程](#8-pr-流程)
9. [Issue 规范](#9-issue-规范)
10. [代码规范](#10-代码规范)
11. [实验代码的特殊规则](#11-实验代码的特殊规则)
12. [发布流程](#12-发布流程)
13. [常见问题](#13-常见问题)

---

## 1. 三分钟上手

```bash
# 1) 克隆
git clone https://github.com/DaLinSang/academic_system.git
cd academic_system

# 2) 依赖（需要 uv：https://docs.astral.sh/uv/）
uv sync --python 3.11

# 3) 环境变量（不填 key 也能跑，会自动用 Mock）
cp .env.example .env

# 4) 验证：环境自检 + 全链路 + 测试
uv run python scripts/check_env.py
uv run python -m app.demo
uv run pytest -q
```

三条命令都成功，你的环境就齐了。**看到 `[WARN] 未配置 API key` 是正常的**，阶段 0 不需要真实模型。

数据库（只有成员 B 需要）：

```bash
docker compose up -d      # pgvector，端口 5434
docker compose ps         # 等 STATUS 变成 healthy
```

---

## 2. 项目简介

**一句话**：一个面向课程学生的学术知识助手，能自己判断「直接回答 / 查我的 PDF / 多步拆解」，回答带页码引用，并且每条 claim 都经过证据校验。

**它为什么算 agentic 而不是普通 RAG**：系统在生成前会主动决策——要不要检索、要不要拆成多步、要不要读记忆。这个决策过程本身就是研究对象（RQ1）。

### 核心能力

| 能力 | 说明 | 对应研究问题 |
|---|---|---|
| 自适应路由 | 判断本次请求该走哪条路径 | RQ1 |
| 文档问答 | 基于上传 PDF 回答，附页码引用 | — |
| 多文档比较 | 沿指定维度对比论文 | — |
| 长期记忆 | 记住背景 / 偏好 / 任务进度，跨会话生效 | RQ2 |
| Claim 校验 | 拆句逐条对证据核验，标记无依据内容 | RQ3 |
| 效率统计 | token、成本、延迟、错误恢复 | RQ4 |

### 明确不做

复杂登录系统、云端部署、多 Agent 协作、完整大型 benchmark、通用代码执行工具。

> 范围一旦松动，四个人三周就会变成"什么都做了个开头"。**这条比功能清单更重要。**

### 四种评测基线

| 模式 | 路由 | 检索 | 记忆 | 校验 | 含义 |
|---|:--:|:--:|:--:|:--:|---|
| B1 | ✗ | ✗ | ✗ | ✗ | 直接 LLM |
| B2 | ✗ | ✓ | ✗ | ✗ | 普通 RAG |
| B3 | ✓ | ✓ | ✓ | ✗ | RAG + 记忆 |
| B4 | ✓ | ✓ | ✓ | ✓ | 完整 Agent |

四个版本**共用一套代码**，只靠 `AgentState` 的四个 `enable_*` 开关区分，见 `app/agent/orchestrator.py:MODE_PRESETS`。

---

## 3. 团队分工与模块所有权

采用**模块所有权**制：一个模块只归一个人，其他人负责验证但不改内部实现。

| 成员 | 负责模块 | 交付物 |
|---|---|---|
| **A** | `app/core/` `app/agent/` `app/routing/` `app/planning/` `app/llm/` | 公共结构、接口、Mock、LLM 层、Router、Planner、Orchestrator、集成、日志 |
| **B** | `app/ingestion/` `app/retrieval/` `app/memory/` | PDF 解析与页码、切分、embedding、混合检索、MemoryStore |
| **C** | `app/verification/` `app/ui/` `app/evaluation/` | claim 拆分与校验、Streamlit、标注数据集、B1–B4 评测脚本 |

### 三条硬规则

**① 不碰别人的模块。** 发现 B 的检索有 bug，开 Issue 或提 PR 让 B 改，不要直接改他文件。理由：模块负责人要对该模块的最终质量负责，被别人悄悄改过之后就没人负责了。

**② 公共契约改动必须三人确认。** 涉及两个文件：

- `app/core/types.py` —— 所有数据结构
- `app/core/interfaces.py` —— 所有模块接口

改这两个文件的流程：

1. 在群里说明「要改什么字段、为什么、影响谁」
2. 两人以上回复同意
3. 提 PR，标题带 `!`（如 `feat(core)!: 给 Evidence 增加 section 字段`）
4. 合并后同步更新 `docs/interfaces.md` 和受影响的 Mock

**③ 跨模块只走接口。** 不许 `import` 对方模块的内部实现类，不许直接操作对方的数据库表。

---

## 4. 环境与依赖

| 项 | 规定 | 为什么 |
|---|---|---|
| Python | **3.11** | 生态兼容性最好；3.13 上部分库没有 wheel |
| 包管理 | **uv** | `uv sync` 一条命令，锁文件保证三人环境一致 |
| 依赖声明 | 只改 `pyproject.toml`，`uv.lock` 一起提交 | 手改锁文件会让别人装出不同版本 |
| 配置 | 只通过 `app/core/config.py` | 禁止任何模块直接 `os.getenv()` |
| 模型调用 | 只通过 `app/llm/client.py` | 禁止任何模块直接 `import openai` |
| 数据库 | Docker 容器 `academic-pgvector`，端口 **5434** | 避开本机其它项目的 5432/5433 |

### 加依赖的正确姿势

```bash
uv add <package>              # 运行时依赖
uv add --dev <package>        # 开发依赖（pytest / ruff 等）
```

**不要** `pip install` 完就完事——那样只有你本地能跑。

### 环境变量

| 变量 | 用途 | 必须 |
|---|---|---|
| `DEEPSEEK_API_KEY` | DeepSeek 模型 | 阶段 1 起 |
| `HKU_API_KEY` / `HKU_BASE_URL` / `HKU_MODEL` | 港大 ITS API | 拿到配额后 |
| `OLLAMA_BASE_URL` / `EMBEDDING_MODEL` | 本地 embedding | B 需要 |
| `POSTGRES_*` | 数据库连接 | B 需要 |

`.env` 已被 `.gitignore` 排除，**永远不要提交**。

---

## 5. 分支规范

### 分支模型

只有一条长期分支：**`main`**。任何改动都走临时分支 + PR。

```
main ──●──●────────────●──────────●──→  永远可运行、可演示
        \            /            /
         ●──●──●────●            /      feat/retrieval-hybrid
                       \        /
                        ●──●──●         fix/memory-user-isolation
```

### 命名规则

```
<type>/<scope>-<简短描述>
```

| 段 | 取值 | 说明 |
|---|---|---|
| `type` | `feat` `fix` `docs` `chore` `test` `refactor` `exp` | 与提交类型一致 |
| `scope` | 模块名，见下表 | 让人一眼知道改哪 |
| `描述` | 小写英文 + 连字符，2–4 个词 | 不要中文、不要下划线 |

**scope 取值**：`core` `agent` `routing` `planning` `llm` `ingestion` `retrieval` `memory` `verification` `ui` `evaluation` `deps` `ci`

### 示例

```bash
feat/retrieval-hybrid-rrf        # B 实现混合检索
feat/agent-answer-generator      # A 实现真实回答生成
fix/memory-user-isolation        # B 修记忆跨用户串数据
fix/orchestrator-stage-degrade   # A 修某阶段异常未降级
docs/interfaces-v0.2             # A 更新接口文档
exp/b1-b4-first-run              # C 跑第一次基线实验
test/verifier-claim-split        # C 补 claim 拆分测试
```

### 规则

- 分支从最新的 `main` 切出：`git switch main && git pull && git switch -c feat/xxx`
- **合并后立刻删分支**，不留尸体
- 分支存活不超过 **5 天**；活太久说明任务拆得太大，该拆
- 不要 `git push --force` 到别人也在用的分支
- 不要 rebase 已推送的公共分支

---

## 6. 提交规范

采用 **Conventional Commits**。目的不是形式主义，而是让 `git log` 能直接生成 CHANGELOG，也让 review 时一眼看出改动性质。

### 格式

```
<type>(<scope>): <subject>

<body>

<footer>
```

- 第一行（subject）**不超过 50 字**，不加句号
- 空一行再写 body
- body 写**为什么**，不写"改了什么"（`git diff` 已经说明了）

### type 取值

| type | 用在什么时候 | 会进 CHANGELOG |
|---|---|---|
| `feat` | 新增功能 | ✅ Added |
| `fix` | 修 bug | ✅ Fixed |
| `perf` | 性能优化 | ✅ Changed |
| `refactor` | 重构，行为不变 | ✅ Changed |
| `docs` | 只改文档 | ✅ Documentation |
| `test` | 只改测试 | ❌ |
| `chore` | 构建、依赖、配置 | ✅ Maintenance |
| `exp` | 实验脚本或实验记录 | ✅ Experiments |
| `revert` | 回滚某次提交 | ✅ |

### scope 取值

同分支命名：`core` `agent` `routing` `planning` `llm` `ingestion` `retrieval` `memory` `verification` `ui` `evaluation` `deps` `ci` `docs`

跨多个模块时可以省略 scope，或写最外层。

### 示例

**✅ 好的提交**

```
feat(retrieval): 实现向量与 BM25 的 RRF 混合排序

单一向量检索在专有名词上召回差（"RRF" 被切成无意义 token）。
先用 pgvector 取 top-20，再用 rank_bm25 取 top-20，按 RRF 融合后取 top-5。

Closes #14
```

```
fix(memory): 修复 search 未按 user_id 过滤导致跨用户串数据

MemoryStore.search 原来只按 tier 过滤，多用户测试时 A 的偏好
出现在 B 的会话里。现在所有查询强制带 user_id 条件。

Closes #21
```

```
feat(core)!: AgentState 增加 enable_retrieval 开关

BREAKING CHANGE: B1 与 B2 原本共用同一套配置，导致两组实验
实际跑的是同一个系统。新增 enable_retrieval 区分"允许检索"与
"路由决定检索"。所有调用 run() 的地方需补传该参数。

Closes #9
```

**❌ 不要这样写**

| 反例 | 问题 |
|---|---|
| `update` | 改了什么？看不出 |
| `修改代码` | 同上，且没有 type |
| `fix bug` | 哪个模块的什么 bug？ |
| `feat: 完成了很多功能` | 一次提交做了太多事 |
| `WIP` | 不要在 main 的 PR 里出现 |
| `改好了` | 没有信息量 |

### 规则

- **一次提交只做一件事。** 改功能顺手格式化整个文件 → 拆成两个提交
- 提交信息用**中文**写 subject，`type(scope)` 用英文
- 合并到 main 时用 **Squash merge**，一个 PR 压成一个提交（见第 8 节）
- 破坏性改动在 type 后加 `!`，并在 footer 写 `BREAKING CHANGE:`

---

## 7. 版本规范

采用 **语义化版本 [SemVer 2.0](https://semver.org/lang/zh-CN/)**：`vMAJOR.MINOR.PATCH`

| 位 | 什么时候升 | 例子 |
|---|---|---|
| `MAJOR` | 对外行为不兼容的改动 | 公共数据结构删字段、接口签名变更 |
| `MINOR` | 向下兼容的新功能 | 新增 Router、新增校验模块 |
| `PATCH` | 向下兼容的修复 | 修页码丢失、修记忆串数据 |

> 本项目在 `0.x` 阶段，按 SemVer 规定 MINOR 位可以包含破坏性改动，但**仍需在 CHANGELOG 中标明**。

### 版本与本项目里程碑的对应

| 版本 | 里程碑 | 判定标准 |
|---|---|---|
| `v0.1.0` | 阶段 0：骨架与接口冻结 | Mock 全链路跑通，24 项契约测试绿 |
| `v0.2.0` | 阶段 1：三个核心模块可用 | LLM 层、检索、校验都能独立跑 |
| `v0.3.0` | 阶段 2：Agent 完整流程 | 记忆 + 多步任务 + 错误恢复可用 |
| `v0.4.0` | 阶段 3：界面与评测 | Streamlit 可演示，B1–B4 能跑 |
| `v0.9.0` | 功能冻结 | 停止加新功能，只修 bug 和跑实验 |
| `v1.0.0` | 最终交付 | 干净环境可复现，报告与代码一致 |

### 打标签

```bash
# 附注标签（不要用轻量标签，附注标签才带作者、日期和说明）
git tag -a v0.2.0 -m "阶段1：LLM 层、混合检索、claim 校验可用"
git push origin v0.2.0

# 查看 / 删除
git tag -l
git tag -d v0.2.0                      # 删本地
git push origin --delete v0.2.0        # 删远程
```

### 版本号写在哪

**三处必须同步**，改一处就要改全部：

1. `pyproject.toml` 的 `version` 字段
2. `CHANGELOG.md` 顶部新增一节
3. Git tag

### CHANGELOG 维护

遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，分六类：`Added` `Changed` `Deprecated` `Removed` `Fixed` `Security`。

**在 PR 合并时就更新 CHANGELOG**，不要等发版时回忆三个月前改了什么。

---

## 8. PR 流程

### 开 PR 之前

```bash
# 1) 同步 main
git switch main && git pull
git switch feat/your-branch
git rebase main            # 或 git merge main

# 2) 三道门必须全绿
uv run pytest -q
uv run ruff check .
uv run python -m app.demo  # 你的改动没把主流程搞坏

# 3) 推送
git push -u origin feat/your-branch
```

### PR 标题

与提交规范一致：`feat(retrieval): 实现 RRF 混合排序`

### PR 描述

用仓库里的 `.github/pull_request_template.md`，必须填：

- 做了什么
- 影响哪些模块（勾选）
- 是否修改公共接口
- 验证方式（贴测试输出）
- 自检清单

### 审查规则

| 规则 | 说明 |
|---|---|
| 作者不能批准自己的 PR | 至少一名其他成员 approve |
| 改公共契约的 PR 需**两人** approve | `types.py` / `interfaces.py` |
| 改数据库结构、`AgentState`、评测配置时，必须 @ 全体 | 影响面最大 |
| 合并后由**模块负责人**继续负责相关 bug | 不把修复责任转给验证人 |

### 合并方式

**统一用 Squash merge**：一个 PR 压成一个提交进 main。

理由：feature 分支上的「修个 typo」「再改一下」这类噪音提交不该进主线历史。压缩后 main 上每个提交都对应一个完整的、可回溯的功能点。

```bash
gh pr merge --squash --delete-branch
# 或在 GitHub 页面上选 "Squash and merge"
```

### PR 大小

- 目标 **≤ 400 行改动**
- 超过就拆。拆分方式：先合并"接口 + 测试"，再合并"实现"
- 只有一个例外：纯格式化/重命名的 PR 可以很大，但**必须单独一个 PR**，不能和逻辑改动混在一起

---

## 9. Issue 规范

### 一个 Issue = 一个可验收的任务

不要建「完成检索模块」这种大 Issue。要建「实现向量检索返回带页码的 Evidence」这种能一句话验收的。

### 标题格式

```
[模块] 动词短语
```

例：`[retrieval] 实现向量检索并返回带页码的 Evidence`

### 必填内容

用 `.github/ISSUE_TEMPLATE/task.md`，至少写清：

- 目标（解决什么问题）
- 负责人 / 验证人
- 实现要求（编号列出）
- **验收标准**（可勾选的 checklist）

### 关联 PR

PR 描述里写 `Closes #14`，合并后 Issue 自动关闭。不写的话最后统计工作量会很痛苦——而个人贡献文档要用这个数据。

---

## 10. 代码规范

### 工具

```bash
uv run ruff check .        # 静态检查
uv run ruff check . --fix  # 自动修可修的
uv run ruff format .       # 格式化（可选）
```

配置在 `pyproject.toml`：行宽 100，规则集 `E F I UP B`。

### 硬性要求

| 要求 | 说明 |
|---|---|
| **全部函数有类型注解** | 参数和返回值都要，`-> None` 也不能省 |
| **docstring 用中文** | 说明「为什么这么设计」，不是复述函数名 |
| **禁止裸 `print`** | 记录用 `state.log()`，演示用 `app/demo.py` |
| **禁止全局可变状态** | 状态一律走 `AgentState` |
| **禁止模块直读环境变量** | 走 `app/core/config.py` |
| **禁止模块直接 `import openai`** | 走 `app/llm/client.py` |
| **跨模块异常必须转类型** | 转成 `app/core/errors.py` 里的类型 |
| **新模块必须有 README** | 用 `docs/module-readme-template.md` |
| **新功能必须有测试** | 至少覆盖正常、空输入、错误输入三种 |

### 为什么禁止裸 print

三个原因：① 实验时要统计延迟和错误，print 无法结构化；② Streamlit 里 print 用户看不到；③ 日志要能写进 JSONL 供分析。`state.log(stage, event, **detail)` 才是正确方式。

### 异常边界的约定

模块内部可以抛任何异常，但**跨模块边界必须转成本系统类型**：

```python
# app/retrieval/pgvector_retriever.py
try:
    rows = self._query(...)
except psycopg.Error as exc:
    raise RetrievalError(f"向量检索失败: {exc}") from exc
```

这样 Orchestrator 才能统一降级，而不是被 `psycopg.OperationalError` 打个措手不及。

---

## 11. 实验代码的特殊规则

这一节是**这个项目区别于普通课程作业的地方**。评测占报告很大比重，实验不可复现 = 结论不可信。

### ① 实验产物不进 git

`runs/` `outputs/` `logs/` 已在 `.gitignore` 里。只提交**脚本、配置和最终汇总表**，不提交原始输出。

### ② 每次实验必须记录上下文

一次实验的完整身份由五项构成，缺一项就无法复现：

| 项 | 例 |
|---|---|
| commit hash | `2635ac4` |
| 模型 | `deepseek-chat` |
| 生成参数 | `temperature=0.0, top_k=5` |
| 数据版本 | `annotated_v1` |
| 运行时间 | `2026-11-15T14:20:00+08:00` |

建议在 `runs/<timestamp>/meta.json` 里自动写这五项。

### ③ 改了评测代码必须重跑

只要动了 `app/evaluation/` 或影响输出的模块，**所有受影响的基线都要重跑**。不能"新结果用新代码、旧结果用旧代码"混在一张表里。

PR 里如果改了实验相关代码，必须写清楚「需要重跑哪几组」。

### ④ 同一组对比必须同条件

B1–B4 对比时，以下必须完全一致：相同问题集、相同模型、相同温度、相同 PDF、相同 `top_k`。

任何一个不同，这组对比就不成立。

### ⑤ 随机性要重复三次

Proposal 里明确写了：对随机性输出重复三次，报告均值和标准差。单次结果不能作为结论。

### ⑥ 失败案例要留档

失败案例比成功案例更能体现研究价值。遇到 `unsupported` 误判、检索找错页、Router 误判，记到 `docs/failure-cases.md`，附上原始输入输出。

---

## 12. 发布流程

```bash
# 1) 确认干净
git switch main && git pull
uv run pytest -q && uv run ruff check .

# 2) 更新版本号
#    - pyproject.toml 的 version
#    - CHANGELOG.md 新增一节（把 Unreleased 的内容移到新版本号下）

# 3) 提交并打标签
git add pyproject.toml CHANGELOG.md
git commit -m "chore(release): v0.2.0"
git tag -a v0.2.0 -m "阶段1：LLM 层、混合检索、claim 校验可用"
git push origin main --follow-tags

# 4) 在 GitHub 建 Release，notes 从 CHANGELOG 摘
```

### 功能冻结之后

从 `v0.9.0` 起**只允许**：bug 修复、测试补充、正式实验、README 更新、结果分析、Demo 稳定性改进。

**不允许**：新增 Agent 工具、改数据库结构、改 `AgentState`、加新页面。

理由：冻结后任何架构改动都会让已跑完的实验失效，而你未必有时间重跑。

---

## 13. 常见问题

### Q1：我改了一个公共接口，怎么办？

1. 在群里说清楚改什么、为什么、影响谁
2. 等两人确认
3. 提 PR，标题加 `!`，body 写 `BREAKING CHANGE:`
4. 同步改：`docs/interfaces.md`、`app/core/mocks.py`、受影响的测试
5. 合并后通知 B 和 C 更新自己那边的调用

### Q2：我的分支落后 main 了，用 merge 还是 rebase？

- **自己独占的分支** → `git rebase main`（历史干净）
- **别人也在用的分支** → `git merge main`（不要 rebase，会改写别人的历史）

### Q3：不小心提交了 `.env` 或 API key 怎么办？

**第一优先级是把 key 作废。** 提交进 git 历史就等于泄露，删文件、改提交都没用——历史里还在。

处理顺序：

1. 立刻去服务商后台**吊销并重新生成** key
2. 更新本地 `.env`
3. 如果还没 push：`git reset --soft HEAD~1` 重做提交
4. 如果已 push：通知全体，用 `git filter-repo` 清理历史（需三人协调，不要自己动）

**预防**：`git status` 提交前看一眼，`.env` 不应该出现在列表里。

### Q4：能不能直接 push 到 main？

**不能。** main 必须始终可运行、可演示。直接推会绕过 review，也会让"哪次改动搞坏了系统"变得无法定位。

唯一例外：只有你自己在改的纯文档 typo，且不涉及接口说明。

### Q5：测试在我机器上过了，CI/别人机器上不过？

99% 是环境差异。检查：

1. Python 版本是不是 3.11（`uv run python --version`）
2. 有没有 `uv sync` 装全依赖
3. 有没有依赖本地状态（比如忘了 Mock、直接连了真实数据库）
4. 路径是不是写死了绝对路径

### Q6：模块 README 什么时候写？

**和功能 PR 一起写**，不能拖到最后补。原因很简单：一周后你自己也记不清当时为什么这么设计。

### Q7：我的 PR 太大，review 不动怎么办？

拆。标准拆法：

- PR 1：接口定义 + Mock + 测试（可独立合并）
- PR 2：真实实现（实现 PR 1 的接口）
- PR 3：接入 Orchestrator + 更新 README

---

## 附：一页速查

```bash
# 开工
git switch main && git pull
git switch -c feat/retrieval-hybrid-rrf

# 干活...

# 收工
uv run pytest -q && uv run ruff check .
git add -A
git commit -m "feat(retrieval): 实现 RRF 混合排序"
git push -u origin feat/retrieval-hybrid-rrf
# → 在 GitHub 开 PR，等 review，Squash merge，删分支

# 发版
git tag -a v0.2.0 -m "阶段1 完成" && git push origin v0.2.0
```

| 我要… | 做这个 |
|---|---|
| 改数据结构 | 先群里问，两人同意，PR 加 `!` |
| 加依赖 | `uv add <pkg>`，提交 `uv.lock` |
| 跑测试 | `uv run pytest -q` |
| 检查风格 | `uv run ruff check .` |
| 跑演示 | `uv run python -m app.demo -m B4` |
| 查环境 | `uv run python scripts/check_env.py` |
| 起数据库 | `docker compose up -d` |
