# 智能学术助手会议问题整理与开发计划

> 重点：PDF 全文总结、Claim Verification、多来源证据、UI 优化与 ABC 协作
>
> 时间背景：项目临近提交，优先完成可演示、可测试、风险可控的 MVP。

> C 进度（2026-11-24）：标题/来源区/代码块过滤、事实列表保留、ClaimSpan
> 字符偏移、非学术 VerificationContext 契约和文档删除 UI 已完成；等待 A 在
> PR #107 中复用统一 Claim Splitter，并等待 B 提供真正的 file-scoped claim retrieval。

## 1. 会议结论概览

本次会议确认了以下核心问题：

1. 当前 PDF 总结依赖少量 Top-K Chunk，不能代表完整阅读了整篇文档。
2. Claim Verification 目前主要使用 RAG/Web Evidence，尚未完整纳入用户 Prompt、长期记忆和会话上下文。
3. Answer Generator、来源引用、Claim 状态和运行信息的边界需要进一步明确。
4. PDF 总结需要增加全文读取或分层总结链路，并在生成答案后按 Claim 回查原始证据。
5. UI 应以回答内容为中心，减少冗余文字、统一排版，并避免展示模型内部思考过程。
6. Web Search 应只保留稳定的自注册 API，停用质量不稳定的 Bing/DDG HTML 抓取链路。

---

## 2. 建议的总体流程

```text
用户问题
  ↓
A：Router 判断任务类型
  ├─ 普通直接问答
  ├─ RAG 文档问答
  ├─ 单篇 PDF 全文总结
  └─ 多文档比较/复杂任务
       ↓
B：准备回答上下文
  ├─ 用户 Prompt
  ├─ 长期记忆
  ├─ 会话上下文
  ├─ RAG Evidence
  ├─ Web Evidence
  └─ PDF 全文或分段摘要
       ↓
A：模型只生成回答正文
       ↓
C：将回答拆分为独立事实 Claim
       ↓
B：针对每条 Claim 补充检索证据
       ↓
C：综合全部证据校验 Claim
       ↓
C/UI：展示正文、引用和验证状态
```

回答前检索负责帮助模型生成高质量回答；回答后检索负责验证回答中的具体事实。两者不能完全互相替代。

---

## 3. PDF 总结功能方案

### 3.1 不建议所有 PDF 都直接塞入一次 Prompt

将完整 PDF 文本一次性传给模型只适合文本较短的文档。长文档可能出现：

- 超过模型 Context Window。
- Prompt 成本和响应时间过高。
- 模型忽略文档中部信息。
- OCR 噪声占用大量 Token。
- 难以稳定生成准确页码引用。
- 调用失败后需要重新处理整篇文档。

因此建议按照 Token Budget 分流。

### 3.2 小型 PDF

当清理后的全文低于安全阈值时：

```text
PDF → 逐页解析/OCR → 文本清理 → 全文缓存 → 一次性总结
```

建议 PDF 全文最多占模型 Context Window 的 50%–60%，为系统提示、用户问题和模型输出保留空间。

### 3.3 大型 PDF

采用分层总结：

```text
PDF
  ↓
按页面或章节分段
  ↓
每段生成结构化摘要
  ↓
合并章节摘要
  ↓
生成全文总摘要
  ↓
针对最终 Claim 回查原始 PDF 页面
```

该方法比 Top-K 总结覆盖更完整，也比无限制地将全文塞入模型稳定。

### 3.4 原文缓存

会议提出的进程内存缓存可以作为 MVP，但不应成为唯一缓存方式，因为 Streamlit 刷新或进程重启后会丢失。

截止前建议至少实现：

- 上传后保存原 PDF。
- 按页保存清理后的文本。
- 使用 `document_id + 文件哈希` 作为缓存键。
- 当前进程使用 Streamlit cache 加速。
- 每页文本保留原始页码。

后续可以增加 `document_pages` 数据表或本地 JSON cache。

### 3.5 Router 判断规则

| 用户请求 | 建议路径 |
|---|---|
| 总结我刚上传的这份 PDF | 单篇 PDF 总结 |
| 总结 `rag_paper.pdf` | 指定文档总结 |
| 这篇论文主要讲了什么，且当前只选择一个文件 | 单篇 PDF 总结 |
| 比较 A.pdf 和 B.pdf | 多文档复杂任务 |
| RAG 有什么优势 | 普通 RAG 问答 |
| 总结资料库，但没有指定文件 | 要求用户选择文件 |
| 文档较短 | 全文直接总结 |
| 文档较长 | 分层总结 |

建议增加：

```python
document_task = "qa" | "summary" | "comparison"
selected_document_ids = [...]
```

为了降低截止前的回归风险，可以先将其作为 `RouteDecision` 的附加字段，而不是立即修改现有 `RouteType`。

### 3.6 总结输出结构

推荐固定输出结构：

```markdown
## 文档概述

## 研究问题

## 方法

## 主要结果

## 局限性

## 结论
```

标题用于排版，不进入 Claim Verification；标题下面的事实段落和事实列表项可以进入校验。

---

## 4. Claim Verification 详细规则

### 4.1 证据来源分类

| 来源 | 可以支持的内容 | 是否作为学术引用 |
|---|---|---|
| PDF/RAG Evidence | 文档事实、论文方法、实验结论 | 是 |
| Web Evidence | 最新事实、公开网页信息 | 是 |
| User Prompt | 用户明确提供的信息、要求和限定 | 否，标记为用户提供 |
| Long-term Memory | 用户偏好、历史选择、用户自述 | 否，标记为长期记忆 |
| Conversation History | 当前会话已经确认的信息 | 否，标记为会话上下文 |
| Model Knowledge | 无外部证据的通用知识 | 不应伪装成已验证证据 |

例如，用户说“我的研究方向是 RAG”，回答中的“你的研究方向是 RAG”可以由 Prompt 或长期记忆支持，但不能显示成论文引用。

回答中的论文年份、实验数字、方法和结论等外部事实，不能仅由 Prompt 或长期记忆支持。

### 4.2 Evidence 类型建议

建议增加：

```python
source_type = (
    "document"
    | "web"
    | "user_prompt"
    | "long_term_memory"
    | "conversation"
)
```

同时保留：

```python
file_name
page_number
url
memory_id
text
```

如果当前阶段不适合修改公共 `Evidence`，C 可以先在 Verifier 内增加独立的 `VerificationContext`，不要将用户 Prompt 伪装成 PDF Chunk。

### 4.3 不应拆成 Claim 的内容

以下内容需要过滤：

- Markdown 标题，例如 `# 方法`、`## 结论`。
- 单独的小节标签，例如“主要发现：”。
- 表格表头。
- 纯引用，例如 `[1]`、`来源：p.3`。
- 礼貌语，例如“当然可以”。
- 引导语，例如“以下是论文总结”。
- 运行状态，例如“我搜索了相关资料”。
- 能力描述，例如“我将为你进行分析”。
- 用户指令复述。
- 格式说明，例如“下面以表格形式呈现”。
- 不完整的列表编号。
- “资料不足”等系统提示。
- 纯主观建议，除非其中包含可验证事实。
- Claim Verification 自己生成的文字。
- 来源区、警告区和运行信息区。

不能简单删除感叹号后的所有内容。例如：

> 实验结果提高了 20%！这一提升在三个数据集上均成立。

感叹号后的句子仍是需要校验的事实。应通过结构化输出和元语句规则过滤，而不是按标点整体截断。

### 4.4 应拆分的 Claim

一个 Claim 应只包含一个可以独立判断真假的事实单位。

原句：

> RAG 使用参数化记忆和非参数化记忆，并在三个任务中优于基线。

建议拆成：

1. RAG 使用参数化记忆。
2. RAG 使用非参数化记忆。
3. RAG 在三个任务中优于基线。

列表项：

```markdown
- 方法：模型使用 BM25 和向量检索。
```

应去掉列表符号和“方法：”标签，保留：

> 模型使用 BM25 和向量检索。

如果一句话包含多个数字、多个比较、多个实验结果或因果关系，应优先拆分，但不能切成失去主体或语义的短语。

### 4.5 标题、列表、表格和引用规则

| 内容 | 处理方式 |
|---|---|
| Markdown heading | 排除 |
| 只有冒号的小节标签 | 排除 |
| 标题后同一行包含完整事实 | 保留事实部分 |
| 列表项包含完整事实 | 保留 |
| 表格表头 | 排除 |
| 表格数据行 | 转成完整句子后校验 |
| 引用标记 | 依附前一个 Claim |
| 引用来源列表 | 排除 |
| 代码块 | 默认排除，除非用户要求校验代码事实 |

示例：

```markdown
## 主要结论
- RAG-Sequence 对整个输出序列使用同一组文档。[1]
```

只生成一个 Claim：

> RAG-Sequence 对整个输出序列使用同一组文档。[1]

“主要结论”不进入 Claim。

### 4.6 回答后检索

```text
最终回答
  ↓
Claim Splitter
  ↓
Claim 1 → 指定 PDF 范围检索
Claim 2 → 指定 PDF 范围检索
Claim 3 → 指定 PDF 范围检索
  ↓
合并原回答证据与新增检索证据
  ↓
Semantic Verifier
```

规则：

- 用户指定 PDF 时，只在指定 PDF 中回查。
- 多文档比较时，只能跨用户选择的文档检索。
- 已有引用的 Claim 优先检查引用对应 Evidence。
- 原证据不足或没有引用时，才触发补充检索。
- 每条 Claim 使用较小的 `top_k`，例如 3–5。
- 相同或相似 Claim 使用缓存。
- 最终引用必须指向原始 PDF 页面。
- 分段摘要可以帮助生成，但不能成为最终唯一证据。

### 4.7 Claim 状态定义

#### Supported

证据支持 Claim 的全部核心内容，包括主体、关系、数字、条件、时间、比较对象和因果关系。

#### Partially supported

证据只支持 Claim 的部分内容。

例如 Claim 表示“模型在三个数据集上显著优于所有基线”，证据只说明它在三个数据集上优于部分基线，应判为 partially supported。

#### Unsupported

- 没有相关证据。
- 证据与 Claim 无关。
- 证据明确与 Claim 矛盾。
- Claim 增加了证据没有提供的数字、时间或因果关系。

#### Unverified

建议保留内部 `unverified` 状态，用于模型调用失败、证据服务不可用或模型无法可靠判断。UI 显示“暂无法校验”，不要错误显示为 unsupported。正式 RQ3 指标应将其从有效判断分母中排除。

### 4.8 Prompt 与长期记忆冲突

推荐优先级：

```text
明确文档/Web 事实 > 用户本轮 Prompt > 当前会话确认信息 > 长期记忆
```

当前 Prompt 与长期记忆冲突时，以当前 Prompt 为准。用户偏好类 Claim 可以由记忆支持；论文结论、数字和日期不应由长期记忆单独支持。

---

## 5. 回答生成、引用和状态区边界

模型只负责生成回答正文，代码负责：

- Claim Verification。
- 来源列表。
- 警告状态。
- Evidence 标签。
- Web 跳转链接。
- 运行信息。

不应让模型生成红色警告区或固定来源区域，否则容易出现重复、错误拼接或伪造来源。

较稳定的模型输出格式：

```json
{
  "answer_markdown": "...",
  "used_evidence_indices": [1, 3]
}
```

如果截止前来不及改为结构化 JSON，至少在 Prompt 中要求：

```text
只输出回答正文，不要输出来源列表、校验结果、运行日志或后续建议。
```

---

## 6. UI 修改建议

### 6.1 截止前必须完成

- 对话中正常显示用户问题。
- 统一正文、标题、列表、表格字号。
- 缩小不必要的行距。
- 删除灰色冗余来源说明，只保留文件名、页码或可点击网页链接。
- 不展示模型内部思考过程。
- Claim 状态不使用大面积红色背景。
- 右侧资料库增加删除按钮，并增加二次确认。
- 设置入口移到左下角齿轮弹窗。
- 删除用户 ID、时间戳和 Real/Mock 说明性小字。
- 保留演示必要信息：索引状态、页数、Chunk 数和错误信息。

### 6.2 Claim 状态展示

- Supported：保持普通正文样式。
- Partially supported：使用主题色虚线下划线。
- Unsupported：使用红色或橙色虚线/波浪下划线。
- Unverified：使用灰色虚线下划线。
- 点击 Claim 后在右栏展示相关 Evidence。
- 不默认展开全部 Claim 卡片。

如果 `ClaimCheck` 尚未保存原答案字符偏移，截止前可以继续在 Claim Verification 折叠区显示状态。不要通过字符串全局替换实现正文高亮，因为重复句子可能被错误标记。

---

## 7. Web Search 调整

- 默认只保留稳定的 Tavily 或团队自注册 API。
- 从 Factory 中停用 DDG/Bing HTML fallback。
- 可以暂时保留旧代码，但不应进入默认运行链路。
- 测试使用 Mock Tavily，不依赖真实网络。
- UI 只显示结果标题与可点击 URL。
- 搜索失败时明确提示“网页搜索未成功”，不能用模型知识伪装成搜索结果。

---

## 8. ABC 分工

### 8.1 A：路由、编排与回答生成

负责：

1. Router 增加 `summary / qa / comparison` 文档意图。
2. 支持用户指定文件或当前选中文件。
3. 增加 PDF 总结链路。
4. 小 PDF 使用全文，大 PDF 调用分层总结。
5. 将 Prompt、Memory、PDF 和 Web 上下文传给生成器。
6. 保证 Answer Generator 只输出正文。
7. 编排回答后 Claim 检索与 Verifier。
8. Factory 默认只启用稳定 Web Search API。
9. 不向 UI 暴露内部思考过程。

A 依赖 B 提供文档全文/分段读取接口，依赖 C 提供 Verifier 新接口。

### 8.2 B：PDF、OCR、缓存与检索

负责：

1. PDF 逐页解析，保留页码和字符偏移。
2. OCR 识别与文本清理。
3. 提供完整文档读取接口。
4. 提供 Token 或字符长度估计。
5. 提供按章节或页面范围分段。
6. 缓存清理后的页面文本。
7. 提供指定文件范围内的 Claim 检索。
8. 保留混合 BM25 + Vector Retrieval。
9. 停用 DDG/Bing HTML Search。
10. 文档删除时同步删除原始文件、Chunk、BM25、向量索引和全文缓存。

B 只返回 Evidence，不负责判断 Claim 状态。

### 8.3 C：Claim Verification、UI 与评测

负责：

1. 扩展 Verification Context：Document、Web、User Prompt、Long-term Memory、Conversation。
2. 重写 Claim Splitter 规则。
3. 排除标题、来源列表、元语句和运行信息。
4. 为 Claim 增加原答案字符 offset。
5. 输出 supported、partially_supported、unsupported 和内部 unverified。
6. 区分学术来源和用户上下文支持。
7. UI 渲染 Claim 状态与证据来源。
8. 完成文档删除按钮、设置弹窗和排版清理。
9. 增加固定 Claim 测试集。
10. 更新 RQ3 指标，避免标题和 unverified 污染分母。

---

## 9. 前后依赖关系

```text
B1：全文读取接口 ─────────────┐
B2：指定文件 Claim 检索 ──────┤
B3：文档删除接口 ──────────────┤
                              ↓
A1：Router 总结意图
A2：全文/分层总结编排
A3：回答后检索编排
                              ↓
C1：Claim 拆分规则
C2：多来源 Verification
C3：UI 状态展示
C4：测试与评测
```

可以并行的工作：

- B 的 PDF 全文接口和 C 的 Claim Splitter 可以同时开发。
- A 可以先使用 Mock Full Document 和 Mock Verification 开发编排。
- UI 设置弹窗、文档删除按钮和排版调整可以独立开发。
- 停用 Bing/DDG 与 PDF 总结可以并行。

建议优先冻结以下接口：

```python
read_document(document_id) -> DocumentText
retrieve_for_claim(claim, document_ids, top_k=3) -> list[Evidence]
verify(answer, evidence, context=...) -> list[ClaimCheck]
```

---

## 10. 截止前优先级

### P0：必须完成

- PDF 总结 Router 可以识别指定文件。
- 小型 PDF 可以使用完整清理文本总结。
- 大型 PDF 至少明确降级为分段总结或提示限制。
- 标题、来源和元语句不进入 Claim。
- Prompt 和长期记忆可以作为带类型的 Verification Context。
- Answer 后能够按 Claim 补检索。
- UI 不显示模型思考过程。
- 默认只启用稳定 Web Search API。
- 全量测试通过。
- 演示 PDF、OCR、Docker、Ollama、PostgreSQL 和 Web API 提前检查。

### P1：能完成最好

- 大 PDF 分层总结。
- Claim 字符 offset 和正文内联下划线。
- 文档删除完整联动。
- 设置齿轮弹窗。
- 表格 Claim 转换。
- 缓存持久化。

### P2：报告中记录为限制

- 超长 PDF 的完整语义覆盖无法绝对保证。
- OCR 质量会影响总结准确度。
- Semantic Verifier 的判断依赖所配置模型。
- Claim 状态可能暂时只在折叠区展示。
- 功能逻辑改变后需要重新运行正式评测。
- 全文总结成本和延迟高于普通 RAG。

---

## 11. 建议交付顺序

1. 三人确认并冻结三个公共接口。
2. B 完成全文读取、指定文件检索和文档删除底层能力。
3. C 同时完成 Claim Splitter 新规则与测试集。
4. A 接入总结 Router 和全文/分层总结。
5. A 接入回答后 Claim 检索。
6. C 接入多来源 Verifier。
7. C 完成 UI 展示。
8. 三人共同执行验收测试。
9. 冻结代码。
10. 再运行正式实验，避免评测数据因功能变化而失效。

---

## 12. 演示验收清单

- [ ] 单篇短 PDF 总结。
- [ ] 单篇长 PDF 总结。
- [ ] 扫描 PDF OCR 总结。
- [ ] 指定 PDF 问答。
- [ ] 两篇 PDF 比较。
- [ ] 标题未被拆成 Claim。
- [ ] 中文和英文列表 Claim 拆分正确。
- [ ] Prompt 能支持用户自述类 Claim。
- [ ] 长期记忆能支持用户偏好类 Claim。
- [ ] Prompt 与长期记忆冲突时使用当前 Prompt。
- [ ] Unsupported Claim 正确显示。
- [ ] Web Search 成功时展示可点击链接。
- [ ] Web Search 失败时明确提示失败。
- [ ] 删除文档后不再能检索该文档。
- [ ] UI 不显示模型内部思考过程。
- [ ] Light/Dark 主题和字体排版正常。

---

## 13. 最终建议

不要把“全文总结”简单实现为无限制地将整篇 PDF 塞进一次 Prompt，也不要把模型生成的分段摘要作为最终唯一证据。

生成阶段可以使用全文或分层摘要，但 Claim Verification 最终应回到带原始文件名和页码的 PDF 文本。这样才能同时兼顾覆盖率、可解释性、引用可靠性和演示稳定性。
