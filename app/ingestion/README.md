# PDF Ingestion 模块

> 成员 B 负责。验证人：A。<br>
> 本模块把用户提供的 PDF 转换为带文件名和页码的 `DocumentChunk`，供后续切分、Embedding、BM25、向量检索和引用生成使用。

---

## 1. 模块定位

本模块位于整个系统的数据入口：

```text
PDF 文件
    ↓
PdfReader 逐页读取
    ↓
提取页面文本
    ↓
跳过空白页面
    ↓
DocumentChunk
    ↓
retrieval / memory / Agent 流程
```

`ingestion` 只负责把 PDF 读出来并保留来源信息，不负责回答问题，也不直接调用 LLM 或数据库。

## 2. 当前负责的内容

- 检查输入路径是否存在；
- 检查输入文件是否为 PDF；
- 使用 `pypdf.PdfReader` 打开 PDF；
- 按原始顺序逐页提取文本；
- 保留从 1 开始的 `page_number`；
- 跳过没有有效文本的空白页；
- 为页面文本生成公共数据结构 `DocumentChunk`；
- 将底层解析失败统一转换为 `IngestionError`。

## 3. 明确不负责的内容

下面的功能不属于当前 parser 的职责：

- 文本长度切分和 chunk overlap；
- Ollama 或其他模型的 Embedding；
- PostgreSQL / pgvector 写入；
- BM25 检索；
- 向量检索和 RRF 混合排序；
- Agent 路由、规划和回答生成；
- Claim Verification；
- Streamlit 用户界面。

这些功能会在后续由 B 的其他 PR，或由其他成员负责的模块中实现。

## 4. 文件结构

```text
app/ingestion/
├── __init__.py
├── pdf_parser.py       # 当前的 PDF 逐页解析实现
└── README.md           # 本模块说明

tests/
└── test_pdf_parser.py  # parser 的单元测试
```

## 5. 公共调用方式

当前提供的函数是：

```python
from app.ingestion.pdf_parser import parse_pdf

chunks = parse_pdf(
    pdf_path="data/raw/demo.pdf",
    document_id="demo-paper",
)
```

函数签名：

```python
def parse_pdf(
    pdf_path: str,
    document_id: str | None = None,
) -> list[DocumentChunk]
```

参数说明：

| 参数 | 类型 | 说明 |
|---|---|---|
| `pdf_path` | `str` | PDF 文件路径，可以是相对路径或绝对路径 |
| `document_id` | `str \| None` | 文档标识；不传时使用 PDF 文件名去掉扩展名的结果 |

返回值：

```python
list[DocumentChunk]
```

返回列表中的每个元素都包含页面文本和来源页码。没有有效文本时返回空列表，不把空页面生成成 chunk。

## 6. `DocumentChunk` 结构

`DocumentChunk` 来自公共契约文件 `app/core/types.py`，本模块不重复定义它。

当前 parser 主要填写以下字段：

| 字段 | 当前值 | 含义 |
|---|---|---|
| `chunk_id` | `document_id:p{page}:c{chunk}` | 当前 chunk 的稳定标识 |
| `document_id` | 参数值或文件名 stem | 文档标识 |
| `file_name` | PDF 文件名 | 原始文件名 |
| `page_number` | 从 `1` 开始 | PDF 原始页码 |
| `text` | 清理首尾空白后的文本 | 页面内容 |

例如：

```text
demo-paper:p2:c0
```

其中：

- `demo-paper`：文档 ID；
- `p2`：PDF 第 2 页，页码从 1 开始；
- `c0`：第 0 个 chunk。当前版本一页只生成一个 chunk，因此每页都是 `c0`。

后续实现页面内文本切分后，同一页可能出现：

```text
demo-paper:p2:c0
demo-paper:p2:c1
demo-paper:p2:c2
```

## 7. 错误处理

本模块不把底层的文件错误或 PDF 解析错误直接暴露给上层，而是统一抛出 `IngestionError`。

| 情况 | 行为 |
|---|---|
| 文件不存在 | 抛出 `IngestionError` |
| 扩展名不是 `.pdf` | 抛出 `IngestionError` |
| PDF 无法打开 | 抛出 `IngestionError` |
| 某一页提取失败 | 抛出包含页码信息的 `IngestionError` |
| 页面为空或只有空白符 | 跳过该页 |
| 所有页面都为空 | 返回 `[]` |

上层模块只需要依赖 `IngestionError`，不需要依赖 `pypdf` 的内部异常类型。

## 8. 与其他模块的关系

```text
app/ingestion
    ↓ 生成 DocumentChunk
app/retrieval
    ↓ 建立 BM25 / embedding / pgvector 索引并检索
app/agent
    ↓ 通过 Retriever 接口获取 Evidence
app/verification
    ↓ 使用 Evidence 校验回答中的 claim
```

跨模块调用必须使用 `app/core/types.py` 和 `app/core/interfaces.py` 中的公共契约。其他模块不应直接导入本模块的内部实现细节，也不应直接操作本模块的局部变量。

## 9. 依赖与配置

当前依赖：

- Python 3.11；
- `pypdf`：读取 PDF 并提取页面文本；
- `app.core.types.DocumentChunk`：公共输出结构；
- `app.core.errors.IngestionError`：统一异常类型。

当前 parser 不需要：

- API key；
- PostgreSQL；
- Docker；
- LLM 调用。

因此它可以在没有网络、没有数据库和没有真实模型的情况下运行单元测试。

## 10. 测试

运行本模块测试：

```bash
uv run pytest tests/test_pdf_parser.py -v
```

运行整个项目测试：

```bash
uv run pytest -q
```

测试覆盖：

- 正常 PDF 页面解析；
- 页码从 1 开始并正确保留；
- 文件名和文档 ID 保留；
- 空白页面跳过；
- 所有页面为空时返回空列表；
- 文件不存在；
- 输入不是 PDF；
- 页面文本提取失败。

测试使用假的 `PdfReader`、页面对象和临时文件，因此不依赖具体的真实 PDF 文件，也不会把测试数据提交到仓库。

## 11. 当前限制与后续计划

当前版本是 ingestion 的第一步，采用“一页一个 chunk”的临时策略。它优先保证页码引用和公共数据结构正确，但还没有针对长页面做文本长度控制。

后续计划：

1. 增加页面内文本切分；
2. 增加 chunk size 和 overlap 配置；
3. 填充 `char_start` 和 `char_end`；
4. 增加稳定的文档导入 ID 和重复导入处理；
5. 将 chunk 交给 retrieval 模块建立 BM25 和向量索引；
6. 补充真实 PDF 的集成测试。

## 12. 验收状态

- [x] PDF 路径和文件类型检查
- [x] 逐页文本提取
- [x] 1-based 页码保留
- [x] 空白页处理
- [x] `DocumentChunk` 输出
- [x] `IngestionError` 错误转换
- [x] 单元测试
- [x] 模块 README
- [ ] 页面内文本切分
- [ ] Embedding 和向量索引
- [ ] BM25 / RRF 混合检索
