# 交接给 C：两个上传入口的 UI 改造（后端已就绪）

> A 已完成后端能力（分支 `feat/conversation-attached-document`，PR 待开）。
> 本文件只描述 **C 在 `app/ui/` 要做的改动**，A 不越界改 UI。
> 目标：区分「知识库上传（持久化 RAG）」和「对话内上传（临时通读）」两类文档。

## 一、后端已经提供什么

`Orchestrator.run()` 新增两个可选参数（不改原有行为）：

```python
orch.run(
    question,
    ...,
    attached_document_text="<带 [Page N] 的全文文本>",   # 对话内临时文档
    attached_document_file_name="论文.pdf",              # 显示名
)
```

语义（A 已实现并测试）：
- 传了 `attached_document_text` → 后端**直接用这份全文通读回答**，不查数据库、不进 RAG 索引；
- 任何问题（不只是"总结"）都针对它；Claim 校验用全文分段做证据；
- 不传这两个参数 → 行为与现在完全一样（回归测试守住）。

关键 trace 事件（UI 可用来显示状态）：
- `document_read/attached`：`detail = {file_name, chars, source: "conversation"}`
- `post_hoc_retrieval/scoped_source`：`detail = {source: "attached_text", segments: N}`

## 二、C 要做的三件事

### 1. 把现有上传按钮移到右侧知识库面板

现状：`_render_upload()` 在聊天区（`streamlit_app.py:1049` 调用），上传即 `index_document()` 入库。
目标：这个入口保留"入库"语义，但摆到右侧知识库区域（`_render_documents()` 附近）。
这是**纯位置调整**，`_render_upload` 内部逻辑不用改。

### 2. 在对话框增加"+"号上传（不入库）

用 `st.file_uploader` 或 popover 放在聊天输入框附近，与知识库上传**用不同的 key**。
上传后（在上传那次交互里）解析成带页码的全文，存 `st.session_state`：

```python
from app.ingestion.pdf_parser import parse_pdf

def _on_chat_attachment(uploaded):
    # 存到临时路径（不进 data/raw，不建索引）
    tmp = Path(tempfile.gettempdir()) / "academic-chat" / safe_pdf_name(uploaded.name)
    tmp.parent.mkdir(parents=True, exist_ok=True)
    tmp.write_bytes(uploaded.getvalue())
    # parse_pdf 只解析、不 index_document → 不写 chunks/向量/document_pages
    chunks = parse_pdf(str(tmp))
    full_text = "\n\n".join(
        f"[Page {c.page_number}]\n{c.text}" for c in chunks
    )
    st.session_state["chat_attachment"] = {
        "file_name": safe_pdf_name(uploaded.name),
        "text": full_text,
    }
```

注意：`st.file_uploader` 对象只在上传那次交互有效，所以**必须在上传时解析成文本**存起来，不能等到发送时再读文件。

### 3. 提问时把临时文档传进后端

现在的调用（`streamlit_app.py:1073` 附近）：

```python
state = orch.run(question, session_id=..., user_id=..., turn_index=..., history=turns, **flags)
```

改成：

```python
attachment = st.session_state.get("chat_attachment")
state = orch.run(
    question,
    session_id=session_id, user_id=user_id, turn_index=len(turns), history=turns,
    attached_document_text=attachment["text"] if attachment else "",
    attached_document_file_name=attachment["file_name"] if attachment else None,
    **flags,
)
```

发送后是否清空附件？两种均可，建议：**保留在同一会话**，提供"清除附件"按钮让用户显式移除——与知识库的"当前文档"语义一致。

### 4.（可选）UI 状态显示

从 trace 读 `document_read/attached`，显示"本轮基于对话内文档《X》通读生成"；
普通 RAG 仍显示"本轮使用文档检索 + 来源页码"。

## 三、验收（对应最终任务安排 §7）

- [ ] 右侧上传 → 进知识库、可跨会话复用、可 Top-K 检索
- [ ] 对话框 + 号上传 → 不出现在知识库列表（`list_indexed_documents()` 里没有它）
- [ ] 上传后问"讲讲这篇文章" → 回答来自临时全文，UI 显示"对话内文档"
- [ ] 临时文档问具体问题（"它用了什么方法"）→ 针对该文档回答，不是六段式总结
- [ ] 删除知识库文档后，临时文档仍可用（互不影响）
- [ ] 不传附件时所有旧行为不变（A 的回归测试已守）

## 四、A 已做的验证

- `tests/test_attached_document_111.py`（5 项）：不查库、任意问题、分段证据匹配、端到端、无附件回归
- 全量 680 项测试通过，ruff 全过
- 真实 DeepSeek 端到端：临时全文 → 通读 → 正确回答 → Claim 校验 → errors=[]

## 五、注意

- 临时文档**不进 `data/raw`**、不写任何数据库表，刷新页面会丢（这是"留在对话框里"的设计代价）。
- OCR：如果 `parse_pdf` 检测到扫描页，会自动走 OCR（与知识库上传一致），需要本机有 poppler/tesseract。
- 大文档：`document_summary` 会自动分层总结，UI 不用关心长度。
