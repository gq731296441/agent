"""运行时日志查看 API（内部接口，需 X-Internal-Token）。"""

import re
from datetime import datetime
from pathlib import Path
from typing import List, Optional

from fastapi import APIRouter, Header, HTTPException, Query
from pydantic import BaseModel

from common.schemas import ApiResponse
from config import get_logger
from config.config import app_config

logger = get_logger(__name__)

router = APIRouter(prefix="/internal/logs", tags=["internal-logs"])

_LOG_DIR = Path(app_config.log_file).resolve().parent
_LOG_BASENAME = Path(app_config.log_file).name  # e.g. "app.log"

# loguru 文件日志格式：YYYY-MM-DD HH:MM:SS.SSS | LEVEL | name:func:line | message
_LOG_LINE_RE = re.compile(
    r"^(?P<time>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d{3})"
    r" \| (?P<level>\w+)"
    r"(?: +)\| (?P<source>[^|]+)"
    r" \| (?P<message>.*)$"
)

_LEVEL_ORDER = {"TRACE": 0, "DEBUG": 10, "INFO": 20, "WARNING": 30, "ERROR": 40, "CRITICAL": 50}


def _verify_internal_token(x_internal_token: str | None) -> None:
    expected = (app_config.SKILL_INTERNAL_TOKEN or "").strip()
    if not expected:
        raise HTTPException(status_code=503, detail="SKILL_INTERNAL_TOKEN 未配置")
    if (x_internal_token or "").strip() != expected:
        raise HTTPException(status_code=401, detail="无效的内部 Token")


def _resolve_log_file(filename: str) -> Path:
    """安全解析日志文件路径，禁止路径穿越。"""
    name = Path(filename).name  # 仅取文件名部分，防止路径穿越
    if ".." in name or "/" in name or "\\" in name:
        raise HTTPException(status_code=400, detail="非法文件名")
    path = _LOG_DIR / name
    if not path.is_file():
        raise HTTPException(status_code=404, detail=f"日志文件不存在: {name}")
    return path


def _parse_level(level_str: str) -> str:
    """标准化日志级别为缩写（INFO, WARNING 等）。"""
    mapping = {
        "trace": "TRACE", "debug": "DEBUG", "info": "INFO",
        "warning": "WARNING", "warn": "WARNING",
        "error": "ERROR", "critical": "CRITICAL",
    }
    return mapping.get(level_str.lower().strip(), level_str.upper())


def _level_ge(level: str, min_level: str) -> bool:
    return _LEVEL_ORDER.get(level, -1) >= _LEVEL_ORDER.get(min_level, -1)


def _iter_lines_reverse(filepath: Path, max_lines: int) -> List[str]:
    """高效反向读取文件最后 N 行（大文件友好，不加载全量到内存）。"""
    lines: List[str] = []
    with open(filepath, "rb") as f:
        f.seek(0, 2)  # 移动到文件末尾
        file_size = f.tell()
        buffer_size = 8192
        chunk = b""
        remaining = file_size

        while len(lines) <= max_lines and remaining > 0:
            read_size = min(buffer_size, remaining)
            remaining -= read_size
            f.seek(remaining)
            chunk = f.read(read_size) + chunk

            # 按换行符分割
            parts = chunk.split(b"\n")
            if remaining > 0:
                # 第一个 part 可能不完整（从中间截断），保留给下一个 chunk
                chunk = parts[0]
                new_lines = parts[1:]
            else:
                chunk = b""
                new_lines = parts

            # 添加到结果前面
            for line_bytes in reversed(new_lines):
                line = line_bytes.decode("utf-8", errors="replace").rstrip("\r")
                if line:
                    lines.insert(0, line)
                if len(lines) >= max_lines:
                    break

        # 处理剩余 chunk
        if chunk and len(lines) < max_lines:
            line = chunk.decode("utf-8", errors="replace").rstrip("\r")
            if line:
                lines.insert(0, line)

    return lines


class LogFileInfo(BaseModel):
    name: str
    size_bytes: int
    size_mb: float
    modified_at: str


class LogEntry(BaseModel):
    timestamp: str
    level: str
    source: str
    message: str


@router.get("/files", response_model=ApiResponse)
async def list_log_files(
    x_internal_token: str | None = Header(default=None),
):
    """列出所有可用日志文件。"""
    _verify_internal_token(x_internal_token)

    files: List[LogFileInfo] = []
    for p in sorted(_LOG_DIR.iterdir()):
        if not p.is_file():
            continue
        name = p.name
        # 只列出 app*.log 和 app*.zip 文件
        if not (name.startswith(_LOG_BASENAME.split(".")[0])):
            continue
        size = p.stat().st_size
        files.append(LogFileInfo(
            name=name,
            size_bytes=size,
            size_mb=round(size / (1024 * 1024), 2),
            modified_at=datetime.fromtimestamp(p.stat().st_mtime).isoformat(),
        ))

    return ApiResponse.success(data=[f.model_dump() for f in files])


@router.get("/view", response_model=ApiResponse)
async def view_logs(
    x_internal_token: str | None = Header(default=None),
    file: str = Query(default="", description="日志文件名，默认最新日志文件"),
    lines: int = Query(default=200, ge=1, le=5000, description="返回最近 N 行"),
    offset: int = Query(default=0, ge=0, le=100000, description="从末尾偏移 N 行开始"),
    level: str = Query(default="", description="仅返回 >= 此级别的日志 (DEBUG/INFO/WARNING/ERROR)"),
    search: str = Query(default="", description="关键字搜索（大小写不敏感）"),
    since: str = Query(default="", description="起始时间 ISO 格式，如 2026-07-13T10:00:00"),
    until: str = Query(default="", description="结束时间 ISO 格式"),
):
    """查看日志文件内容。

    - 默认返回最新日志文件最后 200 行
    - 支持按日志级别、关键字、时间范围过滤
    - 使用 offset 实现分页（offset=100 跳过最后 100 行）
    """
    _verify_internal_token(x_internal_token)

    # 解析日志文件
    filename = file.strip() if file.strip() else _LOG_BASENAME
    log_path = _resolve_log_file(filename)

    # 解析过滤参数
    min_level = _parse_level(level) if level else ""
    search_lower = search.strip().lower() if search.strip() else ""
    since_dt: Optional[datetime] = None
    until_dt: Optional[datetime] = None
    if since.strip():
        try:
            since_dt = datetime.fromisoformat(since.strip())
        except ValueError:
            raise HTTPException(status_code=400, detail="since 格式无效，应为 ISO 8601")
    if until.strip():
        try:
            until_dt = datetime.fromisoformat(until.strip())
        except ValueError:
            raise HTTPException(status_code=400, detail="until 格式无效，应为 ISO 8601")

    # 读取日志（offset + lines 决定需要读取的总行数）
    need_lines = offset + lines
    raw_lines = _iter_lines_reverse(log_path, need_lines)

    # 跳过 offset 行
    if offset > 0:
        raw_lines = raw_lines[:-offset] if len(raw_lines) > offset else []

    # 解析并过滤
    entries: List[LogEntry] = []
    for line in raw_lines:
        m = _LOG_LINE_RE.match(line)
        if not m:
            # 多行日志的续行：追加到最后一条 entry 的 message
            if entries and line.strip():
                entries[-1].message += "\n" + line.strip()
            continue

        ts_str = m.group("time")
        lvl = m.group("level").strip()
        source = m.group("source").strip()
        msg = m.group("message").strip()

        # 时间过滤
        if since_dt or until_dt:
            try:
                ts = datetime.strptime(ts_str, "%Y-%m-%d %H:%M:%S.%f")
            except ValueError:
                try:
                    ts = datetime.strptime(ts_str, "%Y-%m-%d %H:%M:%S")
                except ValueError:
                    ts = None
            if ts:
                if since_dt and ts < since_dt:
                    continue
                if until_dt and ts > until_dt:
                    continue

        # 级别过滤
        if min_level and not _level_ge(lvl, min_level):
            continue

        # 关键字搜索
        if search_lower and search_lower not in line.lower():
            continue

        entries.append(LogEntry(
            timestamp=ts_str,
            level=lvl,
            source=source,
            message=msg,
        ))

    return ApiResponse.success(data={
        "file": filename,
        "total_lines": len(raw_lines),
        "filtered_count": len(entries),
        "params": {
            "lines": lines,
            "offset": offset,
            "level": min_level or None,
            "search": search or None,
            "since": since or None,
            "until": until or None,
        },
        "entries": [e.model_dump() for e in entries],
    })


@router.get("/levels", response_model=ApiResponse)
async def log_levels(
    x_internal_token: str | None = Header(default=None),
):
    """返回支持的日志级别及其排序。"""
    _verify_internal_token(x_internal_token)
    return ApiResponse.success(data={
        "levels": list(_LEVEL_ORDER.keys()),
        "order": _LEVEL_ORDER,
    })
