"""
工具描述补丁模块。

在 deepagents middleware 创建文件工具之前，替换默认工具描述常量和 Schema，
引导 LLM 采用分块写入策略，避免大参数一次性生成导致的慢速问题，
同时强化必填参数的提示防止漏参。

此模块在 import 时即生效，只需在 factory.py 中保证在 create_deep_agent
之前 import 即可（模块级 import 天然满足此约束）。
"""

import logging
import threading
import time

from pydantic import model_validator

from deepagents.middleware import filesystem as _fs_mw

logger = logging.getLogger(__name__)

# ── 补丁 4：write_file/edit_file 参数严格校验（友好错误 + 失败熔断）─
#
# 背景：deepseek 等模型流式输出 write_file 的超大 content 参数时经常被截断，
# 导致工具收到的 args 只有 file_path、缺 content，pydantic 报原始错误
# ("content Field required [type=missing]")，模型不理解错误含义，
# 重新生成仍缺 content，循环重试直到 run 超时。
#
# 方案：替换 WriteFileSchema/EditFileSchema 为带 model_validator 的严格子类：
# 1. content 缺失 → 返回 LLM 可理解的友好错误，明确要求补全参数或改用分块策略
# 2. 同一文件路径连续失败达阈值 → 熔断，禁止再次调用 write_file，
#    迫使模型切换策略（edit_file/execute/向用户报告），避免死循环直到超时
# 注：不做 content 长度硬限制（模型按需输出，长度由模型自行决定）

_FAIL_LIMIT = 3                   # 连续失败阈值
_FAIL_WINDOW_SECONDS = 300        # 计数窗口：窗口内连续失败才累计，窗口外自动重置

_fail_lock = threading.Lock()
_fail_records: dict = {}          # f"{tool}:{file_path}" -> {"count": int, "first_ts": float}


def _register_failure(key: str) -> int:
    """登记一次校验失败，返回窗口内累计失败次数。"""
    now = time.time()
    with _fail_lock:
        rec = _fail_records.get(key)
        if rec is None or now - rec["first_ts"] > _FAIL_WINDOW_SECONDS:
            _fail_records[key] = {"count": 1, "first_ts": now}
            return 1
        rec["count"] += 1
        return rec["count"]


def _reset_failure(key: str) -> None:
    """参数通过校验时重置失败计数。"""
    with _fail_lock:
        _fail_records.pop(key, None)


def _fail_key(tool_name: str, file_path: str) -> str:
    return f"{tool_name}:{file_path}"


def _build_validation_error(key: str, tool_name: str, file_path: str, detail: str, guidance: str) -> ValueError:
    count = _register_failure(key)
    if count >= _FAIL_LIMIT:
        return ValueError(
            f"CRITICAL: {tool_name} 已连续失败 {count} 次（参数不完整）。"
            f"禁止再次调用 {tool_name}！"
            f"立即改用以下策略之一：\n"
            f"1. 若目标文件已存在，用 edit_file 分块填充/替换内容；\n"
            f"2. 用 execute 在沙箱中执行脚本生成文件（如 python 脚本）；\n"
            f"3. 无法完成时，如实向用户报告失败原因，不要重试 {tool_name}。"
        )
    return ValueError(
        f"Error: {tool_name} 参数无效 —— {detail}\n"
        f"目标文件: {file_path or '(未提供)'}\n"
        f"{guidance}"
    )


class _StrictWriteFileSchema(_fs_mw.WriteFileSchema):
    """WriteFileSchema 严格版：content 缺失/超长时给出可执行的友好错误。"""

    _zte_strict: bool = False  # 幂等标记：apply_patches 已替换过

    @model_validator(mode="before")
    @classmethod
    def _check_args(cls, data):
        if isinstance(data, dict):
            file_path = str(data.get("file_path") or "")
            key = _fail_key("write_file", file_path)
            if "content" not in data or data.get("content") is None:
                raise _build_validation_error(
                    key, "write_file", file_path,
                    detail="缺少必填的 content 字段（模型生成参数时 content 被截断或遗漏）。",
                    guidance=(
                        "请重新调用，必须同时提供 file_path 和 content。\n"
                        "若文件内容较大（>4000 字符），不要在 write_file 中输出长文本：\n"
                        "先 write_file 创建骨架（含 <!-- SLOT_1 --> 占位符），\n"
                        "再用 edit_file 逐个替换占位符分块填充内容。"
                    ),
                )
            _reset_failure(key)
        return data


class _StrictEditFileSchema(_fs_mw.EditFileSchema):
    """EditFileSchema 严格版：new_string 缺失/超长时给出可执行的友好错误。"""

    _zte_strict: bool = False  # 幂等标记：apply_patches 已替换过

    @model_validator(mode="before")
    @classmethod
    def _check_args(cls, data):
        if isinstance(data, dict):
            file_path = str(data.get("file_path") or "")
            key = _fail_key("edit_file", file_path)
            if "old_string" not in data or data.get("old_string") is None:
                raise _build_validation_error(
                    key, "edit_file", file_path,
                    detail="缺少必填的 old_string 字段（模型生成参数时被截断或遗漏）。",
                    guidance=(
                        "请重新调用，必须同时提供 file_path、old_string、new_string。\n"
                        "old_string 必须与文件中现有内容精确匹配。"
                    ),
                )
            if "new_string" not in data or data.get("new_string") is None:
                raise _build_validation_error(
                    key, "edit_file", file_path,
                    detail="缺少必填的 new_string 字段（模型生成参数时被截断或遗漏）。",
                    guidance=(
                        "请重新调用，必须同时提供 file_path、old_string、new_string。\n"
                        "new_string 保持 ≤1000 字符，分多次 edit_file 填充大段内容。"
                    ),
                )
            _reset_failure(key)
        return data

# ── 补丁 1：强化 WriteFileSchema.content 的必填提示 ──────────────────────────
# 原描述 "The text content to write to the file. This parameter is required."
# 对 LLM 来说不够醒目，容易被忽略导致漏参。
_patched_schema_content = False


def _patch_schema_fields() -> None:
    """强化 WriteFileSchema 和 EditFileSchema 中必填字段的描述。"""
    global _patched_schema_content
    if _patched_schema_content:
        return
    try:
        content_field = _fs_mw.WriteFileSchema.model_fields.get("content")
        if content_field is not None:
            content_field.description = (
                "REQUIRED — the text content to write. "
                "Even for skeleton files you MUST include this field. "
                "For empty files use content=''. "
                "For chunked writing, keep content under 1000 characters."
            )
        file_path_field = _fs_mw.WriteFileSchema.model_fields.get("file_path")
        if file_path_field is not None:
            file_path_field.description = (
                "REQUIRED — absolute path where the file should be created (e.g., /workspace/file.html). "
                "Must be absolute, not relative."
            )

        new_string_field = _fs_mw.EditFileSchema.model_fields.get("new_string")
        if new_string_field is not None:
            new_string_field.description = (
                "REQUIRED — the replacement text. "
                "Keep under 1000 characters per edit for chunked writing. "
                "Must be different from old_string."
            )
        old_string_field = _fs_mw.EditFileSchema.model_fields.get("old_string")
        if old_string_field is not None:
            old_string_field.description = (
                "REQUIRED — the exact text to find and replace. "
                "Must be unique in the file unless replace_all is True."
            )

        _patched_schema_content = True
        logger.info("[ToolPatches] Schema 字段描述已强化（必填提示）")
    except Exception:
        logger.warning("[ToolPatches] Schema 字段补丁失败，跳过", exc_info=True)


# ── 补丁 2：增强 write_file 工具描述 ──────────────────────────────────────────
ENHANCED_WRITE_FILE_DESCRIPTION = """Creates a NEW file in the filesystem.

**PARAMETERS (all required):**
- `file_path` (string, REQUIRED): Absolute path for the new file.
- `content` (string, REQUIRED): The content to write. Even for skeleton files you MUST pass content (use "" if truly empty).

**CRITICAL RULE:**
- NEVER write more than 1000 characters in a single `write_file` call. For larger files, use the chunked strategy below.

**Chunked writing strategy (for files > 1000 chars):**
1. **Plan with `write_todos`** – break the content into logical blocks (each ≤ 1000 chars).
2. **Create skeleton** – call `write_file` with a minimal structure (e.g., `<html><body><!-- SLOT_1 --></body></html>`) containing unique placeholders like `<!-- SLOT_1 -->`.
3. **Then switch to `edit_file`** – do NOT call `write_file` again for this file; use `edit_file` to replace placeholders one by one.

**Important:**
- `write_file` is for NEW files only. To modify an existing file, always use `edit_file`.
- After writing the skeleton, immediately switch to `edit_file` – never call `write_file` on the same file again.

**Multi-file web projects:**
- When building web pages, create a project subdirectory (e.g., `/workspace/web-project/` or `/workspace/report/`).
- Split HTML structure, CSS styles, and JS logic into separate files — do NOT inline everything into a single .html file.
- Use relative paths in HTML: `<link rel="stylesheet" href="style.css">` and `<script src="script.js"></script>`.
- Do NOT use external CDN URLs (`https://...`) — all resources must be local workspace files in the same directory.
- Standard project structure: `index.html` (entry) + `style.css` (styles) + `script.js` (logic).
	- **IMPORTANT:** `write_file` automatically creates parent directories. Do NOT use `execute("mkdir ...")` to pre-create directories — this causes permission conflicts between the sandbox and host filesystem."""

# ── 补丁 3：增强 edit_file 工具描述 ───────────────────────────────────────────
ENHANCED_EDIT_FILE_DESCRIPTION = """Performs exact string replacements in files. This is the PRIMARY tool for building large files incrementally.

**PARAMETERS (all required unless noted):**
- `file_path` (string, REQUIRED): Absolute path to the file to edit.
- `old_string` (string, REQUIRED): The exact text to find and replace.
- `new_string` (string, REQUIRED): The replacement text. Keep under 1000 characters per edit.
- `replace_all` (boolean, optional): If True, replace all occurrences. Defaults to False.

**CRITICAL RULE:**
- NEVER pass a `new_string` longer than 1000 characters. If your block exceeds that, split it into multiple edits and update your `write_todos` list.

**Chunked editing workflow (for files > 1000 chars):**
1. **Read first** – always call `read_file` before editing to know the current content.
2. **Replace one placeholder at a time** – use `edit_file` to replace `<!-- SLOT_1 -->` with actual content. To allow further appends to the same slot, **keep the placeholder at the end of the new content**, e.g.:edit_file(path, "<!-- SLOT_1 -->", "Section content...\n<!-- SLOT_1 -->")
3. **Verify periodically** – after every 2–3 edits, call `read_file` to check structure (tags closure, indentation, no leftover placeholders).
4. **Final cleanup** – remove all remaining placeholders (e.g., replace `<!-- SLOT_1 -->` with empty string).

**For appending to the end:**
- Include `<!-- END -->` at the bottom of your skeleton.
- Replace it with `new_content + "\n<!-- END -->"` to keep the marker for future appends.

**Prohibited:**
- Editing without a prior `read_file` (the tool will error).
- Omitting `old_string` or `new_string` – all three required fields must be provided on every call.
- Using `edit_file` to replace large chunks (>1000 chars) in one go – split into smaller edits."""


def apply_patches() -> bool:
    """替换 deepagents 的文件工具描述常量和 Schema 字段。幂等，重复调用无害。

    Returns:
        True 表示首次应用成功，False 表示已应用过（幂等跳过）。
    """
    changed = False

    # _patch_schema_fields()

    # if _fs_mw.WRITE_FILE_TOOL_DESCRIPTION != ENHANCED_WRITE_FILE_DESCRIPTION:
    #     _fs_mw.WRITE_FILE_TOOL_DESCRIPTION = ENHANCED_WRITE_FILE_DESCRIPTION
    #     changed = True
    #     logger.info("[ToolPatches] write_file 描述已替换")

    # if _fs_mw.EDIT_FILE_TOOL_DESCRIPTION != ENHANCED_EDIT_FILE_DESCRIPTION:
    #     _fs_mw.EDIT_FILE_TOOL_DESCRIPTION = ENHANCED_EDIT_FILE_DESCRIPTION
    #     changed = True
    #     logger.info("[ToolPatches] edit_file 描述已替换")

    # 替换工具 args_schema 为严格校验版（需在 _patch_schema_fields 之后，
    # 使严格子类继承强化后的字段描述）
    if getattr(_fs_mw.WriteFileSchema, "_zte_strict", False) is not True:
        _fs_mw.WriteFileSchema = _StrictWriteFileSchema
        _fs_mw.EditFileSchema = _StrictEditFileSchema
        _StrictWriteFileSchema._zte_strict = True
        _StrictEditFileSchema._zte_strict = True
        changed = True
        logger.info("[ToolPatches] WriteFileSchema/EditFileSchema 已替换为严格校验版（友好错误+失败熔断）")

    return changed


# ── 模块 import 时自动应用 ──────────────────────────────────────────────────
_patched = apply_patches()
