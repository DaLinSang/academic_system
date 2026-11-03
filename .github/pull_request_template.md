## 这个 PR 做了什么

<!-- 一两句话说清楚，不要只写"更新代码" -->

## 影响的模块

- [ ] `app/core`（公共结构 / 接口 / 配置）—— **需要三人确认**
- [ ] `app/agent`（A）
- [ ] `app/routing` `app/planning` `app/llm`（A）
- [ ] `app/ingestion` `app/retrieval` `app/memory`（B）
- [ ] `app/verification` `app/ui` `app/evaluation`（C）
- [ ] `tests/` `docs/` `scripts/`

## 是否修改公共接口

- [ ] 否
- [ ] 是 → 改了哪些签名？影响谁？是否已通知 B / C？

## 验证方式

```bash
uv run pytest -v
uv run ruff check .
```

<!-- 贴关键输出，或说明手动验证步骤 -->

## 自检清单

- [ ] 测试通过，且新增功能有对应测试
- [ ] 没有提交 `.env` / API key / 真实数据
- [ ] 更新了本模块的 README
- [ ] 关联并关闭了对应的 Issue（`Closes #123`）
- [ ] 若改了实验相关代码，已说明需要重跑哪些实验

## 给 reviewer 的提示

<!-- 希望对方重点看哪里？哪里你不确定？ -->
