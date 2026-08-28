"""DN 智能体调用模块

通过统一接口 `dn_search_imap` 调用 DN 应用智能体，按 `agent_name` 路由：

- `iMAP_Data_Analysis`        【在用】有线imap分析智能体-对接iCenter空间
- `iMAP_Report_Analysis`      【在用】有线imap分析智能体-指标报告
- `iMAP_Indicator_Statistics` 【在用】有线imap分析智能体-数据查询

`query_request` 完全透传，不做字段校验或改写；业务参数由调用方（OpenClaw LLM）
按 SKILL.md 中的场景配方构造。凭证取 `coclaw_empno` / `coclaw_token` 环境变量，
函数参数可显式覆盖。

CLI 用法：
    python scripts/dn_agent.py <agent_name> '<query_request JSON>'
    echo '<query_request JSON>' | python scripts/dn_agent.py <agent_name> -
"""

import argparse
import json
import os
import sys
from typing import Any, Dict, Optional

import requests

# 允许的智能体白名单（agent_name 校验，query_request 不做业务校验）
ALLOWED_AGENTS: tuple = (
    "iMAP_Data_Analysis",
    "iMAP_Report_Analysis",
    "iMAP_Indicator_Statistics",
)

# 默认接口地址；可用 IMAP_DN_API_URL 环境变量覆盖（测试/网关迁移用）
DEFAULT_API_URL = "https://aip.zte.com.cn/dashboard/api/mcp/dn_search_imap"

# 网关约 180 秒超时，客户端默认留出余量；可用 --timeout / 参数覆盖，不自动重试
DEFAULT_TIMEOUT = 175

# iMAP_Report_Analysis 提示词泄漏特征（应用异常时智能体会向调用方索要内容）
_REPORT_LEAK_KEYWORDS: tuple = (
    "请提供需要处理",
    "请提供您需要",
    "用户未提供",
    "请补充包含",
)


def _fail(
    error: str,
    raw_response: Optional[Any] = None,
    raw_text: Optional[str] = None,
) -> Dict[str, Any]:
    """构造统一失败结构"""
    result: Dict[str, Any] = {"status": False, "error": error}
    if raw_response is not None:
        result["raw_response"] = raw_response
    if raw_text is not None:
        result["raw_text"] = raw_text
    return result


def _extract_error_message(body: Dict[str, Any]) -> str:
    """从服务端响应中提取可读错误信息（不透出敏感内容）"""
    data = body.get("data") or {}
    code_info = data.get("code") or {}
    msg = code_info.get("msg") or code_info.get("msgId")
    return str(msg) if msg else str(body.get("code") or "未知错误")


def _build_payload(
    agent_name: str,
    query_request: dict,
    empno: str,
    auth_value: str,
) -> Dict[str, Any]:
    """构造请求体；query_request 原样透传，不修改"""
    return {
        "query_request": query_request,
        "agent_name": agent_name,
        "emp_no": empno,
        "auth_value": auth_value,
    }


def _is_report_leak(result: str) -> bool:
    """判断 iMAP_Report_Analysis 结果是否为提示词泄漏（应用异常）

    正常报告是 markdown 表格（含 `|` 分隔符）且通常较长；
    泄漏文本较短、不含表格分隔符，且命中索要内容的特征词。
    仅在 agent_name == iMAP_Report_Analysis 时调用，避免误伤 iCenter 链接等。
    """
    if not isinstance(result, str):
        return False
    if len(result) >= 200:
        return False
    if "|" in result:
        return False
    return any(keyword in result for keyword in _REPORT_LEAK_KEYWORDS)


def call_dn_agent(
    agent_name: str,
    query_request: dict,
    empno: Optional[str] = None,
    auth_value: Optional[str] = None,
    timeout: int = DEFAULT_TIMEOUT,
    api_url: Optional[str] = None,
) -> Dict[str, Any]:
    """调用 DN 智能体，返回统一结构字典。

    成功：
        {"status": True, "result": str, "agent_name": str, "raw_response": dict}
    失败：
        {"status": False, "error": str, "raw_response": dict, "raw_text": str}

    凭证优先级：显式参数 > coclaw_empno / coclaw_token 环境变量；
    任一缺失时快速失败，不发起请求，错误只提示变量名。
    """
    if agent_name not in ALLOWED_AGENTS:
        return _fail(
            f"未知 agent_name: {agent_name}，允许值: {', '.join(ALLOWED_AGENTS)}"
        )

    empno = empno or os.getenv("coclaw_empno")
    auth_value = auth_value or os.getenv("coclaw_token")
    missing = [name for name, value in (
        ("coclaw_empno", empno),
        ("coclaw_token", auth_value),
    ) if not value]
    if missing:
        return _fail(f"缺少凭证，请设置环境变量: {'、'.join(missing)}")

    url = api_url or os.getenv("IMAP_DN_API_URL") or DEFAULT_API_URL
    payload = _build_payload(agent_name, query_request, empno, auth_value)

    # 构造标准认证 headers（使用统一的 get_auth_headers）
    emp = empno or os.getenv('coclaw_empno')
    auth = auth_value or os.getenv('coclaw_token')
    yx = os.getenv('coclaw_yxdata_token')
    # 若未提供 yxdata_token，尝试从 AuthManager 获取
    if not yx and emp and auth:
        try:
            from auth import AuthManager
            yx = AuthManager().get_token(emp, auth)
        except Exception:
            yx = None
    headers = get_auth_headers(yx, auth, emp)
    # 过滤 None 值
    headers = {k: v for k, v in headers.items() if v is not None}

    try:
        response = requests.post(url, json=payload, headers=headers, timeout=timeout)
    except requests.exceptions.Timeout:
        return _fail(f"请求超时（{timeout} 秒），DN 应用可能仍在处理，请稍后重试")
    except requests.exceptions.RequestException as exc:
        return _fail(f"请求失败: {exc}")

    if response.status_code < 200 or response.status_code >= 300:
        return _fail(f"HTTP {response.status_code}", raw_text=response.text)

    try:
        body = response.json()
    except ValueError:
        return _fail("响应不是合法 JSON", raw_text=response.text)

    data = body.get("data") or {}
    code_info = data.get("code") or {}
    code = code_info.get("code")
    if code is not None and code != "0000":
        return _fail(_extract_error_message(body), raw_response=body)

    bo = data.get("bo") or {}
    result = bo.get("result")
    if not isinstance(result, str) or not result.strip():
        return _fail("DN 应用未返回有效结果", raw_response=body)

    if agent_name == "iMAP_Report_Analysis" and _is_report_leak(result):
        return _fail(
            "DN 应用返回异常（疑似服务端未取到数据），请稍后重试或联系管理员",
            raw_response=body,
        )

    return {
        "status": True,
        "result": result,
        "agent_name": agent_name,
        "raw_response": body,
    }


def _parse_query_request(raw: str) -> Dict[str, Any]:
    """解析 CLI 的 query_request：顶层必须是 JSON 对象，字段值不校验"""
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"query_request 不是合法 JSON: {exc}")
    if not isinstance(parsed, dict):
        raise ValueError("query_request 顶层必须是 JSON 对象")
    return parsed


def _format_raw_output(result: Dict[str, Any]) -> str:
    """构造 --raw 的原始响应文本（JSON 缩进可读，非 JSON 原样）"""
    raw_text = result.get("raw_text")
    if raw_text:
        return raw_text
    raw_body = result.get("raw_response")
    if raw_body is not None:
        return json.dumps(raw_body, ensure_ascii=False, indent=2)
    return ""


def main(argv: Optional[list] = None) -> int:
    parser = argparse.ArgumentParser(
        description="调用 DN 智能体（dn_search_imap）",
    )
    parser.add_argument(
        "agent_name",
        choices=ALLOWED_AGENTS,
        help="智能体名称，允许值: " + ", ".join(ALLOWED_AGENTS),
    )
    parser.add_argument(
        "query_request",
        nargs="?",
        help="JSON 对象；或传 - 从 stdin 读取",
    )
    parser.add_argument(
        "--raw",
        action="store_true",
        help="把原始服务端响应打印到 stderr（默认仅 stdout 输出 result）",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=DEFAULT_TIMEOUT,
        help=f"超时秒数（默认 {DEFAULT_TIMEOUT}）",
    )
    args = parser.parse_args(argv)

    if args.query_request is None:
        print(
            "错误: 缺少 query_request 参数，用法: python dn_agent.py "
            "<agent_name> '<JSON对象>' （或传 - 从 stdin 读取）",
            file=sys.stderr,
        )
        return 2
    try:
        raw = sys.stdin.read() if args.query_request == "-" else args.query_request
        query_request = _parse_query_request(raw)
    except ValueError as exc:
        print(f"错误: {exc}", file=sys.stderr)
        return 2

    result = call_dn_agent(args.agent_name, query_request, timeout=args.timeout)

    if result.get("status"):
        print(result["result"])
        if args.raw:
            raw_output = _format_raw_output(result)
            if raw_output:
                print(raw_output, file=sys.stderr)
        return 0

    print(f"错误: {result.get('error')}", file=sys.stderr)
    if args.raw:
        raw_output = _format_raw_output(result)
        if raw_output:
            print(raw_output, file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
