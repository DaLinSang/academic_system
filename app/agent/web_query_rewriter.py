"""联网搜索查询改写（Issue #89）。

用户问句是自然语言（"你上网帮我搜一下计算机科学专业怎么样"），
直接丢给搜索引擎会被词的面截断误导——实测 Bing 会取首词"你"返回
"你（汉语文字）"，或把"计算机科学"切成"计算器"。

本模块用一次 LLM 调用把问句改写成适合搜索引擎的查询串：
  - 提取核心实体与主题，去掉"帮我/怎么样/请联网"等口语
  - 用完整、标准的术语（如"计算机科学与技术"而非"计算机科学"）
  - 保留问题里的专有名词、年份、机构名，不臆造事实
失败时回退到原始问句，绝不阻断联网链路。
"""

from __future__ import annotations

from app.core.interfaces import LLM

__all__ = ["WebQueryRewriter"]

_SYSTEM_PROMPT = """你是搜索查询优化器。把用户问题改写成一行最适合网页搜索引擎的查询串。

规则：
1. 只输出最核心的实体或主题词，总共 2-4 个词，越简洁越好。实测搜索引擎对长问句
   会做错误的词典式分词，短查询反而准确。
2. 用完整、标准的术语表达核心概念。例如"计算机科学专业"写成"计算机科学与技术"，
   "港大"写成"香港大学"。术语要准确，不要臆造问题里没有的事实。
3. 保留问题中的专有名词、机构名、年份、地点等关键限定词；删掉"帮我/请/上网/
   搜一下/怎么样/给我/介绍/前景"等口语和评价性修饰词。
4. 词之间用空格分隔，不要用标点符号，不要写成问句。
5. 只输出查询串本身一行，不要任何解释、引号或前缀。

示例：
用户："你上网帮我搜一下计算机科学专业怎么样"
→ 计算机科学与技术

用户："请联网查询香港大学目前的 QS 世界大学排名，给出排名年份和官网链接"
→ 香港大学 QS 排名

用户："查今天香港天气"
→ 香港天气"""


class WebQueryRewriter:
    """用 LLM 把自然语言问句改写成搜索查询串。

    `last_usage` 暴露用量，供 RQ4 归集这次改写调用的 token 与延迟。
    """

    def __init__(self, llm: LLM) -> None:
        self.llm = llm
        self.last_usage = None

    def rewrite(self, question: str) -> str:
        """返回改写后的查询串；失败或空结果时返回原问题。"""
        self.last_usage = None
        if not question or not question.strip():
            return question
        try:
            response = self.llm.generate(
                [
                    {"role": "system", "content": _SYSTEM_PROMPT},
                    {"role": "user", "content": question.strip()},
                ],
                temperature=0.0,
                max_tokens=96,
            )
            self.last_usage = response.usage
            query = self._clean(response.text)
            return query or question.strip()
        except Exception:  # noqa: BLE001 - 改写失败不能阻断联网
            return question.strip()

    @staticmethod
    def _clean(text: str) -> str:
        """只取第一行，去掉模型常见的引号/列表符/前缀。"""
        line = (text or "").strip().splitlines()[0] if (text or "").strip() else ""
        line = line.strip().lstrip("-*• ").strip()
        line = line.strip("\"'`")
        # 去掉"查询：/搜索词："这类前缀
        for prefix in ("查询：", "查询:", "搜索词：", "搜索词:", "query:"):
            if line.casefold().startswith(prefix.casefold()):
                line = line[len(prefix):].strip()
        return line[:120]
