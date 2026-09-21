#!/usr/bin/env python
"""
规则智能诊断 - AI + Python分工架构

核心设计：
1. 大模型语义解析：理解自然语言，提取维度（triggers, states, actions, fields, conditions）
2. Python规则匹配：接收解析好的JSON参数，进行多维度匹配和约束检查

架构原则：让AI做AI的事，让Python做Python的事

知识结构（基于 rules-checkstyle）：
- 触发器（17 种）：WHEN_CREATED, WHEN_STATE_IN, WHEN_TRANSITION...
- 条件（14 种）：NULL, WHEN_VALUE, WHEN_EMPTY, WHEN_RELATED_*...
- 动作（25 种）：REQUIRED, SET_STATE, EMAIL, CALL_API...
- 约束规则：trigger × condition → support/unSupport actions
"""

import sys
import json
import argparse
import re
import os
from pathlib import Path
from datetime import datetime
from typing import List, Dict, Any, Optional
from enum import Enum

SCRIPT_DIR = Path(__file__).resolve().parent
SCRIPTS_ROOT = SCRIPT_DIR.parent
SKILL_DIR = SCRIPTS_ROOT.parent
CONFIG_FILE = SKILL_DIR / "assets" / "config.json"
META_FILE = SKILL_DIR / "references" / "rules-checkstyle" / "rule_diagnose_meta.json"

if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

from common.log_helper import get_logger, safe_print, apply_log_config, setup_logging, log_raw_argv, log_complete_params
from common.config_helper import load_base_config
from common.auth_helper import get_auth_headers
from common.rdcloud_api import fetch_rules, fetch_fields, query_work_item_details, fetch_transitions, fetch_states
from common.field_resolver import resolve_field_key

# 容器内 skill 为只读挂载（/mnt/skills/<id>），日志改写到可写工作区，并按 skill 分目录
LOG_DIR = (Path("/workspace/.skill-logs") / SKILL_DIR.name) if Path("/workspace").is_dir() else (SKILL_DIR / "logs")
LOG_FILE = LOG_DIR / f"rule_diagnose_{datetime.now().strftime('%Y%m%d')}.log"
logger = get_logger(__name__)


# ============================================================================
# 第一层：问题分类器
# ============================================================================

class ProblemCategory(Enum):
    """问题类型枚举"""
    TRIGGER = "trigger"           # 触发器问题：什么时候执行
    STATE = "state"               # 状态问题：在什么状态下
    CONDITION = "condition"       # 条件问题：满足什么条件
    ACTION = "action"             # 动作问题：执行什么操作
    FIELD = "field"               # 字段问题：涉及哪些字段
    WORKFLOW = "workflow"         # 工作流问题：状态流转
    RELATED = "related"           # 关联问题：关联工作项
    NOTIFICATION = "notification" # 通知问题：邮件消息
    PERMISSION = "permission"     # 权限问题：用户组/角色
    FUNCTION = "function"         # 功能问题：功能限制
    BASELINE = "baseline"         # 基线问题：基线操作
    PAGE = "page"                 # 页面问题：页面配置
    API = "api"                   # 接口问题：调用外部接口
    UNKNOWN = "unknown"           # 未知类型


class ProblemClassifier:
    """问题分类器 - 识别用户问题属于哪个维度"""

    def __init__(self, meta: Dict):
        self.meta = meta
        self.category_keywords = {
            ProblemCategory.TRIGGER: {
                "time": ["时", "后", "时", "的时候", "的时候", "创建", "更新", "修改", "保存"],
                "event": ["事件", "触发", "自动"]
            },
            ProblemCategory.STATE: {
                "state_word": ["态", "状态", "状态下", "态时"],
                "relation": ["在", "处于", "包含"]
            },
            ProblemCategory.WORKFLOW: {
                "transition": ["流转", "流转", "提交", "审批", "通过", "拒绝", "关闭", "完成"],
                "direction": ["到", "变成", "变为", "转为", "流转到"]
            },
            ProblemCategory.ACTION: {
                "validation": ["必填", "只读", "隐藏", "冻结", "校验"],
                "assignment": ["赋值", "自动填", "自动写", "复制", "清空", "指派"],
                "state_change": ["设置状态", "改变状态"],
                "notification": ["通知", "邮件", "消息", "提醒"],
                "function": ["功能", "限制", "不可用", "禁用"],
                "page": ["页面", "页签", "顺序", "落地页", "红点"],
                "baseline": ["基线", "被基线"],
                "api": ["接口", "API", "调用", "推送"]
            },
            ProblemCategory.FIELD: {
                "field_word": ["字段", "标题", "描述", "处理人", "指派人", "日期"],
                "operation": ["填", "写", "改", "编辑", "查看", "显示"]
            },
            ProblemCategory.CONDITION: {
                "value": ["值", "等于", "为"],
                "empty": ["空", "没有", "未"],
                "changed": ["变更", "变化", "修改", "更新"],
                "related": ["关联", "相关", "子级", "父级"]
            },
            ProblemCategory.RELATED: {
                "related_word": ["关联", "相关", "子任务", "父任务", "下级", "上级"],
                "operation": ["创建关联", "取消关联", "删除关联"]
            },
            ProblemCategory.NOTIFICATION: {
                "notify_word": ["通知", "邮件", "消息", "提醒", "提示"],
                "channel": ["邮件", "iCenter", "消息"]
            },
            ProblemCategory.PERMISSION: {
                "user_group": ["组", "角色", "权限", "当前用户"],
                "user": ["用户", "我", "自己"]
            },
            ProblemCategory.FUNCTION: {
                "function_word": ["功能", "按钮", "操作", "可用", "不可用", "限制", "禁用"]
            },
            ProblemCategory.BASELINE: {
                "baseline_word": ["基线", "被基线", "基线内容"]
            },
            ProblemCategory.PAGE: {
                "page_word": ["页面", "页签", "顺序", "落地页", "红点", "标记"]
            },
            ProblemCategory.API: {
                "api_word": ["接口", "API", "调用", "推送", "DAAS"]
            }
        }

    def classify(self, problem_text: str) -> List[ProblemCategory]:
        """
        分类用户问题
        返回：问题类型列表（一个问题可能属于多个类型）
        """
        categories = []

        for category, keyword_dict in self.category_keywords.items():
            score = 0
            matched_keywords = []

            for sub_type, keywords in keyword_dict.items():
                for kw in keywords:
                    if kw in problem_text:
                        score += 1
                        matched_keywords.append(kw)

            # 阈值判断
            if score >= 1:
                categories.append(category)
                logger.debug(f"分类 {category.value}: 匹配关键词 {matched_keywords}")

        # 如果没有匹配到任何类型，返回 UNKNOWN
        if not categories:
            categories.append(ProblemCategory.UNKNOWN)

        return categories


# ============================================================================
# 第二层：维度解析器
# ============================================================================

class DimensionParser:
    """维度解析器 - 根据问题类型提取关键信息"""

    def __init__(self, meta: Dict):
        self.meta = meta

    def parse_trigger(self, problem_text: str) -> List[str]:
        """解析触发器"""
        triggers = []
        for trigger_key, trigger_info in self.meta.get("triggers", {}).items():
            for kw in trigger_info.get("keywords", []):
                if kw in problem_text:
                    triggers.append(trigger_key)
                    logger.info(f"触发器匹配：'{kw}' → {trigger_key}")
                    break
        return triggers

    def parse_state(self, problem_text: str, known_states: List[str] = None) -> List[str]:
        """
        从用户问题中提取状态名 - 简单策略

        核心逻辑：
        1. 优先精确匹配已知状态（来自规则配置的实际状态值）
        2. 识别"XX 态"模式（如"新建态"→"新建"）

        为什么简单：
        - 用户表达方式多样，正则无法覆盖所有情况
        - 已知状态匹配最准确
        - 当状态不存在时，会在结果解释阶段给出友好提示
        """
        states = []

        # 1. 优先精确匹配已知状态（来自规则配置）
        if known_states:
            # 按长度降序排列，长状态优先匹配（避免"新建"匹配到"新建态"）
            for s in sorted(known_states, key=len, reverse=True):
                if s in problem_text and s not in states:
                    states.append(s)
                    logger.info(f"状态匹配（已知）：'{s}'")

        # 2. 识别"XX 态"模式（如"新建态"→"新建"）
        # 使用与原版 rule_diagnose.py 相同的正则
        state_pattern = re.findall(r'[\u4e00-\u9fff]{2,4}态', problem_text)
        for s in state_pattern:
            base = s[:-1]  # 去掉末尾"态"
            if base not in states and len(base) >= 2:
                states.append(base)
                logger.info(f"状态匹配（态模式）：'{base}'")

        return states

    def parse_action(self, problem_text: str) -> List[str]:
        """解析动作"""
        actions = []

        # 1. 直接匹配动作名称
        for action_key, action_info in self.meta.get("actions", {}).items():
            if action_info.get("name", "") in problem_text:
                actions.append(action_key)

        # 2. 问题关键词推断（完整映射）
        for prob_type, keywords in self.meta.get("problem_keywords", {}).items():
            for kw in keywords:
                if kw in problem_text:
                    # 根据问题类型推断动作
                    mapping = {
                        "必填": ["REQUIRED"],
                        "只读": ["READ_ONLY"],
                        "隐藏": ["HIDDEN"],
                        "冻结": ["FREEZE"],
                        "赋值": ["COPY", "AUTO_APPEND"],
                        "必填校验失败": ["REQUIRED"],
                        "状态流转": ["SET_STATE"],
                        "通知": ["EMAIL", "POP_UP_NOTIFICATION"],
                        "保存": [],  # 保存本身不是动作
                        "指派": ["ASSIGN_TO"],
                        "复制": ["COPY"],
                        "清空": ["EMPTY"],
                        "关联工作项": ["CREATE_RELATED_WORK_ITEM", "CANCEL_RELATED_WORK_ITEM"],
                        "调用接口": ["CALL_API"],
                        "功能限制": ["FUNCTION"],
                        "手动编辑": ["MANUAL_EDIT"],
                        "页面配置": ["ADJUST_PAGE_ORDER", "LAND_PAGE", "PAGE_RED_DOT_MARK"],
                        "基线操作": ["BASELINE_CONTENT_SET", "BASELINE_CONTENT_TO_UPDATE"],
                        "推送 DAAS": ["PUSH_DAAS"],
                        "权限用户组": ["FUNCTION"],
                        "关联状态": ["FUNCTION", "SET_STATE"],
                        "状态不是": ["FUNCTION"],
                        "删除后": ["EMAIL", "POP_UP_NOTIFICATION"],
                        "条件组合": []
                    }

                    if prob_type in mapping:
                        for action in mapping[prob_type]:
                            if action not in actions:
                                actions.append(action)
                    break

        return actions

    def parse_field(self, auth_headers: dict, workspace: str, problem_text: str) -> List[str]:
        """
        从问题描述中提取字段候选词，解析为系统 Key

        Args:
            auth_headers: 认证头
            workspace: 工作区标识
            problem_text: 用户问题描述

        Returns:
            字段 key 列表
        """
        field_items = fetch_fields(workspace, auth_headers)
        if not field_items:
            return []

        # 提取候选词
        candidates = set()
        for w in re.findall(r'[a-zA-Z_][a-zA-Z0-9_]*', problem_text):
            if len(w) > 1:
                candidates.add(w)
        for w in re.findall(r'[一-鿿]{2,}', problem_text):
            candidates.add(w)

        # 解析为系统 Key
        resolved = []
        for word in candidates:
            key = resolve_field_key(word, field_items, auth_headers,
                                    raise_on_not_found=False,
                                    scene="规则诊断字段映射",
                                    workspace=workspace)
            if key and key not in resolved:
                resolved.append(key)

        if resolved:
            logger.info("字段解析结果：%s", resolved)
        return resolved

    def parse_condition(self, problem_text: str) -> Dict[str, Any]:
        """解析条件"""
        conditions = {
            "type": None,
            "field": None,
            "value": None
        }

        # 识别条件类型
        if any(kw in problem_text for kw in ["空", "没有", "未"]):
            conditions["type"] = "WHEN_EMPTY"
        elif any(kw in problem_text for kw in ["不为空", "有值"]):
            conditions["type"] = "WHEN_NOT_EMPTY"
        elif any(kw in problem_text for kw in ["变更", "变化", "修改"]):
            conditions["type"] = "WHEN_CHANGED"
        elif any(kw in problem_text for kw in ["未变更", "未修改", "不变"]):
            conditions["type"] = "WHEN_NOT_CHANGED"
        elif any(kw in problem_text for kw in ["关联", "相关"]):
            conditions["type"] = "WHEN_RELATED"

        # TODO: 提取字段和值

        return conditions


# ============================================================================
# 第三层：规则匹配引擎
# ============================================================================

class RuleMatcher:
    """规则匹配引擎 - 基于完整知识结构进行匹配"""

    def __init__(self, meta: Dict):
        self.meta = meta
        self.checkstyle_rules = self._load_checkstyle_rules()
        # 从 meta 配置构建 actionType 和 trigger 的自动映射
        # API 返回 key 格式（readonly/manualEdit），Meta 配置是 name 格式（READ_ONLY/MANUAL_EDIT）
        # 统一归一化（去除非字母数字+小写）后做双向映射
        self._action_norm_map = {}  # "readonly" → "READ_ONLY"
        for mk in meta.get("actions", {}):
            api_field_name = meta["actions"][mk].get("api_field_name", "")
            norm = re.sub(r'[^a-z0-9]', '', api_field_name.lower()) if api_field_name else ""
            if norm:
                self._action_norm_map[norm] = mk
            # 也支持直接传 name 格式
            norm_name = re.sub(r'[^a-z0-9]', '', mk.lower())
            self._action_norm_map[norm_name] = mk

        self._trigger_norm_map = {}  # "whenstatein" → "WHEN_STATE_IN"
        for mk in meta.get("triggers", {}):
            api_field_name = meta["triggers"][mk].get("api_field_name", "")
            norm = re.sub(r'[^a-z0-9]', '', api_field_name.lower()) if api_field_name else ""
            if norm:
                self._trigger_norm_map[norm] = mk
            norm_name = re.sub(r'[^a-z0-9]', '', mk.lower())
            self._trigger_norm_map[norm_name] = mk

    def _load_checkstyle_rules(self) -> Dict[str, Dict]:
        """
        加载 rules-checkstyle 中的完整规则约束知识

        文件结构：references/rules-checkstyle/{trigger}_checkStyle.json
        每个文件包含该 trigger 下所有 condition 的 support/unSupport 规则

        返回结构：
        {
            "WHEN_STATE_IN": {
                "NULL": {
                    "support": [...],
                    "unSupport": [...]
                },
                "WHEN_VALUE": {...}
            }
        }
        """
        checkstyle_dir = SCRIPTS_ROOT.parent / "references" / "rules-checkstyle"
        rules = {}

        if not checkstyle_dir.exists():
            logger.warning(f"rules-checkstyle 目录不存在：{checkstyle_dir}")
            return rules

        # 遍历所有 checkstyle JSON 文件
        for json_file in checkstyle_dir.glob("*_checkStyle.json"):
            try:
                with open(json_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)

                # 解析文件结构
                for item in data:
                    trigger = item.get("trigger", "")
                    if not trigger:
                        continue

                    rules[trigger] = {}

                    # 解析该 trigger 下的所有 condition
                    for rule_item in item.get("rules", []):
                        condition = rule_item.get("condition", "")
                        if not condition:
                            continue

                        rules[trigger][condition] = {}

                        # 解析 actionRules
                        for action_rule in rule_item.get("actionRules", []):
                            action_dict = action_rule.get("action", {})
                            pre_condition = action_rule.get("preCondition", "true")

                            # 提取 support 和 unSupport
                            if "support" in action_dict:
                                support_list = action_dict["support"].split(",")
                                rules[trigger][condition]["support"] = support_list
                                rules[trigger][condition]["preCondition"] = pre_condition

                            if "unSupport" in action_dict:
                                unSupport_list = action_dict["unSupport"].split(",")
                                rules[trigger][condition]["unSupport"] = unSupport_list

                        logger.debug(f"加载触发器 {trigger} 的条件 {condition}: "
                                   f"{len(rules[trigger][condition].get('support', []))}个支持，"
                                   f"{len(rules[trigger][condition].get('unSupport', []))}个不支持")

            except Exception as e:
                logger.warning(f"加载 checkstyle 文件失败 {json_file}: {e}")

        logger.info(f"总共加载 {len(rules)} 个触发器的约束规则")
        return rules

    def _normalize_action_type(self, action_type: str) -> str:
        """
        归一化 actionType：从 API key 格式或 name 格式 → meta 标准 key。

        Java 枚举：name()=READ_ONLY, key=readonly
        Meta 配置已有 api_field_name 字段，自动构建映射。
        """
        if not action_type:
            return ""
        norm = re.sub(r'[^a-z0-9]', '', action_type.lower())
        return self._action_norm_map.get(norm, norm)

    def match(self, intent: Dict, rules: List[Dict],
              workspace: str = "", work_item_type: str = "",
              trans_name_map: Dict = None, trans_state_map: Dict = None) -> List[Dict]:
        """
        匹配规则

        intent: {
            "category": ProblemCategory,
            "triggers": [...],
            "states": [...],
            "actions": [...],
            "fields": [...],
            "conditions": {...}
        }
        :param workspace: 工作区key（兜底，规则上workspaceKey为空时使用）
        :param work_item_type: 工作项类型key（兜底，规则上workItemTypeKey为空时使用）
        :param trans_name_map: {transition_key: nameZh} 预加载映射（避免重复查API）
        :param trans_state_map: {transition_key: toStateNameZh} 预加载映射
        """
        matched = []
        self._match_workspace = workspace
        self._match_work_item_type = work_item_type
        self._trans_name_map = trans_name_map or {}
        self._trans_state_map = trans_state_map or {}

        # 预先归一化 intent 中的动作类型
        intent_actions_normalized = set(
            self._normalize_action_type(a) for a in intent.get("actions", [])
        )
        intent_triggers = intent.get("triggers", [])
        intent_states = intent.get("states", [])
        intent_fields = intent.get("fields", [])
        intent_transition_names = intent.get("transition_names", [])

        for rule in rules:
            score = 0.0
            reasons = []

            # 1. 触发器匹配（统一大小写：API 可能返回小写）
            rule_trigger = (rule.get("trigger") or "").upper()
            if not rule_trigger:
                inferred = self._extract_trigger_from_condition(rule.get("condition", {}))
                rule_trigger = inferred.upper() if inferred else ""

            trigger_ok = False
            if rule_trigger and rule_trigger in intent_triggers:
                trigger_ok = True
                score += 0.25
                reasons.append(f"触发器匹配：{rule_trigger}")

            # 1.5. 执行步骤名称匹配（针对WHEN_TRANSITION）
            transition_name_ok = False
            if intent_transition_names and rule_trigger == "WHEN_TRANSITION":
                transition_key = self._extract_transition_key_from_condition(rule.get("condition", {}))
                if transition_key:
                    # 从预加载的映射表中直接查nameZh，无需重复调API
                    to_state_name = self._trans_name_map.get(transition_key, "")
                    if to_state_name and to_state_name in intent_transition_names:
                        transition_name_ok = True
                        score += 0.25
                        reasons.append(f"执行步骤匹配：{to_state_name}")

            # 2. 状态匹配（增强：从TRANSITION中提取目标状态）
            rule_states = self._extract_states_from_condition(rule.get("condition", {}))

            # 如果规则条件是TRANSITION且没有明确的状态条件，则从TRANSITION中提取目标状态
            if not rule_states and rule_trigger == "WHEN_TRANSITION":
                transition_key = self._extract_transition_key_from_condition(rule.get("condition", {}))
                if transition_key:
                    to_state_name = self._trans_state_map.get(transition_key, "")
                    if to_state_name:
                        rule_states.append(to_state_name)
                        # logger.debug(f"从TRANSITION {transition_key} 中提取目标状态: {to_state_name}")

            state_overlap = set(rule_states) & set(intent_states)
            state_ok = bool(state_overlap)
            if state_ok:
                score += 0.25
                reasons.append(f"状态匹配：{list(state_overlap)}")

            # 3. 动作匹配（API key 格式 vs Meta name 格式：统一归一化比较）
            rule_actions_raw = [a.get("actionType", "") for a in rule.get("actions", [])]
            rule_actions = [self._normalize_action_type(at) for at in rule_actions_raw]
            action_overlap = set(rule_actions) & intent_actions_normalized
            action_ok = bool(action_overlap)
            if action_ok:
                score += 0.25
                reasons.append(f"动作匹配：{list(action_overlap)}")

            # 4. 字段匹配
            rule_fields = self._extract_fields_from_actions(rule.get("actions", []))
            field_overlap = set(rule_fields) & set(intent_fields)
            field_ok = bool(field_overlap)
            if field_ok:
                score += 0.25
                reasons.append(f"字段匹配：{list(field_overlap)}")

            # 5. 检查规则约束（support/unSupport）
            constraint_check = self._check_constraint(rule_trigger, rule.get("condition", {}), rule_actions)
            logger.debug(f"约束检查：trigger={rule_trigger}, condition={rule.get('condition', {}).get('type', '')}, actions={rule_actions}, valid={constraint_check['valid']}, reason={constraint_check['reason']}")
            if not constraint_check["valid"]:
                reasons.append(f"⚠️ 规则约束：{constraint_check['reason']}")

            # 验证：大模型传了的维度必须全部命中，没传的不要求
            all_ok = True
            if intent_triggers and not trigger_ok:
                all_ok = False
            if intent_transition_names and not transition_name_ok:
                # transition_names可能从from_states/to_states（"从A到B"模式）解析而来，
                # 如果transition没匹配上，尝试用states（解析出的A/B状态）兜底
                if not state_ok:
                    all_ok = False
            if intent_states and not state_ok:
                all_ok = False
            if intent_actions_normalized and not action_ok:
                all_ok = False
            if intent_fields and not field_ok:
                all_ok = False

            if all_ok and score > 0:
                matched.append({
                    "rule": rule,
                    "score": score,
                    "reasons": reasons,
                    "rule_trigger": rule_trigger or "",
                    "rule_states": rule_states,
                    "rule_fields": rule_fields,
                    "rule_action_types": rule_actions,
                    "constraint_valid": constraint_check["valid"]
                })

        # 按得分排序
        matched.sort(key=lambda x: (-x["score"], x["rule"].get("key", "")))
        return matched

    def _extract_trigger_from_condition(self, condition: Dict) -> Optional[str]:
        """
        从条件中提取触发器类型。

        API 返回的规则可能没有顶层 trigger 字段，需要从 condition.type / typeLabel 推断。
        使用 meta 配置的 api_field_name 做规范化匹配，不硬编码。
        """
        def walk(c: Dict) -> Optional[str]:
            if not isinstance(c, dict):
                return None

            # 同时检查 type 和 typeLabel，归一化后查映射表
            for src in [c.get("type", ""), c.get("typeLabel", "")]:
                norm = re.sub(r'[^a-z0-9]', '', (src or "").lower())
                if norm in self._trigger_norm_map:
                    return self._trigger_norm_map[norm]

            # 递归检查 subConditions
            for sub in c.get("subConditions", []):
                if isinstance(sub, dict):
                    result = walk(sub)
                    if result:
                        return result

            return None

        return walk(condition)

    def _extract_states_from_condition(self, condition: Dict) -> List[str]:
        """从条件中提取状态"""
        states = []

        def walk(c: Dict):
            if not isinstance(c, dict):
                return

            ref = c.get("referenceName") or c.get("key", "")
            val = c.get("value", "")

            is_state_field = "state" in (ref or "").lower() or ref == "System_State"

            if is_state_field:
                # val 可能是 dict（whenState）、list（whenStateIn）或 string
                if isinstance(val, dict):
                    nz = (val.get("nameZh") or val.get("name") or "").strip()
                    if nz:
                        states.append(nz)
                elif isinstance(val, list):
                    # whenStateIn: 多个状态
                    for v in val:
                        if isinstance(v, dict):
                            nz = (v.get("nameZh") or v.get("name") or "").strip()
                            if nz:
                                states.append(nz)
                elif isinstance(val, str) and val.strip():
                    # 纯字符串状态值（如 "待审核"、"draft"）
                    v = val.strip()
                    if not v.startswith("State_") and not v.startswith("Transition_"):
                        states.append(v)

            for sub in (c.get("subConditions") or []):
                if isinstance(sub, dict):
                    walk(sub)

        walk(condition)
        return states

    def _extract_transition_key_from_condition(self, condition: Dict) -> Optional[str]:
        """从条件中提取TRANSITION的key"""
        transition_key = None

        def walk(c: Dict):
            nonlocal transition_key
            if not isinstance(c, dict):
                return

            t = c.get("type", "")
            if t.upper() == "TRANSITION":
                val = c.get("value", "")
                if val and isinstance(val, str) and val.startswith("Transition_"):
                    transition_key = val

            for sub in (c.get("subConditions") or []):
                if isinstance(sub, dict):
                    walk(sub)

        walk(condition)
        return transition_key

    def _get_transition_to_state_name(self, workspace: str, work_item_type_key: str, transition_key: str) -> Optional[str]:
        """
        从Transition表中查询指定Transition的目标状态名称（带缓存）

        调用API: GET /rest/workspaces/{workspace}/work_item_types/{workItemTypeKey}/transitions
        响应示例：bo.items[].{key, from, to, fromStateNameZh, toStateNameZh, nameZh, ...}
        """
        # 使用缓存避免重复查询
        cache_key = f"transition_state_{workspace}_{work_item_type_key}_{transition_key}"
        if hasattr(self, '_transition_cache'):
            if cache_key in self._transition_cache:
                return self._transition_cache[cache_key]
        else:
            self._transition_cache = {}

        try:
            from common.rdcloud_api import fetch_transitions
            headers = {
                "X-Tenant-Id": "ZTE",
                "X-Auth-Value": os.environ.get("RDCLOUD_AUTH_TOKEN", ""),
                "X-Emp-No": os.environ.get("RDCLOUD_EMP_NO", ""),
                "X-Lang-Id": "zh-CN",
                "Content-Type": "application/json",
            }

            transitions = fetch_transitions(workspace, work_item_type_key, headers)
            for t in transitions:
                if t.get("key") == transition_key:
                    to_state_name = t.get("toStateNameZh") or t.get("toStateNameEn")
                    self._transition_cache[cache_key] = to_state_name
                    return to_state_name

            self._transition_cache[cache_key] = None
            return None
        except Exception as e:
            logger.debug(f"查询Transition {transition_key} 失败: {e}")
            self._transition_cache[cache_key] = None
            return None

    def _get_transition_name_from_key(self, workspace: str, work_item_type_key: str, transition_key: str, tenant_key: str = "ZTE") -> Optional[str]:
        """
        从Transition表中查询指定Transition的中文名（nameZh）（带缓存）

        调用API: GET /rest/workspaces/{workspace}/work_item_types/{workItemTypeKey}/transitions
        响应示例：bo.items[].{key, nameZh, nameEn, from, to, fromStateNameZh, toStateNameZh, ...}
        :param tenant_key: 租户Key（默认"ZTE"）
        :param workspace: 工作区Key
        :param work_item_type_key: 工作项类型Key
        :param transition_key: Transition的key（如"Transition_125893"）
        :return: nameZh（如"指派实施"），未找到返回None
        """
        # 使用缓存避免重复查询
        cache_key = f"transition_name_{workspace}_{work_item_type_key}_{transition_key}"
        if hasattr(self, '_transition_cache'):
            if cache_key in self._transition_cache:
                return self._transition_cache[cache_key]
        else:
            self._transition_cache = {}

        try:
            from common.rdcloud_api import fetch_transitions
            headers = {
                "X-Tenant-Id": tenant_key,
                "X-Auth-Value": os.environ.get("RDCLOUD_AUTH_TOKEN", ""),
                "X-Emp-No": os.environ.get("RDCLOUD_EMP_NO", ""),
                "X-Lang-Id": "zh-CN",
                "Content-Type": "application/json",
            }

            transitions = fetch_transitions(workspace, work_item_type_key, headers)
            for t in transitions:
                if t.get("key") == transition_key:
                    name_zh = t.get("nameZh") or t.get("nameEn")
                    self._transition_cache[cache_key] = name_zh
                    return name_zh

            self._transition_cache[cache_key] = None
            return None
        except Exception as e:
            logger.debug(f"查询Transition {transition_key} 失败: {e}")
            self._transition_cache[cache_key] = None
            return None

    def _extract_fields_from_actions(self, actions: List[Dict]) -> List[str]:
        """
        从规则动作中提取所有字段 key。

        API 返回的 action 可能将字段名放在不同字段：
        - referenceName（主流格式）
        - key（单个字段，如 copy/set/empty 类 action）
        - keys（批量字段，如 required 类 action）
        - sourceReferenceName（copy 类 action 的源字段）
        """
        fields = []
        for a in actions:
            ref = a.get("referenceName", "") or a.get("sourceReferenceName", "")
            if ref:
                if ref not in fields:
                    fields.append(ref)
                continue
            fk = a.get("key", "")
            if fk:
                if fk not in fields:
                    fields.append(fk)
                continue
            for k in (a.get("keys") or []):
                if k and k not in fields:
                    fields.append(k)
        return fields

    def _check_constraint(self, trigger: str, condition: Dict, actions: List[str]) -> Dict:
        """
        检查规则动作是否满足约束（support/unSupport）

        逻辑：
        1. 从条件中提取 condition type
        2. 查询知识库中该 trigger+condition 组合的 support/unSupport 列表
        3. 检查规则动作是否在 unSupport 列表中 → 违反约束
        4. 检查规则动作是否在 support 列表中 → 符合约束

        返回：{"valid": bool, "reason": str}
        """
        if not trigger or not self.checkstyle_rules:
            return {"valid": True, "reason": "无约束知识"}

        # 从条件中提取 condition type
        condition_type = condition.get("type", "").upper()
        if not condition_type:
            condition_type = "NULL"  # 默认

        # 映射 condition type 到知识库中的 key
        condition_map = {
            "AND": "NULL",  # AND 通常表示无条件
            "OR": "NULL",
            "WHEN_VALUE": "WHEN_VALUE",
            "WHEN_EMPTY": "WHEN_EMPTY",
            "WHEN_NOT_EMPTY": "WHEN_NOT_EMPTY",
            "CHANGED": "WHEN_CHANGED",
            "NOT_CHANGED": "WHEN_NOT_CHANGED",
            "EQ": "WHEN_VALUE",
            "NE": "WHEN_VALUE",
        }

        cond_key = condition_map.get(condition_type, condition_type)

        # 查询知识库
        if trigger not in self.checkstyle_rules:
            return {"valid": True, "reason": f"触发器 {trigger} 无约束知识"}

        if cond_key not in self.checkstyle_rules[trigger]:
            return {"valid": True, "reason": f"条件 {cond_key} 无约束知识"}

        constraint = self.checkstyle_rules[trigger][cond_key]
        unSupport_list = constraint.get("unSupport", [])
        support_list = constraint.get("support", [])

        # 检查动作是否违反约束
        violations = []
        for action in actions:
            if action in unSupport_list:
                violations.append(f"{action} 不支持在 {trigger} + {cond_key} 时使用")

        if violations:
            return {
                "valid": False,
                "reason": "约束违反：" + "; ".join(violations)
            }

        # 如果有 support 列表，检查动作是否在支持范围内
        if support_list:
            for action in actions:
                if action not in support_list:
                    # 动作不在支持列表中，但不一定违反约束
                    # 这里只是警告，不是错误
                    logger.debug(f"动作 {action} 不在支持列表中（{trigger} + {cond_key}）")

        return {"valid": True, "reason": ""}


# ============================================================================
# 第四层：结果解释器
# ============================================================================

class ResultInterpreter:
    """结果解释器 - 给出诊断结论"""

    def __init__(self, meta: Dict):
        self.meta = meta
        # 构建 actionType 映射（与 RuleMatcher 保持一致的规范化逻辑）
        self._action_norm_map = {}
        for mk in meta.get("actions", {}):
            api_field_name = meta["actions"][mk].get("api_field_name", "")
            norm = re.sub(r'[^a-z0-9]', '', api_field_name.lower()) if api_field_name else ""
            if norm:
                self._action_norm_map[norm] = mk
            norm_name = re.sub(r'[^a-z0-9]', '', mk.lower())
            self._action_norm_map[norm_name] = mk

    def interpret(self, matched_rules: List[Dict], intent: Dict, known_states: List[str] = None) -> str:
        """解释匹配结果，生成格式化的诊断输出"""
        lines = []

        # 匹配统计
        lines.append(f"- **相关规则**: {len(matched_rules)} 条")

        if intent.get("triggers"):
            trigger_names = [self.meta["triggers"].get(t, {}).get("name", t) for t in intent["triggers"]]
            lines.append(f"- **识别触发器**: {', '.join(trigger_names)}")

        if intent.get("states"):
            mentioned = intent["states"]
            lines.append(f"- **识别状态**: {', '.join(mentioned)}")

            # 检查状态是否在已知状态列表中
            if known_states:
                unknown = [s for s in mentioned if s not in known_states]
                if unknown:
                    similar = []
                    for us in unknown:
                        for ks in known_states:
                            if us in ks or ks in us:
                                similar.append(ks)
                    if similar:
                        lines.append(f"- ⚠️ **警告**: 状态'{unknown}'不存在，可能是'{', '.join(similar)}'的误写")
                    else:
                        lines.append(f"- ⚠️ **警告**: 状态'{unknown}'不存在。当前工作项类型的状态包括：{', '.join(known_states[:10])}{'...' if len(known_states) > 10 else ''}")

        if intent.get("actions"):
            action_names = [self.meta["actions"].get(a, {}).get("name", a) for a in intent["actions"]]
            lines.append(f"- **识别动作**: {', '.join(action_names)}")

        lines.append("")

        if not matched_rules:
            if intent.get("states") and known_states:
                # 如果用户提到了状态但没有匹配到规则，给出友好提示
                mentioned = [s for s in intent["states"] if s in known_states]
                if not mentioned:
                    unknown = [s for s in intent["states"] if s not in known_states]
                    lines.append(f"未匹配到相关规则。请确认状态名称是否正确，当前工作项类型的状态包括：{', '.join(known_states[:10])}{'...' if len(known_states) > 10 else ''}")
                else:
                    lines.append("未匹配到相关规则，请确认问题描述是否准确。\n")
                    lines.append("💡 提示：可以尝试更具体的描述，例如提到具体的字段名或状态名。\n")
            else:
                lines.append("未匹配到相关规则，请确认问题描述是否准确。\n")
                lines.append("💡 提示：可以尝试更具体的描述，例如提到具体的字段名或状态名。\n")
            return "\n".join(lines)

        # 详细规则信息（最多显示 50 条，其余汇总说明）
        max_display = 50
        for i, mr in enumerate(matched_rules):
            if i >= max_display:
                remaining = len(matched_rules) - max_display
                lines.append(f"\n... 还有 {remaining} 条匹配规则未显示（共 {len(matched_rules)} 条）\n")
                break
            lines.append(self._format_single_rule(mr))

        return "\n".join(lines)

    def _format_single_rule(self, matched_rule: Dict) -> str:
        """格式化单条规则输出（与 v1 format_rule_output 风格一致）"""
        rule = matched_rule["rule"]
        lines = []

        # 标题
        rule_name = rule.get("nameZh") or rule.get("nameEn") or "未命名规则"
        lines.append(f"### [{rule.get('key', '')}] {rule_name}\n")

        # 触发器
        trigger_key = matched_rule.get("rule_trigger", "")
        if trigger_key:
            trigger_name = self.meta["triggers"].get(trigger_key, {}).get("name", trigger_key)
            lines.append(f"**触发器**: {trigger_name} ({trigger_key})\n")

        # 条件
        lines.append("**条件**:")
        if matched_rule.get("rule_states"):
            lines.append(f"- 状态：{', '.join(matched_rule['rule_states'])}")
        if matched_rule.get("rule_fields"):
            lines.append(f"- 字段：{', '.join(matched_rule['rule_fields'])}")
        if not matched_rule.get("rule_states") and not matched_rule.get("rule_fields"):
            lines.append("- 无条件限制")
        lines.append("")

        # 动作
        lines.append("**动作**:")
        for action in rule.get("actions", []):
            action_type_raw = action.get("actionType", "")
            action_type_norm = re.sub(r'[^a-z0-9]', '', action_type_raw.lower())
            action_type = self._action_norm_map.get(action_type_norm, action_type_raw)
            action_name = self.meta.get("actions", {}).get(action_type, {}).get("name", action_type_raw)
            # 字段名：referenceName → key → keys（三级回退）
            action_field = action.get("referenceName", "") or action.get("key", "")
            if not action_field:
                keys = action.get("keys", [])
                if keys:
                    action_field = ", ".join(keys[:3])
                    if len(keys) > 3:
                        action_field += "..."
            action_value = action.get("value", "")
            action_value_display = ""
            if isinstance(action_value, dict):
                action_value_display = action_value.get("nameZh") or action_value.get("name", "")
            elif isinstance(action_value, list):
                items = []
                for v in action_value[:3]:
                    if isinstance(v, dict):
                        items.append(v.get("nameZh") or v.get("name", ""))
                    else:
                        items.append(str(v))
                action_value_display = ", ".join(items)
                if len(action_value) > 3:
                    action_value_display += "..."
            line = f"- {action_field} → {action_name}"
            if action_value_display:
                line += f" = {action_value_display}"
            lines.append(line)
        if not rule.get("actions"):
            lines.append("- 无动作")
        lines.append("")

        # 匹配原因
        lines.append("**匹配原因**:")
        for reason in matched_rule.get("reasons", []):
            lines.append(f"- {reason}")
        lines.append("")

        # 约束检查警告
        if not matched_rule.get("constraint_valid", True):
            lines.append("⚠️ **注意**: 该规则的触发器+条件组合可能不支持当前动作\n")

        lines.append("---\n")
        return "\n".join(lines)


# ============================================================================
# 主函数
# ============================================================================

def run():
    """主运行函数"""
    parser = _build_arg_parser()
    args = parser.parse_args()

    # === 入参记录：原始用户输入 + 命令行参数 + 解析后参数 ===
    if args.user_input:
        logger.info("原始用户输入: %s", args.user_input)
    log_raw_argv(logger, sys.argv)
    log_complete_params(logger, "参数解析完成", vars(args))
    # 保存原始入参留档（输出时展示）
    raw_input_params = {
        "work_item_id": args.work_item_id,
        "user_input": args.user_input or "",
        "problem": args.problem or "",
        "triggers_raw": args.triggers,
        "states_raw": args.states,
        "actions_raw": args.actions,
        "fields_raw": args.fields,
        "conditions_raw": args.conditions,
        "from_states_raw": args.from_states,
        "to_states_raw": args.to_states,
        "transition_names_raw": args.transition_names,
    }

    # 获取认证信息
    base_config = load_base_config(CONFIG_FILE)
    auth_headers = get_auth_headers(None, base_config, logger)

    # 加载元数据
    meta = {
        "triggers": {},
        "actions": {},
        "conditions": {},
        "problem_keywords": {},
        "match_weights": {}
    }
    try:
        with open(META_FILE, 'r', encoding='utf-8') as f:
            meta = json.load(f)
        logger.info("加载元数据配置：%d 个触发器，%d 个动作",
                   len(meta.get("triggers", {})),
                   len(meta.get("actions", {})))
    except Exception as e:
        logger.warning(f"加载元数据失败：{e}")

    # 初始化各层组件
    classifier = ProblemClassifier(meta)
    parser_obj = DimensionParser(meta)
    matcher = RuleMatcher(meta)
    interpreter = ResultInterpreter(meta)

    # 校验：至少需要大模型解析参数或 --problem 描述
    # 注意：argparse 默认值为 "[]" / "{}"，必须先 parse 再判断是否为空
    raw_triggers = json.loads(args.triggers) if args.triggers and args.triggers != "[]" else []
    raw_states = json.loads(args.states) if args.states and args.states != "[]" else []
    raw_actions = json.loads(args.actions) if args.actions and args.actions != "[]" else []
    raw_fields = json.loads(args.fields) if args.fields and args.fields != "[]" else []
    raw_conditions = json.loads(args.conditions) if args.conditions and args.conditions != "{}" else {}
    raw_transition_names = json.loads(args.transition_names) if args.transition_names and args.transition_names != "[]" else []
    raw_from_states = json.loads(args.from_states) if args.from_states and args.from_states != "[]" else []
    raw_to_states = json.loads(args.to_states) if args.to_states and args.to_states != "[]" else []
    has_llm_params = bool(raw_triggers or raw_states or raw_actions or raw_fields or raw_conditions or raw_transition_names or raw_from_states or raw_to_states)
    if not has_llm_params and not args.problem:
        safe_print("错误：请提供大模型解析参数（--triggers/--states/--actions/--fields/--transition-names）或 --problem 问题描述")
        sys.exit(1)

    # 获取工作项信息
    workspace = ""
    if args.work_item_id:
        i = args.work_item_id.rfind("-")
        workspace = args.work_item_id[:i] if i > 0 else args.work_item_id
    work_item_type = ""
    work_item_data = None

    if args.work_item_id:
        # 打印完整的请求 URL 和 body（用于调试）
        logger.info("查询工作项详情：work_item_id=%s", args.work_item_id)
        logger.info("请求 body: %s", json.dumps([args.work_item_id], ensure_ascii=False))
        resp = query_work_item_details([args.work_item_id], auth_headers)
        items = resp.get("bo", {}).get("items", []) if isinstance(resp.get("bo"), dict) else []
        if items:
            work_item_type = items[0].get("workItemTypeKey", "")
            work_item_data = items[0]
            logger.info("工作项类型：%s", work_item_type)
            # 提取实际状态
            actual_state = work_item_data.get("state", {}).get("nameZh")
            if actual_state:
                logger.info("工作项实际状态：%s", actual_state)

    # 获取规则列表
    rules = fetch_rules(auth_headers, workspace, work_item_type) if work_item_type else []
    logger.info("获取到 %d 条规则", len(rules))

    # 只保留已启用的规则
    if rules:
        enabled = [r for r in rules if r.get("enable") is True]
        disabled_count = len(rules) - len(enabled)
        if disabled_count > 0:
            logger.info("过滤掉 %d 条未启用的规则", disabled_count)
        rules = enabled

    # 从规则中提取已知状态
    known_states = []
    for rule in rules:
        condition = rule.get("condition", {})
        states = matcher._extract_states_from_condition(condition)
        for s in states:
            if s not in known_states:
                known_states.append(s)

    # 从工作项实际状态补充（用户问题中的状态可能不在规则中）
    if work_item_data:
        actual_state = work_item_data.get("state", {}).get("nameZh")
        if actual_state and actual_state not in known_states:
            known_states.append(actual_state)
            logger.info("从工作项实际状态补充：%s", actual_state)

    logger.info("从规则中提取到 %d 个已知状态：%s", len(known_states), known_states)

    if has_llm_params:
            # 使用大模型解析好的参数（上面 validation 阶段已预先解析到 raw_* 变量）
            intent = {
                "triggers": raw_triggers,
                "states": raw_states,
                "actions": raw_actions,
                "fields": raw_fields,
                "conditions": raw_conditions,
                "transition_names": raw_transition_names,
                "from_states": raw_from_states,
                "to_states": raw_to_states,
            }

            # 字段解析：对 --fields 中的中文名做映射（如 "标题" → "System_Title"）
            if intent["fields"] and workspace:
                field_items = fetch_fields(workspace, auth_headers)
                if field_items:
                    resolved = []
                    for f in intent["fields"]:
                        # 已经是系统 Key 格式（含下划线）直接使用
                        if re.match(r'^[A-Z][a-zA-Z0-9_]+$', f):
                            if f not in resolved:
                                resolved.append(f)
                        else:
                            # 中文名尝试解析
                            key = resolve_field_key(f, field_items, auth_headers,
                                                    raise_on_not_found=False,
                                                    scene="规则诊断字段映射",
                                                    workspace=workspace)
                            if key and key not in resolved:
                                resolved.append(key)
                    if resolved:
                        intent["fields"] = resolved
                        logger.info("字段解析结果：%s", resolved)

            # === 统一拉取 transitions（后续 from_states 解析和 match 映射表共用） ===
            _all_transitions = []  # 完整的 transitions 列表
            trans_name_map = {}   # {transition_key: nameZh}
            trans_state_map = {}  # {transition_key: toStateNameZh}
            if workspace and work_item_type:
                try:
                    _all_transitions = fetch_transitions(workspace, work_item_type, auth_headers)
                    for t in _all_transitions:
                        k = t.get("key", "")
                        if k:
                            trans_name_map[k] = t.get("nameZh") or t.get("nameEn") or ""
                            trans_state_map[k] = t.get("toStateNameZh") or t.get("toStateNameEn") or ""
                    logger.info("预加载 %d 条流转步骤映射", len(trans_name_map))
                except Exception as e:
                    logger.warning("预加载流转步骤失败: %s", e)

            # === from_states/to_states → transition_names 解析（"从A到B"模式） ===
            if (intent.get("from_states") or intent.get("to_states")) and workspace and work_item_type:
                from_states_in = intent.get("from_states", [])
                to_states_in = intent.get("to_states", [])
                logger.info("从→到状态解析：from_states=%s, to_states=%s", from_states_in, to_states_in)

                try:
                    # 1. 查状态定义：nameZh → key 映射
                    states_def = fetch_states(workspace, work_item_type, auth_headers)
                    resolved_transition_names = []
                    resolved_from_keys = set()
                    resolved_to_keys = set()

                    if states_def:
                        # 构建 nameZh → key 映射
                        name_to_key = {}
                        for sd in states_def:
                            name = sd.get("nameZh") or sd.get("nameEn") or ""
                            key = sd.get("key") or ""
                            if name and key:
                                name_to_key[name] = key
                                name_stripped = name.replace(" ", "")
                                if name_stripped != name:
                                    name_to_key[name_stripped] = key

                        # 2. 解析 from_states → state keys
                        from_keys = [name_to_key.get(s) for s in from_states_in
                                    if name_to_key.get(s)]
                        to_keys = [name_to_key.get(s) for s in to_states_in
                                  if name_to_key.get(s)]

                        logger.info("状态Key解析：from=%s → keys=%s, to=%s → keys=%s",
                                    from_states_in, from_keys, to_states_in, to_keys)

                        # 3. 用 state keys 匹配 transition 的 from/to（用已预加载的 _all_transitions）
                        #    支持三种场景：
                        #    - from+to都有 → 精确匹配 from→to
                        #    - 只有to → 匹配所有到达to的transition（如"状态翻转到XX"）
                        #    - 只有from → 匹配所有从from出发的transition
                        for t in _all_transitions:
                            t_from = t.get("from", "")
                            t_to = t.get("to", "")
                            tn = t.get("nameZh") or t.get("nameEn") or ""
                            matched = False
                            if from_keys and to_keys:
                                if t_from in from_keys and t_to in to_keys:
                                    matched = True
                            elif to_keys and not from_keys:
                                if t_to in to_keys:
                                    matched = True
                            elif from_keys and not to_keys:
                                if t_from in from_keys:
                                    matched = True
                            if matched and tn and tn not in resolved_transition_names:
                                resolved_transition_names.append(tn)
                                logger.info("从→到匹配：'%s'→'%s' → transition='%s'(key=%s)",
                                            from_states_in, to_states_in, tn, t.get("key", ""))
                            if t_from in from_keys:
                                resolved_from_keys.add(t_from)
                            if t_to in to_keys:
                                resolved_to_keys.add(t_to)

                    # 4. 合并结果到 intent
                    if resolved_transition_names:
                        for n in resolved_transition_names:
                            if n not in intent["transition_names"]:
                                intent["transition_names"].append(n)
                        if "WHEN_TRANSITION" not in intent["triggers"]:
                            intent["triggers"].append("WHEN_TRANSITION")
                        for t in _all_transitions:
                            tk = t.get("key", "")
                            tn = t.get("nameZh") or t.get("nameEn") or ""
                            if tn in resolved_transition_names:
                                fs = t.get("fromStateNameZh") or t.get("fromStateNameEn") or ""
                                ts = t.get("toStateNameZh") or t.get("toStateNameEn") or ""
                                if fs and fs not in intent["states"]:
                                    intent["states"].append(fs)
                                if ts and ts not in intent["states"]:
                                    intent["states"].append(ts)
                        logger.info("从→到状态解析结果：transition_names=%s, states补充=%s",
                                    resolved_transition_names, intent["states"])
                    else:
                        logger.warning("未找到从 %s 到 %s 的流转步骤", from_states_in, to_states_in)
                except Exception as e:
                    logger.warning("从→到状态解析异常：%s", e)

    else:
        # 回退到自主解析（向后兼容）
        categories = classifier.classify(args.problem)
        logger.info("问题分类：%s", [c.value for c in categories])

        intent = {
            "category": categories,
            "triggers": parser_obj.parse_trigger(args.problem),
            "states": parser_obj.parse_state(args.problem, known_states),
            "actions": parser_obj.parse_action(args.problem),
            "fields": parser_obj.parse_field(auth_headers, workspace, args.problem),
            "conditions": parser_obj.parse_condition(args.problem),
            "transition_names": [],
            "from_states": [],
            "to_states": [],
        }

    # === 意图识别结果日志（始终记录，便于定位问题） ===
    intent_log = {k: v for k, v in intent.items() if k != "category"}
    logger.info("意图识别结果：%s", json.dumps(intent_log, ensure_ascii=False))
    # 记录到入参 dict，在输出中展示
    raw_input_params["resolved_intent"] = json.dumps(intent_log, ensure_ascii=False)
    raw_input_params["source"] = "大模型参数" if has_llm_params else "关键词回退"

    # 第三层：规则匹配（trans_name_map/trans_state_map 已在上面统一预加载）
    matched_rules = matcher.match(intent, rules, workspace=workspace, work_item_type=work_item_type,
                                  trans_name_map=trans_name_map, trans_state_map=trans_state_map)
    logger.info("匹配到 %d 条规则", len(matched_rules))

    # 第四层：结果解释
    result = interpreter.interpret(matched_rules, intent, known_states)

    # === 输出入参追踪信息（耦合在诊断结果前） ===
    safe_print("## 规则诊断结果\n")
    safe_print("### 入参追踪\n")
    safe_print(f"- **数据来源**: {raw_input_params['source']}")
    if raw_input_params["user_input"]:
        safe_print(f"- **原始用户输入**: {raw_input_params['user_input']}")
    if raw_input_params["work_item_id"]:
        safe_print(f"- **单据**: {raw_input_params['work_item_id']}")
    if raw_input_params["problem"]:
        safe_print(f"- **原始问题**: {raw_input_params['problem']}")
    if has_llm_params:
        safe_print(f"- **原始触发器参数**: `{raw_input_params['triggers_raw']}`")
        safe_print(f"- **原始状态参数**: `{raw_input_params['states_raw']}`")
        safe_print(f"- **原始动作参数**: `{raw_input_params['actions_raw']}`")
        safe_print(f"- **原始字段参数**: `{raw_input_params['fields_raw']}`")
        if raw_input_params.get("from_states_raw") and raw_input_params["from_states_raw"] != "[]":
            safe_print(f"- **起始状态(from_states)**: `{raw_input_params['from_states_raw']}`")
        if raw_input_params.get("to_states_raw") and raw_input_params["to_states_raw"] != "[]":
            safe_print(f"- **目标状态(to_states)**: `{raw_input_params['to_states_raw']}`")
    safe_print(f"- **解析后意图**: `{raw_input_params['resolved_intent']}`")
    safe_print("")

    # 输出诊断结果
    safe_print(result)


def _build_arg_parser():
    """构建命令行参数解析器"""
    p = argparse.ArgumentParser(
        description="规则智能诊断：接收大模型解析好的参数进行规则匹配",
        formatter_class=argparse.RawTextHelpFormatter,
    )
    p.add_argument("--problem", "-p", metavar="TEXT", required=False, default="",
                   help="【可选】用户原始问题描述（如果提供了以下解析好的参数，则不需要）")
    p.add_argument("--work-item-id", "-wi", required=True, metavar="ID",
                   help="单据 ID")
    p.add_argument("--triggers", metavar="JSON", default="[]",
                   help="解析好的触发器列表，JSON数组，如 [\"WHEN_STATE_IN\"]")
    p.add_argument("--states", metavar="JSON", default="[]",
                   help="解析好的状态列表，JSON数组，如 [\"测试中\"]")
    p.add_argument("--actions", metavar="JSON", default="[]",
                   help="解析好的动作列表，JSON数组，如 [\"REQUIRED\"]")
    p.add_argument("--fields", metavar="JSON", default="[]",
                   help="字段中文名列表，脚本自动解析为系统Key，如 [\"需求描述\"]")
    p.add_argument("--conditions", metavar="JSON", default="{}",
                   help="解析好的条件对象，JSON对象，如 {\"type\": \"WHEN_VALUE\", \"field\": \"priority\", \"value\": \"P0\"}")
    p.add_argument("--transition-names", "--transitionNames", dest="transition_names", metavar="JSON", default="[]",
                   help="解析好的执行步骤名称列表（中文名），JSON数组，如 [\"指派实施\"]")
    p.add_argument("--from-states", "--fromStates", dest="from_states", metavar="JSON", default="[]",
                   help="【从A到B模式】起始状态列表（'从新建到处理中'中的'新建'），JSON数组，如 [\"新建\"]")
    p.add_argument("--to-states", "--toStates", dest="to_states", metavar="JSON", default="[]",
                   help="【从A到B模式】目标状态列表（'从新建到处理中'中的'处理中'），JSON数组，如 [\"处理中\"]")
    p.add_argument("--user-input", "--userInput", dest="user_input", default="",
                   help="用户的原始输入，用于日志记录")
    p.add_argument("--verbose", "-v", action="store_true",
                   help="显示详细信息")
    return p


if __name__ == "__main__":
    apply_log_config(CONFIG_FILE)
    setup_logging(LOG_FILE, add_stderr=True)
    run()
