#!/usr/bin/env python3
"""
一致性检查器
检查部件层次关系、系统部件下层限制、外部组件API一致性等
"""

import logging
from typing import Dict, Any, List, Optional, Tuple
from .excel_processor import ExcelProcessor
from ..api_clients.part_api_client import PartAPIClient
from ..api_clients.component_api_client import ComponentAPIClient
from ..validators.field_validator import FieldValidator
from ..validators.rule_engine import RuleEngine

logger = logging.getLogger(__name__)

class ConsistencyError(Exception):
    """一致性检查错误"""
    def __init__(self, message: str, row: int, field: str = None,details: Dict = None):
        self.message = message
        self.row = row
        self.field = field
        self.details = details or {}
        super().__init__(f"第{row}行{f' ({field})' if field else ''}: {message}")

class ConsistencyChecker:
    """一致性检查器"""

    # 系统部件下层允许的软件类部件类别
    SOFTWARE_CATEGORIES = {'系统', '软件', '软件子系统', '软件模块', '组件'}

    # 硬件类部件类别（除了software_categories之外的类别）
    HARDWARE_CATEGORIES = {'外部第三方组件'}  # 根据实际业务可能有更多

    def __init__(self, excel_processor: ExcelProcessor, product_model: str = None, work_group_1: str = None, work_group_2: str = None, work_group_3: str = None):
        """
        初始化一致性检查器

        Args:
            excel_processor: Excel处理器实例
            product_model: 用户输入的产品规格型号（用于跨产品检查）
            work_group_1: 工作组类1编号（TEAM1）
            work_group_2: 工作组类2编号（TEAM2）
            work_group_3: 工作组类3编号（TEAM3）
        """
        self.processor = excel_processor
        self.product_model = product_model
        self.rule_engine = RuleEngine(product_model=product_model, work_group_1=work_group_1, work_group_2=work_group_2, work_group_3=work_group_3)
        self.part_api_client = PartAPIClient()
        self.component_api_client = ComponentAPIClient()
        self.api_cache = {}  # 缓存API查询结果
        self.warnings = []
        self.errors = []

    def check_all(self) -> Tuple[bool, List[Dict]]:
        """
        执行所有一致性检查

        Returns:
            (是否通过, 问题列表)
        """
        issues = []

        if self.processor.df is None or len(self.processor.df) == 0:
            return False, [{'type': 'error', 'message': 'Excel数据为空'}]

        # 1. 层次关系检查
        issues.extend(self._check_level_relationships())

        # 2. 系统部件下层检查
        issues.extend(self._check_system_sub_components())

        # 遍历每一行进行特定检查
        for idx, row in self.processor.df.iterrows():
            row_issues = self._check_row_consistency(row, idx)
            issues.extend(row_issues)

        # 按行号排序
        issues.sort(key=lambda x: x.get('row', 0))

        # 分离错误和警告
        errors = [i for i in issues if i.get('severity') != 'warning']
        self.errors = errors
        self.warnings = [i for i in issues if i.get('severity') == 'warning']

        return len(errors) == 0, issues

    def _check_level_relationships(self) -> List[Dict]:
        """
        检查层次关系断层（后序与前序层次差<=1）

        提示语：该部件与上层部件层次关系出现断层，请检查
        """
        issues = []
        if len(self.processor.df) <= 1:
            return issues

        prev_level = None
        for idx, row in self.processor.df.iterrows():
            current_level = row.get('层次')
            if not self._is_valid_level(current_level):
                continue

            if prev_level is not None:
                # 计算与前序层差
                diff = current_level - prev_level
                if diff > 1:
                    issues.append({
                        'type': 'level_gap',
                        'severity': 'error',
                        'row': idx + 1,
                        'field': '层次',
                        'message': f'该部件与上层部件层次关系出现断层（前序层次={prev_level}，当前层次={current_level}，差值={diff}），请检查',
                        'prev_level': prev_level,
                        'current_level': current_level,
                        'diff': diff
                    })

            prev_level = current_level

        return issues

    def _check_system_sub_components(self) -> List[Dict]:
        """
        检查系统部件下层是否混入硬件类部件

        提示语：该"系统"部件下层已有硬件类部件，禁止将"系统"按软件组件模式进行导入
        """
        issues = []
        hierarchy = []  # 构建层次栈

        for idx, row in self.processor.df.iterrows():
            level = row.get('层次')
            category = row.get('部件类别')

            if not self._is_valid_level(level):
                continue

            # 维护层次栈
            while len(hierarchy) > level:
                hierarchy.pop()

            # 检查当前行是否是"系统"的下层
            if len(hierarchy) > 0 and hierarchy[-1].get('部件类别') == '系统':
                if category in self.HARDWARE_CATEGORIES:
                    issues.append({
                        'type': 'system_with_hardware',
                        'severity': 'error',
                        'row': idx + 1,
                        'field': '部件类别',
                        'message': f'该"系统"部件下层已有硬件类部件（{category}），禁止将"系统"按软件组件模式进行导入',
                        'system_row': hierarchy[-1].get('row_index', 0) + 1,
                        'system_category': '系统',
                        'sub_category': category
                    })

            # 将当前行压入栈
            hierarchy.append({
                'row_index': idx,
                '层次': level,
                '部件类别': category
            })

        return issues

    def _check_row_consistency(self, row: Dict[str, Any], row_index: int) -> List[Dict]:
        """
        检查单行数据的一致性

        Args:
            row: 行数据
            row_index: 行索引

        Returns:
            问题列表
        """
        issues = []
        category = row.get('部件类别')
        part_number = row.get('部件编号')
        level = row.get('层次')
        status = row.get('部件状态')

        if FieldValidator._is_empty(category):
            return issues

        # === 外部第三方组件检查 ===
        if category == '外部第三方组件':
            issues.extend(self._check_external_component(row, row_index))

        # === 常规组件检查（非外部第三方组件）===
        else:
            issues.extend(self._check_normal_component(row, row_index))

            # 检查部件状态是否失效
            if not FieldValidator._is_empty(status) and status == '失效':
                issues.append({
                    'type': 'component_invalid',
                    'severity': 'error',
                    'row': row_index + 1,
                    'field': '部件状态',
                    'message': '部件状态为"失效"，禁止维护下层',
                    'status': status
                })

            # 检查是否在同一产品下（如果用户提供了产品规格型号）
            if not FieldValidator._is_empty(part_number):
                product_ok, api_product = self._get_part_product(part_number, category)
                if self.product_model and api_product and api_product != self.product_model:
                    issues.append({
                        'type': 'cross_product',
                        'severity': 'error',
                        'row': row_index + 1,
                        'field': '部件编号',
                        'message': f'部件不在同一产品下（Excel产品={self.product_model}，API产品={api_product}），禁止对其维护下层',
                        'excel_product': self.product_model,
                        'api_product': api_product,
                        'part_number': part_number
                    })

        return issues

    def _check_external_component(self, row: Dict[str, Any], row_index: int) -> List[Dict]:
        """
        检查外部第三方组件的特殊要求

        Returns:
            问题列表
        """
        issues = []
        part_number = row.get('部件编号')
        level = row.get('层次')

        # （1）检查"部件编号"是否已填写
        if FieldValidator._is_empty(part_number):
            issues.append({
                'type': 'required_field',
                'severity': 'error',
                'row': row_index + 1,
                'field': '部件编号',
                'message': '外部第三方组件必须填写部件编号'
            })
            return issues  # 如果没有部件编号，后续检查无法进行

        # （2）是否维护下层（不允许维护）
        # 检查是否有下级（通过检查后续行，层次大于当前行的）
        current_level = level
        if not FieldValidator._is_empty(current_level):
            has_sub = any(
                r.get('层次', -1) > current_level
                for _, r in self.processor.df.iloc[row_index+1:].iterrows()
            )
            if has_sub:
                issues.append({
                    'type': 'sub_component_not_allowed',
                    'severity': 'error',
                    'row': row_index + 1,
                    'field': '层次',
                    'message': '外部第三方组件不允许维护下层部件',
                    'part_number': part_number
                })

        # （3）"层次"禁止为0（外部组件不能是顶层）
        if not FieldValidator._is_empty(level) and level == 0:
            issues.append({
                'type': 'invalid_level',
                'severity': 'error',
                'row': row_index + 1,
                'field': '层次',
                'message': '外部第三方组件"层次"禁止为0（不能作为顶层部件）',
                'level': level
            })

        # （4）检查"部件状态"!=废弃（调用API获取状态）
        status_ok, api_status = self._get_component_status(part_number, is_external=True)
        if not status_ok:
            if api_status:
                issues.append({
                    'type': 'component_status',
                    'severity': 'error',
                    'row': row_index + 1,
                    'field': '部件状态',
                    'message': f'外部第三方组件状态为"{api_status}"，不允许导入（状态必须为有效状态）',
                    'api_status': api_status
                })
        # 对于外部组件，如果状态是"废弃"也视为错误
        elif api_status == '废弃':
            issues.append({
                'type': 'component_status',
                'severity': 'error',
                'row': row_index + 1,
                'field': '部件状态',
                'message': '外部第三方组件状态为"废弃"，不允许导入',
                'api_status': api_status
            })

        # （5）检查"部件编号"是否正确，"中文名称""英文名称""英文代号"的一致性（调用API）
        name_cn = row.get('中文名称', '')
        name_en = row.get('英文名称', '')
        code_en = row.get('英文代号', '')

        match_ok, api_data = self._get_external_component_match(part_number)
        if not match_ok:
            issues.append({
                'type': 'api_mismatch',
                'severity': 'error',
                'row': row_index + 1,
                'field': '部件编号',
                'message': f'外部第三方组件编号"{part_number}"在API中不存在或数据不匹配',
                'part_number': part_number
            })
        else:
            # 检查字段一致性：中文名称、英文名称、英文代号都必须与API返回的title一致
            api_title = api_data.get('title', '')

            inconsistencies = []
            if name_cn and api_title and name_cn != api_title:
                inconsistencies.append(f'中文名称（Excel:{name_cn} vs API:{api_title}）')
            if name_en and api_title and name_en != api_title:
                inconsistencies.append(f'英文名称（Excel:{name_en} vs API:{api_title}）')
            if code_en and api_title and code_en != api_title:
                inconsistencies.append(f'英文代号（Excel:{code_en} vs API:{api_title}）')

            if inconsistencies:
                issues.append({
                    'type': 'field_inconsistency',
                    'severity': 'error',
                    'row': row_index + 1,
                    'field': '部件编号',
                    'message': f'外部第三方组件信息不一致：{"; ".join(inconsistencies)}',
                    'part_number': part_number,
                    'inconsistencies': inconsistencies
                })

        return issues

    def _check_normal_component(self, row: Dict[str, Any], row_index: int) -> List[Dict]:
        """
        检查常规组件（非外部第三方组件）的要求

        Returns:
            问题列表
        """
        issues = []
        part_number = row.get('部件编号')
        status = row.get('部件状态')
        name_cn = row.get('中文名称', '')
        name_en = row.get('英文名称', '')
        code_en = row.get('英文代号', '')

        # （1）"部件编号"为空时，检查"中文名称""英文名称""英文代号"必填，且长度<25
        if FieldValidator._is_empty(part_number):
            missing = []
            for field in ['中文名称', '英文名称', '英文代号']:
                if FieldValidator._is_empty(row.get(field)):
                    missing.append(field)

            if missing:
                issues.append({
                    'type': 'required_fields',
                    'severity': 'error',
                    'row': row_index + 1,
                    'field': '部件编号为空',
                    'message': f'部件编号为空时，以下字段必填: {", ".join(missing)}'
                })

            # 检查长度是否<25
            for field, value in [('中文名称', name_cn), ('英文名称', name_en), ('英文代号', code_en)]:
                if not FieldValidator._is_empty(value) and len(str(value)) >= 25:
                    issues.append({
                        'type': 'field_length',
                        'severity': 'warning',
                        'row': row_index + 1,
                        'field': field,
                        'message': f'{field}长度应为小于25个字符，实际为{len(str(value))}',
                        'length': len(str(value))
                    })

            return issues

        # （2）"部件编号"不为空时，检查部件编号是否正确，部件状态是否为审批完成，检查一致性

        # 区分是部件（PDM）还是外部组件（已在前面的规则处理）
        # 这里调用PDM API验证部件编号
        api_ok, api_data = self._get_part_info(part_number)
        if not api_ok:
            issues.append({
                'type': 'part_not_found',
                'severity': 'error',
                'row': row_index + 1,
                'field': '部件编号',
                'message': f'部件编号"{part_number}"在PDM系统中不存在',
                'part_number': part_number
            })
        else:
            # 检查部件状态是否为审批完成
            api_status = api_data.get('status', '')
            if api_status != '审批完成':
                issues.append({
                    'type': 'part_status',
                    'severity': 'error',
                    'row': row_index + 1,
                    'field': '部件状态',
                    'message': f'部件状态应为"审批完成"，实际为"{api_status}"',
                    'api_status': api_status
                })

            # 检查"部件编号"与"部件类别""中文名称""英文名称""英文代号"的一致性
            # 注意：PDM返回的字段名可能与Excel列名不同
            api_part_category = api_data.get('partType', '')  # 或part_category
            api_name_cn = api_data.get('nameCn', '')
            api_name_en = api_data.get('nameEn', '')
            api_code_en = api_data.get('engCode', '')

            inconsistencies = []

            # 部件类别检查（如果Excel有填写）
            excel_category = row.get('部件类别')
            if excel_category and api_part_category and excel_category != api_part_category:
                inconsistencies.append(f'部件类别（Excel:{excel_category} vs PDM:{api_part_category}）')

            # 中文名称
            if name_cn and api_name_cn and name_cn != api_name_cn:
                inconsistencies.append(f'中文名称（Excel:{name_cn} vs PDM:{api_name_cn}）')

            # 英文名称
            if name_en and api_name_en and name_en != api_name_en:
                inconsistencies.append(f'英文名称（Excel:{name_en} vs PDM:{api_name_en}）')

            # 英文代号
            if code_en and api_code_en and code_en != api_code_en:
                inconsistencies.append(f'英文代号（Excel:{code_en} vs PDM:{api_code_en}）')

            if inconsistencies:
                issues.append({
                    'type': 'field_inconsistency',
                    'severity': 'error',
                    'row': row_index + 1,
                    'field': '部件编号',
                    'message': f'部件信息不一致：{"; ".join(inconsistencies)}',
                    'part_number': part_number,
                    'inconsistencies': inconsistencies
                })

        # （3）检查"部件状态"!=失效（已在前面统一检查）

        return issues

    def _check_subordinate_maintenance(self, row: Dict[str, Any], row_index: int) -> List[Dict]:
        """
        检查是否对不允许维护下层的部件维护了下层
        （外部组件和失效部件）

        Returns:
            问题列表
        """
        issues = []
        part_number = row.get('部件编号')
        category = row.get('部件类别')
        level = row.get('层次')
        status = row.get('部件状态')

        if FieldValidator._is_empty(level):
            return issues

        # 检查是否有下级
        current_level = level
        has_sub = any(
            r.get('层次', -1) > current_level
            for _, r in self.processor.df.iloc[row_index+1:].iterrows()
        )

        if has_sub:
            # 外部组件或失效部件不允许有下层
            if category == '外部第三方组件':
                issues.append({
                    'type': 'sub_not_allowed',
                    'severity': 'error',
                    'row': row_index + 1,
                    'field': '层次',
                    'message': f'外部第三方组件不允许维护下层部件',
                    'part_number': part_number
                })
            elif status == '失效':
                issues.append({
                    'type': 'sub_not_allowed',
                    'severity': 'error',
                    'row': row_index + 1,
                    'field': '部件状态',
                    'message': f'部件状态为"失效"，不允许维护下层部件',
                    'part_number': part_number
                })

        return issues

    # ========== API查询辅助方法 ==========

    def _get_part_info(self, part_number: str) -> Tuple[bool, Optional[Dict]]:
        """
        查询PDM部件信息（带缓存）

        Returns:
            (查询是否成功, 部件数据)
        """
        if part_number in self.api_cache:
            return self.api_cache[part_number]

        try:
            part = self.part_api_client.query_part(part_number)
            if part:
                self.api_cache[part_number] = (True, part)
                return True, part
            else:
                self.api_cache[part_number] = (False, None)
                return False, None
        except Exception as e:
            logger.error(f"查询部件信息失败 {part_number}: {str(e)}")
            self.api_cache[part_number] = (False, None)
            return False, None

    def _get_component_status(self, part_number: str, is_external: bool = False) -> Tuple[bool, Optional[str]]:
        """
        获取组件/部件状态

        Args:
            part_number: 部件编号
            is_external: 是否为外部第三方组件

        Returns:
            (查询是否成功, 状态字符串)
        """
        try:
            if is_external:
                comp = self.component_api_client.get_component_by_id(part_number)
                if comp:
                    return True, comp.get('status')  # 注意：新API可能无status字段
                return False, None
            else:
                part = self.part_api_client.query_part(part_number)
                if part:
                    return True, part.get('status') or part.get('审核状态')
                return False, None
        except Exception as e:
            logger.error(f"查询状态失败 {part_number}: {str(e)}")
            return False, None

    def _get_external_component_match(self, part_number: str) -> Tuple[bool, Optional[Dict]]:
        """
        检查外部第三方组件编号是否存在，并获取完整数据用于一致性检查

        Returns:
            (是否匹配, 组件数据)
        """
        try:
            comp = self.component_api_client.get_component_by_id(part_number)
            if comp:
                return True, comp
            return False, None
        except Exception as e:
            logger.error(f"查询外部组件失败 {part_number}: {str(e)}")
            return False, None

    def _get_part_product(self, part_number: str, category: str) -> Tuple[bool, Optional[str]]:
        """
        获取部件所属产品

        Returns:
            (查询是否成功, 产品规格型号)
        """
        try:
            part = self.part_api_client.query_part(part_number)
            if part:
                # 假设PDM返回的产品字段为 product_model 或 model
                product = part.get('product_model') or part.get('model') or part.get('productModel') or part.get('product_name')
                return True, product
            return False, None
        except Exception as e:
            logger.error(f"查询部件产品失败 {part_number}: {str(e)}")
            return False, None

    @staticmethod
    def _is_valid_level(value: Any) -> bool:
        """判断是否为有效层次值"""
        if value is None:
            return False
        try:
            return isinstance(value, (int, float)) or (isinstance(value, str) and value.strip().isdigit())
        except:
            return False

    def get_summary(self) -> Dict[str, Any]:
        """
        获取检查摘要

        Returns:
            统计摘要
        """
        return {
            'total_errors': len(self.errors),
            'total_warnings': len(self.warnings),
            'error_types': self._group_by_type(self.errors),
            'warning_types': self._group_by_type(self.warnings)
        }

    def _group_by_type(self, issues: List[Dict]) -> Dict[str, int]:
        """按类型分组统计"""
        counts = {}
        for issue in issues:
            t = issue.get('type', 'unknown')
            counts[t] = counts.get(t, 0) + 1
        return counts

    # ========== 字段级一致性检查（用于主流程逐行API数据对比）==========

    def check_fields(self, excel_row: Dict[str, Any], api_data: Dict[str, Any],
                    fields_to_check: List[str] = None) -> List[Dict[str, Any]]:
        """
        检查Excel行与API数据字段的一致性

        Args:
            excel_row: Excel行数据
            api_data: API返回的数据
            fields_to_check: 要检查的字段列表，默认使用所有映射字段

        Returns:
            不一致的字段列表，每个元素包含 field, excel_value, api_value 等
        """
        discrepancies = []

        # 字段映射：Excel字段名 -> API字段名列表
        FIELD_MAPPING = {
            '中文名称': ['name', 'nameCn', 'title'],
            '英文名称': ['nameEn', 'name', 'title'],
            '英文代号': ['engCode', 'nameEn', 'name', 'title'],
            '部件类别': ['opsTypeIdName', 'typeName', 'partType', 'PART_TYPE']
        }

        if fields_to_check is None:
            fields_to_check = list(FIELD_MAPPING.keys())

        for excel_field in fields_to_check:
            api_fields = FIELD_MAPPING.get(excel_field, [excel_field])

            # 获取Excel值
            excel_value = excel_row.get(excel_field)
            if excel_value is None:
                continue

            # 规范化Excel值
            excel_value_norm = self._normalize_value(excel_value)

            # 从API数据中获取对应的值
            api_value = None
            for api_field in api_fields:
                if api_field in api_data:
                    api_value = api_data[api_field]
                    break

            if api_value is None:
                logger.debug(f"API数据中未找到字段 {excel_field} 对应的值 (尝试: {api_fields})")
                continue

            # 规范化API值
            api_value_norm = self._normalize_value(api_value)

            # 比较
            if excel_value_norm != api_value_norm:
                discrepancy = {
                    'field': excel_field,
                    'excel_value': excel_value,
                    'api_value': api_value,
                    'excel_value_normalized': excel_value_norm,
                    'api_value_normalized': api_value_norm
                }
                discrepancies.append(discrepancy)
                logger.debug(f"发现不一致: {excel_field} -> Excel: {excel_value!r} vs API: {api_value!r}")

        return discrepancies

    def has_discrepancy(self, excel_row: Dict[str, Any], api_data: Dict[str, Any],
                       fields_to_check: List[str] = None) -> bool:
        """
        检查是否存在不一致（便捷方法）

        Returns:
            是否存在不一致
        """
        discrepancies = self.check_fields(excel_row, api_data, fields_to_check)
        return len(discrepancies) > 0

    def get_corrections(self, excel_row: Dict[str, Any], api_data: Dict[str, Any],
                       fields_to_check: List[str] = None) -> Dict[str, Any]:
        """
        获取需要修正的字段（从API值）

        Returns:
            修正字典 {字段名: API值}
        """
        corrections = {}
        discrepancies = self.check_fields(excel_row, api_data, fields_to_check)

        for disc in discrepancies:
            field = disc['field']
            api_value = disc['api_value']
            corrections[field] = api_value

        return corrections

    @staticmethod
    def _normalize_value(value: Any) -> str:
        """
        规范化值用于比较

        Returns:
            去除前后空白的字符串
        """
        if value is None:
            return ''
        return str(value).strip()

# 便捷函数
def create_consistency_checker(processor: ExcelProcessor, product_model: str = None) -> ConsistencyChecker:
    """创建一致性检查器实例"""
    return ConsistencyChecker(processor, product_model)
