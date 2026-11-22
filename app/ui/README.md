# Streamlit UI

## 职责

Member C 负责把现有 Orchestrator 的输出展示为可演示的 Web 页面，不在 UI
中重新实现 Router、Retriever、MemoryStore 或 Verifier。

## 启动

```bash
PYTHONPATH=. uv run streamlit run app/ui/streamlit_app.py
```

默认使用 Mock 链路，无需 API key、Ollama 或 PostgreSQL。切换到 Real 后需要按
根目录 README 启动数据库、配置模型并完成文档索引。

## 当前功能

- Mock / Real 链路切换
- B1-B4 模式选择和 Top-K 设置
- 长期记忆与 Claim Verification 开关
- 桌面三栏布局：左侧会话导航、中间聊天、右侧运行/文档/记忆状态
- 左侧按最近时间展示会话标题与最后问题摘要，支持切换、新建、重命名和删除
- 右侧在桌面宽度保持 sticky；窄屏自动移到聊天区下方，原生侧栏可折叠
- 聊天输入区附近快速上传 PDF；Real 模式通过 Retriever 接口建立索引
- 上传过程区分保存、文字层检查、扫描页 OCR、embedding、索引完成或失败
- OCR 失败保留页码/工具诊断，并给出 Poppler、Tesseract 与语言包安装提示
- 文档列表从 PostgreSQL 读取持久化索引元数据，并与本次上传状态分开呈现
- 同名文件重导入失败时显示旧索引仍可检索，并警告当前上传内容未入库
- 失败状态保留错误类型、失败阶段和诊断；无旧索引时明确不可检索
- 可持久化的多轮聊天；创建、切换、重命名、删除和清空当前会话
- 页面刷新或服务重启后通过 SessionStore 恢复完整回答状态
- 文档页码引用与可点击网页引用
- 只展示回答正文实际使用的引用编号，不把全部 Top-K 候选伪装成引用
- “资料不足”拒答不展示无关候选页码，并单独提示未找到支持本问题的证据
- Claim 的绿色、黄色、红色状态展示
- 来源引用、Claim Verification 和运行信息默认折叠，并在标题显示数量
- unsupported / unverified claim 与运行错误在折叠面板外保留简洁提示
- Claim 置信度及对应文档页码或网页链接展示
- 运行信息显示网页搜索未触发、成功条数或失败原因
- 来源状态区分未上传、已上传未索引、已索引但未命中、文档、网页、direct 与检索失败
- 记忆区显示当前 user/session、本轮保存状态与已保存数量，并区分无记录和读取失败
- 运行信息按 trace 展示路由、检索、搜索、生成、校验和记忆阶段及耗时
- 记忆查看、编辑和删除；数据库不可用时明确显示读取失败
- token、模型调用次数、成本、延迟和错误信息展示

## 测试

```bash
uv run pytest tests/test_ui.py -q
uv run pytest -q
uv run ruff check app/ui streamlit_app.py tests/test_ui.py
```

`app/ui/presenters.py` 保持为纯展示转换层，因此不启动浏览器也能测试 claim
颜色映射、真实 evidence 页码解析、实际引用过滤、资料不足呈现、OCR 预检测、
安全错误提示、来源状态、会话导航摘要、详情面板计数和执行阶段摘要。
