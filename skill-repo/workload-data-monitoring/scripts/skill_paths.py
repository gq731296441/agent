#!/usr/bin/env python3
"""跨环境路径解析：依赖技能定位与产物输出目录。

本技能需与另外三个技能协作（studio-rdcloud-workitem 取数、oa-zmail-manage 发信、
studio-space-manager 发 Wiki），且自身产物要落到会话工作区。这些目录在不同运行
环境下位置不同：

| 环境       | 依赖技能根目录                      | 可写产物目录          |
|------------|-------------------------------------|-----------------------|
| 平台沙箱内 | /mnt/skills/<skill_id>/             | /workspace/           |
| openclaw   | ~/.openclaw/workspace/skills/<id>/  | ~/.openclaw/workspace/|
| 本地开发   | 与本技能同级的兄弟目录              | <skill_dir>/          |

平台沙箱内技能是**只读**挂载（/mnt/skills/<id>），一切产物必须写到 /workspace。
因此所有跨技能调用路径与产物输出路径都必须经本模块解析，禁止在业务代码中硬编码。
"""

import os
from pathlib import Path

# 本技能根目录（scripts/ 的上一级）
SKILL_DIR = Path(__file__).resolve().parent.parent

# 容器内 execute 的技能挂载根（与平台 mount_paths.CONTAINER_SKILL_MOUNT_PREFIX 一致）
_CONTAINER_SKILLS_ROOT = Path("/mnt/skills")

# 会话工作区（容器内可写，平台会将其中的文件登记为文件卡片）
_WORKSPACE_ROOT = Path("/workspace")

# 依赖的兄弟技能 ID
SKILL_STUDIO_RDCLOUD = "studio-rdcloud-workitem"
SKILL_OA_ZMAIL = "oa-zmail-manage"
SKILL_STUDIO_SPACE = "studio-space-manager"


def _openclaw_workspace() -> Path:
    """openclaw 运行环境下的工作区根目录。"""
    return Path(os.path.expanduser("~/.openclaw/workspace"))


def resolve_skill_dir(skill_id: str) -> str:
    """定位依赖技能的根目录。

    按容器挂载点 → openclaw 目录 → 本地开发同级目录 依次探测，返回第一个存在的。
    全部不存在时返回容器挂载路径，便于调用方报错时暴露真实缺失位置。
    """
    candidates = []
    env_root = (os.getenv("SKILLS_ROOT") or "").strip()
    if env_root:
        candidates.append(Path(env_root) / skill_id)
    candidates.append(_CONTAINER_SKILLS_ROOT / skill_id)
    candidates.append(_openclaw_workspace() / "skills" / skill_id)
    candidates.append(SKILL_DIR.parent / skill_id)  # 本地开发：与兄弟技能并排

    for candidate in candidates:
        if candidate.is_dir():
            return str(candidate)
    return str(_CONTAINER_SKILLS_ROOT / skill_id)


def resolve_output_dir(subdir: str = "") -> str:
    """产物输出目录：会话工作区优先，本地开发回退到技能目录。

    :param subdir: 工作区下的子目录（如 "reports"），空串表示工作区根目录
    """
    env_dir = (os.getenv("SKILL_OUTPUT_DIR") or "").strip()
    if env_dir:
        base = Path(env_dir)
    elif _WORKSPACE_ROOT.is_dir():
        base = _WORKSPACE_ROOT / subdir if subdir else _WORKSPACE_ROOT
    elif _openclaw_workspace().is_dir():
        base = _openclaw_workspace() / subdir if subdir else _openclaw_workspace()
    else:
        base = SKILL_DIR / subdir if subdir else SKILL_DIR
    base.mkdir(parents=True, exist_ok=True)
    return str(base)


def resolve_report_dir() -> str:
    """报告产物目录（会话工作区 /workspace/reports/）。"""
    return resolve_output_dir("reports")


def resolve_export_dir(skill_id: str = SKILL_STUDIO_RDCLOUD) -> str:
    """读取 studio-rdcloud-workitem 导出 xlsx 的目录。

    必须与其 export_work_item.py 的写入位置保持一致：
      会话工作区内 → /workspace/exports/<skill_id>/
      本地开发     → <studio-rdcloud-workitem>/logs/
    两侧任一处改动，另一侧需同步，否则分析器将找不到导出文件。
    """
    if _WORKSPACE_ROOT.is_dir():
        export_dir = _WORKSPACE_ROOT / "exports" / skill_id
    else:
        export_dir = Path(resolve_skill_dir(skill_id)) / "logs"
    export_dir.mkdir(parents=True, exist_ok=True)
    return str(export_dir)


def latest_export_file(skill_id: str = SKILL_STUDIO_RDCLOUD) -> str:
    """返回导出目录下最新的 export_work_item_*.xlsx 路径；不存在时返回空串。"""
    export_dir = resolve_export_dir(skill_id)
    if not os.path.isdir(export_dir):
        return ""
    files = sorted(
        (f for f in os.listdir(export_dir)
         if f.startswith("export_work_item_") and f.endswith(".xlsx")),
        reverse=True,
    )
    return os.path.join(export_dir, files[0]) if files else ""
