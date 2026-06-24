"""Session 启动状态。

状态流转：CREATED → WARMING_AGENT → WARMING_SANDBOX → READY
失败时进入 ERROR，可自动恢复重新预热。
"""

from enum import Enum


class SessionStartStatus(str, Enum):
    CREATED = "CREATED"
    WARMING_AGENT = "WARMING_AGENT"
    WARMING_SANDBOX = "WARMING_SANDBOX"
    READY = "READY"
    ERROR = "ERROR"
