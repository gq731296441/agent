# -*- coding: utf-8 -*-
"""
icenter-msg 发送消息技能 - Python 版入口。
"""
import json
import logging
import os
import stat
import subprocess
import sys
from logging.handlers import RotatingFileHandler
from typing import Any, Dict, List

from icenter_msg_utils import (
    check_icenter_available,
    get_user_auth_info,
    search_group,
    search_users,
    send_message,
    GroupInfoItem,
    SearchResult,
    UserInfo,
)

# 配置日志记录器（20M 循环覆盖），日志目录与当前脚本同路径
_log_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs")
if not os.path.exists(_log_dir):
    os.makedirs(_log_dir)

_log_file = os.path.join(_log_dir, "icenter-msg.log")
_file_handler = RotatingFileHandler(
    _log_file,
    maxBytes=20 * 1024 * 1024,  # 20MB
    backupCount=5,
    encoding="utf-8",
)
_file_handler.setLevel(logging.DEBUG)
_formatter = logging.Formatter("%(asctime)s - [%(name)s] - %(levelname)s - %(message)s")
_file_handler.setFormatter(_formatter)

# 控制台 Handler：与 icenter_msg_utils 共用同一 logger，日志一起输出到终端和文件
_console_handler = logging.StreamHandler(sys.stderr)
_console_handler.setLevel(logging.DEBUG)
_console_handler.setFormatter(_formatter)

_app_logger = logging.getLogger("icenter-msg")
_app_logger.setLevel(logging.DEBUG)
_app_logger.propagate = False
_app_logger.addHandler(_file_handler)
_app_logger.addHandler(_console_handler)


def show_usage() -> None:
    """
    打印脚本使用方法说明到 stderr。
    """
    prog = "icenter-msg.py"
    sys.stderr.write(f"用法: {prog} [选项]\n")
    sys.stderr.write("\n发送消息（单聊/群聊可同时指定）: -s [--msg <消息内容>|--img <图片路径>|--file <文件路径>]\n")
    sys.stderr.write("   -s 表示发送；--msg、--img、--file 三者必填其一。\n")
    sys.stderr.write("   单聊: --userId/--userNo/--empNo <接收者ID/工号> [--userName <用户名>]。\n")
    sys.stderr.write("   群聊: --groupId <群组ID/URI> [--groupName <群组名>] [--at <at列表>]。\n")
    sys.stderr.write("   可同时指定 --userId/--empNo 与 --groupId，一次请求同时发单聊和群聊。\n")
    sys.stderr.write("   --msg 为文本消息内容；--img 为图片路径（只支持 bmp、jpg、png）；--file 为文件路径。\n")
    sys.stderr.write("\n3. 搜索用户信息: -q --userName <用户名>\n")
    sys.stderr.write("   -q 表示搜索；搜索用户时 --userName 必填。\n")
    sys.stderr.write("\n4. 搜索群组信息: -q --groupName <群组名>\n")
    sys.stderr.write("   -q 表示搜索；搜索群组时 --groupName 必填。\n")
    sys.stderr.write("\n示例:\n")
    sys.stderr.write(f"  {prog} -s --userId 0668000408 --userName 小张 --msg '明天开会'\n")
    sys.stderr.write(f"  {prog} -s --groupId 'sip:p1_xxx@gm.zte.com.cn' --groupName 项目组 --msg '明天开会'\n")
    sys.stderr.write(f"  {prog} -s --groupId 'sip:p1_xxx@gm.zte.com.cn' --groupName 项目组 --at '0668000290|all' --msg '明天开会'\n")
    sys.stderr.write(f"  {prog} -s --userId 0668000408 --img '/home/user/photo.jpg'\n")
    sys.stderr.write(f"  {prog} -s --userId 0668000408 --file '/home/user/report.pdf'\n")
    sys.stderr.write(f"  {prog} -s --userId 0668000408 --groupId 'sip:xxx@gm.zte.com.cn' --msg '通知'  # 同时发单聊和群聊\n")
    sys.stderr.write(f"  {prog} -q --userName 小张\n")
    sys.stderr.write(f"  {prog} -q --groupName 项目组\n")


def _strip_quotes(s: str) -> str:
    """
    去掉值首尾的一层双引号或单引号。

    支持 "..." 或 '...' 形式的参数值。

    Args:
        s: 原始字符串

    Returns:
        去掉引号后的字符串
    """
    if not s or len(s) < 2:
        return s
    if (s[0] == '"' and s[-1] == '"') or (s[0] == "'" and s[-1] == "'"):
        return s[1:-1]
    return s


def parse_args(args: List[str]) -> Dict[str, str]:
    """
    解析命令行参数。

    支持 --key value 与 --key=value；值支持用 "" 或 '' 包裹。

    Args:
        args: 命令行参数列表

    Returns:
        键为小写带下划线的参数字典
    """
    out = {}
    i = 0
    while i < len(args):
        arg = args[i]
        if arg == "-s":
            out["send_mode"] = "1"
            i += 1
        elif arg == "-q":
            out["search_mode"] = "1"
            i += 1
        elif arg == "--empNo":
            if i + 1 >= len(args):
                sys.stderr.write("[错误] --empNo 参数需要指定工号\n")
                sys.exit(1)
            out["emp_no"] = _strip_quotes(args[i + 1])
            i += 2
        elif arg.startswith("--empNo="):
            out["emp_no"] = _strip_quotes(arg.split("=", 1)[1])
            i += 1
        elif arg == "--userId":
            if i + 1 >= len(args):
                sys.stderr.write("[错误] --userId 参数需要指定工号\n")
                sys.exit(1)
            out["emp_no"] = _strip_quotes(args[i + 1])
            i += 2
        elif arg.startswith("--userId="):
            out["emp_no"] = _strip_quotes(arg.split("=", 1)[1])
            i += 1
        elif arg == "--userNo":
            if i + 1 >= len(args):
                sys.stderr.write("[错误] --userNo 参数需要指定工号\n")
                sys.exit(1)
            out["emp_no"] = _strip_quotes(args[i + 1])
            i += 2
        elif arg.startswith("--userNo="):
            out["emp_no"] = _strip_quotes(arg.split("=", 1)[1])
            i += 1
        elif arg == "--userName":
            if i + 1 >= len(args):
                sys.stderr.write("[错误] --userName 参数需要指定用户名\n")
                sys.exit(1)
            out["user_name"] = _strip_quotes(args[i + 1])
            i += 2
        elif arg.startswith("--userName="):
            out["user_name"] = _strip_quotes(arg.split("=", 1)[1])
            i += 1
        elif arg == "--msg":
            if i + 1 >= len(args):
                sys.stderr.write("[错误] --msg 参数需要指定消息内容\n")
                sys.exit(1)
            out["msg"] = _strip_quotes(args[i + 1])
            i += 2
        elif arg.startswith("--msg="):
            out["msg"] = _strip_quotes(arg.split("=", 1)[1])
            i += 1
        elif arg == "--img":
            if i + 1 >= len(args):
                sys.stderr.write("[错误] --img 参数需要指定图片路径\n")
                sys.exit(1)
            out["img"] = _strip_quotes(args[i + 1])
            i += 2
        elif arg.startswith("--img="):
            out["img"] = _strip_quotes(arg.split("=", 1)[1])
            i += 1
        elif arg == "--file":
            if i + 1 >= len(args):
                sys.stderr.write("[错误] --file 参数需要指定文件路径\n")
                sys.exit(1)
            out["file"] = _strip_quotes(args[i + 1])
            i += 2
        elif arg.startswith("--file="):
            out["file"] = _strip_quotes(arg.split("=", 1)[1])
            i += 1
        elif arg == "--groupId":
            if i + 1 >= len(args):
                sys.stderr.write("[错误] --groupId 参数需要指定群组ID或群组URI\n")
                sys.exit(1)
            out["group_id"] = _strip_quotes(args[i + 1])
            i += 2
        elif arg.startswith("--groupId="):
            out["group_id"] = _strip_quotes(arg.split("=", 1)[1])
            i += 1
        elif arg == "--groupName":
            if i + 1 >= len(args):
                sys.stderr.write("[错误] --groupName 参数需要指定群组名称\n")
                sys.exit(1)
            out["group_name"] = _strip_quotes(args[i + 1])
            i += 2
        elif arg.startswith("--groupName="):
            out["group_name"] = _strip_quotes(arg.split("=", 1)[1])
            i += 1
        elif arg == "--at":
            if i + 1 >= len(args):
                sys.stderr.write("[错误] --at 参数需要指定 at 列表\n")
                sys.exit(1)
            out["at_list"] = _strip_quotes(args[i + 1])
            i += 2
        elif arg.startswith("--at="):
            out["at_list"] = _strip_quotes(arg.split("=", 1)[1])
            i += 1
        else:
            i += 1
    return out


def output_json(obj: Any) -> None:
    """
    向 stdout 输出格式化的 JSON。

    Args:
        obj: 要输出为 JSON 的对象
    """
    json.dump(obj, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")


def handle_send_message(
    current_emp_no: str,
    token: str,
    emp_no: str,
    user_name: str,
    group_id: str,
    group_name: str,
    msg: str,
    img: str,
    file: str,
    at_list: str,
    msg_type: str,
) -> Dict[str, Any]:
    """
    统一发送消息（单聊、群聊或同时发送）。

    Args:
        current_emp_no: 发送者工号
        token:          认证令牌
        emp_no:         单聊接收者工号
        user_name:      单聊接收者姓名
        group_id:       群聊群 ID
        group_name:     群聊群名
        msg:            文本消息内容
        img:            图片路径
        file:           文件路径
        at_list:        @ 列表字符串
        msg_type:       消息类型：text | image | file

    Returns:
        包含发送结果的字典
    """
    # 根据消息类型确定最终消息内容
    if msg_type == "text":
        final_msg = msg + "\n（本消息由兴小助Co-Claw发出）"
    elif msg_type == "image":
        final_msg = img
    else:  # file
        final_msg = file
    targets = []
    if emp_no:
        targets.append(f"单聊 {user_name or emp_no}({emp_no})")
    if group_id:
        targets.append(f'群聊 "{group_name or group_id}"')
    _app_logger.debug(
        "发送: emp_no=%s user_name=%s group_id=%s group_name=%s at_list=%s msg_type=%s",
        emp_no, user_name, group_id, group_name, at_list, msg_type,
    )
    print(f"\n[步骤2] 正在发送消息到: {', '.join(targets)}...")
    resp, err = send_message(
        current_emp_no,
        token,
        final_msg,
        msg_type=msg_type,
        user_id=emp_no or None,
        user_name=user_name or None,
        group_id=group_id or None,
        group_name=group_name or None,
        at_list=at_list or None,
    )
    if err:
        _app_logger.error("发送失败: %s", err)
        return {"success": False, "message": f"发送消息失败: {err}"}
    _app_logger.info("发送成功: %s", resp)
    bo = (resp or {}).get("bo") or {}
    msgid = bo.get("msgId") or bo.get("msgid") or ""
    result = {"success": True, "message": "消息发送成功", "msgid": msgid, "response": resp or {}}
    if emp_no:
        result["single"] = {"receiver": user_name, "userId": emp_no}
    if group_id:
        result["group"] = {"groupName": group_name, "groupUri": group_id}
    return result


def display_users(users: List[UserInfo]) -> None:
    """
    打印用户列表到 stdout。

    Args:
        users: UserInfo 对象列表
    """
    print(f"\n[成功] 找到 {len(users)} 个匹配的用户\n")
    print("用户列表（请选择序号）：")
    print("=" * 60)
    for idx, user in enumerate(users, 1):
        name_part = user.name
        if user.name_en:
            name_part += f" ({user.name_en})"
        print(f"{idx}. 姓名：{name_part}")
        print(f"   - 工号：{user.emp_no}")
        if user.dept_name:
            print(f"   - 部门：{user.dept_name}")
        if user.email:
            print(f"   - 邮箱：{user.email}")
        print()
    print("=" * 60)


def display_groups(groups: List[GroupInfoItem]) -> None:
    """
    打印群组列表到 stdout。

    Args:
        groups: GroupInfoItem 对象列表
    """
    print(f"\n[成功] 找到 {len(groups)} 个匹配的群组\n")
    print("群组列表（请选择序号）：")
    print("=" * 60)
    for idx, group in enumerate(groups, 1):
        print(f"{idx}. 群名称：{group.group_name}")
        print(f"   - 群URI：{group.group_uri}")
        if group.admin_id:
            print(f"   - 管理员工号：{group.admin_id}")
        print()
    print("=" * 60)


def handle_search_users(current_emp_no: str, token: str, user_name: str) -> Dict[str, Any]:
    """
    搜索用户（-q --userName）。

    Args:
        current_emp_no: 当前用户工号
        token:          认证令牌
        user_name:      搜索用户名关键字

    Returns:
        包含搜索结果的字典
    """
    _app_logger.debug("搜索用户: keyword=%s", user_name)
    print(f'\n[步骤1] 正在搜索用户 "{user_name}"...')
    search_result = search_users(current_emp_no, token, user_name)
    if search_result is None or search_result.status != "success":
        error_msg = "搜索用户失败"
        if search_result and search_result.message:
            error_msg = search_result.message
        _app_logger.error("搜索用户失败 user_name=%s: %s", user_name, error_msg)
        print(f"[失败] {error_msg}")
        return {"success": False, "message": error_msg}
    if len(search_result.users) == 0:
        _app_logger.info("搜索用户无结果: user_name=%s", user_name)
        print(f'[提示] 未找到用户 "{user_name}"')
        return {"success": False, "message": f"未找到用户: {user_name}"}
    if len(search_result.users) == 1:
        u = search_result.users[0]
        _app_logger.info(
            "搜索用户命中 1 个: user_name=%s, emp_no=%s, employeeType=%d",
            user_name,
            u.emp_no,
            getattr(u, "employee_type", 0),
        )
        return {
            "success": True,
            "message": "找到 1 个匹配用户",
            "user": {
                "name": u.name,
                "nameEn": u.name_en,
                "empNo": u.emp_no,
                "employeeType": getattr(u, "employee_type", 0),
                "deptName": u.dept_name,
                "email": u.email,
            },
        }
    _app_logger.info("搜索用户命中多个: user_name=%s, count=%d", user_name, len(search_result.users))
    display_users(search_result.users)
    candidates = [
        {
            "name": u.name,
            "nameEn": u.name_en,
            "empNo": u.emp_no,
            "employeeType": getattr(u, "employee_type", 0),
            "deptName": u.dept_name,
            "email": u.email,
        }
        for u in search_result.users
    ]
    return {
        "success": False,
        "action": "select_user",
        "message": f"找到 {len(search_result.users)} 个名为 '{user_name}' 的用户，请选择具体用户",
        "candidates": candidates,
    }


def handle_search_groups(group_name: str) -> Dict[str, Any]:
    """
    搜索群组（-q --groupName）。

    Args:
        group_name: 搜索群组名关键字

    Returns:
        包含搜索结果的字典
    """
    _app_logger.debug("搜索群组: group_name=%s", group_name)
    print(f'\n[步骤1] 正在搜索群组 "{group_name}"...')
    groups, err = search_group(group_name)
    if err:
        _app_logger.error("搜索群组失败 group_name=%s: %s", group_name, err)
        print(f"[失败] 搜索群组失败: {err}")
        return {"success": False, "message": f"搜索群组失败: {err}"}
    if len(groups) == 0:
        _app_logger.info("搜索群组无结果: group_name=%s", group_name)
        print(f'[提示] 未找到群组 "{group_name}"')
        return {"success": False, "message": f"未找到群组: {group_name}"}
    if len(groups) == 1:
        g = groups[0]
        _app_logger.info("搜索群组命中 1 个: group_name=%s, group_uri=%s", g.group_name, g.group_uri)
        return {
            "success": True,
            "message": "找到 1 个匹配群组",
            "group": {
                "groupName": g.group_name,
                "groupUri": g.group_uri,
                "adminID": g.admin_id,
            },
        }
    _app_logger.info("搜索群组命中多个: group_name=%s, count=%d", group_name, len(groups))
    display_groups(groups)
    candidates = [
        {"groupName": g.group_name, "groupUri": g.group_uri, "adminID": g.admin_id}
        for g in groups
    ]
    return {
        "success": False,
        "action": "select_group",
        "message": f"找到 {len(groups)} 个名为 '{group_name}' 的群组，请选择具体群组",
        "candidates": candidates,
    }


def main() -> None:
    args = sys.argv[1:]
    
    if not args:
        show_usage()
        sys.exit(1)

    parsed = parse_args(args)
    send_mode = "send_mode" in parsed
    search_mode = "search_mode" in parsed

    if send_mode == search_mode:
        if send_mode:            
            sys.stderr.write("[错误] 不能同时使用 -s 和 -q\n")
        else:
            sys.stderr.write("[错误] 必须指定 -s（发送消息）或 -q（搜索）\n")
        show_usage()
        sys.exit(1)

    if send_mode:
        is_single = bool(parsed.get("emp_no"))
        is_group = bool(parsed.get("group_id"))
        if not is_single and not is_group:
            sys.stderr.write("[错误] 发送消息必须指定 --userId/--userNo/--empNo（单聊）或 --groupId（群聊）或同时指定\n")
            show_usage()
            sys.exit(1)
        msg = parsed.get("msg", "")
        img = parsed.get("img", "")
        file = parsed.get("file", "")
        # 添加校验，如果是单聊，工号必须为纯数字
        if is_single:
            emp_no = parsed.get("emp_no", "")
            if not emp_no.isdigit():
                sys.stderr.write("[错误] 单聊工号必须为纯数字\n")
                show_usage()
                sys.exit(1)
        # 检查消息类型参数：三者只能出现一个
        msg_count = sum([bool(msg), bool(img), bool(file)])
        if msg_count == 0:
            sys.stderr.write("[错误] 发送必须提供 --msg、--img 或 --file 参数之一\n")
            show_usage()
            sys.exit(1)
        if msg_count > 1:
            sys.stderr.write("[错误] --msg、--img、--file 三者只能出现一个\n")
            show_usage()
            sys.exit(1)
        # 确定消息类型
        if msg:
            msg_type = "text"
        elif img:
            msg_type = "image"
            # 验证图片格式（只支持 bmp、jpg、png）
            img_lower = img.lower()
            if not (img_lower.endswith(".bmp") or img_lower.endswith(".jpg") or img_lower.endswith(".jpeg") or img_lower.endswith(".png")):
                sys.stderr.write("[错误] 图片格式不支持，只支持 bmp、jpg、png\n")
                show_usage()
                sys.exit(1)
        else:  # file
            msg_type = "file"

    if search_mode:
        search_user = bool(parsed.get("user_name"))
        search_group_name = bool(parsed.get("group_name"))
        if not search_user and not search_group_name:            
            sys.stderr.write("[错误] 搜索必须指定 --userName（搜索用户）或 --groupName（搜索群组）\n")
            show_usage()
            sys.exit(1)
        if search_user and search_group_name:            
            sys.stderr.write("[错误] 不能同时指定 --userName 和 --groupName\n")
            show_usage()
            sys.exit(1)

    # 检查是否有 MASS_COCLAW_OWNER 环境变量或 iCenter 不可用
    has_owner_env = bool(os.environ.get("MASS_COCLAW_OWNER"))
    icenter_ok, icenter_err_msg = check_icenter_available()
    
    if not icenter_ok and sys.platform == "win32":
        err_msg = icenter_err_msg or "请先启动/登录 iCenter 客户端"
        _app_logger.error("iCenter 不可用: %s", icenter_err_msg or "unknown")
        print(f"[失败] {err_msg}")
        output_json({"success": False, "message": err_msg})
        sys.exit(1)

    if has_owner_env or not icenter_ok:
        # 若是容器版环境、或者是iCenter没有启动，尝试走icenter-msg二进制
        # 检查是否带有 --img 或 --file 参数
        has_img = bool(parsed.get("img", ""))
        has_file_param = bool(parsed.get("file", ""))
        if has_img or has_file_param:
            err_msg = "不支持发送图片和文件"
            _app_logger.warning("当前环境不支持发送图片/文件（MASS_COCLAW_OWNER=%s, iCenter可用=%s）",
                                has_owner_env, icenter_ok)
            print(f"[失败] {err_msg}")
            output_json({"success": False, "message": err_msg})
            sys.exit(1)
        else:
            # 调用可执行文件 icenter-msg，参数与 icenter-msg.py 相同
            exe_dir = os.path.dirname(os.path.abspath(__file__))
            exe_path = os.path.join(exe_dir, "icenter-msg")
            if os.path.isfile(exe_path) and sys.platform != "win32":
                try:
                    mode = os.stat(exe_path).st_mode
                    os.chmod(
                        exe_path,
                        mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH,
                    )
                except OSError as e:
                    _app_logger.warning("无法为 %s 添加执行权限: %s", exe_path, e)
            _app_logger.info("重定向到可执行文件: %s，参数: %s", exe_path, args)
            try:
                proc = subprocess.run([exe_path] + args)
                sys.exit(proc.returncode)
            except FileNotFoundError:
                err_msg = f"找不到可执行文件: {exe_path}"
                _app_logger.error(err_msg)
                print(f"[失败] {err_msg}")
                output_json({"success": False, "message": err_msg})
                sys.exit(1)
    
    #以下走本地环境的iCenter客户端发送...
    try:
        current_emp_no, token = get_user_auth_info()        
    except ValueError as e:
        _app_logger.error("获取用户认证失败: %s", e, exc_info=True)
        output_json({"success": False, "message": str(e)})
        sys.exit(1)

    if send_mode:
        emp_no = parsed.get("emp_no", "")
        user_name = parsed.get("user_name", "")
        msg = parsed.get("msg", "")
        img = parsed.get("img", "")
        file = parsed.get("file", "")
        group_id = parsed.get("group_id", "")
        group_name = parsed.get("group_name", "")
        at_list = parsed.get("at_list", "")
        # 确定消息类型
        if msg:
            msg_type = "text"
        elif img:
            msg_type = "image"
        else:  # file
            msg_type = "file"
        _app_logger.info(
            "开始发送: emp_no=%s user_name=%s group_id=%s group_name=%s msg_type=%s",
            emp_no, user_name, group_id, group_name, msg_type,
        )
        result = handle_send_message(
            current_emp_no, token, emp_no, user_name, group_id, group_name, msg, img, file, at_list, msg_type
        )
        if not result.get("success"):
            err_msg = result.get("message", "")
            _app_logger.error("发送消息失败: %s", err_msg)
            print(f"[失败] {err_msg}")
            output_json(result)
            sys.exit(1)
        _app_logger.info("发送消息成功: %s", result)
        print("[成功] 消息已发送")
        output_json(result)
        return

    user_name = parsed.get("user_name", "")
    group_name = parsed.get("group_name", "")
    if user_name:
        _app_logger.info("开始搜索用户: user_name=%s", user_name)
        result = handle_search_users(current_emp_no, token, user_name)
    else:
        _app_logger.info("开始搜索群组: group_name=%s", group_name)
        result = handle_search_groups(group_name)

    if result.get("action") in ("select_user", "select_group"):
        _app_logger.info("搜索到多个结果需选择: action=%s", result.get("action"))
        output_json(result)
        sys.exit(1)
    if not result.get("success"):
        err_msg = result.get("message", "")
        _app_logger.error("搜索失败: %s", err_msg)
        print(f"[失败] {err_msg}")
        output_json(result)
        sys.exit(1)
    _app_logger.info("搜索成功: %s", result.get("message", ""))
    output_json(result)


if __name__ == "__main__":
    main()
