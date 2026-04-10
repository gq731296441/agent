# app/security.py
"""
安全防护中间件模块

防护能力：
1. 恶意请求特征拦截（Spring4Shell、路径遍历、代理探测等）
2. IP 速率限制（滑动窗口）
3. 可疑 User-Agent 过滤
"""

import time
import re
from collections import defaultdict
from typing import Tuple

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import JSONResponse

from config import get_logger

logger = get_logger(__name__)

# ==================== 恶意特征规则 ====================

# URL / 查询参数中的恶意关键词（不区分大小写）
_MALICIOUS_PATTERNS: list[re.Pattern] = [
    # Spring4Shell / Spring RCE 探测
    re.compile(r"class\.module\.classLoader", re.IGNORECASE),
    re.compile(r"class\.getClass\(\)", re.IGNORECASE),
    # 路径遍历
    re.compile(r"\.\./"),
    re.compile(r"\.\.\\"),
    # 常见漏洞探测路径
    re.compile(r"(etc/(passwd|shadow|hosts))", re.IGNORECASE),
    re.compile(r"(proc/self/environ)", re.IGNORECASE),
    # 代理探测（URL 以 http:// 开头的路径）
    re.compile(r"^https?%3A//", re.IGNORECASE),
    re.compile(r"^https?://", re.IGNORECASE),
    # PHP / JSP / ASP 探测
    re.compile(r"\.(php|jsp|asp|aspx|cgi|env|git|svn)", re.IGNORECASE),
    # Shell 注入
    re.compile(r"(\$\{|%24%7B).*(\}|%7D)", re.IGNORECASE),
    re.compile(r"(;|\|)\s*(ls|cat|curl|wget|bash|sh|nc|ncat)\b", re.IGNORECASE),
]

# 可疑 User-Agent 关键词
_SUSPICIOUS_UA_KEYWORDS = [
    "sqlmap", "nmap", "nikto", "dirbuster", "gobuster",
    "masscan", "nuclei", "zgrab", "httpx",
]

# ==================== 速率限制器 ====================

class _RateLimiter:
    """基于滑动窗口的 IP 速率限制器。"""

    def __init__(self, max_requests: int = 60, window_seconds: int = 60):
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        # ip -> list[timestamp]
        self._requests: dict[str, list[float]] = defaultdict(list)
        self._last_cleanup = time.monotonic()

    def is_allowed(self, ip: str) -> Tuple[bool, int]:
        """
        检查 IP 是否在限速范围内。
        Returns: (是否允许, 剩余可用次数)
        """
        now = time.monotonic()

        # 定期清理过期数据（每 60s 一次，避免内存泄漏）
        if now - self._last_cleanup > 60:
            self._cleanup(now)

        timestamps = self._requests[ip]
        cutoff = now - self.window_seconds
        # 移除窗口外的旧请求
        while timestamps and timestamps[0] < cutoff:
            timestamps.pop(0)

        remaining = self.max_requests - len(timestamps)
        if remaining <= 0:
            return False, 0

        timestamps.append(now)
        return True, remaining - 1

    def _cleanup(self, now: float):
        cutoff = now - self.window_seconds
        expired_ips = [
            ip for ip, ts in self._requests.items()
            if not ts or ts[-1] < cutoff
        ]
        for ip in expired_ips:
            del self._requests[ip]
        self._last_cleanup = now


# 被拦截的 IP 黑名单（自动封禁）
class _AutoBanList:
    """恶意 IP 自动封禁（触发恶意规则后封禁一段时间）。"""

    def __init__(self, ban_seconds: int = 600, threshold: int = 5):
        self.ban_seconds = ban_seconds
        self.threshold = threshold
        # ip -> {"count": int, "banned_until": float}
        self._records: dict[str, dict] = defaultdict(lambda: {"count": 0, "banned_until": 0.0})

    def record_violation(self, ip: str) -> bool:
        """记录一次违规，返回是否触发封禁。"""
        now = time.monotonic()
        record = self._records[ip]
        record["count"] += 1
        if record["count"] >= self.threshold:
            record["banned_until"] = now + self.ban_seconds
            logger.warning(f"【安全】🚫 IP {ip} 已被自动封禁 {self.ban_seconds}s（累计违规 {record['count']} 次）")
            return True
        return False

    def is_banned(self, ip: str) -> bool:
        now = time.monotonic()
        record = self._records.get(ip)
        if record and record["banned_until"] > now:
            return True
        # 封禁过期，重置计数
        if record and record["banned_until"] > 0 and record["banned_until"] <= now:
            record["count"] = 0
            record["banned_until"] = 0.0
        return False


# ==================== 全局实例 ====================

# 普通请求速率限制：每个 IP 每 60 秒最多 120 次请求
_rate_limiter = _RateLimiter(max_requests=120, window_seconds=60)

# 恶意 IP 自动封禁：累计 5 次恶意请求后封禁 10 分钟
_auto_ban = _AutoBanList(ban_seconds=600, threshold=5)


# ==================== 检测函数 ====================

def _is_malicious_request(path: str, query_string: str) -> bool:
    """检查 URL 路径 + 查询字符串是否匹配恶意特征。"""
    full_url = f"{path}?{query_string}" if query_string else path
    for pattern in _MALICIOUS_PATTERNS:
        if pattern.search(full_url):
            return True
    return False


def _is_suspicious_ua(user_agent: str) -> bool:
    """检查 User-Agent 是否包含已知扫描工具特征。"""
    if not user_agent:
        return False
    ua_lower = user_agent.lower()
    return any(kw in ua_lower for kw in _SUSPICIOUS_UA_KEYWORDS)


# ==================== 中间件 ====================

class SecurityMiddleware(BaseHTTPMiddleware):
    """
    安全防护中间件。

    执行顺序（每个请求）：
    1. IP 封禁检查 → 被封禁的 IP 直接 403
    2. 恶意特征检测 → 命中则 403 + 累计违规
    3. 可疑 UA 检测 → 命中则 403 + 累计违规
    4. IP 速率限制 → 超限则 429
    """

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        client_ip = request.client.host if request.client else "unknown"
        path = request.url.path
        query_string = request.url.query or ""
        user_agent = request.headers.get("user-agent", "")

        # 1. 封禁检查
        if _auto_ban.is_banned(client_ip):
            return JSONResponse(
                status_code=403,
                content={"detail": "Forbidden"},
            )

        # 2. 恶意特征检测
        if _is_malicious_request(path, query_string):
            _auto_ban.record_violation(client_ip)
            logger.warning(
                f"【安全】🛡️ 拦截恶意请求: {client_ip} {request.method} {path}"
                f"{'?' + query_string if query_string else ''}"
            )
            return JSONResponse(
                status_code=403,
                content={"detail": "Forbidden"},
            )

        # 3. 可疑 UA 检测
        if _is_suspicious_ua(user_agent):
            _auto_ban.record_violation(client_ip)
            logger.warning(f"【安全】🛡️ 拦截可疑 UA: {client_ip} UA={user_agent[:100]}")
            return JSONResponse(
                status_code=403,
                content={"detail": "Forbidden"},
            )

        # 4. 速率限制
        allowed, remaining = _rate_limiter.is_allowed(client_ip)
        if not allowed:
            logger.warning(f"【安全】⚡ IP {client_ip} 请求过于频繁，已限流")
            return JSONResponse(
                status_code=429,
                content={"detail": "Too Many Requests"},
                headers={"Retry-After": "60"},
            )

        # 放行，附加安全响应头
        response = await call_next(request)

        # 安全响应头
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["X-RateLimit-Remaining"] = str(remaining)

        return response
