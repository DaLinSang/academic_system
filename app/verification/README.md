# Verification module

## 1. 模块职责

把草稿答案拆成独立 claim，使用 Orchestrator 已检索到的 Evidence 判断每条 claim 是 supported、partially_supported 或 unsupported，并返回置信度、证据 chunk_id 和说明。本模块不负责检索、生成答案或修改公共数据结构。

## 2. 负责人

- 实现：Member C
- 验证：待组员 review

## 3. 对外公共接口

LexicalVerifier 实现冻结的 Verifier Protocol：

    def verify(self, answer: str, evidence: list[Evidence]) -> list[ClaimCheck]

## 4. 输入数据格式

- answer：AnswerGenerator 生成的草稿答案。
- evidence：app.core.types.Evidence 列表；允许为空。

## 5. 输出数据格式

返回 app.core.types.ClaimCheck 列表。supporting_evidence 只包含输入 Evidence 中真实存在的 chunk_id，供 UI 定位文件和页码。

## 6. 依赖的其他模块

只依赖 app.core.types 与冻结的 app.core.interfaces.Verifier 契约，不依赖数据库、LLM、Retriever 内部实现或 API key。

## 7. 安装或配置要求

没有新增依赖或环境变量。阶段 0 Mock 环境即可运行。

## 8. 最小运行示例

    from app.core.types import Evidence
    from app.verification import LexicalVerifier

    verifier = LexicalVerifier()
    checks = verifier.verify(answer, evidence)

## 9. 测试命令

    uv run pytest tests/test_verification.py -v
    uv run pytest -q
    uv run ruff check app/verification tests/test_verification.py

## 10. 正常输出示例

ClaimCheck 包含 claim、status、confidence、supporting_evidence 和 note。无证据时返回 unsupported，不抛异常。

## 11. 常见错误及处理方法

| 现象 | 原因 | 处理 |
|---|---|---|
| supporting_evidence 无法定位 | 使用了文件名而不是 chunk_id | 只返回输入 Evidence.chunk_id |
| unsupported 比例异常高 | 把引导语、结语或纯引用当成 claim | 检查 split_claims 的元语句过滤 |
| 深度改写被误判 | V1 是词汇覆盖率 baseline | 后续保持接口不变，替换为 NLI 或 LLM verifier |

## 12. 当前限制

V1 是确定性词汇覆盖率方法，不能可靠识别否定、数字冲突、深度改写或多步证据。中文使用相邻双字 token，英文过滤常见功能词。

## 13. 最近一次验收状态

- 日期：2026-11-06
- 验收人：Member C 本地自动测试
- Verifier 固定测试集与单元测试：通过
- 完整仓库测试：通过
- Ruff 静态检查：通过
- Mock Orchestrator B4 注入测试：通过
- 结论：本地开发完成，等待组员 review 后再推送并创建 PR
