"""工作区挂载健康检查。

生产/测试环境依赖虚机 NFS export（agent-dev|agent-test|agent-production）挂载到
WORKSPACE_ROOT（默认 /data/cmtool/agent）。若挂载失败，代码中的 mkdir(exist_ok=True)
会静默降级到本地磁盘，造成"看起来正常、实际数据没上 NFS"的隐患（远程 dockerd
在虚机上 bind 不到文件）。

本模块在启动时探测工作区根目录：是否缺失、是否可写、是否落在 NFS 挂载上，
供 main.py 启动阶段输出告警，避免故障静默。
"""

from __future__ import annotations

import os
import sys
import uuid
from pathlib import Path
from typing import Optional

from config.config import app_config
from config import get_logger

logger = get_logger(__name__)

# NFS 可接受的文件系统类型（fstype）；其余（xfs/ext4/btrfs 等）视为本地磁盘
_NFS_FSTYPES = {"nfs", "nfs4"}


def _parse_mountinfo(mountinfo_text: str) -> list[dict]:
    """解析 /proc/self/mountinfo 每行为结构化字典。

    mountinfo 行格式（' - ' 前为挂载点信息，后为 fs 类型、source、super options）：
        id parent major:minor root mount_point mount_opts optional... - fstype source super_opts

    Returns:
        [{"mount_point":..., "fstype":..., "source":..., "super_opts":...}, ...]
    """
    entries = []
    for line in mountinfo_text.splitlines():
        if " - " not in line:
            continue
        head, _, tail = line.partition(" - ")
        head_parts = head.split()
        tail_parts = tail.split()
        if len(head_parts) < 6 or len(tail_parts) < 3:
            continue
        mount_point = head_parts[4]
        fstype = tail_parts[0]
        source = tail_parts[1]
        super_opts = tail_parts[2]
        entries.append(
            {
                "mount_point": mount_point,
                "fstype": fstype,
                "source": source,
                "super_opts": super_opts,
            }
        )
    return entries


def _unescape_mount_source(source: str) -> str:
    """解析 mountinfo source 中的八进制转义（\040=空格, \057=/ 等）。"""
    try:
        return source.encode("utf-8").decode("unicode_escape")
    except (UnicodeDecodeError, ValueError):
        return source


def _fstype_of(path: Path) -> Optional[str]:
    """返回路径所在挂载点的文件系统类型；无法确定时返回 None。

    优先用 statvfs 的 f_fsid 精确匹配（仅当 f_fsid 非 0 时可信），
    再回退到"最长路径前缀"匹配。f_fsid 在部分容器/受限环境下对所有
    文件系统恒为 0，此时只能靠路径前缀判断。
    """
    target = str(path).rstrip("/\\")

    try:
        entries = _parse_mountinfo(Path("/proc/self/mountinfo").read_text())
    except OSError:
        entries = []

    # 1) f_fsid 精确匹配（仅 f_fsid 非 0 时可信）
    try:
        f_fsid = os.statvfs(str(path)).f_fsid
    except OSError:
        f_fsid = None

    if f_fsid:
        for e in entries:
            mp = e["mount_point"]
            try:
                if os.statvfs(mp).f_fsid == f_fsid:
                    return e["fstype"]
            except OSError:
                continue

    # 2) 最长路径前缀匹配：target 从最深的挂载点前缀开始找
    prefix_candidates = [
        e for e in entries
        if e["mount_point"] != "/" and target.startswith(e["mount_point"])
    ]
    if prefix_candidates:
        prefix_candidates.sort(key=lambda e: len(e["mount_point"]), reverse=True)
        return prefix_candidates[0]["fstype"]

    return None


def _read_write_test(path: Path, timeout: float = 5.0) -> tuple[bool, str]:
    """尝试在工作区根目录写入并删除一个临时文件，验证可读写。

    Returns:
        (成功, 错误信息)。成功时 error 为空字符串。
    """
    probe = path / f".health_probe_{uuid.uuid4().hex}.tmp"
    try:
        with open(probe, "w") as f:
            f.write("ok")
        # NFS 写入有延迟，读回确认（部分 soft 挂载写返回后数据不可见）
        with open(probe) as f:
            if f.read() != "ok":
                return False, "写入后回读内容不一致（NFS 同步延迟/挂载异常）"
        probe.unlink()
        return True, ""
    except OSError as e:
        try:
            probe.unlink(missing_ok=True)
        except OSError:
            pass
        return False, f"{e}"
    except Exception as e:  # noqa: BLE001 - 探测失败统一转告警
        try:
            probe.unlink(missing_ok=True)
        except OSError:
            pass
        return False, f"{e}"


def _expects_nfs(root_str: str) -> bool:
    """按部署拓扑判断该工作区根路径是否预期为 NFS 挂载。

    生产/测试/开发环境统一将虚机 agent-dev|agent-test|agent-production NFS
    挂到 /data/cmtool/agent；若根路径不在此目录下（如开发机自定义 WORKSPACE_ROOT
    指向本地目录），则不做 NFS 强制检查。
    """
    return root_str.startswith("/data/cmtool")


def check_workspace_health(root: Optional[str] = None) -> dict:
    """检查工作区根目录的健康状态。

    Returns:
        {
            "root": 实际检查路径,
            "exists": 目录是否存在,
            "writable": 是否可读写,
            "fstype": 文件系统类型（nfs4/xfs/None）,
            "is_nfs": 是否 NFS 挂载,
            "expected_nfs": 按部署拓扑是否预期为 NFS,
            "ok": 是否健康,
            "reason": 不健康时的原因描述,
        }
    """
    root = root or app_config.get_workspace_root()
    path = Path(root)

    # Windows 开发环境自动重定位到项目目录，非 NFS 部署拓扑，直接视为健康
    if sys.platform == "win32":
        return {
            "root": str(path),
            "exists": path.is_dir(),
            "writable": os.access(path, os.W_OK),
            "fstype": None,
            "is_nfs": False,
            "expected_nfs": False,
            "ok": True,
            "reason": "Windows 开发环境，工作区重定位到项目目录，跳过 NFS 检查",
        }

    result = {
        "root": str(path),
        "exists": path.is_dir(),
        "writable": False,
        "fstype": None,
        "is_nfs": False,
        "expected_nfs": False,
        "ok": False,
        "reason": "",
    }

    if not path.exists():
        result["reason"] = (
            f"工作区目录不存在: {path}。NFS 未挂载或挂载失败，"
            f"请检查: mount | grep /data/cmtool"
        )
        return result

    if not path.is_dir():
        result["reason"] = f"工作区路径不是目录: {path}"
        return result

    fstype = _fstype_of(path)
    is_nfs = fstype in _NFS_FSTYPES
    result["fstype"] = fstype
    result["is_nfs"] = is_nfs

    # 部署拓扑预期：非 Windows 下，根路径应落在 /data/cmtool 的 NFS 挂载上。
    # 若用户显式将 WORKSPACE_ROOT 指到本地路径（如开发机 /workspace），不强制。
    root_str = str(path).rstrip("/\\")
    result["expected_nfs"] = _expects_nfs(root_str)

    writable, err = _read_write_test(path)
    result["writable"] = writable

    if not writable:
        result["reason"] = (
            f"工作区目录不可写: {path} ({err})。"
            f"请检查 NFS 挂载权限与磁盘空间"
        )
        return result

    if result["expected_nfs"] and not is_nfs:
        result["reason"] = (
            f"工作区 {path} 位于 /data/cmtool 下但非 NFS 挂载（当前 fstype={fstype}）。"
            f"部署环境应挂载虚机 agent-dev|agent-test|agent-production NFS，"
            f"否则数据不会同步到虚机/沙箱。请检查: mount | grep /data/cmtool"
        )
        return result

    result["ok"] = True
    result["reason"] = ""
    return result


def log_workspace_health(result: dict) -> None:
    """按检查结果输出启动日志：健康打 info，不健康打 warning 告警。"""
    if result.get("ok"):
        logger.info(
            f"【Workspace】✅ 工作区健康检查通过 root={result['root']} "
            f"fstype={result.get('fstype')} nfs={result.get('is_nfs')} "
            f"writable={result.get('writable')}"
        )
    else:
        logger.warning(
            f"【Workspace】⚠️ 工作区健康检查未通过: {result.get('reason')}"
        )
