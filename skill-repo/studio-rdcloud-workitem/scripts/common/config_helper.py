#!/usr/bin/env python
"""
配置统一加载：从 assets/config.json 读取 api、format、create、log 等，返回统一 base_config。
各脚本通过 load_base_config(config_path) 获取配置，不再各自实现 load_base_config。
"""

import json
from pathlib import Path
from typing import Any, Dict, Optional


def load_base_config(config_path: Optional[Path] = None) -> Dict[str, Any]:
    """
    加载基础配置（仅非敏感信息）。敏感信息（token、工号）不落盘，由认证入口从入参/环境变量获取。
    :param config_path: config.json 路径，如 SKILL_DIR / "assets" / "config.json"
    :return: base_config 含 workspace, tenant_id, format_as_markdown, smart_complete, byPassRules 等
    """
    base_config: Dict[str, Any] = {
        "workspace": None,
        "tenant_id": "ZTE",
        "byPassRules": 0,
        "format_as_markdown": False,
        "filter_virtual_root_for_related": True,
        "omit_large_children_for_display": False,
        "children_display_limit": 0,
        # 直接关联查询（oneHop）专用 children 省略阈值；None 表示回退 children_display_limit
        "one_hop_children_display_limit": None,
        "smart_complete": True,
        "fallback_bypass_on_error": True,
        # 查询结果为 0 时，是否仍允许发送邮件/iCenter（默认否）
        "send_notify_when_query_empty": False,
    }
    if not config_path or not Path(config_path).exists():
        return base_config
    try:
        with open(config_path, "r", encoding="utf-8") as f:
            config = json.load(f)
        api_config = config.get("api", {})
        if "workspace" in api_config:
            base_config["workspace"] = api_config["workspace"]
        base_config["tenant_id"] = api_config.get("tenant_id", base_config["tenant_id"])
        base_config["byPassRules"] = api_config.get("byPassRules", base_config["byPassRules"])
        format_config = config.get("format", {})
        base_config["format_as_markdown"] = format_config.get("as_markdown", base_config["format_as_markdown"])
        base_config["filter_virtual_root_for_related"] = bool(
            format_config.get(
                "filter_virtual_root_for_related",
                base_config["filter_virtual_root_for_related"],
            )
        )
        base_config["omit_large_children_for_display"] = bool(
            format_config.get(
                "omit_large_children_for_display",
                base_config["omit_large_children_for_display"],
            )
        )
        try:
            base_config["children_display_limit"] = max(
                0,
                int(format_config.get("children_display_limit", base_config["children_display_limit"])),
            )
        except (TypeError, ValueError):
            base_config["children_display_limit"] = base_config["children_display_limit"]
        if "one_hop_children_display_limit" in format_config:
            try:
                base_config["one_hop_children_display_limit"] = max(
                    0,
                    int(format_config.get("one_hop_children_display_limit")),
                )
            except (TypeError, ValueError):
                base_config["one_hop_children_display_limit"] = None
        query_config = config.get("query", {})
        if "send_notify_when_empty_result" in query_config:
            _qv = query_config["send_notify_when_empty_result"]
            if isinstance(_qv, bool):
                base_config["send_notify_when_query_empty"] = _qv
            elif isinstance(_qv, str):
                base_config["send_notify_when_query_empty"] = _qv.strip().lower() in (
                    "1",
                    "true",
                    "yes",
                    "on",
                )
            else:
                base_config["send_notify_when_query_empty"] = bool(_qv)

        create_config = config.get("create", {})
        if "smart_complete" in create_config:
            base_config["smart_complete"] = bool(create_config["smart_complete"])
        elif "smart_complete" in config:
            base_config["smart_complete"] = bool(config["smart_complete"])
        base_config["fallback_bypass_on_error"] = create_config.get("fallback_bypass_on_error", True)
        # 市场需求 MR：为 true 时 mr_common 不在提交前强制写入 PLM_Question07=false
        if "mr_plm_question07_no_force_false" in create_config:
            _v = create_config["mr_plm_question07_no_force_false"]
            if isinstance(_v, bool):
                base_config["mr_plm_question07_no_force_false"] = _v
            elif isinstance(_v, str):
                base_config["mr_plm_question07_no_force_false"] = _v.strip().lower() in (
                    "1",
                    "true",
                    "yes",
                    "on",
                )
            else:
                base_config["mr_plm_question07_no_force_false"] = bool(_v)
    except Exception:
        pass
    return base_config
