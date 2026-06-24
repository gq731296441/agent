from __future__ import annotations


def build_zmail_network_error(e: Exception, hint: str = "请求网络错误") -> RuntimeError:
    """
    将网络异常统一为 zmail 的对外错误文案。

    说明：
    - 只负责“文案一致化”，不做日志、不做重试、不做吞错；
    - 调用方如需保留异常链，请使用：raise build_zmail_network_error(e) from e
    """
    reason = getattr(e, "reason", None) or str(e)
    hint = (hint or "").strip() or "请求网络错误"
    return RuntimeError(f"zmail {hint}: {reason}")

