"""s3canner 适配器：进程结果与安全结论分离。只出本地报告，不上报。"""

from __future__ import annotations

import asyncio
import copy
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import threading
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, List, Optional

from config.config import app_config

logger = logging.getLogger(__name__)

_SEVERITY_RANK = {"critical": 4, "high": 3, "medium": 2, "low": 1, "info": 0}

# id, 展示名, 完成时百分比
SCAN_STAGE_DEFS = [
    ("queue", "排队等待处理", 4),
    ("validate", "校验包结构", 12),
    ("prepare", "准备扫描环境", 18),
    ("start", "启动 s3canner", 24),
    ("rules", "加载扫描规则", 30),
    ("spec", "规范检查", 38),
    ("metadata", "元数据检查", 46),
    ("instructions", "指令风险", 54),
    ("paths", "路径检查", 62),
    ("general", "通用检查", 68),
    ("scripts", "脚本检查", 76),
    ("sensitive", "敏感信息", 84),
    ("binaries", "二进制检查", 90),
    ("analyze", "启发式分析", 95),
    ("report", "生成报告", 98),
    ("done", "扫描完成", 100),
]

_SCANNER_LINE = re.compile(r"scanner=([a-z]+)")
ProgressCallback = Optional[Callable[[Dict[str, Any]], Awaitable[None]]]

_RULE_STAGE = {
    "META": "metadata",
    "INST": "instructions",
    "SCRIPT": "scripts",
    "SENS": "sensitive",
    "SPEC": "spec",
    "BIN": "binaries",
    "PATH": "paths",
    "GEN": "general",
    "PKG": "validate",
    "STRUCT": "validate",
    "DUP": "validate",
}


def _now_str() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def new_scan_progress() -> Dict[str, Any]:
    now = _now_str()
    stages = [
        {"id": sid, "label": label, "status": "pending", "findings": None, "at": None}
        for sid, label, _pct in SCAN_STAGE_DEFS
    ]
    if stages:
        stages[0]["status"] = "running"
        stages[0]["at"] = now
    return {
        "phase": "queue",
        "percent": 4,
        "label": "排队等待处理",
        "stages": stages,
    }


def _stage_percent(stage_id: str) -> int:
    for sid, _label, pct in SCAN_STAGE_DEFS:
        if sid == stage_id:
            return pct
    return 0


def apply_scan_stage(
    progress: Dict[str, Any],
    stage_id: str,
    status: str,
    findings: Optional[int] = None,
) -> Dict[str, Any]:
    stages = copy.deepcopy(list(progress.get("stages") or []))
    reached = False
    label = progress.get("label") or ""
    now = _now_str()
    for item in stages:
        if item["id"] == stage_id:
            reached = True
            item["status"] = status
            item["at"] = now
            if findings is not None:
                item["findings"] = findings
            label = item.get("label") or label
            break
        if item.get("status") in ("pending", "running"):
            item["status"] = "done"
            item["at"] = item.get("at") or now
    if stage_id == "done" and status == "done":
        for item in stages:
            if item.get("status") in ("pending", "running"):
                item["status"] = "done"
                item["at"] = item.get("at") or now
    if not reached:
        logger.debug("scan stage not found: %s", stage_id)
    percent = _stage_percent(stage_id)
    if status == "running":
        percent = max(4, percent - 4)
    return {
        "phase": stage_id,
        "percent": percent,
        "label": label,
        "stages": stages,
    }


def _attach_finding_counts(progress: Dict[str, Any], findings: List[Any]) -> None:
    counts: Dict[str, int] = {}
    for item in findings or []:
        rid = getattr(item, "rule_id", None)
        if rid is None and isinstance(item, dict):
            rid = item.get("rule_id")
        prefix = str(rid or "").split("-")[0].upper()
        sid = _RULE_STAGE.get(prefix)
        if not sid:
            continue
        counts[sid] = counts.get(sid, 0) + 1
    for stage in progress.get("stages") or []:
        n = counts.get(stage.get("id") or "")
        if n:
            stage["findings"] = n


def parse_s3can_progress_line(line: str) -> Optional[Dict[str, Any]]:
    text = line.strip()
    if "s3can starting" in text:
        return {"stage": "start", "status": "running"}
    if "load_rules_ms=" in text:
        return {"stage": "rules", "status": "done"}
    if "analyze_ms=" in text:
        return {"stage": "analyze", "status": "done"}
    if "report_write_ms=" in text or "JSON report written" in text:
        return {"stage": "report", "status": "done"}
    m = _SCANNER_LINE.search(text)
    if m:
        sid = m.group(1)
        if sid in {x[0] for x in SCAN_STAGE_DEFS}:
            status = "done" if " ms=" in text or re.search(r"\sms=", text) else "running"
            return {"stage": sid, "status": status}
    return None


@dataclass
class Finding:
    scanner: str
    severity: str
    rule_id: str
    file: str
    line: int
    evidence_redacted: str
    message: str = ""


@dataclass
class ScanOutcome:
    ok: bool
    blocked: bool = False
    unavailable: bool = False
    failure_reason: str = ""
    findings: List[Finding] = field(default_factory=list)
    details: Dict[str, Any] = field(default_factory=dict)


def _run_cmd_streaming_sync(
    args: List[str],
    cwd: Optional[Path],
    timeout: float,
    env: Optional[dict],
    on_line: Optional[Callable[[str], None]] = None,
    ready_event: Optional[threading.Event] = None,
    startup_timeout: Optional[float] = None,
) -> tuple[int, str, str]:
    flags = 0
    if sys.platform == "win32" and hasattr(subprocess, "CREATE_NO_WINDOW"):
        flags = subprocess.CREATE_NO_WINDOW
    proc = subprocess.Popen(
        args,
        cwd=str(cwd) if cwd else None,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
        creationflags=flags,
    )
    timed_out = threading.Event()
    out_parts: List[bytes] = []
    err_parts: List[bytes] = []

    def _pump(stream, bucket) -> None:
        try:
            if stream is None:
                return
            while True:
                line = stream.readline()
                if not line:
                    break
                bucket.append(line)
                if on_line:
                    on_line(line.decode("utf-8", errors="replace"))
        except Exception:
            pass

    def _kill_on_timeout() -> None:
        """进程存活期间持续检查：启动阶段用更短的独立超时。"""
        import time as _time

        started_at = _time.monotonic()
        while proc.poll() is None:
            elapsed = _time.monotonic() - started_at
            limit = timeout
            if (
                startup_timeout
                and ready_event is not None
                and not ready_event.is_set()
            ):
                limit = min(startup_timeout or timeout, timeout)
            if elapsed >= limit:
                timed_out.set()
                try:
                    proc.kill()
                except Exception:
                    pass
                return
            _time.sleep(0.2)
        if timed_out.is_set():
            return

    t_out = threading.Thread(target=_pump, args=(proc.stdout, out_parts), daemon=True)
    t_err = threading.Thread(target=_pump, args=(proc.stderr, err_parts), daemon=True)
    watchdog = threading.Thread(target=_kill_on_timeout, daemon=True)
    t_out.start()
    t_err.start()
    watchdog.start()
    try:
        proc.wait()
        t_out.join(timeout=2)
        t_err.join(timeout=2)
        if timed_out.is_set():
            startup_expired = bool(
                startup_timeout
                and ready_event is not None
                and not ready_event.is_set()
            )
            effective = min(startup_timeout, timeout) if startup_expired else timeout
            stdout_tail = b"".join(out_parts).decode("utf-8", errors="replace")[-600:]
            stderr_tail = b"".join(err_parts).decode("utf-8", errors="replace")[-400:]
            logger.warning(
                "s3canner %s after %.0fs exit=%s stdout=%s stderr=%s",
                "startup timeout" if startup_expired else "timeout",
                effective,
                proc.returncode,
                stdout_tail,
                stderr_tail,
            )
            raise TimeoutError(
                f"{'startup timeout' if startup_expired else 'scanner timeout'} "
                f"after {effective:.0f}s; "
                f"last_stdout={stdout_tail.strip()[-300:] or '(empty)'} "
                f"last_stderr={stderr_tail.strip()[-200:] or '(empty)'}"
            )
    except TimeoutError:
        raise
    except Exception:
        proc.kill()
        proc.wait()
        raise
    finally:
        timed_out.set()
    return (
        proc.returncode or 0,
        b"".join(out_parts).decode("utf-8", errors="replace"),
        b"".join(err_parts).decode("utf-8", errors="replace"),
    )


def _map_severity(raw: str) -> str:
    s = (raw or "").strip().lower()
    if s in ("critical", "crit"):
        return "Critical"
    if s in ("high",):
        return "High"
    if s in ("medium", "med", "moderate"):
        return "Medium"
    if s in ("low",):
        return "Low"
    return "Info"


def _scan_timeout() -> float:
    return float(getattr(app_config, "SKILL_SCAN_TIMEOUT_SECONDS", 120))


def _startup_timeout() -> Optional[float]:
    """扫描器启动/规则加载阶段独立超时；0 表示不单独限制。"""
    try:
        value = float(getattr(app_config, "SKILL_SCAN_STARTUP_TIMEOUT_SECONDS", 45))
    except (TypeError, ValueError):
        return None
    return value if value > 0 else None


def _has_report(path: Path) -> bool:
    return path.is_file() and path.stat().st_size > 0


def _project_root() -> Path:
    """仓库根目录：adapter.py → scan → marketplace → skills → deep_agent_server → <root>。"""
    return Path(__file__).resolve().parents[4]


def _s3canner_bin() -> Optional[str]:
    """定位 s3canner 可执行文件。

    扫描是固定流程，与 skill 仓库无关，二进制统一放在仓库根的 vendor/s3canner/。
    查找顺序：SKILL_S3CANNER_BIN 配置 → vendor/s3canner/ → PATH。
    """
    configured = getattr(app_config, "SKILL_S3CANNER_BIN", None)
    if configured:
        p = Path(str(configured))
        return str(p) if p.is_file() else None

    vendor_dir = _project_root() / "vendor" / "s3canner"
    if sys.platform == "win32":
        exe = vendor_dir / "s3canner.exe"
        if exe.is_file():
            return str(exe)
    native = vendor_dir / "s3canner"
    if native.is_file():
        return str(native)
    return shutil.which("s3canner") or shutil.which("s3canner.exe")


def _exclude_rules() -> List[str]:
    raw = getattr(app_config, "SKILL_S3CANNER_EXCLUDE", None) or "SPEC-006"
    return [p.strip() for p in str(raw).split(",") if p.strip()]


_OVERLAY_DEFAULT = Path(__file__).with_name("s3can_rules_overlay.yaml")


def _overlay_rules_path() -> Optional[Path]:
    configured = getattr(app_config, "SKILL_S3CANNER_RULES_OVERLAY", None)
    if configured:
        p = Path(str(configured))
        return p if p.is_file() else None
    return _OVERLAY_DEFAULT if _OVERLAY_DEFAULT.is_file() else None


def _configured_rules_path() -> Optional[Path]:
    configured = getattr(app_config, "SKILL_S3CANNER_RULES", None)
    if not configured:
        return None
    p = Path(str(configured))
    return p if p.is_file() else None


def _local_rules_dir() -> Path:
    """规则文件落到本机临时目录，避免 NFS 读取阻塞扫描器启动。"""
    import tempfile

    root = Path(tempfile.gettempdir()) / "s3can-rules"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _builtin_rules_cache_path(bin_path: str) -> Path:
    root = _local_rules_dir()
    return root / f"s3can-rules.builtin.{Path(bin_path).stat().st_mtime_ns}.yaml"


def _export_builtin_rules(bin_path: str) -> Path:
    cache = _builtin_rules_cache_path(bin_path)
    if cache.is_file() and cache.stat().st_size > 0:
        return cache
    flags = 0
    if sys.platform == "win32" and hasattr(subprocess, "CREATE_NO_WINDOW"):
        flags = subprocess.CREATE_NO_WINDOW
    proc = subprocess.run(
        [bin_path, "--generate-rules", "--rules", str(cache)],
        capture_output=True,
        timeout=30,
        creationflags=flags,
    )
    if proc.returncode != 0 or not cache.is_file():
        raise RuntimeError(
            (proc.stderr or proc.stdout or b"").decode("utf-8", errors="replace")[:300]
            or "generate-rules failed"
        )
    return cache


def _merge_rule_files(base: Path, overlay: Path, dest: Path) -> Path:
    import yaml

    base_doc = yaml.safe_load(base.read_text(encoding="utf-8")) or {}
    extra_doc = yaml.safe_load(overlay.read_text(encoding="utf-8")) or {}
    rules = [r for r in (base_doc.get("rules") or []) if isinstance(r, dict)]
    index = {str(r.get("id")): i for i, r in enumerate(rules) if r.get("id")}
    for extra in extra_doc.get("rules") or []:
        if not isinstance(extra, dict) or not extra.get("id"):
            continue
        rid = str(extra["id"])
        if rid in index:
            rules[index[rid]] = extra
        else:
            rules.append(extra)
    dest.write_text(
        yaml.safe_dump({"rules": rules}, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    return dest


def _prepare_rules_file(bin_path: str, work_dir: Path) -> Optional[str]:
    """内置规则 + 中文指令叠加 → 临时 YAML，供 --rules 使用。"""
    overlay = _overlay_rules_path()
    base = _configured_rules_path()
    if base is None:
        try:
            base = _export_builtin_rules(bin_path)
        except Exception as e:
            logger.warning("s3canner generate-rules failed: %s", e)
            return None
    if overlay is None:
        return str(base)
    work_dir.mkdir(parents=True, exist_ok=True)
    merged = work_dir / "s3can-rules.merged.yaml"
    try:
        _merge_rule_files(base, overlay, merged)
    except Exception:
        logger.exception("merge s3canner overlay rules failed")
        return str(base)
    return str(merged)


def _load_rule_labels(rules_path: Optional[str]) -> Dict[str, Dict[str, str]]:
    """从规则文件提取 {rule_id: {name_zh, description_zh, suggestion_zh}}。

    s3canner 的报告 JSON 只输出英文 message/suggestion，中文标签（规则定义里的
    *_zh 字段）不会带出来，这里自行加载用于把提示本地化。
    """
    if not rules_path:
        return {}
    try:
        import yaml

        doc = yaml.safe_load(Path(rules_path).read_text(encoding="utf-8")) or {}
    except Exception as e:
        logger.debug("load rule labels failed: %s", e)
        return {}
    labels: Dict[str, Dict[str, str]] = {}
    for rule in doc.get("rules") or []:
        if not isinstance(rule, dict) or not rule.get("id"):
            continue
        labels[str(rule["id"])] = {
            "name_zh": str(rule.get("name_zh") or "").strip(),
            "description_zh": str(rule.get("description_zh") or "").strip(),
            "suggestion_zh": str(rule.get("suggestion_zh") or "").strip(),
            "category": str(rule.get("category") or "").strip(),
            "severity": str(rule.get("severity") or "").strip(),
        }
    return labels


def _build_scan_rules(
    data: dict, labels: Optional[Dict[str, Dict[str, str]]] = None
) -> Dict[str, Any]:
    """汇总每条规则的检查结果（通过/未通过），供前端展示扫描覆盖面。

    数据来自报告 JSON 的 skill_summaries[*].rules_{scanned,passed,failed}；
    规则的中文名称/说明来自规则文件。未通过项排在前面。
    """
    summaries = data.get("skill_summaries")
    if not isinstance(summaries, list):
        return {}

    scanned: List[str] = []
    passed_set = set()
    failed_set = set()
    for summary in summaries:
        if not isinstance(summary, dict):
            continue
        for rid in summary.get("rules_scanned") or []:
            rid = str(rid)
            if rid not in scanned:
                scanned.append(rid)
        for rid in summary.get("rules_passed") or []:
            passed_set.add(str(rid))
        for rid in summary.get("rules_failed") or []:
            failed_set.add(str(rid))
    if not scanned:
        return {}

    items = []
    for rid in scanned:
        label = (labels or {}).get(rid) or {}
        items.append({
            "id": rid,
            "name": label.get("name_zh") or label.get("name") or "",
            "description": label.get("description_zh") or "",
            "category": label.get("category") or "",
            "severity": label.get("severity") or "",
            "passed": rid not in failed_set,
        })
    items.sort(key=lambda x: (x["passed"], x["id"]))
    passed_count = sum(1 for i in items if i["passed"])
    return {
        "total": len(items),
        "passed": passed_count,
        "failed": len(items) - passed_count,
        "items": items,
    }


def _block_min_rank() -> int:
    raw = str(getattr(app_config, "SKILL_SCAN_BLOCK_MIN_SEVERITY", "high") or "high").lower()
    return _SEVERITY_RANK.get(raw, 3)


def _should_block(severity: str) -> bool:
    return _SEVERITY_RANK.get(severity.lower(), 0) >= _block_min_rank()


def parse_s3can_report(
    data: Any, labels: Optional[Dict[str, Dict[str, str]]] = None
) -> List[Finding]:
    """解析扫描报告。有中文标签时优先输出中文，代码片段类证据保留原文。"""
    if not isinstance(data, dict):
        return []
    items = data.get("findings")
    if not isinstance(items, list):
        items = []
        summaries = data.get("skill_summaries")
        if isinstance(summaries, list):
            for summary in summaries:
                if isinstance(summary, dict) and isinstance(summary.get("findings"), list):
                    items.extend(summary["findings"])
    findings: List[Finding] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        rule_id = str(item.get("rule_id") or "s3can")
        label = (labels or {}).get(rule_id) or {}
        en_message = str(item.get("message") or "").strip()
        # 报告自带 rule_name_zh，其次查规则文件的 name_zh/description_zh
        zh_name = str(item.get("rule_name_zh") or label.get("name_zh") or "").strip()
        zh_desc = str(label.get("description_zh") or "").strip()

        # 描述列优先展示中文；真实代码片段（snippet）作为证据时保留原文
        snippet = str(item.get("snippet") or "").strip()
        if snippet:
            evidence = snippet
        else:
            evidence = zh_desc or zh_name or en_message
        message = zh_name or zh_desc or en_message

        findings.append(
            Finding(
                scanner="s3canner",
                severity=_map_severity(str(item.get("severity") or "Info")),
                rule_id=rule_id,
                file=str(item.get("file_path") or ""),
                line=int(item.get("line") or 0),
                evidence_redacted=evidence[:120],
                message=message[:300],
            )
        )
    return findings


def _offline_env() -> dict:
    """构造扫描器的离线环境。

    扫描期间不允许任何外部交互：上报地址置空、LLM key 清除。此外必须剔除
    openclaw / coclaw / LLM 相关变量 —— 二进制在启动阶段会据此尝试访问内网
    令牌或模型网关，无超时等待时表现为卡在 rules 阶段直至 120s 超时。
    """
    env = os.environ.copy()
    env["S3CAN_REPORT_URL"] = ""
    env.pop("OPENAI_API_KEY", None)
    env["SKILL_S3CANNER_NO_LLM_KEY"] = ""
    for key in list(env):
        upper = key.upper()
        if upper.startswith(("OPENCLAW_", "COCLAW_", "OPENAI_", "LLM_")):
            env.pop(key, None)
    return env


def _skill_dir_name(unpacked: Path) -> Optional[str]:
    skill_md = unpacked / "SKILL.md"
    if not skill_md.is_file():
        return None
    try:
        from deep_agent_server.skills.parser import parse_skill_md

        meta = parse_skill_md(str(unpacked))
        name = (getattr(meta, "name", None) or "").strip()
    except Exception:
        name = ""
    if not name:
        return None
    safe = "".join(ch if ch.isalnum() or ch in "._-" else "-" for ch in name).strip(".-")
    return safe or None


def _scan_root(unpacked: Path, report_dir: Path) -> Path:
    """s3canner SPEC-008 要求目录名与 SKILL.md name 一致；流水线解包目录固定叫 unpacked。

    这里必须**复制**而不是符号链接：s3canner 1.1.1 在扫描根是软链时，会对目录
    fd 调用 read()，拿到 EISDIR 后陷入忙循环，直到外部超时（表现为规则加载完成
    后进度卡住、120s 报 SCAN_UNAVAILABLE）。报告里的 file_path 是相对扫描根的
    路径，复制不影响结果展示。
    """
    name = _skill_dir_name(unpacked)
    if not name or unpacked.name == name:
        return unpacked
    alias = report_dir / "_root" / name
    if alias.exists() or alias.is_symlink():
        if alias.is_dir() and not alias.is_symlink():
            shutil.rmtree(alias, ignore_errors=True)
        else:
            alias.unlink(missing_ok=True)
    alias.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(unpacked, alias)
    return alias


async def _run_s3canner(
    unpacked: Path,
    report_dir: Path,
    on_progress: ProgressCallback = None,
) -> ScanOutcome:
    bin_path = _s3canner_bin()
    if not bin_path:
        return ScanOutcome(
            ok=False,
            unavailable=True,
            failure_reason="SCAN_UNAVAILABLE",
            details={"scanner": "s3canner", "error": "binary not found"},
        )

    report_dir.mkdir(parents=True, exist_ok=True)
    report_path = report_dir / "s3can-report.json"
    progress = new_scan_progress()
    progress_lock = threading.Lock()

    async def _emit(next_progress: Dict[str, Any]) -> None:
        with progress_lock:
            progress.clear()
            progress.update(next_progress)
            snapshot = copy.deepcopy(progress)
        if on_progress:
            await on_progress(snapshot)

    await _emit(apply_scan_stage(progress, "prepare", "running"))
    scan_root = await asyncio.to_thread(_scan_root, unpacked, report_dir)
    await _emit(apply_scan_stage(progress, "prepare", "done"))
    await _emit(apply_scan_stage(progress, "start", "running"))

    rules_file = await asyncio.to_thread(_prepare_rules_file, bin_path, _local_rules_dir())
    args = [
        bin_path,
        "--no-report",
        "--skip-scanner-info",
        "--no-open-report",
        "--stage-timing",
        "-f",
        "json",
        "-o",
        str(report_dir),
        "--fail-on",
        "critical",
        "--log-level",
        "info",
    ]
    if rules_file:
        args.extend(["--rules", rules_file])
    args.append(str(scan_root))
    for rule in _exclude_rules():
        args.extend(["--exclude", rule])

    loop = asyncio.get_running_loop()
    pending_futs = []
    rules_ready = threading.Event()

    def _on_line(line: str) -> None:
        parsed = parse_s3can_progress_line(line)
        if not parsed:
            return
        # 出现任何实质阶段（rules 及之后）即认为启动完成，撤销启动 watchdog
        if parsed.get("stage") not in (None, "start"):
            rules_ready.set()
        with progress_lock:
            updated = apply_scan_stage(
                progress,
                parsed["stage"],
                parsed["status"],
                parsed.get("findings"),
            )
            progress.clear()
            progress.update(updated)
            snapshot = copy.deepcopy(progress)
        if on_progress:
            pending_futs.append(
                asyncio.run_coroutine_threadsafe(on_progress(snapshot), loop)
            )

    try:
        code, out, err = await asyncio.to_thread(
            _run_cmd_streaming_sync,
            args,
            None,
            _scan_timeout(),
            _offline_env(),
            _on_line,
            rules_ready,
            _startup_timeout(),
        )
        if pending_futs:
            await asyncio.gather(
                *[asyncio.wrap_future(fut) for fut in pending_futs],
                return_exceptions=True,
            )
    except TimeoutError as e:
        logger.warning("s3canner timed out after %.0fs", _scan_timeout())
        return ScanOutcome(
            ok=False,
            unavailable=True,
            failure_reason="SCAN_UNAVAILABLE",
            details={"scanner": "s3canner", "error": str(e) or "timeout"},
        )
    except Exception as e:
        logger.exception("s3canner failed")
        return ScanOutcome(
            ok=False,
            unavailable=True,
            failure_reason="SCAN_UNAVAILABLE",
            details={
                "scanner": "s3canner",
                "error": str(e) or type(e).__name__,
            },
        )

    if not _has_report(report_path):
        if out.strip().startswith("{"):
            report_path.write_text(out, encoding="utf-8")
        else:
            return ScanOutcome(
                ok=False,
                unavailable=True,
                failure_reason="SCAN_UNAVAILABLE",
                details={
                    "scanner": "s3canner",
                    "error": "missing report",
                    "exit": code,
                    "stderr": err[:500],
                },
            )

    try:
        data = json.loads(report_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return ScanOutcome(
            ok=False,
            unavailable=True,
            failure_reason="SCAN_UNAVAILABLE",
            details={"scanner": "s3canner", "error": "invalid json", "exit": code},
        )
    if not isinstance(data, dict) or "findings" not in data:
        return ScanOutcome(
            ok=False,
            unavailable=True,
            failure_reason="SCAN_UNAVAILABLE",
            details={"scanner": "s3canner", "error": "unexpected report shape", "exit": code},
        )

    rule_labels = await asyncio.to_thread(_load_rule_labels, rules_file)
    findings = parse_s3can_report(data, rule_labels)
    scan_rules = _build_scan_rules(data, rule_labels)
    blocked = any(_should_block(f.severity) for f in findings)
    _attach_finding_counts(progress, findings)
    await _emit(apply_scan_stage(progress, "done", "done"))
    return ScanOutcome(
        ok=True,
        blocked=blocked,
        findings=findings,
        details={
            "scanner": "s3canner",
            "exit": code,
            "passed": data.get("passed"),
            "scan_id": data.get("scan_id"),
            "s3can_version": data.get("s3can_version"),
            "duration_ms": data.get("duration_ms"),
            "scan_progress": dict(progress),
            "scan_rules": scan_rules,
        },
    )


async def run_security_scans(
    unpacked: Path,
    report_dir: Path,
    on_progress: ProgressCallback = None,
) -> ScanOutcome:
    """只跑 s3canner。工具故障 → unavailable；Critical/High（可配）→ blocked。"""
    if getattr(app_config, "SKILL_SCAN_FIXTURE_PASS", False) and getattr(app_config, "ENVIRONMENT", "") in (
        "dev",
        "test",
    ):
        return ScanOutcome(ok=True, findings=[], details={"fixture": True})
    return await _run_s3canner(unpacked, report_dir, on_progress=on_progress)
