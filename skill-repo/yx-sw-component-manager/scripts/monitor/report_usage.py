#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
技能使用埋点 - 核心上报脚本（yx-sw-component-manager 自包含副本）

源自 skill-usage-monitor 组件（scripts/report_usage.py），复制进本技能以实现自包含，
不依赖兄弟目录 skill-usage-monitor。技能方在任务结束时调用，记录"谁、用了哪个技能、
成没成功、耗时"。技能方只需提供少量参数，其余字段由组件自动采集 / 服务端补全。

设计原则：
  1. 静默失败：上报失败绝不影响技能主流程（超时短、快速失败）。
  2. 幂等：event_id 全局唯一，重复上报服务端不重复入库。
  3. 离线兜底：上报失败时写入本地缓存文件，可后续补传。
  4. 身份字段由服务端从 X-Emp-No/X-Auth-Value 反查补全，组件不上报 emp_name 等。

用法（Python import）：
    from report_usage import send_report
    send_report(skill_name="...", status="success", ...)

用法（CLI）：
    python3 report_usage.py --skill-name yx-sw-component-manager --status success [--module xxx]

退出码：
    0  上报成功（或已被幂等去重）
    2  上报失败（已写入离线缓存）——技能方无需关心，主流程不受影响
"""

import argparse
import json
import os
import sys
import time
import uuid
import re
from datetime import datetime

# ============================================================================
# 配置
# ============================================================================
DEFAULT_BASE_URL = "http://10.57.153.195:8080/v1/cmai/skill_usage"
REPORT_PATH = "/report"
TIMEOUT_SECONDS = 3          # 上报请求超时，绝不影响主流程
MAX_RETRIES = 2              # 网络重试次数（快速失败）
MAX_FIELD_LEN = {
    "event_id": 64,
    "env": 20,
    "emp_no": 20,
    "emp_name": 50,
    "dept_name": 100,
    "client_source": 20,
    "session_id": 64,
    "skill_name": 100,
    "skill_version": 20,
    "skill_owner": 20,
    "module": 100,
    "input_summary": 500,
    "status": 20,
    "error_code": 50,
    "output_files": 500,
}
# 认证环境变量候选（按入口体系从高到低）：co-mind / co-claw / 通用
EMP_NO_ENVS = ["coclaw_empno", "RDC_EMP_NO", "X_Emp_No", "X_EMP_NO", "X-Emp-No"]
TOKEN_ENVS = ["coclaw_token", "RDC_AUTH_TOKEN", "X_Auth_Value", "X_AUTH_VALUE", "X-Auth-Value"]

# 离线缓存：自包含副本放在技能目录内 .skill_usage_cache/（由 .skillignore 排除，不污染技能包）
_skill_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DEFAULT_CACHE_DIR = os.path.join(_skill_root, ".skill_usage_cache")


def detect_client_source():
    """根据环境变量自动判定使用渠道（co-mind / co-claw 等）。

    规则（按优先级）：
      有 COMIND_ENTRYPOINT        -> co-mind
      有 coclaw_empno/token       -> co-claw
      否则                        -> unknown（调用方可显式传 client_source 覆盖）
    """
    if os.getenv("COMIND_ENTRYPOINT"):
        return "co-mind"
    if os.getenv("coclaw_empno") or os.getenv("coclaw_token"):
        return "co-claw"
    return "unknown"

# ============================================================================
# 工具函数
# ============================================================================
def now_str():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _truncate(value, max_len):
    """按字段上限截断字符串"""
    if value is None:
        return value
    value = str(value).strip()
    if max_len and len(value) > max_len:
        return value[: max_len]
    return value


def _sanitize(value, max_len=500):
    """脱敏：截断并屏蔽常见敏感信息（工号、Token、路径、邮箱）"""
    if value is None:
        return None
    value = str(value).strip()
    # 先屏蔽 token 型长串（32位以上十六进制），再处理数字，避免被拆散
    value = re.sub(r"[a-f0-9]{32,}", "****", value, flags=re.IGNORECASE)
    # 屏蔽 6 位以上数字串（工号/订单号）
    value = re.sub(r"\d{6,}", "****", value)
    # 屏蔽邮箱
    value = re.sub(r"[\w.+-]+@[\w-]+\.[\w.]+", "****@****", value)
    return _truncate(value, max_len)


def read_skill_meta(skill_dir):
    """从技能目录的 SKILL.md frontmatter 读取 name/version/author"""
    meta = {"skill_name": None, "skill_version": None, "skill_owner": None}
    if not skill_dir:
        return meta
    skill_md = os.path.join(skill_dir, "SKILL.md")
    if not os.path.isfile(skill_md):
        return meta
    try:
        with open(skill_md, "r", encoding="utf-8") as f:
            content = f.read()
        front = content.split("---", 2)
        if len(front) >= 2:
            for line in front[1].splitlines():
                if line.startswith("name:"):
                    meta["skill_name"] = line.split(":", 1)[1].strip().strip('"\'')
                elif line.startswith("version:"):
                    meta["skill_version"] = line.split(":", 1)[1].strip().strip('"\'')
                elif line.startswith("author:"):
                    meta["skill_owner"] = line.split(":", 1)[1].strip().strip('"\'')
    except Exception:
        pass
    return meta


def _skill_dir():
    """调用方技能目录（本脚本的上上层，即 <skills>/<skill>/）"""
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _skill_meta():
    """读取调用方技能目录的 SKILL.md frontmatter（name/version/author）"""
    return read_skill_meta(_skill_dir())


def infer_skill_name(override=None):
    """技能名：优先用户传入；否则从调用方技能目录 SKILL.md 推断"""
    if override:
        return _truncate(override, MAX_FIELD_LEN["skill_name"])
    return _skill_meta().get("skill_name") or os.path.basename(_skill_dir())


def infer_skill_version(override=None):
    """技能版本：优先用户传入；否则从调用方技能目录 SKILL.md 推断"""
    if override:
        return _truncate(override, MAX_FIELD_LEN["skill_version"])
    return _truncate(_skill_meta().get("skill_version"), MAX_FIELD_LEN["skill_version"])


def infer_skill_owner(override=None):
    """技能开发人工号：优先用户传入；否则从调用方技能目录 SKILL.md 推断"""
    if override:
        return _truncate(override, MAX_FIELD_LEN["skill_owner"])
    return _truncate(_skill_meta().get("skill_owner"), MAX_FIELD_LEN["skill_owner"])


# ============================================================================
# 核心上报
# ============================================================================
def _build_payload(extra):
    """组装上报数据：组件采集 + 用户传入，身份字段由服务端补全。
    注意：服务端为 Java(Spring) DTO，字段统一使用驼峰命名（eventId/skillName/...）。
    """
    payload = {
        # 身份类：由服务端从请求头反查补全，组件只带工号（可选）以便服务端校验
        "empNo": _truncate(_first_env(EMP_NO_ENVS), MAX_FIELD_LEN["emp_no"]),
        # 通用
        "eventId": extra.get("event_id") or str(uuid.uuid4()),
        # 技能类（name/version/owner 均自动从 SKILL.md 推断，用户可覆盖）
        "skillName": infer_skill_name(extra.get("skill_name")),
        "skillVersion": infer_skill_version(extra.get("skill_version")),
        "skillOwner": infer_skill_owner(extra.get("skill_owner")),
        "module": _truncate(extra.get("module"), MAX_FIELD_LEN["module"]),
        "inputSummary": _sanitize(extra.get("input_summary"), MAX_FIELD_LEN["input_summary"]),
        # 结果类
        "status": _truncate(extra.get("status") or "success", MAX_FIELD_LEN["status"]),
        "errorCode": _truncate(extra.get("error_code"), MAX_FIELD_LEN["error_code"]),
        "errorMessage": _sanitize(extra.get("error_message"), 2000),
        "startTime": extra.get("start_time") or now_str(),
        "endTime": extra.get("end_time") or now_str(),
        "durationMs": int(extra.get("duration_ms") or 0),
        "retryCount": int(extra.get("retry_count") or 0),
        "outputFiles": _truncate(extra.get("output_files"), MAX_FIELD_LEN["output_files"]),
        # 可选（client_source 由组件主动上报并自动判定来源；服务端无法反查来源）
        "clientSource": _truncate(extra.get("client_source") or detect_client_source(), MAX_FIELD_LEN["client_source"]),
        "sessionId": _truncate(extra.get("session_id"), MAX_FIELD_LEN["session_id"]),
        "env": _truncate(extra.get("env"), MAX_FIELD_LEN["env"]),
    }
    return {k: v for k, v in payload.items() if v not in (None, "")}


def _first_env(keys):
    """按候选顺序返回第一个存在的环境变量值"""
    for k in keys:
        v = os.getenv(k)
        if v:
            return v
    return None


def _get_headers():
    """认证头：X-Emp-No / X-Auth-Value，从调用方环境变量按候选顺序读取"""
    emp_no = _first_env(EMP_NO_ENVS) or ""
    auth = _first_env(TOKEN_ENVS) or ""
    return {
        "Content-Type": "application/json",
        "X-Emp-No": emp_no,
        "X-Auth-Value": auth,
    }


def _is_private_host(url):
    """判断目标主机是否为内网/私网地址（10/172.16-31/192.168/127/内网域名），是则直连绕代理"""
    from urllib.parse import urlparse
    host = urlparse(url).hostname or ""
    if not host:
        return False
    # IP 形式：私网段
    if re.match(r"^\d{1,3}(\.\d{1,3}){3}$", host):
        parts = host.split(".")
        if parts[0] == "10":
            return True
        if parts[0] == "172" and 16 <= int(parts[1]) <= 31:
            return True
        if parts[0] == "192" and parts[1] == "168":
            return True
        if parts[0] == "127":
            return True
        return False
    # 域名形式：内网域名后缀（zte.intra / .zte.com.cn 等）
    return host.endswith(".zte.intra") or host.endswith(".zte.com.cn") or host == "localhost"


def _http_post(url, payload, headers, timeout=TIMEOUT_SECONDS, max_retries=MAX_RETRIES):
    """带重试的 POST，快速失败"""
    import urllib.request
    import urllib.error

    data = json.dumps(payload).encode("utf-8")
    last_err = None
    # 内网/私网地址直连（绕过 http_proxy 环境变量），保证测试环境可达
    opener = None
    if _is_private_host(url):
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    for attempt in range(max_retries + 1):
        req = urllib.request.Request(url, data=data, headers=headers, method="POST")
        try:
            if opener:
                with opener.open(req, timeout=timeout) as resp:
                    return resp.status, resp.read().decode("utf-8", "ignore")
            else:
                with urllib.request.urlopen(req, timeout=timeout) as resp:
                    return resp.status, resp.read().decode("utf-8", "ignore")
        except urllib.error.HTTPError as e:
            last_err = e
            if e.code in (400, 401, 403, 422):
                # 客户端错误，重试无意义，快速失败
                return e.code, e.read().decode("utf-8", "ignore")
        except Exception as e:  # URLError / Timeout / ConnectionError
            last_err = e
        if attempt < max_retries:
            time.sleep(0.3)
    return None, str(last_err)


def _write_cache(payload):
    """离线缓存：上报失败时落盘，供后续补传"""
    try:
        os.makedirs(DEFAULT_CACHE_DIR, exist_ok=True)
        path = os.path.join(DEFAULT_CACHE_DIR, f"{payload['eventId']}.json")
        record = {"payload": payload, "cached_at": now_str()}
        with open(path, "w", encoding="utf-8") as f:
            json.dump(record, f, ensure_ascii=False, indent=2)
        return path
    except Exception:
        return None


def _is_biz_success(body):
    """判断上报响应体是否为业务成功：code 为 0/"0"/"0000"/"000" 等成功码"""
    if not body:
        return False
    try:
        data = json.loads(body)
    except Exception:
        # 非 JSON（如纯文本 ok / 空），仅当状态码 2xx 时视为成功
        return True
    if not isinstance(data, dict):
        return True
    code = data.get("code")
    if code is None:
        return True
    # 成功码统一规约：0 / "0" / "0000" / "000" / "success"（宽松判断数字全为0）
    if isinstance(code, (int, float)):
        return int(code) == 0
    if isinstance(code, str):
        return code.strip() in ("0", "000", "0000", "00000", "success", "SUCCESS")
    return True


def send_report(base_url=None, cache_dir=None, **kwargs):
    """
    上报一条技能使用记录（可 import 使用）。
    参数与 CLI 选项一一对应；返回 (ok, message)。
      ok=True   上报成功（或被幂等去重）
      ok=False  上报失败（已尝试写入离线缓存）
    """
    global DEFAULT_CACHE_DIR
    if cache_dir:
        DEFAULT_CACHE_DIR = cache_dir
    url = (base_url or DEFAULT_BASE_URL).rstrip("/") + REPORT_PATH

    payload = _build_payload(kwargs)

    # 1) 若已存在同名缓存，说明之前上报失败过，先直接补传缓存（幂等由服务端去重）
    cache_path = os.path.join(DEFAULT_CACHE_DIR, f"{payload['eventId']}.json")
    if os.path.isfile(cache_path):
        try:
            with open(cache_path, "r", encoding="utf-8") as f:
                cached = json.load(f)
            payload = cached.get("payload", payload)
        except Exception:
            pass

    headers = _get_headers()
    status, body = _http_post(url, payload, headers)

    # 成功判定：HTTP 2xx 且业务 code 为成功（兼容 0 / "0" / "0000" 等）
    if status is not None and 200 <= status < 300 and _is_biz_success(body):
        # 成功：删除离线缓存
        try:
            if os.path.isfile(cache_path):
                os.remove(cache_path)
        except Exception:
            pass
        return True, "success"
    else:
        # 失败：写离线缓存，静默不阻塞主流程
        _write_cache(payload)
        return False, f"report failed: status={status} body={body}"


# ============================================================================
# CLI
# ============================================================================
def build_parser():
    p = argparse.ArgumentParser(description="技能使用埋点上报")
    p.add_argument("--base-url", default=DEFAULT_BASE_URL, help="上报接口根地址")
    p.add_argument("--skill-name", default=None, help="技能名（默认从 SKILL.md 推断）")
    p.add_argument("--skill-version", default=None, help="技能版本（默认从 SKILL.md 推断）")
    p.add_argument("--skill-owner", default=None, help="技能开发人工号（默认从 SKILL.md 推断）")
    p.add_argument("--status", default="success",
                   choices=["success", "failed", "partial", "cancelled"], help="执行结果")
    p.add_argument("--error-code", default=None, help="失败错误码")
    p.add_argument("--error-message", default=None, help="失败原因（脱敏后）")
    p.add_argument("--module", default=None, help="技能内子模块")
    p.add_argument("--input-summary", default=None, help="用户输入摘要（脱敏）")
    p.add_argument("--output-files", default=None, help="产出物清单")
    p.add_argument("--start-time", default=None, help="开始时间 YYYY-MM-DD HH:MM:SS")
    p.add_argument("--end-time", default=None, help="结束时间 YYYY-MM-DD HH:MM:SS")
    p.add_argument("--duration-ms", type=int, default=None, help="耗时毫秒")
    p.add_argument("--retry-count", type=int, default=0, help="重试次数")
    p.add_argument("--event-id", default=None, help="事件ID（默认自动生成）")
    p.add_argument("--client-source", default=None, help="使用渠道")
    p.add_argument("--session-id", default=None, help="会话ID")
    p.add_argument("--env", default=None, help="环境 prod/test")
    return p


def main():
    args = build_parser().parse_args()
    ok, msg = send_report(**vars(args))
    # 静默失败：不向 stderr 输出错误细节，仅记录；成功也无须输出
    if ok:
        print("usage reported")
        return 0
    else:
        # 上报失败不阻塞主流程；打印一行提示便于排查（技能方可忽略）
        print(f"warning: usage report failed: {msg} (cached locally, will not affect your task)",
              file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
