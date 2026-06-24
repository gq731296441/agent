#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
icenter-group 公共工具函数
"""

import sys
sys.stdout.reconfigure(encoding='utf-8')
import os
import base64
import urllib3
import requests
from typing import Dict, Any, Tuple

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# ── 环境配置 ─────────────────────────────────────────────────────────────────
COCLAW_ENV = os.environ.get('coclaw_env', '').lower()
IS_TEST_ENV = COCLAW_ENV == 'test'

_PROD_MSG_URL = "https://icentermsg.dt.zte.com.cn"
_TEST_MSG_URL = "https://icentermsg.test.zte.com.cn"

_PROD_API_URL = "https://icenterapi.zte.com.cn"
_TEST_API_URL = "https://icenter-systest.test.zte.com.cn"

def get_msg_base_url() -> str:
    """
    获取消息接口的 base URL。

    Returns:
        测试环境返回 icentermsg.test.zte.com.cn，生产环境返回 icentermsg.dt.zte.com.cn
    """
    return _TEST_MSG_URL if IS_TEST_ENV else _PROD_MSG_URL

def get_api_base_url() -> str:
    """
    获取用户 API 的 base URL。

    Returns:
        测试环境返回 icenter-systest.test.zte.com.cn，生产环境返回 icenterapi.zte.com.cn
    """
    return _TEST_API_URL if IS_TEST_ENV else _PROD_API_URL

# ── 常量 ──────────────────────────────────────────────────────────────────────
BASE_URL = get_msg_base_url()
TIMEOUT = 15


# ── 数据结构 ──────────────────────────────────────────────────────────────────

class GrpBulletin:
    """群公告信息"""
    def __init__(self, editor="", editorName="", editTime="", fileurl="",
                 summary="", type=0, content=""):
        self.editor = editor              # 群公告编辑者账号（SIP URI），为空表示暂无公告
        self.editorName = editorName      # 群公告编辑者姓名（base64编码，解码格式: zh=中文名|en=英文名）
        self.editTime = editTime          # 群公告最后编辑时间，为空表示暂无公告
        self.fileurl = fileurl            # 群公告附件文件URL（base64编码），为空表示无附件
        self.summary = summary            # 群公告摘要（base64编码），为空表示无摘要
        self.type = type                  # 群公告内容类型：0-无公告，1-文本类型
        self.content = content            # 群公告正文内容


class GroupInfo:
    """群组详细信息"""
    def __init__(self, sysCode=0, guri="", group_name="", default_group_name="",
                 admin_userid="", admin_unionid="", max_member_count="",
                 member_invite_enable="", groupConfCallID="",
                 ForbidenDisplayDeleteInfo="", topMsgStamp="", grpbulletin=None,
                 forbid_qrcode_join="", fullFlag=1, pluginBar=0,
                 servicePluginList="", enable_trans_notify="", has_mem_trans=""):
        self.sysCode = sysCode                          # 系统编码，标识群组所属的业务系统
        self.guri = guri                                # 群组唯一标识URI，格式为 sip:<groupid>@gm.zte.com.cn
        self.group_name = group_name                    # 群组名称（用户自定义）
        self.default_group_name = default_group_name    # 默认群组名称（最多4个群成员姓名拼接）
        self.admin_userid = admin_userid                # 群主工号，从 owner_uri（sip:<userid>@zte.com.cn）解析而来
        self.admin_unionid = admin_unionid              # 群主的统一用户ID（UID）
        self.max_member_count = max_member_count        # 本群最大成员数上限
        self.member_invite_enable = member_invite_enable  # 是否允许普通成员邀请他人入群（"true"/"false"）
        self.groupConfCallID = groupConfCallID          # 当前群组进行中的会议CallID，为空表示无进行中的会议
        self.ForbidenDisplayDeleteInfo = ForbidenDisplayDeleteInfo  # 被踢成员是否展示"被删除"提示（"0"-展示，"1"-不展示）
        self.topMsgStamp = topMsgStamp                  # 消息置顶标识（时间戳字符串）
        self.grpbulletin = grpbulletin if grpbulletin is not None else GrpBulletin()  # 群公告信息
        self.forbid_qrcode_join = forbid_qrcode_join    # 是否禁止扫码入群（"true"/"false"）
        self.fullFlag = fullFlag                        # 响应数据是否全量（1-全量，0-增量）
        self.pluginBar = pluginBar                      # 是否展示插件栏（0-不展示，1-展示）
        self.servicePluginList = servicePluginList      # 群组绑定的插件ID列表（逗号分隔）
        self.enable_trans_notify = enable_trans_notify  # 是否启用群转让通知（"true"/"false"）
        self.has_mem_trans = has_mem_trans              # 群组是否存在成员转让记录（"true"/"false"）


class GroupMember:
    """群成员信息"""
    def __init__(self, userid="", status="", type="", join_time="",
                 name_zh="", name_en="", memberType=0, outType=""):
        self.userid = userid          # 成员工号，从 member_uri（sip:<userid>@zte.com.cn）解析而来
        self.status = status          # 成员状态："1"-正常在群，"4"-已离开群组
        self.type = type              # 成员角色类型："admin"-管理员，"member"-普通成员，"robot"-机器人
        self.join_time = join_time    # 成员加入群组的时间（ISO 8601格式）
        self.name_zh = name_zh        # 成员中文姓名，从 memberName（base64）解析而来
        self.name_en = name_en        # 成员英文全名，从 memberName（base64）解析而来
        self.memberType = memberType  # 成员类型：0-自然人，1-业务号/机器人，2-数字员工
        self.outType = outType        # 成员外部标识类型："0"-股份公司，"1"-子公司，"2"-供应商等


class GroupDetail:
    """群详情完整结构"""
    def __init__(self, groupinfo=None, memberlist=None, robotMembers=None):
        self.groupinfo = groupinfo if groupinfo is not None else GroupInfo()  # 群组基本信息
        self.memberlist = memberlist if memberlist is not None else []        # 普通成员列表（不含机器人）
        self.robotMembers = robotMembers if robotMembers is not None else []  # 机器人成员列表


# ── 工具函数 ──────────────────────────────────────────────────────────────────

def _parse_grpbulletin(data: Dict[str, Any]) -> GrpBulletin:
    """
    将字典解析为 GrpBulletin 对象。

    Args:
        data: 群公告信息字典

    Returns:
        GrpBulletin 对象
    """
    return GrpBulletin(
        editor=data.get("editor", ""),
        editorName=data.get("editorName", ""),
        editTime=data.get("editTime", ""),
        fileurl=data.get("fileurl", ""),
        summary=data.get("summary", ""),
        type=data.get("type", 0),
        content=data.get("content", ""),
    )


def _parse_groupinfo(data: Dict[str, Any]) -> GroupInfo:
    """
    将字典解析为 GroupInfo 对象。

    Args:
        data: 群信息字典

    Returns:
        GroupInfo 对象
    """
    bulletin_data = data.get("grpbulletin") or {}
    return GroupInfo(
        sysCode=data.get("sysCode", 0),
        guri=data.get("guri", ""),
        group_name=data.get("group_name", ""),
        default_group_name=data.get("default_group_name", ""),
        admin_userid=_extract_userid(data.get("owner_uri", "")),
        admin_unionid=data.get("owner_uri_uID", ""),
        max_member_count=data.get("max_member_count", ""),
        member_invite_enable=data.get("member_invite_enable", ""),
        groupConfCallID=data.get("groupConfCallID", ""),
        ForbidenDisplayDeleteInfo=data.get("ForbidenDisplayDeleteInfo", ""),
        topMsgStamp=data.get("topMsgStamp", ""),
        grpbulletin=_parse_grpbulletin(bulletin_data),
        forbid_qrcode_join=data.get("forbid_qrcode_join", ""),
        fullFlag=data.get("fullFlag", 1),
        pluginBar=data.get("pluginBar", 0),
        servicePluginList=data.get("servicePluginList", ""),
        enable_trans_notify=data.get("enable_trans_notify", ""),
        has_mem_trans=data.get("has_mem_trans", ""),
    )


def _decode_member_name(member_name_b64: str) -> Tuple[str, str]:
    """
    解析 memberName（base64编码）。

    原始格式（解码后）：zh=中文名|en=英文全名=简拼
    示例：zh=林明东|en=Lin Mingdong=LMD

    Args:
        member_name_b64: base64 编码的成员名称字符串

    Returns:
        (中文名, 英文全名)，解析失败时返回 ("", "")
    """
    if not member_name_b64:
        return "", ""
    try:
        decoded = base64.b64decode(member_name_b64).decode("utf-8")
        name_zh, name_en = "", ""
        for part in decoded.split("|"):
            if part.startswith("zh="):
                name_zh = part[3:]
            elif part.startswith("en="):
                # en 部分格式：英文全名=简拼，取第一个 "=" 之前的内容
                name_en = part[3:].split("=")[0]
        return name_zh, name_en
    except Exception:
        return "", ""


def _extract_userid(sip_uri: str) -> str:
    """
    从 SIP URI 中提取工号。

    Args:
        sip_uri: SIP URI，格式为 sip:<userid>@zte.com.cn

    Returns:
        工号字符串；无法提取时返回原始输入
    """
    if sip_uri.startswith("sip:") and "@" in sip_uri:
        return sip_uri[4:sip_uri.index("@")]
    return sip_uri


def _parse_member(data: Dict[str, Any]) -> GroupMember:
    """
    将字典解析为 GroupMember 对象。

    Args:
        data: 成员信息字典

    Returns:
        GroupMember 对象
    """
    name_zh, name_en = _decode_member_name(data.get("memberName", ""))
    return GroupMember(
        userid=_extract_userid(data.get("member_uri", "")),
        status=data.get("status", ""),
        type=data.get("type", ""),
        join_time=data.get("join_time", ""),
        name_zh=name_zh,
        name_en=name_en,
        memberType=data.get("memberType", 0),
        outType=data.get("outType", ""),
    )


def getGroupInfo(groupuri: str, userid: str, token: str) -> GroupDetail:
    """
    获取群详情

    调用 iCenter 接口查询指定群组的详细信息，包含群基本信息、成员列表和机器人成员列表。

    Args:
        groupuri: 群的URI，格式为 sip:<groupid>@gm.zte.com.cn
        userid:   调用者工号
        token:    调用者的认证令牌

    Returns:
        GroupDetail 对象，包含 groupinfo、memberlist、robotMembers 三个字段

    Raises:
        requests.exceptions.HTTPError: HTTP 请求失败（4xx/5xx）
        requests.exceptions.RequestException: 网络请求异常
        ValueError: 接口返回业务错误（code != 200）
    """
    url = f"{BASE_URL}/zte-icenter-msg-bff/services/extGroup/getGroupInfo"
    params = {"guri": groupuri}

    headers = {
        "Content-Type": "application/json",
        "X-Emp-No": userid,
        "X-Auth-Value": token,
        "Terminal-Type": "Web@Desktop_999_ID1004",
        "X-Origin-ServiceName": "zte-icenter-msg-coclaw",
        "authKey": "msgSkill",
    }

    response = requests.get(
        url,
        headers=headers,
        params=params,
        timeout=TIMEOUT,
        proxies={"http": None, "https": None},
        verify=False,
    )
    response.raise_for_status()
    resp = response.json()

    # 检查业务状态码
    code_info = resp.get("code", {})
    if str(code_info.get("code")) != "200":
        raise ValueError(
            f"获取群详情失败：code={code_info.get('code')}, msg={code_info.get('msg', '未知错误')}"
        )

    bo = resp.get("bo", {})

    # 解析群基本信息
    groupinfo = _parse_groupinfo(bo.get("groupinfo") or {})

    # 解析普通成员列表
    memberlist = [_parse_member(m) for m in (bo.get("memberlist") or [])]

    # 解析机器人成员列表
    robotMembers = [_parse_member(m) for m in (bo.get("robotMembers") or [])]

    return GroupDetail(
        groupinfo=groupinfo,
        memberlist=memberlist,
        robotMembers=robotMembers,
    )

