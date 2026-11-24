# Verification module

## 1. 模块职责

把草稿答案拆成独立 claim，使用 Orchestrator 已检索到的 Evidence 判断每条 claim 是 supported、partially_supported 或 unsupported，并返回置信度、证据 chunk_id 和说明。本模块不负责检索、生成答案或修改公共数据结构。

## 2. 负责人

- 实现：Member C
- 验证：待组员 review

## 3. 对外公共接口

SemanticVerifier 与 LexicalVerifier 都实现冻结的 Verifier Protocol：

    def verify(self, answer: str, evidence: list[Evidence]) -> list[ClaimCheck]

回答后补检索必须复用 `split_claims()` / `split_claim_spans()`，不要在 Agent
编排层再维护第二套切句规则。`ClaimSpan` 同时提供原回答中的字符偏移，供 UI
做正文内联标记。

## 4. 输入数据格式

- answer：AnswerGenerator 生成的草稿答案。
- evidence：app.core.types.Evidence 列表；允许为空。

## 5. 输出数据格式

返回 app.core.types.ClaimCheck 列表。supporting_evidence 只包含输入 Evidence 中真实存在的 chunk_id，供 UI 定位文件和页码。

Prompt、当前会话和长期记忆通过 `build_verification_context()` 建立独立的
`VerificationContext`。这些来源可以支持用户自述或偏好，但
`academic_citation=False`，不能伪装成 PDF/Web Evidence 或显示为学术引用。冲突时
优先级为：当前 Prompt > 最近会话 > 长期记忆。

## 5.1 Claim 切分规则

- Markdown 标题和纯小节标签不进入 Claim。
- 事实性列表项保留，并去掉列表符号。
- 来源/参考资料区、代码块、表格分隔线不进入 Claim。
- “资料不足”、礼貌语、能力描述和运行状态不进入 Claim。
- 引用编号会附着到前一条事实，便于后续定位证据。

## 6. 依赖的其他模块

SemanticVerifier 只通过 A 提供的统一 LLM Protocol 调用模型，不直接访问供应商 SDK、数据库或 Retriever。LexicalVerifier 不需要模型，保留为可复现规则基线和语义校验失败时的降级路径。

## 7. 安装或配置要求

没有新增第三方依赖。Real 模式需要项目已有的 HKU 或 DeepSeek 模型配置；没有可用模型时自动降级到 LexicalVerifier。

## 8. 最小运行示例

    from app.core.types import Evidence
    from app.verification import SemanticVerifier
    from app.llm.client import get_llm

    verifier = SemanticVerifier(get_llm())
    checks = verifier.verify(answer, evidence)

## 9. 测试命令

    uv run pytest tests/test_verification.py -v
    uv run pytest -q
    uv run ruff check app/verification tests/test_verification.py

## 10. 正常输出示例

ClaimCheck 包含 claim、status、confidence、supporting_evidence 和 note。Real 链路没有 Evidence 时由 Orchestrator 跳过校验；Verifier 被单独调用且无 Evidence 时返回带 UNVERIFIED note 的降级结果。

## 11. 常见错误及处理方法

| 现象 | 原因 | 处理 |
|---|---|---|
| supporting_evidence 无法定位 | 使用了文件名而不是 chunk_id | 只返回输入 Evidence.chunk_id |
| unsupported 比例异常高 | 把引导语、结语或纯引用当成 claim | 检查 split_claims 的元语句过滤 |
| 指令确认被标 unsupported | 把“明白/收到/后续不再”等承诺当成事实 | `split_claims` 会排除确认、承诺和运行状态元话语 |
| 中英文语义一致但词面无重叠 | 使用了 LexicalVerifier | Real 模式使用 SemanticVerifier；词法版本仅作 baseline/fallback |
| 引用编号存在但内容不支持 | 把引用存在误当成支持 | SemanticVerifier 只检查该 claim 实际引用的 Evidence 内容 |
| 无 Evidence 的 direct 回答显示错误 | 把未校验当 unsupported | Orchestrator 跳过校验，UI 显示“基于模型通用知识” |

## 12. 当前限制

SemanticVerifier 的判断质量取决于所配置模型，不能把 confidence 当作事实正确概率。UI 将其明确展示为“校验判断置信度”。无法可靠判断时使用 UNVERIFIED note，并从 RQ3 claim support rate 分母排除。LexicalVerifier 的数值只是词面支持得分。

## 13. 最近一次验收状态

- 日期：2026-11-17
- 验收人：Member C 本地自动测试
- Verifier 固定测试集与单元测试：通过
- 完整仓库测试：通过
- Ruff 静态检查：通过
- 跨语言支持、部分支持、错误引用、矛盾 Evidence、无 Evidence 路径测试：通过
- Mock Orchestrator B4 注入测试：通过
- 指令确认、行为承诺、搜索运行状态和纯页码来源说明：已从事实 claim 中排除（Issue #80）
- 结论：Issue #30 的代码与测试修复完成，等待团队 review
