#!/usr/bin/env python3
"""会话工作区路径解析：技能导出产物输出目录。

平台沙箱内技能是**只读**挂载（/mnt/skills/<id>），且用户主目录下并无
.openclaw/workspace —— 产物只有写到会话工作区 /workspace 才能被用户
在前端看到并下载。本模块统一解析该目录，兼容三种运行环境：

| 环境       | 产物目录                                      |
|------------|-----------------------------------------------|
| 平台沙箱内 | /workspace/exports/imap-indicator-query/      |
| openclaw   | ~/.openclaw/workspace/exports/<skill_id>/     |
| 本地开发   | <skill_dir>/exports/                          |

禁止在业务代码中再硬编码 ~/.openclaw 之类的主目录路径。
"""

import os
from pathlib import Path

# 本技能根目录（scripts/ 的上一级）
SKILL_DIR = Path(__file__).resolve().parent.parent

# 会话工作区（容器内可写，平台会将其中的文件登记为文件卡片）
_WORKSPACE_ROOT = Path("/workspace")


def resolve_export_dir() -> str:
    """返回导出文件的输出目录，不存在时自动创建。

    会话工作区下按技能 ID 分子目录（exports/<skill_id>/），与
    studio-rdcloud-workitem 等技能的导出约定保持一致，避免多技能互相覆盖。
    """
    env_dir = (os.getenv("IMAP_EXPORT_DIR") or "").strip()
    if env_dir:
        base = Path(env_dir)
    elif _WORKSPACE_ROOT.is_dir():
        base = _WORKSPACE_ROOT / "exports" / SKILL_DIR.name
    elif Path(os.path.expanduser("~/.openclaw/workspace")).is_dir():
        base = Path(os.path.expanduser("~/.openclaw/workspace")) / "exports" / SKILL_DIR.name
    else:
        # 本地开发：无工作区时退到技能目录下
        base = SKILL_DIR / "exports"
    base.mkdir(parents=True, exist_ok=True)
    return str(base)
