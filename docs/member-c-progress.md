# Member C Progress

更新日期：2026-11-16

## 任务边界

依据项目规划和仓库分工，Member C 只负责：

1. Claim 拆分与证据校验
2. Streamlit Web 界面与验证结果展示
3. 小型标注数据集
4. B1-B4 评测脚本、结果整理和失败案例分析
5. Demo 中与 UI、Verifier、实验图表相关的部分

不修改 A 的编排逻辑、公共接口和模型调用层，也不实现 B 的检索、PDF 解析、
embedding、数据库或记忆存储。

## 已完成

### Claim Verification V1

- 回答拆分为独立 claim
- `supported`、`partially_supported`、`unsupported` 三分类
- 置信度、真实 Evidence `chunk_id` 和说明
- 空证据及内部异常降级
- 固定 claim 测试集、单元测试和 B4 集成测试
- 模块 README
- 已通过 PR #9 合入 `main`

### Streamlit UI V1

- 四区页面基础结构：设置、聊天、文档、记忆
- Mock / Real 和 B1-B4 模式
- PDF 上传与 Retriever 接口导入
- 回答、页码引用和证据原文
- Claim 绿色、黄色、红色状态、置信度和证据页码
- 记忆查看、编辑和删除
- 运行 token、成本、延迟及错误展示
- UI 纯展示逻辑测试和 Mock 浏览器验收

## 下一步

1. 与组员在 Real 环境完成 PDF 上传、检索、记忆和 Verifier 联调
2. 准备固定 Demo 问题及 unsupported claim 示例
3. 建立小型标注数据集，先确定 JSONL/CSV 字段与 5-10 条样例
4. 编写统一 B1-B4 批量运行和结果落盘脚本
5. 汇总质量、延迟、token、成本及失败案例图表

其中第 1 项依赖 A/B 的真实服务环境，但 C 不修改其内部实现。
