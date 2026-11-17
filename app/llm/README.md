# LLM 统一调用层

本模块是系统调用聊天模型的唯一入口。Router、Planner、Answer Generator 和
LLM Memory Manager 都通过 `get_llm()` 获得模型，不应在各自模块中直接创建
OpenAI 客户端或读取环境变量。

## 1. 支持的运行方式

| 模式 | `LLM_PROVIDER` | 认证方式 | 用途 |
|---|---|---|---|
| HKU Student GenAI | `hku` | `api-key` Header | 有 HKU Active subscription 的成员 |
| DeepSeek 官方 API | `deepseek` | Bearer authentication | 使用个人 DeepSeek key 的成员 |
| MockLLM | 任一 provider，但对应 key 留空 | 无外部请求 | 无 key 开发、单元测试和界面调试 |

认证方式由 `app/llm/client.py` 根据 provider 自动选择。团队成员只修改自己
电脑上的 `.env`，无需修改 Python 代码。

## 2. 首次配置

在项目根目录执行：

```bash
cp .env.example .env
```

`.env` 已加入 `.gitignore`。任何成员都不得提交、截图或在群聊中发送真实 API key。

### 2.1 使用 HKU Student GenAI

先在 HKU Developer Portal 订阅 `Student GenAI Services`，确认 subscription
状态为 `Active`。在个人 `.env` 中设置：

```dotenv
LLM_PROVIDER=hku

HKU_API_KEY=your-own-subscription-key
HKU_BASE_URL=https://api.hku.hk/foundry/student/models
HKU_MODEL=DeepSeek-V3.2
```

HKU Chat Completions 的完整请求地址是：

```text
https://api.hku.hk/foundry/student/models/chat/completions
```

`HKU_BASE_URL` 不应包含末尾的 `/chat/completions`，因为 OpenAI SDK 会自动追加。
HKU 网关要求 `api-key` Header；`get_llm()` 会仅在 `LLM_PROVIDER=hku` 时添加它。

### 2.2 使用 DeepSeek 官方 API

在个人 `.env` 中设置：

```dotenv
LLM_PROVIDER=deepseek

DEEPSEEK_API_KEY=your-own-deepseek-key
DEEPSEEK_BASE_URL=https://api.deepseek.com/v1
DEEPSEEK_MODEL=deepseek-chat
```

DeepSeek 使用 SDK 默认的 Bearer authentication，不会收到 HKU 的 `api-key` Header。

### 2.3 使用 MockLLM

如果当前没有可用 key，把所选 provider 对应的 key 留空即可：

```dotenv
LLM_PROVIDER=deepseek
DEEPSEEK_API_KEY=
```

程序会自动退回 `MockLLM`。Mock 模式可测试编排流程，但答案是固定测试文本，
不能用于评价真实 Router、回答质量、引用质量或 Memory 的 LLM 决策效果。

## 3. 验证配置

每次修改 `.env` 后重新启动 Python 进程，然后运行：

```bash
uv run python scripts/check_env.py
```

HKU 成功示例：

```text
[1] LLM 调用
      provider = hku
  [OK] model=DeepSeek-V3.2
```

DeepSeek 成功时 `provider` 应显示 `deepseek`，并返回所配置的模型名。

再运行完整真实链路：

```bash
docker compose up -d
uv run python -m app.demo --real -m B4 \
  -q "这篇论文解决了什么问题？请引用检索证据。"
```

成功的真实链路不应出现“模型已退回 MockLLM”。

## 4. 其他模块如何调用

所有模块只通过统一工厂获取模型：

```python
from app.llm.client import get_llm

llm = get_llm()
response = llm.generate(
    [{"role": "user", "content": "Explain RAG briefly."}]
)
print(response.text)
print(response.usage.total_tokens)
```

不要在业务模块中：

- 直接 `import OpenAI`；
- 直接读取 `HKU_API_KEY` 或 `DEEPSEEK_API_KEY`；
- 手工添加认证 Header；
- 把 key 放进函数参数、日志、测试数据或异常信息。

这样 Router、Planner、Answer Generator 和 Memory Manager 会共同使用同一 provider，
并且 token、调用次数和延迟能够统一计入 `LLMResponse.usage`。

## 5. 常见错误

| 输出 | 原因 | 处理方式 |
|---|---|---|
| `未配置 API key，当前是 MockLLM` | provider 对应的 key 为空 | 检查 `LLM_PROVIDER` 和对应 key |
| `401 missing subscription key` | HKU 请求没有携带 `api-key` Header | 确认使用包含本次修复的代码，并设置 `LLM_PROVIDER=hku` |
| `401 Unauthorized` | key 无效、已轮换或复制错误 | 在对应平台重新生成 key 并更新本地 `.env` |
| `429` | 达到每分钟或配额限制 | 等待后重试，减少连续调用 |
| 修改 `.env` 后仍使用旧 provider | 当前 Python 进程缓存了配置 | 关闭并重新启动终端进程 |
| Ollama 返回 `502` | 本机代理/VPN 拦截 localhost | 关闭相关代理，或为 localhost 设置 `NO_PROXY` |

## 6. 开发与测试

修改本模块后运行：

```bash
uv run pytest tests/test_llm_client.py -v
uv run pytest -q
uv run ruff check .
git diff --check
```

`tests/test_llm_client.py` 会检查：

- HKU provider 会添加 `api-key` Header；
- DeepSeek provider 不会添加 HKU Header；
- Header 能正确传入 OpenAI-compatible 客户端。
