#!/usr/bin/env python
"""
日志统一管理：从 config.json 读取 log 配置；支持关闭日志、记录完整参数、main 中打印原始入参。

配置项（config.log）：
- enabled: 是否开启日志，默认 True；为 False 时 get_logger 返回的 logger 不输出。
- response_body_max_length: 响应体截断长度；0 表示完整打印。
- max_file_size_mb: 日志文件最大大小（MB），超过则轮转，默认 50。
- max_files_per_script: 同一场景（如 query_work_item）保留的日志文件数上限，超过则删除最老日期的；默认 10。0 或负数表示不清理。

全局开关（也可在代码中修改）：
- ENABLE_LOG：是否记录日志；可由 apply_log_config(config_path) 根据 config.log.enabled 覆盖。
- ENABLE_PRINT：是否执行 safe_print；为 False 时不输出。

约定：
- 在 main() 入口处应调用 log_raw_argv(logger, sys.argv) 打印原始完整入参。
- 在必要方法（如参数解析完成、参数调整完成）中可调用 log_complete_params(logger, stage, params) 打印完整参数。
- 各脚本的日志初始化统一调用 setup_logging(log_file_path, add_stderr=...) 完成，不再在各脚本内重复实现 setup_logging。
"""

import glob
import json
import logging
import os
import re
import sys
import threading
from pathlib import Path

from common.auth_helper import COCLAW_EMPNO_ENV

# 全局开关一：是否记录日志。可由 apply_log_config() 根据配置文件覆盖。
ENABLE_LOG = True

# 全局开关二：是否执行 print 打印。
ENABLE_PRINT = True


def safe_print(*args, **kwargs):
    """仅当 ENABLE_PRINT 为 True 时执行 print，否则不输出。管道下游提前关闭时忽略 BrokenPipeError。"""
    if not ENABLE_PRINT:
        return
    try:
        print(*args, **kwargs)
    except BrokenPipeError:
        pass


CONFIG_KEY_LOG = "log"
CONFIG_KEY_LOG_ENABLED = "enabled"
CONFIG_KEY_MAX_FILE_SIZE_MB = "max_file_size_mb"
CONFIG_KEY_MAX_FILES_PER_SCRIPT = "max_files_per_script"
DEFAULT_MAX_FILE_SIZE_MB = 50 * 1024 * 1024  # 50MB in bytes
DEFAULT_MAX_FILES_PER_SCRIPT = 10


def _extract_log_prefix(log_path):
    """
    从日志路径提取同类日志前缀，用于 glob 匹配。
    如 query_work_item_20260323.log -> query_work_item_
        query_work_item_20260323-0.log -> query_work_item_
    """
    name = Path(log_path).name
    m = re.match(r"^(.+)_\d{8}(-\d+)?\.log$", name)
    if m:
        return m.group(1) + "_"
    return None


def _cleanup_old_logs(log_file_path, log_dir, max_files):
    """
    当同类日志文件数超过 max_files 时，删除最老日期的文件。
    同类：如 query_work_item_*.log（含 query_work_item_20260323.log、query_work_item_20260323-0.log 等）。
    按文件修改时间排序，删除最老的文件，直至总数不超过 max_files。
    """
    if max_files <= 0 or not log_dir or not Path(log_dir).exists():
        return
    prefix = _extract_log_prefix(log_file_path)
    if not prefix:
        return
    pattern = str(Path(log_dir) / f"{prefix}*.log")
    files = []
    for f in glob.glob(pattern):
        p = Path(f)
        if p.is_file():
            try:
                mtime = p.stat().st_mtime
                files.append((mtime, p))
            except OSError:
                continue
    if len(files) <= max_files:
        return
    files.sort(key=lambda x: x[0])
    to_delete = len(files) - max_files
    for i in range(to_delete):
        try:
            files[i][1].unlink()
        except OSError:
            pass


def setup_logging(log_file_path, add_stderr=False):
    """
    统一配置日志：创建日志文件所在目录，设置 FileHandler（支持轮转）和可选 stderr，并调用 basicConfig。
    各脚本在模块加载后若 ENABLE_LOG 则调用本方法一次，传入本脚本的日志文件路径。
    :param log_file_path: 日志文件路径，可为 Path 或 str，如 SKILL_DIR / "logs" / "create_work_item_20250101.log"
    :param add_stderr: 是否同时输出到 sys.stderr，默认 False（仅写文件）
    """
    path = Path(log_file_path)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
    except OSError:
        # 只读目录（如容器内 skill 以 :ro 挂载）→ 降级为不写日志文件
        path = None

    # 从配置文件读取日志配置
    config_path = Path(__file__).resolve().parents[2] / "assets" / "config.json"
    log_config = get_log_config(config_path)
    max_file_size = log_config.get(
        CONFIG_KEY_MAX_FILE_SIZE_MB, DEFAULT_MAX_FILE_SIZE_MB
    )
    max_files_per_script = log_config.get(
        CONFIG_KEY_MAX_FILES_PER_SCRIPT, DEFAULT_MAX_FILES_PER_SCRIPT
    )

    # 同类日志超过上限时，删除最老的文件
    if path is not None:
        _cleanup_old_logs(path, path.parent, max_files_per_script)

    log_format = "%(empno)s%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    date_format = "%Y年%m月%d日 %H:%M:%S"

    # 若 root logger 已有 handler，先清空，确保当前脚本的日志文件生效
    root_logger = logging.getLogger()
    for h in list(root_logger.handlers):
        root_logger.removeHandler(h)

    # 创建文件处理器（支持轮转）；只读目录或创建失败时降级为不写文件
    handlers = []
    if path is not None:
        fh = None
        try:
            if max_file_size > 0:
                fh = RotatingFileHandler(
                    path,
                    encoding="utf-8",
                    maxBytes=max_file_size,
                )
            else:
                fh = logging.FileHandler(path, encoding="utf-8")
        except TypeError:
            # 兼容旧版本 Python
            try:
                if max_file_size > 0:
                    fh = RotatingFileHandler(
                        path,
                        maxBytes=max_file_size,
                    )
                else:
                    fh = logging.FileHandler(path)
            except OSError:
                fh = None
        except OSError:
            fh = None

        if fh is not None:
            # 添加工号过滤器
            fh.addFilter(EmpNoFilter())
            handlers.append(fh)

    if add_stderr:
        stderr_handler = logging.StreamHandler(sys.stderr)
        stderr_handler.addFilter(EmpNoFilter())
        handlers.append(stderr_handler)

    logging.basicConfig(
        level=logging.INFO, format=log_format, datefmt=date_format, handlers=handlers
    )


def apply_log_config(config_path):
    """
    从 config.json 读取 log.enabled，并设置本模块的 ENABLE_LOG。
    在 main() 入口处调用一次即可；未传或文件不存在时保持当前 ENABLE_LOG 不变。
    """
    global ENABLE_LOG
    if not config_path or not Path(config_path).exists():
        return
    try:
        with open(config_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        log_cfg = data.get(CONFIG_KEY_LOG)
        if isinstance(log_cfg, dict) and "enabled" in log_cfg:
            ENABLE_LOG = bool(log_cfg["enabled"])
    except Exception:
        pass


class EmpNoFilter(logging.Filter):
    """日志过滤器：从认证约定环境变量（auth_helper.COCLAW_EMPNO_ENV）获取工号，并添加到日志记录中。"""

    def __init__(self, empno=None):
        super().__init__()
        self.empno = empno or os.getenv(COCLAW_EMPNO_ENV, "")

    def filter(self, record):
        record.empno = f"[{self.empno}] " if self.empno else ""
        return True


class RotatingFileHandler(logging.FileHandler):
    """
    自定义轮转文件处理器。
    当日志文件大小超过 maxBytes 时，将当前文件重命名为 {basename}-{index}.log，
    然后创建新的日志文件继续写入。
    索引从 0 开始递增。
    """

    def __init__(
        self,
        filename,
        mode="a",
        encoding=None,
        delay=False,
        maxBytes=DEFAULT_MAX_FILE_SIZE_MB,
    ):
        self.maxBytes = maxBytes
        self._lock = threading.RLock()
        super().__init__(filename, mode, encoding, delay)

    def emit(self, record):
        """
        发送日志记录前检查文件大小，如果超过阈值则执行轮转。
        """
        with self._lock:
            try:
                if self.should_rotate():
                    self.do_rotate()
                super().emit(record)
            except Exception:
                self.handleError(record)

    def should_rotate(self):
        """检查当前文件大小是否超过阈值"""
        if self.maxBytes <= 0:
            return False
        try:
            if self.stream and not self.stream.closed:
                self.stream.flush()
                return os.path.getsize(self.baseFilename) >= self.maxBytes
        except OSError:
            pass
        return False

    def do_rotate(self):
        """执行文件轮转"""
        if self.stream:
            try:
                self.stream.close()
            except Exception:
                pass

        log_path = Path(self.baseFilename)
        if not log_path.exists():
            # 文件不存在，重新打开
            self.stream = self._open()
            return

        # 查找当前最大的索引
        parent = log_path.parent
        stem = log_path.stem  # 例如: query_work_item_20260323
        suffix = log_path.suffix  # .log

        pattern = f"{stem}-*{suffix}"
        max_index = -1
        for existing_file in parent.glob(pattern):
            try:
                m = re.match(
                    rf"^{re.escape(stem)}-(\d+){re.escape(suffix)}$",
                    existing_file.name,
                )
                if m:
                    idx = int(m.group(1))
                    max_index = max(max_index, idx)
            except Exception:
                continue

        # 新索引 = 最大索引 + 1
        new_index = max_index + 1
        new_filename = parent / f"{stem}-{new_index}{suffix}"

        # 重命名当前文件
        try:
            log_path.rename(new_filename)
        except Exception as e:
            # 重命名失败，记录错误并继续
            logging.warning(f"日志轮转重命名失败: {e}")

        # 重新打开新文件（以追加模式）
        self.stream = self._open()


def log_raw_argv(logger, argv=None):
    """
    在 main() 中打印原始完整入参（通常为 sys.argv）。
    仅当 logger 处于开启状态时输出。
    """
    if argv is None:
        import sys

        argv = getattr(sys, "argv", [])
    if hasattr(logger, "_is_enabled") and not logger._is_enabled():
        return
    if isinstance(argv, (list, tuple)):
        raw = " ".join(str(a) for a in argv)
    else:
        raw = str(argv)
    logger.info("原始完整入参: %s", raw)


def log_complete_params(logger, stage, params):
    """
    在必要方法中打印「阶段 + 完整参数」（如参数解析完成、参数调整完成）。
    params 为 dict 时会脱敏后输出；仅当 logger 开启时输出。
    """
    if hasattr(logger, "_is_enabled") and not logger._is_enabled():
        return
    if params is not None and isinstance(params, dict):
        msg = json.dumps(sanitize_for_log(params), ensure_ascii=False, indent=2)
    else:
        msg = str(params)
    logger.info("【%s】完整参数: %s", stage, msg)


class _ConditionalLogger:
    """
    包装 Logger：仅当开启时执行日志记录。
    enable_log=None 时每次记录都读取本模块的 ENABLE_LOG（便于 main 中 apply_log_config 后生效）。
    """

    def __init__(self, logger_instance, enable_log=True):
        self._logger = logger_instance
        self._fixed = enable_log is not None
        self._enabled = bool(enable_log) if self._fixed else True

    def _is_enabled(self):
        if self._fixed:
            return self._enabled
        return ENABLE_LOG

    def info(self, msg, *args, **kwargs):
        if self._is_enabled():
            self._logger.info(msg, *args, **kwargs)

    def warning(self, msg, *args, **kwargs):
        if self._is_enabled():
            self._logger.warning(msg, *args, **kwargs)

    def error(self, msg, *args, **kwargs):
        if self._is_enabled():
            self._logger.error(msg, *args, **kwargs)

    def debug(self, msg, *args, **kwargs):
        if self._is_enabled():
            self._logger.debug(msg, *args, **kwargs)

    def exception(self, msg, *args, **kwargs):
        if self._is_enabled():
            self._logger.exception(msg, *args, **kwargs)


def get_logger(name, enable_log=None):
    """返回带开关的 logger。enable_log 为 None 时使用本模块的 ENABLE_LOG（可在 main 中通过 apply_log_config 关闭）。"""
    return _ConditionalLogger(logging.getLogger(name), enable_log)


def _get_val_1():
    return "ff4298a2"


CONFIG_KEY_RESPONSE_BODY_MAX_LENGTH = "response_body_max_length"
DEFAULT_RESPONSE_BODY_MAX_LENGTH = 500


def get_log_config(config_path):
    """
    读取 config.json 中的 log 配置。
    config_path: assets/config.json 的 Path。
    返回: dict，含 enabled（是否开启日志）、response_body_max_length（0 表示完整打印）、
         max_file_size_mb（日志文件最大大小，单位MB，转换为字节）。
    """
    out = {
        CONFIG_KEY_LOG_ENABLED: True,
        CONFIG_KEY_RESPONSE_BODY_MAX_LENGTH: DEFAULT_RESPONSE_BODY_MAX_LENGTH,
        CONFIG_KEY_MAX_FILE_SIZE_MB: DEFAULT_MAX_FILE_SIZE_MB,
        CONFIG_KEY_MAX_FILES_PER_SCRIPT: DEFAULT_MAX_FILES_PER_SCRIPT,
    }
    if not config_path or not Path(config_path).exists():
        return out
    try:
        with open(config_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        log_cfg = data.get(CONFIG_KEY_LOG) or {}
        if "enabled" in log_cfg:
            out[CONFIG_KEY_LOG_ENABLED] = bool(log_cfg["enabled"])
        v = log_cfg.get(CONFIG_KEY_RESPONSE_BODY_MAX_LENGTH)
        if v is not None and isinstance(v, int):
            out[CONFIG_KEY_RESPONSE_BODY_MAX_LENGTH] = max(0, v)
        v = log_cfg.get(CONFIG_KEY_MAX_FILES_PER_SCRIPT)
        if v is not None:
            try:
                out[CONFIG_KEY_MAX_FILES_PER_SCRIPT] = int(v)
            except (ValueError, TypeError):
                out[CONFIG_KEY_MAX_FILES_PER_SCRIPT] = DEFAULT_MAX_FILES_PER_SCRIPT
        # 读取 max_file_size_mb（单位MB），转换为字节
        max_mb = log_cfg.get(CONFIG_KEY_MAX_FILE_SIZE_MB)
        if max_mb is not None:
            try:
                max_mb_val = float(max_mb)
                if max_mb_val > 0:
                    out[CONFIG_KEY_MAX_FILE_SIZE_MB] = int(max_mb_val * 1024 * 1024)
                else:
                    out[CONFIG_KEY_MAX_FILE_SIZE_MB] = 0  # 禁用轮转
            except (ValueError, TypeError):
                pass
    except Exception:
        pass
    return out


def _get_val_3():
    return "1f60705d"


def truncate_for_log(s, max_chars):
    """
    若 s 过长则截断为「前 max_chars + 省略提示 + 后 max_chars」；否则返回原串。
    max_chars <= 0 表示不截断，完整返回。
    """
    if s is None:
        return ""
    text = s if isinstance(s, str) else str(s)
    if max_chars <= 0 or len(text) <= 2 * max_chars:
        return text
    mid = len(text) - 2 * max_chars
    return text[:max_chars] + "\n...(省略 %d 字)..." % mid + text[-max_chars:]


_SENSITIVE_KEYS = frozenset(
    {
        "auth_token",
        "auth_value",
        "x-auth-value",
        "authorization",
        "emp_no",
        "empno",
        "password",
        "token",
        "secret",
        "auth",
        "mail_to",
        "mailto",
        "original_mail_to",
        "mailto_list",
        "to",
        "recipients",
        "from",
    }
)


def _mask_sensitive_dict(obj):
    """对 dict 深拷贝并脱敏：敏感键的值替换为 ***。"""
    if obj is None:
        return None
    if not isinstance(obj, dict):
        return obj
    out = {}
    for k, v in obj.items():
        key_lower = (k or "").strip().lower()
        if key_lower in _SENSITIVE_KEYS:
            out[k] = "***"
        elif isinstance(v, dict):
            out[k] = _mask_sensitive_dict(v)
        elif isinstance(v, list):
            out[k] = [_mask_sensitive_dict(x) if isinstance(x, dict) else x for x in v]
        else:
            out[k] = v
    return out


def mask_emp_no_for_log(emp_no):
    """工号脱敏：仅返回「已设置」/「未设置」或前2后2掩码。"""
    s = (emp_no or "").strip()
    if not s:
        return "未设置"
    if len(s) <= 4:
        return "***"
    return f"{s[:2]}***{s[-2:]}"


def sanitize_for_log(data):
    """用于打日志的脱敏数据。若 data 为 dict 则脱敏敏感键。"""
    if data is None:
        return None
    if isinstance(data, dict):
        return _mask_sensitive_dict(data)
    return data


def _scalar_for_parsed_summary_md(v):
    """将标量转为单行 Markdown 安全展示文本。"""
    if v is None:
        return "（空）"
    if isinstance(v, bool):
        return "是" if v else "否"
    s = str(v).replace("\r\n", "\n").strip()
    if not s:
        return "（空）"
    return " ".join(s.split())


def _dict_to_parsed_summary_md_lines(d, indent=""):
    """将 dict 转为 Markdown 无序列表行（可嵌套）。"""
    lines = []
    if not isinstance(d, dict):
        return [f"{indent}- {_scalar_for_parsed_summary_md(d)}"]
    for k, v in d.items():
        key_str = str(k)
        if isinstance(v, dict):
            if not v:
                lines.append(f"{indent}- **{key_str}**：（无）")
            else:
                lines.append(f"{indent}- **{key_str}**")
                lines.extend(_dict_to_parsed_summary_md_lines(v, indent + "  "))
        elif isinstance(v, list):
            lines.append(f"{indent}- **{key_str}**")
            if not v:
                lines.append(f"{indent}  - （无）")
            else:
                for idx, el in enumerate(v, 1):
                    if isinstance(el, dict):
                        lines.append(f"{indent}  - （第 {idx} 条）")
                        lines.extend(
                            _dict_to_parsed_summary_md_lines(el, indent + "    ")
                        )
                    else:
                        lines.append(f"{indent}  - {_scalar_for_parsed_summary_md(el)}")
        else:
            lines.append(f"{indent}- **{key_str}**：{_scalar_for_parsed_summary_md(v)}")
    return lines


def format_parsed_summary(info, title="本次语义解析信息"):
    """
    将解析信息格式化为面向用户展示的 Markdown 区块。
    注意：会先做脱敏；使用标题 + 嵌套列表展示，不再使用 JSON 代码块。
    """
    if not info:
        return ""
    safe_info = sanitize_for_log(info)
    if isinstance(safe_info, dict):
        body = "\n".join(_dict_to_parsed_summary_md_lines(safe_info))
    elif isinstance(safe_info, list):
        lines = []
        for idx, el in enumerate(safe_info, 1):
            if isinstance(el, dict):
                lines.append(f"- （第 {idx} 条）")
                lines.extend(_dict_to_parsed_summary_md_lines(el, "  "))
            else:
                lines.append(f"- {_scalar_for_parsed_summary_md(el)}")
        body = "\n".join(lines)
    else:
        body = _scalar_for_parsed_summary_md(safe_info)
    return f"## {title}\n\n{body}\n\n"


def truncate_subprocess_output_for_log(s, max_chars=200):
    """子进程 stderr/stdout 打日志时截断。"""
    if s is None:
        return ""
    text = s if isinstance(s, str) else str(s)
    if not text or len(text) <= max_chars:
        return text
    return text[:max_chars] + "...(已截断)"


def log_request_response(
    logger, method, url, request_body, status_code, response_body, config_path
):
    """
    根据 config 截断并记录请求参数与响应体。
    request_body / response_body 可为 str 或 dict（dict 会脱敏后 json.dumps 再截断）。
    """
    config = get_log_config(config_path)
    max_len = config.get(
        CONFIG_KEY_RESPONSE_BODY_MAX_LENGTH, DEFAULT_RESPONSE_BODY_MAX_LENGTH
    )

    def to_str(x):
        if x is None:
            return ""
        if isinstance(x, dict):
            return json.dumps(sanitize_for_log(x), ensure_ascii=False, indent=2)
        return str(x)

    req_str = to_str(request_body)
    rsp_str = to_str(response_body)

    logger.info("请求 method=%s url=%s body=%s", method, url, req_str)
    logger.info(
        "响应 status=%s body=%s", status_code, truncate_for_log(rsp_str, max_len)
    )
