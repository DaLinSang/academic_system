# 评测契约 v1

> **这份文档是什么**：A 与 C 之间的接口约定——数据从哪来、每个字段什么含义、每个 RQ 的指标怎么算。
>
> **不是什么**：不是评测脚本。脚本、标注数据集、Streamlit 界面都是成员 C 的交付物，本文只定义口径。

---

## 1. 入口

一次实验 = 一个模式 × 一个数据集 × 一个 run 目录。

```python
from app.agent.factory import build_stage1_orchestrator
from app.agent.orchestrator import MODE_PRESETS
from app.agent.run_logger import RunLogger

orch = build_stage1_orchestrator(top_k=5)

with RunLogger(
    mode="B4",
    flags=MODE_PRESETS["B4"],
    dataset_name="annotated_v1",
    dataset_size=len(samples),
    model="deepseek-chat",
    top_k=5,
) as run:
    for sample in samples:
        state = orch.run(sample["question"], user_id=sample["user_id"], **MODE_PRESETS["B4"])
        run.log(state)
```

跑完得到：

```
runs/20261116-114530-B4/
  meta.json       运行身份（五项上下文）+ 汇总
  samples.jsonl   一行一个 AgentState
```

`RunLogger` 由 A 维护（`app/agent/run_logger.py`）。**写失败会直接抛异常**，不会静默丢数据。

---

## 2. 一次「样本」的定义

| 情况 | 记几条样本 |
|---|---|
| 一个问题 + 一个 `user_id`，跑一次 `run()` | 1 条 |
| B3/B4 跨会话记忆实验：同一 `user_id` 连续两轮 | **2 条**（`turn_index` 分别 0、1） |
| 同一问题换不同模式（B1→B4） | 每个模式各 1 条，落在**不同 run 目录** |

⚠️ **跨会话实验必须复用同一个 `user_id`**，否则记忆读不到。B1/B2 不读记忆，`user_id` 随便。

---

## 3. `meta.json` 字段

| 字段 | 含义 | 用途 |
|---|---|---|
| `commit` | 运行时的 git 短哈希 | 复现 |
| `model` | 模型名 | 复现、成本对比 |
| `generation.top_k` / `.mode` / `.flags` | 检索深度、基线、消融开关 | 复现 |
| `dataset.name` / `.size` | 数据集版本与条数 | 复现 |
| `started_at` / `finished_at` | ISO 8601 UTC | 耗时、排序 |
| `sample_count` | 实际写入条数 | 校验数据集是否跑全 |
| `totals.tokens` / `.cost_usd` / `.errors` | 全 run 汇总 | RQ4 |
| `summary` | 调用方传入的任意汇总；异常中断时含 `aborted` + `error` | 失败分析 |

**五项上下文缺一项，这个实验就无法复现。** 这是 CONTRIBUTING 第 11 节的硬要求。

---

## 4. `samples.jsonl` 字段语义

每行是一个完整的 `AgentState`（`model_dump_json()`），外加 `_sample_index`（从 0 开始）。

### 4.1 最容易搞错的四个字段

**① `route.route` 与 `route.need_retrieval` 不是一回事**

- `route.route`：Router 的三分类结果（`direct` / `document_retrieval` / `complex_task`）
- `route.need_retrieval`：归一化后的布尔

`Orchestrator` 已强制三者一致（direct 必 False、document_retrieval 必 True、complex_task 看情况）。
**RQ1 的准确率用 `route.route` 算**；`need_retrieval` 只用来算"不必要检索率"。

**② `citations` 是「召回的全部证据」，不是「答案里实际引用的」**

`state.citations` 由 `build_citations()` 按 `evidence` 顺序编号生成，**包含所有检索到的证据**，
哪怕模型一条都没引用。

要算「实际引用了哪些」有两个来源：
- 从 `final_answer` 里正则抽 `\[(\d+)\]`（`app/agent/answer_generator.py:extract_citation_indices` 可直接用）
- 读 `trace` 里 `stage="generate"`、`event="citation_audit"` 的 `detail.used` 与 `detail.invalid`

**③ `evidence` 已按 `chunk_id` 去重**，保持检索顺序。多步检索合并后不会重复。

**④ `usage` 已经包含 Router / Planner / MemoryManager 的调用**

A 在 Orchestrator 里用 `_absorb_usage()` 统一归集。所以 `usage.total_tokens` 是**整条链路**的成本，
不是只算回答生成那一次。RQ4 直接用，不要再自己加。

### 4.2 其他字段

| 字段 | 说明 |
|---|---|
| `evidence[].file_name` / `.page_number` | 页码引用的事实来源 |
| `evidence[].retrieval_method` | `vector` / `bm25` / `rrf` / `mock`，按通道拆统计用 |
| `evidence[].score` | **只保证越大越相关**，RRF 分与余弦分不可跨方法比较 |
| `claims[].status` | `supported` / `partially_supported` / `unsupported` |
| `claims[].supporting_evidence` | `chunk_id` 列表，可回 `evidence` 查页码 |
| `memory_context` | 本轮读到的记忆（`list[MemoryItem]`） |
| `errors` | 降级记录，形如 `"retrieval: RetrievalError: ..."` |
| `trace` | 全部事件；`stage` 有 `router` / `plan` / `retrieval` / `generate` / `verify` / `memory_write` / `pipeline` |

---

## 5. 每个 RQ 的指标口径

> 下面只给**定义与字段来源**。计算代码由 C 实现。

### RQ1：agent 能否选对信息来源

设 N = 样本数，标注为 `gold_route`。

| 指标 | 公式 | 字段 |
|---|---|---|
| tool-selection accuracy | `count(route.route == gold_route) / N` | `route.route` |
| 不必要检索率 | `count(gold=direct 且 need_retrieval=True) / count(gold=direct)` | `route.need_retrieval` |
| 漏检率 | `count(gold≠direct 且 need_retrieval=False) / count(gold≠direct)` | 同上 |
| 混淆矩阵 | 3×3，行=gold，列=pred | `route.route` |

**报告里必须同时给准确率和混淆矩阵。** 只报准确率会被问"错在哪一类"。

### RQ2：长期记忆是否提升个性化与跨会话完成度

| 指标 | 公式 | 字段 |
|---|---|---|
| 记忆命中率 | `count(本轮 memory_context 含标注的相关记忆) / count(应命中样本)` | `memory_context[].content` |
| 记忆写入拒绝率 | `1 - accepted / proposed` | `trace` 里 `memory_write` / `stats` 的 `proposed`、`accepted` |
| 跨会话完成度 | 第 2 轮是否需要用户重复第 1 轮已提供的信息（人工或规则判定） | 对比同 `user_id` 的相邻两条 |

⚠️ 写入拒绝率只有开了 `enable_memory` 且当轮有记忆信号时才有值。`stats.skipped` 非空表示这一轮没调模型（正常）。

### RQ3：验证是否提升 claim 支持度与引用可靠性

| 指标 | 公式 | 字段 |
|---|---|---|
| claim 支持率 | `count(supported) / count(可判定 claims)` | `claims[].status` + `note` |
| 引用精确率 | `1 - len(invalid) / len(used)` | `trace` 的 `citation_audit` |
| 引用召回率 | `被引用的证据数 / 标注应引用的证据数` | 需要标注 `gold_pages` |

⚠️ 关闭 `enable_verification` 时 `claims` 为空，**RQ3 指标不可算**。B1/B2 只能报引用精确率，不能报支持率。

`note` 以 `UNVERIFIED:` 开头表示校验器明确无法可靠判断，不等同于证据反驳。
这类 claim 单独计入 `unverified_claims`，不进入 claim 支持率分母。

### RQ4：效率与错误恢复

| 指标 | 公式 | 字段 |
|---|---|---|
| 单查询 token | `sum(usage.total_tokens) / N` | `usage.total_tokens` |
| 单查询成本 | `sum(usage.cost_usd) / N` | `usage.cost_usd` |
| 单查询延迟 | `sum(usage.latency_ms) / N` | `usage.latency_ms` |
| 错误恢复成功率 | `count(errors≠[] 且 final_answer≠"") / count(errors≠[])` | `errors`、`final_answer` |

---

## 6. 实验纪律（写进报告前必须满足）

1. **同条件对比**：B1–B4 用同一问题集、同一模型、同一温度、同一 `top_k`、同一份 PDF。
2. **重复三次**：随机性输出重复 3 次，报均值 ± 标准差。单次结果不能当结论。
3. **改评测代码必须重跑**：动了 `app/evaluation/` 或任何影响输出的模块，**全部基线重跑**。
   不能新结果用新代码、旧结果用旧代码拼一张表。
4. **失败案例留档**：`unsupported` 误判、检索错页、路由误判，记到 `docs/failure-cases.md`，附原始输入输出。
   失败案例比成功案例更能体现研究价值。

---

## 7. 已知的数据坑

| 坑 | 说明 |
|---|---|
| Mock 模式的 token 是估算值 | `MockLLM` 按 4 字符≈1 token 估算，**不能当真实成本**。RQ4 必须用真实模型跑 |
| RRF 的 score 很小 | 量级 0.03 左右（`1/(60+rank)` 累加），**不要当概率解读**，只能排序 |
| 无证据时不生成 claims | Orchestrator 跳过 Verification，UI 区分 direct 通用知识回答与检索未命中；RQ3 不纳入这些样本 |
| `UNVERIFIED:` 被当成 unsupported | 它表示证据矛盾或模型无法可靠判断，不是反驳；单独统计并从支持率分母排除 |
| `citations` 含未被引用的证据 | 见 4.1 ② |
| `evidence` 顺序 = 编号顺序 | Orchestrator 在生成后立刻 `build_citations()`，顺序绑定。若检索排序变了，编号会跟着变 |

---

## 8. 需要 C 确认的三件事

1. **数据集格式**：建议 JSONL，每行
   `{"id": "q001", "question": "...", "user_id": "u1", "gold_route": "document_retrieval", "gold_pages": [9, 27], "gold_claims": [...]}`。
   字段名定了之后 A 可以配合调整 `RunLogger` 的 `dataset` 元信息。
2. **是否需要 A 提供批量运行脚本**（读数据集 → 逐条跑 → 落盘）。若 C 自己写，A 不重复实现。
3. **Streamlit 要读哪个目录**：`runs/` 下按 run_id 列，还是由 C 自己维护索引。

---

## 变更记录

| 日期 | 版本 | 变更 |
|---|---|---|
| 2026-11-16 | v1 | 初版：入口、落盘结构、字段语义、四个 RQ 的指标口径、数据坑 |
