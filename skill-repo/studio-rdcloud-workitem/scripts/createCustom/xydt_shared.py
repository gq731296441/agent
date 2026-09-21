# -*- coding: utf-8 -*-
"""
xydt 组织下 createCustom 模块的共用方法。
"""

import json
from pathlib import Path
from typing import List, Optional

from common.log_helper import truncate_for_log, get_logger

from atomic.create_work_item import (
    _is_field_value_empty,
    _normalize_smart_candidate_value,
)

logger = get_logger(__name__)

_CREATECUSTOM_DIR = Path(__file__).resolve().parent
_CONFIG_FILE = _CREATECUSTOM_DIR / "smart_fill_supplement_config.json"

_config_cache: Optional[dict] = None


def _load_smart_fill_supplement_config() -> dict:
    """从 createCustom/smart_fill_supplement_config.json 加载配置。"""
    global _config_cache
    if _config_cache is not None:
        return _config_cache
    if not _CONFIG_FILE.exists():
        _config_cache = {"default_keys": [], "by_workspace_type": {}}
        return _config_cache
    try:
        with open(_CONFIG_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        _config_cache = {
            "default_keys": data.get("default_keys") or [],
            "by_workspace_type": data.get("by_workspace_type") or {},
        }
        return _config_cache
    except Exception as e:
        logger.warning("加载 smart_fill_supplement_config.json 失败: %s", e)
        _config_cache = {"default_keys": [], "by_workspace_type": {}}
        return _config_cache


def _get_supplement_keys(workspace: str, work_item_type_key: str) -> List[str]:
    """按工作区、工作项模型 key 获取需补充的字段 key 列表。"""
    cfg = _load_smart_fill_supplement_config()
    ws = (workspace or "").strip()
    wt = (work_item_type_key or "").strip()
    by_ws = cfg.get("by_workspace_type") or {}
    if ws and wt:
        ws_cfg = by_ws.get(ws)
        if isinstance(ws_cfg, dict):
            keys = ws_cfg.get(wt)
            if isinstance(keys, list):
                return [str(k).strip() for k in keys if (k or "").strip()]
            keys = ws_cfg.get("default") or ws_cfg.get("*")
            if isinstance(keys, list):
                return [str(k).strip() for k in keys if (k or "").strip()]
    return [str(k).strip() for k in (cfg.get("default_keys") or []) if (k or "").strip()]


def apply_smart_fill_supplement_fields(
    fields: List[dict],
    smart_filled_keys: List[str],
    smart_complete_candidates: dict,
    workspace: str,
    work_item_type_key: str,
) -> None:
    """
    若 smart_filled_keys 不为空，则按配置（工作区+工作项模型）从 smart_complete_candidates 取指定字段 key 的值并放入 fields。
    配置见 createCustom/smart_fill_supplement_config.json，字段使用 key（如 Team、System_IterationPath、System_AreaPath）。
    供 xydt_bug、xydt_common 等共用。
    """
    if not smart_filled_keys or not smart_complete_candidates:
        return
    supplement_keys = _get_supplement_keys(workspace, work_item_type_key)
    if not supplement_keys:
        return
    key_to_field = {(f.get("key") or "").strip(): f for f in fields if isinstance(f, dict) and (f.get("key") or "").strip()}
    added = []
    for key in supplement_keys:
        if not key:
            continue
        f = key_to_field.get(key)
        if f is not None and not _is_field_value_empty(f.get("value")):
            continue
        cand = smart_complete_candidates.get(key)
        value_to_fill = _normalize_smart_candidate_value(cand)
        if _is_field_value_empty(value_to_fill):
            continue
        if f is None:
            f = {"key": key, "value": value_to_fill}
            fields.append(f)
            key_to_field[key] = f
        else:
            f["value"] = value_to_fill
        added.append((key, truncate_for_log(str(value_to_fill), 60)))
    if added:
        logger.info("智能填充补充: 以下 %d 个字段从 smart_complete_candidates 取值: %s", len(added), added)
