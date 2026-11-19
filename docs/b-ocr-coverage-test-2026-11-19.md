# B：扫描 PDF 与长文本、多轮检索测试记录

## 问题与复现材料

用户提供 HKU TEC x Plug and Play.pdf（1 页，pypdf 提取 0 字符），导入报 No searchable text。test_longtext.pdf、test_memory.pdf、router.pdf、websearch.pdf 是界面测试记录，不是论文语料。

长文本问题：总结 rag_paper.pdf 的目标、方法、结论，原结果仅利用第三页技术片段。多轮问题：第二轮的“它”原样送入检索，即使生成阶段收到历史，也无法保证检索理解指代。

## 本次实现

- 指定已索引文件名时，仅在该文件集合内选证据；不存在的显式 PDF 返回空并记录警告。
- 指定文档的总结问题：先寻找摘要、引言、方法、结果、结论标题，然后按查询维度轮流补充不同页面，保留字符块原文、文件名和页码。标题匹配项 score=0，为启发式选择，不代表相关性概率。
- 文件内向量检索与 BM25 均保留；向量失败时仍限定来源并暴露降级警告。
- 总结问题预算至少六条；普通问题保留用户 top-k。生成器现有最多六条证据预算仍生效。
- 部分中英文短指代追问补充最近两轮用户问题，单轮最多300字符；不将历史助手答案当作检索事实。此为有限规则，不声称覆盖所有自然语言指代。
- OCR 执行失败显示页码和受限长度的 stderr，便于区分缺少语言包、执行失败和超时。
- --real 批量评测缺少 key 时拒绝启动，不允许静默产出 Mock 正式表格。

## 已执行验证

全量原有测试通过；新增覆盖文件范围、缺失文件、摘要页面覆盖、向量服务离线、top-k预算、多轮查询及缺少key的评测保护。
实际连接当前 PostgreSQL 与 Ollama，查询“请总结 rag_paper.pdf 的研究目标、方法和主要结论”，返回同文件第1、2、5、3、12、9页。这验证来源范围和多页召回，不代表生成质量或完整论文覆盖已经验收。

## OCR 环境状态与复验

代码已有自动 OCR：空文字页 → pdftoppm 渲染 → Tesseract → 页内分块 → embedding → PostgreSQL。本机 Homebrew 已安装 tesseract 5.5.4；pdftoppm 可执行。`tesseract --list-langs` 当前含 eng、osd。OCR 扫描件端到端验收已完成；中文扫描件仍需对应语言包。

安装完成后执行：

```bash
which pdftoppm tesseract
tesseract --list-langs
uv run python -c 'from app.ingestion import parse_pdf; c=parse_pdf("data/raw/HKU_TEC_x_Plug_and_Play.pdf"); print([(x.page_number, x.text[:120]) for x in c])'
```

真实检索路径已完成验证；UI 仍可按同样步骤复验。确认返回 chunk 数量，询问“HKU_TEC_x_Plug_and_Play.pdf 中活动的主题是什么？引用原文和页码”，对照原页人工核验。中文需要已安装 chi_sim/chi_tra；PDF_OCR_LANG 只能填已安装语言。OCR 不保证复杂图表、公式或手写文字准确。

## 真实 OCR → 索引 → 引用结果

执行 `parse_pdf("data/raw/HKU_TEC_x_Plug_and_Play.pdf")` 得到 1 条 chunk，page_number=1，OCR 文本含 `LECTURE 6`、`19 NOV 2026`、`18:30 — 20:00`、`Rayson Huang Theatre`。使用 HybridRetriever.index_document 同文件得到 1 条入库 chunk；retrieve 返回同文件第1页（vector）。

随后在已配置 HKU DeepSeek-V3.2 的真实 B4 链路提问“请根据 HKU_TEC_x_Plug_and_Play.pdf 回答 LECTURE 6 的日期、时间和地点，并引用页码。”Router 选 document_retrieval，回答给出 2026年11月19日星期四、18:30至20:00、Rayson Huang Theatre，三处均标 [1]；引用显示 HKU_TEC_x_Plug_and_Play.pdf p.1；Verifier 三条 supported；无错误。OCR 将图片文字识别为可检索原文后，后续流程与普通 PDF 相同。

OCR 也把页眉误识为 `EAI icadomy aa`，说明扫描页仍可能有错字；本例仅核验日期、时间、地点。该运行是单例端到端检查，不能代替课程正式评价指标。

## A/C 集成与边界

公共 Retriever 签名不变。A 审核生成器六条证据上限、规划查询与指代规则；C 展示 last_warning 对应的 trace、页码和文件状态。尚未解决任意长文全文总结、任意指代、OCR依赖缺失时的部分页面跳过、跨会话BM25缓存更新等问题，不能据此宣称全功能完美。

## Issue #49

保留开放。团队冻结云端模型、语料、代码、查询预算后，再运行：

```bash
uv run python -m app.evaluation.batch_runner --real --modes B1 B2 B3 B4 --top-k 5 --repeats 3 --runs-dir runs/formal-v1
```

本次检索策略发生变化，旧正式结果不可与新策略混合。需C生成并审核50×4×3结果、12个run目录和失败案例；本PR不包含或冒充正式研究结果。
