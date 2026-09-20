"""沙箱相关异常。"""

from typing import List


class SandboxUserLimitExceeded(Exception):
    """用户 running 沙箱数已达 SANDBOX_MAX_PER_USER 上限。"""

    def __init__(self, message: str, running_sessions: List[str] | None = None):
        super().__init__(message)
        self.running_sessions = running_sessions or []

    def to_error_payload(self) -> dict:
        return {
            "code": "SANDBOX_USER_LIMIT",
            "msg": str(self),
            "running_sessions": self.running_sessions,
        }


class SandboxUnavailable(Exception):
    """沙箱基础设施不可用。

    覆盖 Docker daemon 故障、ssh:// 隧道断开、远端账号异常、镜像缺失等
    **非用户可控**的故障。

    与 `SandboxUserLimitExceeded` 的区别是关键的：配额超限是用户可自行处理的
    业务错误（应上报前端），本异常是基础设施故障（调用方应降级为"无 execute
    继续对话"）。此前两者都是裸 Exception，泛捕获无法区分，导致过一个真实
    缺陷：`manager_ext` 的降级分支把配额错误也吞成了静默降级。

    判别优先用 `isinstance`；消息模式匹配仅作兜底（见 errors/recovery.py）。
    """

    def __init__(self, message: str, cause: Exception | None = None):
        super().__init__(message)
        self.cause = cause

    def to_error_payload(self) -> dict:
        return {
            "code": "SANDBOX_UNAVAILABLE",
            "msg": str(self),
        }
