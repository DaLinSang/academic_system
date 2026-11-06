# PDF Ingestion 模块

> 成员 B 负责。  
> 本模块是后续 Embedding、BM25、向量检索和页码引用的输入层。

---

## 1. 模块职责

本模块负责：

- 检查 PDF 文件路径和文件类型；
- 使用 `pypdf` 打开 PDF；
- 逐页提取文本；
- 保留 PDF 原始页码；
- 跳过空白页面；
- 将页面文本转换为公共数据结构 `DocumentChunk`；
- 将底层 PDF 解析错误转换为 `IngestionError`。

本模块暂时不负责：

- 文本切分；
- Ollama Embedding；
- PostgreSQL/pgvector 写入；
- BM25 检索；
- 向量检索；
- RRF 混合排序；
- Agent 路由；
- 回答生成；
- Claim Verification。

当前限制
- 当前一页生成一个 chunk。
- 尚未实现文本长度切分。
- 尚未实现 Ollama embedding。
- 尚未写入 PostgreSQL。
- 尚未实现 BM25、向量检索和 RRF。
- 真实 PDF 集成测试将在后续补充。

当前阶段的目标是先保证：

```text
PDF → 页面文本 → DocumentChunk
