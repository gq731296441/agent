#!/usr/bin/env python3
"""NFS 挂载验证脚本
在 Agent 容器/服务器上执行，验证是否能正常读写虚机挂载的工作区目录。
成功则虚机上可看到: /data/cmtool/agent-test/nfs_test_{hostname}_{timestamp}.txt
"""
import os
import socket
import time
from datetime import datetime

from config.config import app_config


def test_workspace():
    root = app_config.get_workspace_root()
    hostname = socket.gethostname()
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"nfs_test_{hostname}_{ts}.txt"
    filepath = os.path.join(root, filename)

    print(f"WORKSPACE_ROOT: {root}")
    print(f"测试文件: {filepath}")

    # 检查目录
    if not os.path.exists(root):
        print(f"\n❌ 目录不存在: {root}")
        print("   NFS 未挂载或挂载失败，请检查: mount | grep /data/cmtool/agent")
        return False

    if not os.access(root, os.W_OK):
        print(f"\n❌ 目录无写权限: {root}")
        print(f"   当前用户: {os.getuid()}, 目录权限: {oct(os.stat(root).st_mode)[-3:]}")
        return False

    # 写入
    content = (
        f"NFS 挂载验证 - 测试文件\n"
        f"时间: {datetime.now().isoformat()}\n"
        f"主机: {hostname}\n"
        f"用户: UID={os.getuid()}\n"
        f"ENVIRONMENT: {app_config.ENVIRONMENT}\n"
    )
    try:
        with open(filepath, "w") as f:
            f.write(content)
        print(f"\n✅ 写入成功: {filepath}")
    except Exception as e:
        print(f"\n❌ 写入失败: {e}")
        return False

    # 回读
    try:
        with open(filepath) as f:
            readback = f.read()
        print(f"✅ 回读成功 ({len(readback)} 字节)")
    except Exception as e:
        print(f"❌ 回读失败: {e}")
        return False

    # 虚机验证提示
    print(f"\n虚机验证: ssh root@10.57.69.129 'cat {filepath}'")
    return True


if __name__ == "__main__":
    ok = test_workspace()
    exit(0 if ok else 1)
