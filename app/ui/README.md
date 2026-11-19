# Streamlit UI

## 职责

Member C 负责把现有 Orchestrator 的输出展示为可演示的 Web 页面，不在 UI
中重新实现 Router、Retriever、MemoryStore 或 Verifier。

## 启动

```bash
uv run streamlit run streamlit_app.py
```

默认使用 Mock 链路，无需 API key、Ollama 或 PostgreSQL。切换到 Real 后需要按
根目录 README 启动数据库、配置模型并完成文档索引。

## 当前功能

- Mock / Real 链路切换
- B1-B4 模式选择和 Top-K 设置
- 长期记忆与 Claim Verification 开关
- PDF 上传；Real 模式通过 Retriever 接口建立索引
- 可持久化的多轮聊天；创建、切换、重命名、删除和清空当前会话
- 页面刷新或服务重启后通过 SessionStore 恢复完整回答状态
- 文档页码引用与可点击网页引用
- 只展示回答正文实际使用的引用编号，不把全部 Top-K 候选伪装成引用
- “资料不足”拒答不展示无关候选页码，并单独提示未找到支持本问题的证据
- Claim 的绿色、黄色、红色状态展示
- Claim 置信度及对应文档页码或网页链接展示
- 运行信息显示网页搜索未触发、成功条数或失败原因
- 来源状态区分上传文档、网页、direct、无证据与检索失败
- 记忆查看、编辑和删除；数据库不可用时友好降级
- token、模型调用次数、成本、延迟和错误信息展示

## 测试

```bash
uv run pytest tests/test_ui.py -q
uv run pytest -q
uv run ruff check app/ui streamlit_app.py tests/test_ui.py
```

`app/ui/presenters.py` 保持为纯展示转换层，因此不启动浏览器也能测试 claim
颜色映射、真实 evidence 页码解析、实际引用过滤、资料不足呈现和上传文件名安全处理。
