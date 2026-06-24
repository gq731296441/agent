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
