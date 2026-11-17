#!/usr/bin/env python3
"""
规则引擎
协调和管理各种验证规则
"""

import logging
from typing import Dict, Any, List, Optional, Callable, Tuple
from .field_validator import FieldValidator, ValidationError

logger = logging.getLogger(__name__)

class Rule:
    """规则基类"""

    def __init__(self, name: str, description: str = ""):
        self.name = name
        self.description = description

    def evaluate(self, row_data: Dict[str, Any], row_index: int, context: Dict = None) -> Dict[str, Any]:
        """
        评估规则

        Args:
            row_data: 行数据
            row_index: 行索引
            context: 上下文信息

        Returns:
            规则评估结果字典
        """
        raise NotImplementedError

class RuleEngine:
    """规则引擎"""

    def __init__(self, product_model: str = None, work_group_1: str = None, work_group_2: str = None, work_group_3: str = None):
        """
        初始化规则引擎

        Args:
            product_model: 用户输入的产品规格型号，用于跨产品检查
            work_group_1: 工作组类1（TEAM1编号）
            work_group_2: 工作组类2（TEAM2编号）
            work_group_3: 工作组类3（TEAM3编号）
        """
        self.rules: List[Rule] = []
        self.context: Dict = {
            'product_model': product_model,
            'work_group_1': work_group_1,
            'work_group_2': work_group_2,
            'work_group_3': work_group_3
        }
        self.field_validator = FieldValidator()
        self._level_stack = []  # 层次栈，用于检查层次关系
        self._register_default_rules()

    def register_rule(self, rule: Rule):
        """注册规则"""
        self.rules.append(rule)

    def _register_default_rules(self):
        """注册默认规则"""
        # 必填字段规则
        self.register_rule(RequiredFieldsRule())
        # 外部第三方组件部件编号要求规则
        self.register_rule(ExternalComponentRequirementRule())
        # 第一行层次规则
        self.register_rule(FirstRowLevelRule())
        # 部件类别枚举规则
        self.register_rule(ComponentCategoryRule())
        # 层次关系检查
        self.register_rule(LevelGapRule())
        # 系统部件下层硬件检查
        self.register_rule(SystemHardwareCheckRule())
        # 外部组件详细规则
        self.register_rule(ExternalComponentDetailedRule())
        # 维护下层检查
        self.register_rule(SubordinateMaintenanceRule())
        # 常规组件详细规则
        self.register_rule(NormalComponentDetailedRule())
        # 部件状态检查已整合到NormalComponentDetailedRule中，移除单独检查规则
        # 跨产品一致性检查
        self.register_rule(ProductConsistencyRule())
        # 名称首位符号检查规则
        self.register_rule(NameFirstCharRule())

    def execute_rules(self, row_data: Dict[str, Any], row_index: int) -> List[Dict[str, Any]]:
        """
        执行所有规则

        Args:
            row_data: 行数据
            row_index: 行索引

        Returns:
            规则执行结果列表
        """
        results = []

        # 维护层次栈（用于层次关系检查）
        level = row_data.get('层次')
        if self._is_valid_level(level):
            # 弹出栈中所有大于等于当前层次的层级（处理同层或降级）
            while self._level_stack and self._level_stack[-1] >= level:
                self._level_stack.pop()
            # 记录前序层次（用于差值检查）
            prev_level = self._level_stack[-1] if self._level_stack else None
            self.context['prev_level'] = prev_level
            self._level_stack.append(level)
        else:
            self.context['prev_level'] = None

        # 注入当前行数据和行号到上下文
        self.context['current_row'] = row_data
        self.context['row_index'] = row_index

        for rule in self.rules:
            try:
                result = rule.evaluate(row_data, row_index, self.context)
                results.append(result)

                # 记录日志
                if not result['passed']:
                    logger.warning(f"规则 '{rule.name}' 未通过: {result['message']}")
                else:
                    logger.debug(f"规则 '{rule.name}' 通过")

            except Exception as e:
                logger.error(f"执行规则 '{rule.name}' 时发生异常: {str(e)}")
                results.append({
                    'rule': rule.name,
                    'passed': False,
                    'message': str(e),
                    'error': True
                })

        # 清理当前行的上下文
        self.context.pop('current_row', None)
        self.context.pop('row_index', None)

        return results

    def validate_row(self, row_data: Dict[str, Any], row_index: int) -> Tuple[bool, List[Dict]]:
        """
        验证一行数据

        Args:
            row_data: 行数据
            row_index: 行索引

        Returns:
            (是否通过所有规则, 失败详情列表（含suggested_value）)
        """
        results = self.execute_rules(row_data, row_index)

        failed = [r for r in results if not r['passed'] and not r.get('warning', False)]
        passed = len(results) - len(failed)

        return passed > 0 and len(failed) == 0, failed

    def validate_consistency(self, processor) -> Tuple[bool, List[Dict]]:
        """
        执行跨行一致性检查（需要Excel Processor）

        Args:
            processor: ExcelProcessor实例

        Returns:
            (是否通过, 问题列表)
        """
        try:
            checker = create_consistency_checker(processor, self.context.get('product_model'))
            return checker.check_all()
        except Exception as e:
            logger.error(f"执行一致性检查时发生异常: {str(e)}")
            return False, [{
                'type': 'consistency_check_error',
                'severity': 'error',
                'row': 0,
                'message': f'一致性检查失败: {str(e)}'
            }]

    @staticmethod
    def _is_valid_level(value: Any) -> bool:
        """判断是否为有效层次值"""
        if value is None:
            return False
        try:
            # 处理数值或字符串
            if isinstance(value, (int, float)):
                return True
            if isinstance(value, str):
                return value.strip().isdigit()
            return False
        except:
            return False

# 规则实现
class RequiredFieldsRule(Rule):
    """必填字段规则"""

    def __init__(self):
        super().__init__("required_fields", "检查必填字段")

    def evaluate(self, row_data: Dict[str, Any], row_index: int, context: Dict = None) -> Dict[str, Any]:
        required = ['层次', '部件类别']
        missing = [field for field in required if FieldValidator._is_empty(row_data.get(field))]

        if missing:
            # 生成符合用户期望的错误消息格式
            error_messages = []
            for field in missing:
                if field == '层次':
                    error_messages.append(f'"{field}"应该为必填字段')
                else:
                    error_messages.append(f'{field}应该为必填字段')
            return {
                'rule': self.name,
                'passed': False,
                'message': '; '.join(error_messages),
                'suggested_value': '请填写必填字段',
                'field': ', '.join(missing),
                'current_value': '（空）',
                'missing_fields': missing,
                'row': row_index + 2  # row_index从0开始，+2后对应Excel实际行号（表头在第1行）
            }

        return {
            'rule': self.name,
            'passed': True,
            'message': '所有必填字段已填写'
        }

class FirstRowLevelRule(Rule):
    """第一行层次规则"""

    def __init__(self):
        super().__init__("first_row_level", "第一行层次必须为0")

    def evaluate(self, row_data: Dict[str, Any], row_index: int, context: Dict = None) -> Dict[str, Any]:
        if row_index == 0:
            level = row_data.get('层次')
            if FieldValidator._is_empty(level):
                return {
                    'rule': self.name,
                    'passed': False,
                    'message': '第一行层次字段为空',
                    'suggested_value': 0,
                    'field': '层次',
                    'current_value': '（空）',
                    'row': row_index + 2
                }
            elif level != 0:
                return {
                    'rule': self.name,
                    'passed': False,
                    'message': f'第一行层次应为0，实际为{level}',
                    'suggested_value': 0,
                    'field': '层次',
                    'current_value': level,
                    'row': row_index + 2,
                    'warning': True
                }

        return {
            'rule': self.name,
            'passed': True,
            'message': '第一行层次正确'
        }

class ComponentCategoryRule(Rule):
    """部件类别枚举规则"""

    def __init__(self):
        super().__init__("component_category", "验证部件类别枚举值")
        self.valid_categories = FieldValidator.VALID_COMPONENT_CATEGORIES

    def evaluate(self, row_data: Dict[str, Any], row_index: int, context: Dict = None) -> Dict[str, Any]:
        category = row_data.get('部件类别')
        if FieldValidator._is_empty(category):
            return {
                'rule': self.name,
                'passed': True,  # 必填字段由其他规则检查
                'message': '部件类别为空，由必填字段规则处理'
            }

        if category not in self.valid_categories:
            return {
                'rule': self.name,
                'passed': False,
                'message': f'部件类别"{category}"无效',
                'suggested_value': f'有效值: {", ".join(self.valid_categories)}',
                'field': '部件类别',
                'current_value': category,
                'valid_values': self.valid_categories,
                'row': row_index + 2
            }

        return {
            'rule': self.name,
            'passed': True,
            'message': f'部件类别"{category}"有效'
        }

class ExternalComponentRequirementRule(Rule):
    """外部第三方组件要求规则"""

    def __init__(self):
        super().__init__("external_component_requirement", "外部第三方组件必须填写部件编号")

    def evaluate(self, row_data: Dict[str, Any], row_index: int, context: Dict = None) -> Dict[str, Any]:
        category = row_data.get('部件类别')
        if category != '外部第三方组件':
            return {
                'rule': self.name,
                'passed': True,
                'message': '非外部第三方组件，跳过此规则'
            }

        part_number = row_data.get('部件编号')
        if FieldValidator._is_empty(part_number):
            return {
                'rule': self.name,
                'passed': False,
                'message': '外部第三方组件必须填写部件编号',
                'suggested_value': '请填写有效的部件编号',
                'field': '部件编号',
                'current_value': '（空）',
                'row': row_index + 2
            }

        return {
            'rule': self.name,
            'passed': True,
            'message': '外部第三方组件已填写部件编号'
        }

class NormalComponentRequirementRule(Rule):
    """常规组件必填字段规则"""

    def __init__(self):
        super().__init__("normal_component_requirement", "常规组件部件编号为空时的必填字段检查")

    def evaluate(self, row_data: Dict[str, Any], row_index: int, context: Dict = None) -> Dict[str, Any]:
        category = row_data.get('部件类别')
        if category == '外部第三方组件':
            return {
                'rule': self.name,
                'passed': True,
                'message': '外部第三方组件，跳过此规则'
            }

        part_number = row_data.get('部件编号')
        if not FieldValidator._is_empty(part_number):
            return {
                'rule': self.name,
                'passed': True,
                'message': '部件编号已填写，无需检查其他字段'
            }

        # 部件编号为空，检查中文名称、英文名称、英文代号
        required_fields = ['中文名称', '英文名称', '英文代号']
        missing = [f for f in required_fields if FieldValidator._is_empty(row_data.get(f))]

        if missing:
            return {
                'rule': self.name,
                'passed': False,
                'message': f'部件编号为空时，缺少必填字段',
                'suggested_value': f'请填写: {", ".join(missing)}',
                'field': ', '.join(missing),
                'current_value': '（空）',
                'missing_fields': missing,
                'row': row_index + 2
            }

        return {
            'rule': self.name,
            'passed': True,
            'message': '部件编号为空，但其他必填字段已填写'
        }

class LevelGapRule(Rule):
    """层次断层检查规则"""

    def __init__(self):
        super().__init__("level_gap", "检查层次关系是否出现断层")

    def evaluate(self, row_data: Dict[str, Any], row_index: int, context: Dict = None) -> Dict[str, Any]:
        level = row_data.get('层次')
        if not RuleEngine._is_valid_level(level):
            return {
                'rule': self.name,
                'passed': True,
                'message': '层次无效，跳过检查'
            }

        prev_level = context.get('prev_level') if context else None
        if prev_level is None:
            return {
                'rule': self.name,
                'passed': True,
                'message': '第一行或无前序层次，无需检查'
            }

        diff = level - prev_level
        if diff > 1:
            part_number = row_data.get('部件编号', '')
            return {
                'rule': self.name,
                'passed': False,
                'severity': 'error',
                'row': row_index + 2,
                'field': '层次',
                'message': f'第{row_index+2}行，部件编号：{part_number}，该部件与上层部件层次关系出现断层',
                'suggested_value': f'层次应不大于{prev_level + 1}',
                'current_value': level,
                'prev_level': prev_level,
                'current_level': level,
                'diff': diff
            }

        return {
            'rule': self.name,
            'passed': True,
            'message': f'层次关系正常（差值={diff}）'
        }

class SystemHardwareCheckRule(Rule):
    """系统部件下层硬件类检查规则"""

    # 允许的部件类别（软件相关类别）
    ALLOWED_CATEGORIES = {'系统', '软件', '软件子系统', '软件模块', '外部第三方组件'}

    def __init__(self):
        super().__init__("system_hardware_check", "检查系统部件下层是否存在不允许的硬件类部件")

    def evaluate(self, row_data: Dict[str, Any], row_index: int, context: Dict = None) -> Dict[str, Any]:
        level = row_data.get('层次')
        if not RuleEngine._is_valid_level(level):
            return {
                'rule': self.name,
                'passed': True,
                'message': '层次无效，跳过检查'
            }

        category = row_data.get('部件类别')
        if not category:
            return {
                'rule': self.name,
                'passed': True,
                'message': '部件类别为空，跳过检查'
            }

        # 如果是"系统"类别，检查其下层是否出现不允许的硬件类
        if category == '系统':
            processor = context.get('processor') if context else None
            if processor:
                df = processor.df
                current_idx = row_index
                sub_rows = df.iloc[current_idx+1:]
                invalid_category = None
                for _, sub_row in sub_rows.iterrows():
                    sub_level = sub_row.get('层次')
                    if RuleEngine._is_valid_level(sub_level) and sub_level > level:
                        sub_category = sub_row.get('部件类别')
                        # 检查是否是不允许的硬件类部件（不在允许列表中且不为空）
                        if sub_category and sub_category not in self.ALLOWED_CATEGORIES:
                            invalid_category = sub_category
                            break
                    elif RuleEngine._is_valid_level(sub_level) and sub_level <= level:
                        # 已离开当前子树
                        break

                if invalid_category:
                    return {
                        'rule': self.name,
                        'passed': False,
                        'severity': 'error',
                        'row': row_index + 2,
                        'field': '部件类别',
                        'message': '该"系统"部件下层已有硬件类部件，禁止将"系统"按软件组件模式进行导入',
                        'suggested_value': '请将硬件类部件移至其他系统下或删除',
                        'current_value': category
                    }

        return {
            'rule': self.name,
            'passed': True,
            'message': '系统部件下层检查通过'
        }

class ExternalComponentDetailedRule(Rule):
    """外部第三方组件详细规则（包含API验证）"""

    def __init__(self):
        super().__init__("external_component_detailed", "外部第三方组件的API验证和详细检查")
        try:
            from ..api_clients.component_api_client import ComponentAPIClient
            self.component_client = ComponentAPIClient()
        except Exception as e:
            logger.warning(f"初始化ComponentAPIClient失败: {e}")
            self.component_client = None

    def evaluate(self, row_data: Dict[str, Any], row_index: int, context: Dict = None) -> Dict[str, Any]:
        category = row_data.get('部件类别')
        if category != '外部第三方组件':
            return {
                'rule': self.name,
                'passed': True,
                'message': '非外部第三方组件，跳过此规则'
            }

        if not self.component_client:
            return {
                'rule': self.name,
                'passed': False,
                'severity': 'warning',
                'row': row_index + 2,
                'message': '无法初始化组件API客户端，跳过API验证'
            }

        part_number = row_data.get('部件编号')
        # 部件编号为空时，跳过API验证（由ExternalComponentRequirementRule处理）
        if FieldValidator._is_empty(part_number):
            return {
                'rule': self.name,
                'passed': True,
                'message': '部件编号为空，跳过API验证（由部件编号要求规则处理）',
                'row': row_index + 2
            }

        level = row_data.get('层次')
        name_cn = row_data.get('中文名称', '')
        name_en = row_data.get('英文名称', '')
        code_en = row_data.get('英文代号', '')

        # (1) 检查是否维护下层
        if self._has_subordinate(row_data, row_index, context):
            return {
                'rule': self.name,
                'passed': False,
                'severity': 'error',
                'row': row_index + 2,
                'field': '层次',
                'message': '外部第三方组件不允许维护下层部件',
                'suggested_value': '删除该部件的下层或将其移出',
                'current_value': '存在下层',
                'part_number': part_number
            }

        # (3) 层次禁止为0
        if level == 0:
            return {
                'rule': self.name,
                'passed': False,
                'severity': 'error',
                'row': row_index + 2,
                'field': '层次',
                'message': '外部第三方组件"层次"禁止为0（不能作为顶层部件）',
                'suggested_value': '请将层次修改为非0值',
                'current_value': 0,
                'level': level
            }

        # (4) 检查部件状态 != 废弃
        try:
            comp = self.component_client.get_component_by_id(part_number)
            if not comp:
                return {
                    'rule': self.name,
                    'passed': False,
                    'severity': 'error',
                    'row': row_index + 2,
                    'field': '部件编号',
                    'message': f'外部第三方组件"{part_number}"在API中不存在',
                    'suggested_value': '请确认部件编号正确或使用已存在的部件',
                    'current_value': part_number,
                    'part_number': part_number
                }

            # 注意：新API可能无status字段，根据实际API调整
            status = comp.get('status')
            if status == '废弃':
                return {
                    'rule': self.name,
                    'passed': False,
                    'severity': 'error',
                    'row': row_index + 2,
                    'field': '部件状态',
                    'message': f'外部第三方组件状态为"{status}"，不允许导入',
                    'suggested_value': '请使用状态为 有效 的组件',
                    'current_value': status,
                    'status': status
                }
        except Exception as e:
            logger.error(f"API查询失败 {part_number}: {e}")
            return {
                'rule': self.name,
                'passed': False,
                'severity': 'warning',
                'row': row_index + 2,
                'message': f'API查询异常: {str(e)}',
                'suggested_value': '请检查网络或稍后重试',
                'current_value': part_number
            }

        # (5) 检查部件编号与名称等的一致性
        # 注意：新API返回的name字段包含组件的规范名称
        api_name_cn = comp.get('name', '')
        api_name_en = comp.get('name', '')
        api_code_en = comp.get('name', '')

        inconsistencies = []
        if name_cn and api_name_cn and name_cn != api_name_cn:
            inconsistencies.append(f'中文名称（Excel:{name_cn} vs API:{api_name_cn}）')
        if name_en and api_name_en and name_en != api_name_en:
            inconsistencies.append(f'英文名称（Excel:{name_en} vs API:{api_name_en}）')
        if code_en and api_code_en and code_en != api_code_en:
            inconsistencies.append(f'英文代号（Excel:{code_en} vs API:{api_code_en}）')

        if inconsistencies:
            # 构造具体的API返回值供用户参考
            api_values = []
            if api_name_cn:
                api_values.append(f'中文名称: {api_name_cn}')
            if api_name_en:
                api_values.append(f'英文名称: {api_name_en}')
            if api_code_en:
                api_values.append(f'英文代号: {api_code_en}')
            suggested = '; '.join(api_values) if api_values else '以API返回数据为准'

            return {
                'rule': self.name,
                'passed': False,
                'severity': 'error',
                'row': row_index + 2,
                'field': '部件编号',
                'message': f'外部第三方组件信息不一致：{"; ".join(inconsistencies)}',
                'suggested_value': f'API返回数据: {suggested}',
                'current_value': f'存在{len(inconsistencies)}处不一致',
                'part_number': part_number,
                'inconsistencies': inconsistencies
            }

        return {
            'rule': self.name,
            'passed': True,
            'message': f'外部第三方组件"{part_number}"验证通过'
        }

    def _has_subordinate(self, row_data: Dict[str, Any], row_index: int, context: Dict = None) -> bool:
        """检查是否有下层部件"""
        level = row_data.get('层次')
        if not RuleEngine._is_valid_level(level):
            return False

        processor = context.get('processor') if context else None
        if not processor:
            return False

        df = processor.df
        for idx in range(row_index + 1, len(df)):
            sub_level = df.at[idx, '层次']
            if RuleEngine._is_valid_level(sub_level) and sub_level > level:
                return True
            if RuleEngine._is_valid_level(sub_level) and sub_level <= level:
                break
        return False

class SubordinateMaintenanceRule(Rule):
    """维护下层检查规则（外部组件、失效部件）"""

    def __init__(self):
        super().__init__("subordinate_maintenance", "检查是否对禁止维护下层的部件维护了下层")

    def evaluate(self, row_data: Dict[str, Any], row_index: int, context: Dict = None) -> Dict[str, Any]:
        category = row_data.get('部件类别')
        status = row_data.get('部件状态')
        part_number = row_data.get('部件编号')

        # 只检查外部组件和失效部件
        if category != '外部第三方组件' and status != '失效':
            return {
                'rule': self.name,
                'passed': True,
                'message': '非禁止类型，跳过检查'
            }

        level = row_data.get('层次')
        if not RuleEngine._is_valid_level(level):
            return {
                'rule': self.name,
                'passed': True,
                'message': '层次无效，跳过检查'
            }

        processor = context.get('processor') if context else None
        if not processor:
            return {
                'rule': self.name,
                'passed': True,
                'message': '无processor上下文，跳过检查'
            }

        # 检查是否有下层
        df = processor.df
        current_idx = row_index
        for idx in range(current_idx + 1, len(df)):
            sub_level = df.at[idx, '层次']
            if RuleEngine._is_valid_level(sub_level) and sub_level > level:
                reason = '外部第三方组件' if category == '外部第三方组件' else '部件状态为失效'
                return {
                    'rule': self.name,
                    'passed': False,
                    'severity': 'error',
                    'row': row_index + 2,
                    'field': '层次',
                    'message': f'{reason}，不允许维护下层部件',
                    'suggested_value': '删除该部件的下层',
                    'current_value': '存在下层',
                    'part_number': part_number,
                    'has_subordinate': True
                }
            if RuleEngine._is_valid_level(sub_level) and sub_level <= level:
                break

        return {
            'rule': self.name,
            'passed': True,
            'message': '维护下层检查通过'
        }

class NormalComponentDetailedRule(Rule):
    """常规组件详细规则（包含PDM API验证）"""

    def __init__(self):
        super().__init__("normal_component_detailed", "常规组件的PDM API验证")
        try:
            from ..api_clients.part_api_client import PartAPIClient
            self.part_client = PartAPIClient()
        except Exception as e:
            logger.warning(f"初始化PartAPIClient失败: {e}")
            self.part_client = None

    def evaluate(self, row_data: Dict[str, Any], row_index: int, context: Dict = None) -> Dict[str, Any]:
        category = row_data.get('部件类别')
        if category == '外部第三方组件':
            return {
                'rule': self.name,
                'passed': True,
                'message': '外部第三方组件，跳过此规则'
            }

        if not self.part_client:
            return {
                'rule': self.name,
                'passed': False,
                'severity': 'warning',
                'row': row_index + 2,
                'message': '无法初始化部件API客户端，跳过API验证'
            }

        part_number = row_data.get('部件编号')
        name_cn = row_data.get('中文名称', '')
        name_en = row_data.get('英文名称', '')
        code_en = row_data.get('英文代号', '')

        # 部件编号为空时，已在NormalComponentRequirementRule检查了必填字段
        if FieldValidator._is_empty(part_number):
            return {
                'rule': self.name,
                'passed': True,
                'message': '无部件编号，无需API验证'
            }

        try:
            part = self.part_client.query_part(part_number)
            if not part:
                return {
                    'rule': self.name,
                    'passed': False,
                    'severity': 'error',
                    'row': row_index + 2,
                    'field': '部件编号',
                    'message': f'部件编号"{part_number}"在PDM中不存在',
                    'suggested_value': '请确认部件编号正确或使用已存在的部件',
                    'current_value': part_number,
                    'part_number': part_number
                }

            # 检查状态是否为审批完成
            status = part.get('LIFECYCLE_STATUS')
            if status != '审批完成':
                return {
                    'rule': self.name,
                    'passed': False,
                    'severity': 'error',
                    'row': row_index + 2,
                    'field': '部件状态',
                    'message': f'部件状态应为"审批完成"，实际为"{status}"',
                    'suggested_value': '请使用审批完成的部件',
                    'current_value': status,
                    'api_status': status
                }

            # 检查字段一致性
            api_part_type = part.get('PART_TYPE', '')
            api_name_cn = part.get('NAME_CN', '')
            api_name_en = part.get('NAME_EN', '')
            api_code_en = part.get('EN_CODE', '')

            inconsistencies = []
            excel_category = row_data.get('部件类别')
            if excel_category and api_part_type and excel_category != api_part_type:
                inconsistencies.append(f'部件类别（Excel:{excel_category} vs PDM:{api_part_type}）')
            if name_cn and api_name_cn and name_cn != api_name_cn:
                inconsistencies.append(f'中文名称（Excel:{name_cn} vs PDM:{api_name_cn}）')
            if name_en and api_name_en and name_en != api_name_en:
                inconsistencies.append(f'英文名称（Excel:{name_en} vs PDM:{api_name_en}）')
            if code_en and api_code_en and code_en != api_code_en:
                inconsistencies.append(f'英文代号（Excel:{code_en} vs PDM:{api_code_en}）')

            if inconsistencies:
                return {
                    'rule': self.name,
                    'passed': False,
                    'severity': 'error',
                    'row': row_index + 2,
                    'field': '部件编号',
                    'message': f'部件信息不一致：{"; ".join(inconsistencies)}',
                    'suggested_value': f'【部件类别】：{api_part_type}  【中文名称】：{api_name_cn}  【英文名称】：{api_name_en}  【英文代号】：{api_code_en}',
                    'current_value': f'存在{len(inconsistencies)}处不一致',
                    'part_number': part_number,
                    'inconsistencies': inconsistencies
                }

        except Exception as e:
            logger.error(f"PDM API查询失败 {part_number}: {e}")
            return {
                'rule': self.name,
                'passed': False,
                'severity': 'warning',
                'row': row_index + 2,
                'message': f'PDM API查询异常: {str(e)}',
                'suggested_value': '请检查网络或稍后重试',
                'current_value': part_number
            }

        return {
            'rule': self.name,
            'passed': True,
            'message': f'部件"{part_number}"PDM验证通过'
        }

class ProductConsistencyRule(Rule):
    """跨产品一致性检查规则"""

    def __init__(self):
        super().__init__("product_consistency", "检查部件是否在同一产品下")
        try:
            from ..api_clients.part_api_client import PartAPIClient
            self.part_client = PartAPIClient()
        except Exception as e:
            logger.warning(f"初始化PartAPIClient失败: {e}")
            self.part_client = None

    def evaluate(self, row_data: Dict[str, Any], row_index: int, context: Dict = None) -> Dict[str, Any]:
        product_model = context.get('product_model') if context else None
        if not product_model:
            # 用户未提供产品规格型号，跳过检查
            return {
                'rule': self.name,
                'passed': True,
                'message': '未提供产品规格型号，跳过跨产品检查'
            }

        if not self.part_client:
            return {
                'rule': self.name,
                'passed': False,
                'severity': 'warning',
                'row': row_index + 2,
                'message': '无法初始化部件API客户端，跳过产品一致性检查'
            }

        part_number = row_data.get('部件编号')
        if FieldValidator._is_empty(part_number):
            return {
                'rule': self.name,
                'passed': True,
                'message': '无部件编号，无需检查产品一致性'
            }

        try:
            part = self.part_client.query_part(part_number)
            if not part:
                return {
                    'rule': self.name,
                    'passed': True,
                    'message': '部件不存在，跳过产品检查'
                }

            # 获取PDM中的产品规格型号和工作组信息
            api_product = part.get('PRODUCT_MODEL')
            api_team1 = part.get('TEAM1_NAME')
            api_team2 = part.get('TEAM2_NAME')
            api_team3 = part.get('TEAM3_NAME')

            if not api_product:
                return {
                    'rule': self.name,
                    'passed': True,
                    'message': 'PDM未返回产品信息，跳过检查'
                }

            # 检查四参数是否完全匹配（只检查用户提供的参数）
            mismatches = []
            if product_model and api_product != product_model:
                mismatches.append(f'产品规格型号(Excel={product_model}, PDM={api_product})')

            user_wg1 = context.get('work_group_1')
            if user_wg1 and api_team1 != user_wg1:
                mismatches.append(f'工作组类1(Excel={user_wg1}, PDM={api_team1})')

            user_wg2 = context.get('work_group_2')
            if user_wg2 and api_team2 != user_wg2:
                mismatches.append(f'工作组类2(Excel={user_wg2}, PDM={api_team2})')

            user_wg3 = context.get('work_group_3')
            if user_wg3 and api_team3 != user_wg3:
                mismatches.append(f'工作组类3(Excel={user_wg3}, PDM={api_team3})')

            # 如果存在不匹配，检查是否维护了下层
            if mismatches:
                # 检查是否维护了下层
                processor = context.get('processor') if context else None
                has_subordinate = False
                if processor:
                    df = processor.df
                    current_level = row_data.get('层次')
                    part_number = row_data.get('部件编号')
                    logger.info(f"[DEBUG] ProductConsistencyRule: part={part_number}, row_index={row_index}, current_level={current_level}, df.shape={df.shape}")
                    for idx in range(row_index + 1, len(df)):
                        logger.info(f"[DEBUG] part={part_number}   row_index + 1={row_index + 1}")
                        sub_level = df.at[idx, '层次']
                        logger.info(f"[DEBUG] 当前层级和下层层级  part={part_number}: sub_level={sub_level}, current_level={current_level}")
                        if sub_level > current_level:
                            logger.info(f"[DEBUG]   -> 发现下层!")
                            has_subordinate = True
                            break
                        elif sub_level <= current_level:
                            logger.info(f"[DEBUG]   -> 遇到同级/上层，停止")
                            # 已离开当前子树
                            break

                    logger.info(f"[DEBUG] has_subordinate={has_subordinate}")

                if not has_subordinate:
                    mismatch_list = ', '.join(mismatches)
                    return {
                        'rule': self.name,
                        'passed': True,
                        'message': f'部件信息不一致[{mismatch_list}]，但未维护下层，允许导入'
                    }

                mismatch_list = ', '.join(mismatches)
                return {
                    'rule': self.name,
                    'passed': False,
                    'severity': 'error',
                    'row': row_index + 2,
                    'field': '部件编号',
                    'message': f'部件信息不一致（{mismatch_list}），禁止对其维护下层',
                    'suggested_value': '当前部件为引用部件，只体现一层即可，请删除下层',
                    'current_value': f'存在{len(mismatches)}处不一致',
                    'api_product': api_product,
                    'api_team1': api_team1,
                    'api_team2': api_team2,
                    'api_team3': api_team3,
                    'part_number': part_number
                }

        except Exception as e:
            logger.error(f"产品一致性检查失败 {part_number}: {e}")
            return {
                'rule': self.name,
                'passed': False,
                'severity': 'warning',
                'row': row_index + 2,
                'message': f'产品一致性检查异常: {str(e)}',
                'suggested_value': '请检查网络或稍后重试',
                'current_value': part_number
            }

        return {
            'rule': self.name,
            'passed': True,
            'message': f'部件"{part_number}"产品一致性检查通过'
        }

class NameFirstCharRule(Rule):
    """名称首位字符检查规则"""

    def __init__(self):
        super().__init__("name_first_char", "检查名称首位字符是否符合规范")
        # 符号集合（英文符号和中文符号）
        self.symbols = set('!@#$%^&*()_+-=[]{}|;:\'\",./<>?`~·！@#￥%……&*（）——+【】{}|；:\'\"，。、<>？「」『』（）〔〕【】〈〉《》「」『』〖〗【】')

    def evaluate(self, row_data: Dict[str, Any], row_index: int, context: Dict = None) -> Dict[str, Any]:
        errors = []

        # 1. 检查中文名称首位不能是符号
        name_cn = row_data.get('中文名称', '')
        if name_cn and not FieldValidator._is_empty(name_cn):
            first_char = str(name_cn)[0]
            if first_char in self.symbols:
                errors.append(f'中文名称"{name_cn}"的首位"{first_char}"不能是符号')
                logger.warning(f"第{row_index+2}行：中文名称首位不能是符号，实际为'{first_char}'")

        # 2. 检查英文名称首位不能是符号且不能有中文
        name_en = row_data.get('英文名称', '')
        if name_en and not FieldValidator._is_empty(name_en):
            first_char = str(name_en)[0]
            if first_char in self.symbols:
                errors.append(f'英文名称"{name_en}"的首位"{first_char}"不能是符号')
                logger.warning(f"第{row_index+2}行：英文名称首位不能是符号，实际为'{first_char}'")
            # 检查是否包含中文
            if self._contains_chinese(name_en):
                errors.append(f'英文名称"{name_en}"不能包含中文')
                logger.warning(f"第{row_index+2}行：英文名称不能包含中文")

        # 3. 检查英文代号首位不能是符号且不能有中文
        code_en = row_data.get('英文代号', '')
        if code_en and not FieldValidator._is_empty(code_en):
            first_char = str(code_en)[0]
            if first_char in self.symbols:
                errors.append(f'英文代号"{code_en}"的首位"{first_char}"不能是符号')
                logger.warning(f"第{row_index+2}行：英文代号首位不能是符号，实际为'{first_char}'")
            # 检查是否包含中文
            if self._contains_chinese(code_en):
                errors.append(f'英文代号"{code_en}"不能包含中文')
                logger.warning(f"第{row_index+2}行：英文代号不能包含中文")

        if errors:
            # 提取第一个错误的具体字段和建议
            first_error = errors[0]
            field = '该行数据'
            suggested = '请修正名称'

            if '中文名称' in first_error:
                field = '中文名称'
                if '首位' in first_error:
                    suggested = '请修正首位字符，不能使用符号'
                else:
                    suggested = '请移除中文'
            elif '英文名称' in first_error:
                field = '英文名称'
                if '首位' in first_error:
                    suggested = '请修正首位字符，不能使用符号'
                else:
                    suggested = '请移除中文，仅使用英文字符'
            elif '英文代号' in first_error:
                field = '英文代号'
                if '首位' in first_error:
                    suggested = '请修正首位字符，不能使用符号'
                else:
                    suggested = '请移除中文，仅使用英文字母和数字'

            current_val = first_error.split('首位')[1].split('不能')[0].strip() if '首位' in first_error else first_error.split('"')[1] if '"' in first_error else '（有误）'

            return {
                'rule': self.name,
                'passed': False,
                'message': '; '.join(errors),
                'suggested_value': suggested,
                'field': field,
                'current_value': current_val,
                'row': row_index + 2,
                'errors': errors
            }

        return {
            'rule': self.name,
            'passed': True,
            'message': '名称首位检查通过'
        }

    def _contains_chinese(self, text: str) -> bool:
        """检查字符串是否包含中文"""
        for char in str(text):
            if '\u4e00' <= char <= '\u9fff':
                return True
        return False

# 便捷函数
def create_rule_engine(product_model: str = None) -> RuleEngine:
    """创建规则引擎实例"""
    return RuleEngine(product_model)