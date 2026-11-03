"""共享异常。

约定：模块内部可以抛任何异常，但**跨模块边界**必须转成这里的类型，
这样 Orchestrator 才能统一做降级和错误记录（Proposal 里的 "recovering from tool errors"）。
"""

from __future__ import annotations


class AcademicSystemError(Exception):
    """所有本系统异常的基类。"""


class RetrievalError(AcademicSystemError):
    """检索失败：索引不存在、数据库不可达、embedding 服务挂了。"""


class IngestionError(AcademicSystemError):
    """PDF 解析失败：加密、损坏、扫描件无文本层。"""


class MemoryStoreError(AcademicSystemError):
    """记忆存取失败。"""


class LLMError(AcademicSystemError):
    """模型调用失败：超时、鉴权、限流、返回空。"""


class VerificationError(AcademicSystemError):
    """校验过程失败。注意：判定为 unsupported 不算错误。"""
