"""
IMAP指标查询主模块(智能版) - v2.0
自动识别用户意图,直接返回查询结果
"""

import os
import re
import json
import logging
from typing import Dict, Any, List, Optional, Tuple
from datetime import datetime, timedelta

import requests
import time

# 配置日志
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# 导入真实API模块(如果可用,确保模块在同一个目录)
try:
    # 使用绝对导入,假设脚本所在目录在Python路径中
    from query_indicator_list import query_indicator_list, fuzzy_find_indicator
    from query_name_id import query_name_to_id, extract_dimension_info
    from query_indicator_data import query_indicator_data
    from auth import AuthManager
    from query_user_auth import query_user_auth as _query_user_auth_api, get_user_auth_types as _get_user_auth_types_api
    from query_user_info import query_user_info as _query_user_info_api, extract_user_org_info as _extract_user_org_info_api
    from query_by_url import query_by_url
    from query_indicator_detail_by_url import query_indicator_detail_data_by_url
    REAL_API_AVAILABLE = True
except ImportError as e:
    logger.warning(f"部分API模块导入失败: {e},将使用模拟数据")
    REAL_API_AVAILABLE = False
    # 给未导入成功的符号补默认值,避免后续包装函数 NameError
    _query_user_auth_api = None
    _get_user_auth_types_api = None
    _query_user_info_api = None
    _extract_user_org_info_api = None
    query_by_url = None
    query_indicator_detail_data_by_url = None


class ImapQueryOrchestrator:
    """IMAP查询编排器 - 智能路由用户请求"""

    # 意图识别模式（顺序敏感：更具体的意图放前面）
    INTENT_PATTERNS = {
        'milestone_retrospective': [
            # 原始精确模式
            r'获取.*里程碑.*复盘.*报告.*数据',
            r'里程碑.*复盘.*报告.*数据',
            r'复盘.*报告.*数据',
            r'里程碑.*数据.*获取',
            r'里程碑.*复盘.*数据',
            # 新增：更广泛的报告数据获取表达（应对用户提及"报告数据获取"类似表述）
            r'获取.*报告.*数据',          # 获取报告数据
            r'报告.*数据.*获取',          # 报告数据获取
            r'报告数据获取',              # 精确匹配
            r'报告.*数据',                # 报告数据
            r'数据.*报告',                # 数据报告
            r'复盘报告',                  # 复盘报告
            r'里程碑报告',                # 里程碑报告
            r'项目复盘',                  # 项目复盘
            r'阶段总结',                  # 阶段总结
            r'复盘.*数据',                # 复盘数据
            r'里程碑.*复盘',              # 里程碑复盘
            r'里程碑.*报告.*数据',        # 里程碑报告数据
        ],
        'list_indicators': [
            r'列出.*指标',
            r'查看.*指标.*列表',
            r'所有.*指标',
            r'指标.*列表',
            r'有哪些指标',
            r'IMAP.*指标',
            r'指示器.*列表',
            r'获取.*指标'
        ],
        'list_capabilities': [
            r'查询.*imap.*能力',
            r'imap.*有哪些能力',
            r'imap.*功能',
            r'imap.*能做什么',
            r'查询.*技能.*功能'
        ],
        'query_indicator': [
            r'查询.*(?:MR交付周期|需求交付|产出|缺陷|代码量)',
            r'查.*(?:ZXUFP|BVB|FM开发)',
            r'获取.*(?:项目组|部门|中心)的.*数据',
            r'统计.*(?:交付周期|速率|数量)',
            r'(\w+的)?(MR交付周期|需求交付速率|.*指标)'
        ],
        'query_auth': [
            r'我的.*权限',
            r'查看.*权限',
            r'有哪些.*权限',
            r'权限.*列表',
            r'能访问.*哪些'
        ],
        'query_user_info': [
            r'我的.*信息',
            r'查询.*用户信息',
            r'查询.*我的.*信息',
            r'查询.*imap.*信息',
            r'我的.*部门',
            r'我的.*项目组',
            r'我是.*哪个部门'
        ],
        'query_by_url': [
            r'https://aip\.zte\.com\.cn/.*',
            r'dashboard.*url',
            r'通过.*链接.*查询',
            r'URL.*取数'
        ],
        'query_indicator_detail_by_url': [
            r'详单.*数据',
            r'单指示器.*详单',
            r'下钻.*数据',
            r'获取.*明细',
            r'查询.*详单',
            r'查看.*明细',
            r'指标.*明细',
            r'column.*name',
            r'single_indicator_url'
        ],
        'query_indicator_detail': [
            r'导出.*详单',
            r'导出.*excel',
            r'获取.*详单',
            r'下载.*详单',
            r'导出.*数据'
        ],
        'query_all_detail': [
            r'\|\s*指标详单数据\s*\|',
            r'指标详单数据',
            r'all_api.*指标',
            r'全量.*详单',
            r'导出.*全量.*数据'
        ]
    }

    def __init__(self, empno=None, token=None):
        self.empno = empno or os.getenv('coclaw_empno')
        self.token = token or os.getenv('coclaw_token')
        self._yxdata_token = None  # 惰性获取，避免不必要的认证

    def _split_y_axis(self, y_axis_value: Optional[str]) -> list:
        """把接口1返回的 '图表y轴' 字符串拆成 series_fields 列表"""
        if not y_axis_value:
            return []
        # 可能是"平均数,中位数"或"平均数,中位数"
        parts = [p.strip() for p in str(y_axis_value).replace(",", ",").split(",")]
        return [p for p in parts if p]

    def _map_chart_type(self, chinese_type: Optional[str]) -> str:
        """中文图表类型映射(用于前端图表组件)"""
        mapping = {
            '柱状图': 'bar',
            '折线图': 'line',
            '饼图': 'pie',
            '表格': 'table',
            '散点图': 'scatter'
        }
        if not chinese_type:
            return 'bar'
        return mapping.get(chinese_type, 'bar')

    def _allowed_auth_types(self, query_type: str) -> list:
        """根据用户请求的资源类型,推导接口2里可能的 auth_type 集合"""
        # 注:接口2示例里"FM产品研发中心"属于"分中心级",但接口4/用户视角可能叫"中心"
        mapping = {
            '项目组': ['项目组级'],
            '部门': ['部门级'],
            '中心': ['分中心级', '中心级'],
            '院': ['院级'],
            '项目': ['项目级'],
        }
        return mapping.get(query_type, [])

    def _extract_target_object(self, name: str) -> str:
        """从指标名称提取目标对象(MR/PR/US等)"""
        name_lower = name.lower()
        # 按优先级匹配
        mapping_list = [
            ('us', 'US'),
            ('mr', 'MR'),
            ('pr', 'PR'),
            ('rr', 'RR'),
            ('版本', '版本'),
            ('单板', '单板'),
            ('硬件', '硬件'),
            ('人力', '人力'),
            ('代码', '代码'),
            ('缺陷', '缺陷'),
            ('故障', '故障'),
            ('需求', '需求')
        ]
        for key, val in mapping_list:
            if key in name_lower:
                return val
        return "其他"

    def _infer_indicator_type(self, name: str, description: str = "") -> List[str]:
        """推断指标类型标签(交付周期/吞吐量/缺陷/比率等)"""
        types = []
        name_lower = name.lower()
        desc_lower = description.lower() if description else ""
        text = name_lower + " " + desc_lower
        
        if any(kw in text for kw in ['交付周期', '周期', '周转']):
            types.append('交付周期')
        if any(kw in text for kw in ['吞吐量', '吞吐率', '吞吐', '产出']):
            types.append('吞吐量')
        if any(kw in text for kw in ['缺陷', '泄露', '故障', '问题']):
            types.append('缺陷')
        if '完成度' in text or '完成率' in text:
            types.append('完成度')
        if any(kw in text for kw in ['率', '占比', '达成率', '合格率', '成功率', '比例', '比率']):
            types.append('比率')
        if '代码' in text or '千行' in text:
            types.append('代码')
        if '人力' in text or '人月' in text:
            types.append('人力')
        if '开发' in text and '周期' in text:
            types.append('开发周期')
        if '测试' in text and '周期' in text:
            types.append('测试周期')
        if '分析' in text and '周期' in text:
            types.append('分析周期')
        if not types:
            types.append('其他')
        return types

    def process_query(self, user_query: str):
        logger.info(f"处理查询: {user_query}")

        # v2.0 优化：先检查是否为标准格式输入（里程碑复盘报告数据）
        # 严格匹配标准格式，跳过意图识别
        standard_data = self._parse_standard_input_strict(user_query)
        if standard_data:
            logger.info("检测到标准格式输入，直接调用里程碑复盘API")
            try:
                result = self._call_milestone_retrospective_api(standard_data)
                # 优化：成功时只返回 API 的 result 部分完整内容
                if result.get('status') and 'data' in result:
                    return result['data']
                else:
                    # 失败时保留错误结构，添加 _meta 便于调试
                    result['_meta'] = {
                        'intent': 'milestone_retrospective_std',
                        'query': user_query,
                        'timestamp': datetime.now().isoformat(),
                        'processor': 'imap-orchestrator-v2-std'
                    }
                    return result
            except Exception as e:
                logger.error(f"标准格式调用失败: {e}")
                # 失败时回退到意图识别

        # 常规意图识别
        intent, params = self._recognize_intent(user_query)
        logger.info(f"识别意图: {intent}, 参数: {params}")

        handlers = {
            'list_indicators': self._handle_list_indicators,
            'query_indicator': self._handle_query_indicator,
            'milestone_retrospective': self._handle_milestone_retrospective,
            'query_auth': self._handle_query_auth,
            'query_user_info': self._handle_query_user_info,
            'query_by_url': self._handle_query_by_url,
            'list_capabilities': self._handle_list_capabilities,
            'unknown': self._handle_unknown,
            'query_indicator_detail_by_url': self._handle_query_indicator_detail_by_url,
            'query_all_detail': self._handle_query_all_detail
        }

        handler = handlers.get(intent, self._handle_unknown)
        result = handler(user_query, params)

        result['_meta'] = {
            'intent': intent,
            'query': user_query,
            'timestamp': datetime.now().isoformat(),
            'processor': 'imap-orchestrator-v2'
        }

        return result

    def _recognize_intent(self, query: str):
        """识别用户意图"""
        query_lower = query.lower().strip()

        # 检查URL直连
        if 'http://' in query or 'https://' in query:
            return 'query_by_url', {'url': query}

        # 匹配意图模式
        for intent, patterns in self.INTENT_PATTERNS.items():
            for pattern in patterns:
                if re.search(pattern, query, re.IGNORECASE):
                    params = self._extract_parameters(query, intent)
                    return intent, params

        # 默认尝试作为指标查询
        return 'query_indicator', self._extract_parameters(query, 'query_indicator')

    def _get_auth_tokens(self):
        """获取认证令牌,返回 (yxdata_token, AuthManager) 或 (None, None)"""
        if not self.token:
            return None, None
        try:
            auth = AuthManager()
            yxdata_token = auth.get_token(self.empno, self.token)
            return yxdata_token, auth
        except Exception as e:
            logger.error(f"认证失败: {e}")
            return None, None

    def _infer_chart_type(self, indicator_name: str) -> str:
        """根据指标名称推断图表类型"""
        if any(kw in indicator_name for kw in ['速率', '趋势', '走势', '变化']):
            return "折线图"
        return "柱状图"
    
    def _match_detail_field(self, indicator_name: str, available_fields: List[str]) -> Optional[str]:
        """从可用字段中匹配最相关的详单字段
        
        Args:
            indicator_name: 指标名称（如"MR交付周期（天）"）
            available_fields: 详单信息中的字段名列表
            
        Returns:
            匹配的字段名，未匹配返回None
        """
        if not available_fields:
            return None
        
        # 清理指标名称，移除括号和特殊字符
        clean_name = re.sub(r'[（()）]', '', indicator_name)
        clean_name = clean_name.replace(' ', '').lower()
        
        # 得分映射
        scores = {}
        for field in available_fields:
            score = 0
            field_lower = field.lower()
            
            # 1. 完全包含匹配
            if clean_name in field_lower or field_lower in clean_name:
                score += 100
            
            # 2. 核心关键词匹配
            keywords = ['mr', 'pr', 'us', '交付周期', '吞吐量', '缺陷', '关闭', '发现', '完成', '人数', '代码']
            for kw in keywords:
                if kw in clean_name and kw in field_lower:
                    score += 50
            
            # 3. 对象类型匹配
            if 'mr' in clean_name and 'mr' in field_lower:
                score += 30
            if 'pr' in clean_name and 'pr' in field_lower:
                score += 30
            if '缺陷' in clean_name and ('缺陷' in field_lower or '问题' in field_lower):
                score += 30
            
            # 4. 长度惩罚（避免过长字段）
            if len(field) > 50:
                score -= 20
            
            scores[field] = score
        
        # 取最高分
        if not scores:
            return None
        best = max(scores.items(), key=lambda x: x[1])
        return best[0] if best[1] > 0 else None

    def _detect_x_axis(self, content: list) -> str:
        """自动识别X轴字段(时间/周期类)"""
        if not content:
            return ""
        sample = content[0]
        time_candidates = ['周期', '时间', '日期', '月份', '年份', 'year', 'month', 'date']
        for candidate in time_candidates:
            if candidate in sample:
                return candidate
        # 尝试找看起来像日期的字段
        for key, value in sample.items():
            if isinstance(value, str) and ('-' in value or '年' in value or '月' in value):
                return key
        return list(sample.keys())[0] if sample else ""

    def _detect_series_fields(self, content: list) -> list:
        """自动识别数值系列字段"""
        if not content:
            return []
        sample = content[0]
        x_axis = self._detect_x_axis(content)
        numeric_fields = []
        for key, value in sample.items():
            if key == x_axis:
                continue
            if isinstance(value, (int, float)):
                numeric_fields.append(key)
            elif isinstance(value, str) and value.replace('.', '').isdigit():
                numeric_fields.append(key)
        # 如果没有识别到,返回除x轴外的所有字段
        return numeric_fields if numeric_fields else [k for k in sample.keys() if k != x_axis]

    def _detect_y_axis(self, content: list) -> str:
        """生成Y轴描述"""
        series_fields = self._detect_series_fields(content)
        return ",".join(series_fields) if series_fields else "数值"

    def _extract_parameters(self, query: str, intent: str):
        """从查询中提取参数"""
        params = {}

        if intent == 'query_indicator':
            # 提取指标名称
            # 优化：优先匹配更长的、更具体的指标名称（避免'缺陷'过早匹配）
            indicator_keywords = [
                '系统测试缺陷关闭占比', '系统测试自动化发现缺陷占比',
                'MR交付周期', 'MR人均分析吞吐率', 'MR人均开发吞吐率', 'MR人均测试吞吐率',
                'MR分析周期', 'MR吞吐量', 'MR实现周期', 'MR开发周期', 'MR测试周期',
                'PR交付周期', 'PR吞吐量', 'PR开发周期', 'PR分析周期',
                'RR按期达成率', 'US吞吐量', 'US故事点吞吐量',
                '人均MR吞吐率', '人均PR吞吐率', '人均US吞吐率', '人均US故事点吞吐率',
                '代码优选率', '位号优选率', '关键故障业务恢复时长', '关键故障软件定位时长',
                '单板一年批量商用率', '单板一版成功率', '单板半年批量商用率', '单板直通率的达标率',
                '吞吐量_MR/PR/US', '商用发布版本的补丁占比', '外场关键故障数', '外场故障数',
                '开发人均代码行', '开发人均泄露缺陷数', '开发千行代码缺陷数',
                '异常出厂比例', '整机直通率的达标率', '方案引入缺陷率', '标模调用率',
                '测试策略原因导致的外场故障数', '测试设计原因导致的外场故障数',
                '版本交付周期', '版本发布1个月前关闭缺陷占比', '版本发布1个月前发现缺陷占比',
                '硬件交付周期', '硬件开发周期', '硬件故障定位时长', '硬件转产周期',
                '系统测试周期', '系统测试缺陷关闭占比', '系统测试自动化发现缺陷占比',
                '缺陷平均解决周期', '缺陷CCB审核周期',
                '补丁数', '里程碑基准计划达成率', '需求分析引入缺陷率', '研究院内非外包人力',
                '已规划MR-折算0.1', '已开发MR-折算0.5', '已验收MR-折算0.3', '可交付MR-折算0.1',
                'AI需求分析评审问题采纳率', 'AI需求分析占比', 'AI需求分析内容采纳率',
                'AI需求分析生成率', 'AI文本测试用例生成率', 'AI系统测试文本测试用例生成率',
                '需求交付', '代码产出', '代码量', '交付速率', '交付周期', '产出量', '完成率'
            ]
            for kw in indicator_keywords:
                if kw in query:
                    params['indicator_name'] = kw
                    break
            else:
                params['indicator_name'] = self._fuzzy_match_indicator(query)

            # 提取查询类型和名称
            # 优先基于已知实体列表精确匹配,提高鲁棒性
            known_project_groups = ['ZXUFP', 'BVB', '有线效能工具', 'VBP', 'VBS', 'ZXSUP', 'ZXROSNG', 'ZXA10 C6xx', 'ZXA10 C3xx', 'ZXMP M721', 'ZXUCP', 'ZXONE 7000']
            known_departments = ['FM开发二部', '软件平台开发六部', '有线研究院']
            known_centers = ['FM产品研发中心', '软件平台研发中心']

            type_set = False
            # 项目组
            for pg in known_project_groups:
                if pg in query:
                    params['query_type'] = '项目组'
                    params['query_name'] = pg
                    type_set = True
                    break
            # 部门
            if not type_set:
                for dept in known_departments:
                    if dept in query:
                        params['query_type'] = '部门'
                        params['query_name'] = dept
                        type_set = True
                        break
            # 中心
            if not type_set:
                for center in known_centers:
                    if center in query:
                        params['query_type'] = '中心'
                        params['query_name'] = center
                        type_set = True
                        break

            # 如果没有匹配到精确实体,回退到关键词提取
            if not type_set:
                if '项目组' in query:
                    params['query_type'] = '项目组'
                    for pg in known_project_groups:
                        if pg in query:
                            params['query_name'] = pg
                            break
                    else:
                        params['query_name'] = self._extract_name_after(query, ['项目组', '项目'])
                elif '项目' in query:
                    params['query_type'] = '项目'
                    params['query_name'] = self._extract_name_after(query, ['项目'])
                elif '部门' in query:
                    params['query_type'] = '部门'
                    for dept in known_departments:
                        if dept in query:
                            params['query_name'] = dept
                            break
                    else:
                        params['query_name'] = self._extract_name_after(query, ['部门'])
                elif '中心' in query or '分中心' in query:
                    params['query_type'] = '中心'
                    for center in known_centers:
                        if center in query:
                            params['query_name'] = center
                            break
                    else:
                        params['query_name'] = self._extract_name_after(query, ['中心', '分中心'])
                elif '院' in query:
                    params['query_type'] = '院'
                    params['query_name'] = self._extract_name_after(query, ['院'])

            # 提取时间范围
            params.update(self._extract_time_range(query))

        elif intent == 'list_indicators':
            # 允许用户在"列出/查看指标"时带上关键词做筛选
            indicator_keywords = [
                'MR交付周期', '需求交付', '代码产出', '缺陷', '代码量',
                '交付速率', '交付周期', '产出量', '完成率'
            ]
            for kw in indicator_keywords:
                if kw in query:
                    params['indicator_keyword'] = kw
                    break
            else:
                params['indicator_keyword'] = ''
        
        elif intent == 'milestone_retrospective':
            # 里程碑复盘报告数据获取 - 参数将在_handle_milestone_retrospective中解析
            params['requires_standard_input'] = True
        
        elif intent in ['query_auth', 'query_user_info']:
            params['include_details'] = True
        
        return params

    def _fuzzy_match_indicator(self, query: str) -> str:
        """模糊匹配指标名称"""
        if '交付周期' in query:
            return "MR交付周期(天)"
        elif '交付速率' in query or '速率' in query:
            return "需求交付速率"
        elif '产出' in query or '代码' in query:
            return "代码产出量"
        elif '缺陷' in query:
            return "缺陷密度"
        return ""

    def _extract_name_after(self, query: str, keywords: list) -> str:
        """提取关键词后面的名称"""
        for kw in keywords:
            # 捕获直到遇到空白/常见标点为止
            pattern = rf'{kw}[::]?\s*([^\s,,。;;::]+)'
            match = re.search(pattern, query)
            if match:
                return match.group(1)
        return ""

    def _extract_time_range(self, query: str):
        """提取时间范围"""
        result = {}

        # 月份范围
        month_match = re.search(r'(\d{4})年(\d{1,2})月\s*[到至]\s*(\d{1,2})月', query)
        if month_match:
            year = month_match.group(1)
            start_month = month_match.group(2).zfill(2)
            end_month = month_match.group(3).zfill(2)
            result['start_date'] = f"{year}-{start_month}-01"
            result['end_date'] = f"{year}-{end_month}-31"
            return result

        # 最近N个月
        recent_match = re.search(r'最近(\d+)个月', query)
        if recent_match:
            months = int(recent_match.group(1))
            end_date = datetime.now()
            start_date = end_date - timedelta(days=30 * months)
            result['start_date'] = start_date.strftime('%Y-%m-%d')
            result['end_date'] = end_date.strftime('%Y-%m-%d')
            return result

        # 具体日期
        dates = re.findall(r'\d{4}-\d{2}-\d{2}', query)
        if len(dates) >= 2:
            result['start_date'] = dates[0]
            result['end_date'] = dates[1]
        elif len(dates) == 1:
            result['start_date'] = dates[0]

        return result

    def _handle_list_indicators(self, query: str, params: Dict):
        """处理列出所有指标"""
        indicator_keyword = params.get('indicator_keyword', '')
        # 尝试使用真实API获取全部指标列表
        if REAL_API_AVAILABLE:
            try:
                yxdata_token, _ = self._get_auth_tokens()
                if not yxdata_token:
                    raise Exception("未获取到有效认证令牌")
                result = query_indicator_list(indicator_keyword, yxdata_token, self.token, self.empno)

                if result.get('status'):
                    indicators_raw = result.get('data', {}).get('content', [])
                    indicators = []
                    for ind in indicators_raw:
                        name = ind.get('指示器名称', '')
                        desc = ind.get('指示器描述') or ind.get('描述', '')
                        # 尝试从API获取图表类型,或使用推断
                        chart_type = ind.get('图表类型') or ind.get('chart_type') or self._infer_chart_type(name)
                        indicators.append({
                            "name": name,
                            "description": desc,
                            "chart_type": chart_type
                        })
                    return {
                        "status": True,
                        "type": "indicator_list",
                        "total": len(indicators),
                        "indicators": indicators,
                        "message": f"共找到 {len(indicators)} 个指标"
                    }
            except Exception as e:
                logger.warning(f"实时查询指标列表失败,回退到示例数据: {e}")

        # 回退示例数据
        return {
            "status": True,
            "type": "indicator_list",
            "total": 87,
            "indicators": [
                {"name": "MR交付周期(天)", "description": "项目组按月统计MR需求交付周期", "chart_type": "柱状图"},
                {"name": "需求交付速率", "description": "统计需求交付速度", "chart_type": "折线图"}
            ],
            "message": "认证失败或未配置token,显示示例数据(共87个指标)"
        }

    def _parse_response_page(self, result: Any) -> List[Any]:
        """解析API单页响应数据（支持多种返回格式）"""
        page_data = []
        if isinstance(result, dict):
            if result.get('status') is True or result.get('code') == 200:
                payload = result.get('data', result.get('content', []))
                if isinstance(payload, dict) and 'content' in payload:
                    page_data = payload['content']
                elif isinstance(payload, list):
                    page_data = payload
            elif 'column' in result and 'row' in result:
                columns = result['column']
                rows = result['row']
                if isinstance(columns, list) and isinstance(rows, list):
                    page_data = [dict(zip(columns, row)) for row in rows if isinstance(row, list)]
            elif 'content' in result:
                content = result['content']
                if isinstance(content, list):
                    page_data = content
        elif isinstance(result, list):
            page_data = result
        return page_data

    def _fetch_paginated_data(self, api_url: str, base_params: Dict[str, Any], headers: Dict[str, str], page_size: int = 5000) -> List[Any]:
        """自动翻页获取全量数据（从支持分页的API）"""
        import time
        all_data = []
        total = None
        page = 1
        
        while True:
            params = base_params.copy()
            params['pageSize'] = str(page_size)
            params['page'] = str(page)
            
            try:
                resp = requests.get(api_url, params=params, headers=headers, timeout=60)
                resp.raise_for_status()
                result = resp.json()
                
                page_data = self._parse_response_page(result)
                all_data.extend(page_data)
                
                # 尝试获取总数
                if total is None:
                    if isinstance(result, dict):
                        if 'total' in result:
                            total = result['total']
                        elif 'data' in result and isinstance(result['data'], dict) and 'total' in result['data']:
                            total = result['data']['total']
                
                # 判断是否继续翻页
                if total is not None and len(all_data) >= total:
                    break
                if len(page_data) < page_size:
                    break
                page += 1
                time.sleep(0.2)
            except Exception as e:
                logger.warning(f"第{page}页失败: {e}")
                break
        
        return all_data

    def _fetch_detail_data(self, detail_config: Dict, indicator_name: str, indicator_type: Any, y_axis_meta: str, 
                          dimension_param: str, dimension_id: str, yxdata_token: str = None, auth_token: str = None, empno: str = None, export_excel: bool = False) -> Tuple[Dict, Dict]:
        """获取详单数据并可选导出Excel
        
        Returns:
            (detail_data_dict, excel_export_info)
        """
        detail_data = {}
        excel_info = None
        
        # 确定分子分母或主字段
        numerator_name = None
        denominator_name = None
        main_field = None
        
        # 规范化指标类型字符串
        if isinstance(indicator_type, list):
            indicator_type_str = ' '.join(indicator_type)
        else:
            indicator_type_str = str(indicator_type) if indicator_type else ''
        
        # 率值类判断
        is_ratio = False
        if '率值' in indicator_type_str or '占比' in indicator_name or '率' in indicator_name:
            is_ratio = True
            # 尝试从Y轴字段解析
            y_fields = [f.strip() for f in y_axis_meta.split(',')] if y_axis_meta else []
            if len(y_fields) >= 2:
                numerator_name = y_fields[0]
                denominator_name = y_fields[1]
            else:
                # 智能识别
                keys = list(detail_config.keys())
                for key in keys:
                    if any(kw in key for kw in ['关闭', '完成', '达标', '交付']):
                        numerator_name = key
                        break
                for key in keys:
                    if any(kw in key for kw in ['发现', '新增', '总数', '需求']):
                        if key != numerator_name:
                            denominator_name = key
                            break
                # 如果仍未找到，尝试查找人力、人数相关字段
                if not denominator_name:
                    for key in keys:
                        if any(kw in key for kw in ['人力', '人数', '人月', '人员', 'headcount']):
                            if key != numerator_name:
                                denominator_name = key
                                break
        else:
            # 数量类/周期类：匹配指标名称
            main_field = self._match_detail_field(indicator_name, list(detail_config.keys()))
        
        # 获取详单数据
        def fetch_one(field_name: str) -> Optional[Dict]:
            info = detail_config.get(field_name)
            if not info or not isinstance(info, dict):
                return None
            api_url = info.get('all_api') or info.get('association_api')
            if not api_url:
                return None
            try:
                # 构造请求头
                headers = {}
                if yxdata_token:
                    headers['yxdata-token'] = yxdata_token
                if auth_token:
                    headers['X-Auth-Value'] = auth_token
                if empno:
                    headers['X-Emp-No'] = empno
                
                # 使用统一分页方法获取全量数据
                all_data = self._fetch_paginated_data(api_url, {dimension_param: dimension_id}, headers)
                
                return {
                    'field': field_name,
                    'url': f"{api_url}?{dimension_param}={dimension_id}",
                    'count': len(all_data),
                    'data': all_data
                }
            except Exception as e:
                logger.error(f"详单获取失败[{field_name}]: {e}")
                return None
        
        if is_ratio:
            if numerator_name:
                num = fetch_one(numerator_name)
                if num:
                    detail_data['numerator'] = num
            if denominator_name:
                den = fetch_one(denominator_name)
                if den:
                    detail_data['denominator'] = den
        else:
            if main_field:
                main = fetch_one(main_field)
                if main:
                    detail_data['main'] = main
        
        # 导出数据为多个 CSV 文件（不依赖 pandas）
        if detail_data and export_excel:
            try:
                from datetime import datetime
                import csv
                
                safe_name = re.sub(r'[^\w\u4e00-\u9fa5]', '_', indicator_name)[:50]
                base_filename = f"{safe_name}_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
                # 导出到 workspace 根目录（统一使用用户目录）
                workspace_dir = os.path.join(os.path.expanduser('~'), '.openclaw', 'workspace')
                os.makedirs(workspace_dir, exist_ok=True)
                base_path = os.path.join(workspace_dir, base_filename)
                
                exported_files = []
                
                # 为每个详单字段导出独立 CSV
                for key, val in detail_data.items():
                    if val and val.get('data'):
                        data_rows = val['data']
                        if not data_rows:
                            continue
                        # 尝试确定列名
                        if isinstance(data_rows[0], dict):
                            columns = list(data_rows[0].keys())
                            rows = [list(row.values()) for row in data_rows]
                        else:
                            columns = [f'列{i+1}' for i in range(len(data_rows[0]))]
                            rows = data_rows
                        csv_file = f"{base_path}_{key}.csv"
                        with open(csv_file, 'w', newline='', encoding='utf-8-sig') as f:
                            writer = csv.writer(f)
                            writer.writerow(columns)
                            for row in rows:
                                writer.writerow([str(item) if item is not None else '' for item in row])
                        exported_files.append(csv_file)
                
                # 导出汇总 info CSV
                summary = [{
                    '类型': k,
                    '字段': v.get('field', ''),
                    '记录数': v.get('count', len(v.get('data', [])) if v else 0),
                    'URL': v.get('url', '')
                } for k, v in detail_data.items() if v]
                if summary:
                    info_file = f"{base_path}_info.csv"
                    with open(info_file, 'w', newline='', encoding='utf-8-sig') as f:
                        if summary:
                            writer = csv.DictWriter(f, fieldnames=summary[0].keys())
                            writer.writeheader()
                            writer.writerows(summary)
                    exported_files.append(info_file)
                
                excel_info = {
                    'available': True,
                    'filename': base_filename,
                    'exported_files': exported_files,
                    'count': len(exported_files)
                }
            except Exception as e:
                excel_info = {'available': False, 'reason': str(e)}
        
        return detail_data, excel_info

    def _handle_query_indicator(self, query: str, params: Dict):
        """处理指标查询"""
        missing = []
        if not params.get('indicator_name'):
            missing.append('指标名称')
        if not params.get('query_type'):
            missing.append('查询类型')
        if not params.get('query_name'):
            missing.append('查询条件')

        if missing:
            return {
                "status": False,
                "error": f"缺少必要参数: {', '.join(missing)}",
                "suggestion": "请提供完整的查询描述,例如:'查询ZXUFP项目组的MR交付周期'"
            }
        
        # 是否自动获取详单数据（用户未明确，默认False；可通过参数控制）
        auto_fetch_detail = params.get('auto_fetch_detail', False)
        export_excel = params.get('export_excel', False)

        # 尝试使用真实API查询(接口2权限校验 -> 接口1取指标元信息 -> 接口4名称转ID -> 接口5取数)
        if REAL_API_AVAILABLE:
            try:
                yxdata_token, _ = self._get_auth_tokens()
                if not yxdata_token:
                    raise Exception("缺少可用的 yxdata-token")

                # 1) 接口2:权限校验
                auth_data = _query_user_auth_api(self.empno, yxdata_token, self.token)
                if not auth_data.get('status'):
                    raise Exception(f"查询用户权限失败: {auth_data.get('error', '未知错误')}")

                auth_content = auth_data.get('data', {}).get('content', [])
                allowed_types = self._allowed_auth_types(params['query_type'])
                has_permission = False
                for item in auth_content:
                    if item.get('key_name') == params['query_name'] and item.get('auth_type') in allowed_types:
                        has_permission = True
                        break

                if not has_permission:
                    return {
                        "status": False,
                        "error": f"用户无权访问 {params['query_type']}:{params['query_name']}",
                        "suggestion": "请检查您的权限配置或联系系统管理员"
                    }

                # 2) 接口1:根据指示器标题查询元信息(含api_接口地址、图表配置字段)
                indicator_keyword = params['indicator_name']
                indicator_list_result = query_indicator_list(indicator_keyword, yxdata_token, self.token, self.empno)
                if not indicator_list_result.get('status'):
                    raise Exception(f"查询指标列表失败: {indicator_list_result.get('error', '未知错误')}")

                indicators = indicator_list_result.get('data', {}).get('content', [])
                if not indicators:
                    raise Exception(f"未找到名为'{params['indicator_name']}'的指标")

                # 指示器精确优先;否则按"包含匹配度"选最优
                indicator = None
                for ind in indicators:
                    if ind.get('指示器名称') == params['indicator_name']:
                        indicator = ind
                        break
                if not indicator:
                    # 指示器匹配打分:精确命中更高分,包含命中次之
                    def score(ind: Dict[str, Any]) -> int:
                        name = ind.get('指示器名称', '')
                        if name == params['indicator_name']:
                            return 1000
                        if params['indicator_name'] and params['indicator_name'] in name:
                            return 100
                        return 0
                    indicator = max(indicators, key=score)

                api_url = indicator.get('api_接口地址')
                if not api_url:
                    raise Exception("指标缺少API地址")

                # ✅ 从接口1返回中直接读取三个字段（data_analysis_indicator_config表）
                # 1. 指标类型：优先使用接口返回，否则回退到推断
                raw_indicator_type = indicator.get('指标类型') or indicator.get('indicator_type')
                if not raw_indicator_type:
                    raw_indicator_type = self._infer_indicator_type(
                        indicator.get('指示器名称', ''),
                        indicator.get('指示器描述', '')
                    )
                # 2. 指向性：直接读取接口返回（应为结构化对象）
                raw_directivity = indicator.get('指向性') or indicator.get('directivity')
                # 3. 详单信息：直接读取接口返回
                raw_detail_info = indicator.get('详单信息') or indicator.get('detail_info')
                
                chart_type_cn = indicator.get('图表类型') or indicator.get('chart_type') or self._infer_chart_type(indicator.get('指示器名称', ''))
                chart_type = self._map_chart_type(chart_type_cn)
                x_axis_meta = indicator.get('图表x轴', '')
                series_fields = self._split_y_axis(indicator.get('图表y轴'))
                y_axis_meta = indicator.get('图表y轴') or indicator.get('api_返回字段') or ''

                # 3) 接口4:名称转ID(type/name -> param/id)
                dim_result = query_name_to_id(params['query_type'], params['query_name'], yxdata_token, self.token, self.empno)
                if not dim_result.get('status'):
                    raise Exception(f"获取维度ID失败: {dim_result.get('error', '未知错误')}")
                dim_info = extract_dimension_info(dim_result)
                if not dim_info:
                    raise Exception("维度信息解析失败")

                dimension_id = dim_info['id']
                dimension_param = dim_info['param']

                # 4) 接口5:查询指标详单数据
                data_result = query_indicator_data(
                    api_url,
                    dimension_param,
                    dimension_id,
                    yxdata_token,
                    self.token,
                    self.empno,
                    params.get('start_date'),
                    params.get('end_date')
                )
                if not data_result.get('status'):
                    raise Exception(f"查询指标数据失败: {data_result.get('error', '未知错误')}")

                raw_data = data_result.get('data', {})
                content = raw_data.get('content', [])

                # 5) 图表结构生成:优先使用接口1元数据的x/y,否则回退检测
                x_field = x_axis_meta or self._detect_x_axis(content)
                if not series_fields:
                    series_fields = self._detect_series_fields(content)
                if not y_axis_meta:
                    y_axis_meta = self._detect_y_axis(content)

                # 计算指标类型和目标对象
                indicator_type = self._infer_indicator_type(
                    indicator.get('指示器名称', ''),
                    indicator.get('指示器描述', '')
                )
                target_object = self._extract_target_object(indicator.get('指示er名称', ''))
                
                # ========== 新增：自动获取详单数据（使用当前指标的详单配置） ==========
                detail_data = {}
                excel_info = None
                if raw_detail_info:
                    logger.info(f"检测到指标'{indicator.get('指示器名称')}'的详单配置，自动获取详单数据...")
                    try:
                        # 解析 raw_detail_info（可能是字符串或字典）
                        detail_config = None
                        if isinstance(raw_detail_info, dict):
                            detail_config = raw_detail_info
                        elif isinstance(raw_detail_info, str):
                            try:
                                detail_config = json.loads(raw_detail_info)
                            except json.JSONDecodeError as e:
                                logger.warning(f"详单配置JSON解析失败: {e}")
                                detail_config = None
                        
                        if detail_config:
                            detail_data, excel_info = self._fetch_detail_data(
                                detail_config=detail_config,
                                indicator_name=indicator.get('指示器名称', ''),
                                indicator_type=raw_indicator_type or indicator_type,
                                y_axis_meta=y_axis_meta,
                                dimension_param=dimension_param,
                                dimension_id=dimension_id,
                                yxdata_token=yxdata_token,
                                auth_token=self.token,
                                empno=self.empno,
                                export_excel=True
                            )
                            logger.info(f"详单数据获取完成: {len(detail_data)} 个字段")
                        else:
                            logger.warning("详单配置为空或格式错误，跳过详单获取")
                    except Exception as e:
                        logger.warning(f"自动获取详单数据失败: {e}")
                        detail_data = {}
                        excel_info = None
                else:
                    logger.info(f"指标'{indicator.get('指示器名称')}'未配置详单信息，跳过详单获取")
                # ========== 新增结束 ==========
                
                return {
                    "status": True,
                    "type": "indicator_data",
                    "indicator": {
                        "name": indicator.get('指示器名称'),
                        "description": indicator.get('指示器描述'),
                        "chart_type": chart_type_cn,
                        # ✅ 使用从接口返回的字段（已做回退处理）
                        "indicator_type": raw_indicator_type,
                        "directivity": raw_directivity,
                        "detail_info": raw_detail_info,
                        "target_object": target_object,
                        "x_axis": x_field,
                        "y_axis": y_axis_meta,
                        "api_url": api_url,
                        "raw_metadata": {
                            "指示器名称": indicator.get('指示器名称'),
                            "指示器描述": indicator.get('指示器描述'),
                            "api_接口地址": api_url,
                            "图表类型": chart_type_cn,
                            "图表x轴": x_axis_meta,
                            "图表y轴": indicator.get('图表y轴'),
                            "api_返回字段": indicator.get('api_返回字段'),
                            # ✅ 保留原始三个字段的原始值
                            "指标类型": indicator.get('指标类型'),
                            "指向性": indicator.get('指向性'),
                            "详单信息": indicator.get('详单信息')
                        }
                    },
                    "query_condition": {
                        "type": params['query_type'],
                        "name": params['query_name'],
                        "id": dimension_id,
                        "param": dimension_param,
                        "scope": f"{params['query_type']}视图"
                    },
                    "data": {
                        "count": len(content),
                        "content": content
                    },
                    "chart": {
                        "x_field": x_field,
                        "series_fields": series_fields,
                        "rows": content
                    },
                    "visualization": {
                        "chart_type": chart_type,
                        "suggested_config": {
                            "title": f"{indicator.get('指示器名称')} - {params['query_name']}",
                            "x_label": x_field,
                            "y_label": y_axis_meta or '数值'
                        }
                    },
                    # ✅ 详单信息优先使用接口返回的数据，否则沿用detail_info结构
                    "detail_info": raw_detail_info if raw_detail_info else {
                        "available": False,
                        "reason": "未提供single_indicator_url",
                        "suggestion": "如需要获取详单（下钻）数据，请提供IMAP Dashboard中该指标的URL链接，或使用URL取数功能。"
                    },
                    "message": "查询成功(实时数据)",
                    "detail_data": detail_data,
                    "excel_export": excel_info
                }
            except Exception as e:
                logger.error(f"实时查询失败,回退到示例数据: {e}")

        # 回退示例数据
        # 注意：示例模式下无法从接口获取 指标类型/指向性/详单信息，使用推断或默认值
        example_indicator_type = self._infer_indicator_type(params['indicator_name'], '')
        example_target_object = self._extract_target_object(params['indicator_name'])
        example_directivity = {"scope": "示例模式", "note": "实际数据需配置认证"}
        example_detail_info = {
            "available": False,
            "reason": "示例模式不提供详单数据",
            "suggestion": "配置有效 token 后可获取实时数据及详单信息。"
        }
        
        return {
            "status": True,
            "type": "indicator_data",
            "indicator": {
                "name": params['indicator_name'],
                "chart_type": "柱状图",
                "indicator_type": example_indicator_type,
                "directivity": example_directivity,
                "detail_info": example_detail_info,
                "target_object": example_target_object,
                "x_axis": "周期",
                "y_axis": "平均数,中位数",
                "api_url": "",
                "raw_metadata": {
                    "指示器名称": params['indicator_name'],
                    "图表类型": "柱状图",
                    "指标类型": example_indicator_type,
                    "指向性": example_directivity,
                    "详单信息": example_detail_info
                }
            },
            "query_condition": {
                "type": params['query_type'],
                "name": params['query_name'],
                "id": "48",
                "param": "projectGroup_id",
                "scope": f"{params['query_type']}视图"
            },
            "data": {
                "count": 29,
                "content": [
                    {"周期": "2026-04", "平均数": 51, "中位数": 51},
                    {"周期": "2026-05", "平均数": 345, "中位数": 345}
                ]
            },
            "chart": {
                "x_field": "周期",
                "series_fields": ["平均数", "中位数"],
                "rows": [
                    {"周期": "2026-04", "平均数": 51, "中位数": 51},
                    {"周期": "2026-05", "平均数": 345, "中位数": 345}
                ]
            },
            "visualization": {
                "chart_type": "bar",
                "suggested_config": {
                    "title": f"{params['indicator_name']} - {params['query_name']}",
                    "x_label": "周期",
                    "y_label": "平均数,中位数"
                }
            },
            "detail_info": {
                "available": False,
                "reason": "示例模式不提供详单数据",
                "suggestion": "配置有效 token 后可获取实时数据及详单信息。"
            },
            "message": "查询成功(示例数据)",
            "detail_data": {},
            "excel_export": None
        }

    def _handle_list_capabilities(self, query: str, params: Dict):
        """返回IMAP技能的能力描述"""
        return {
            "status": True,
            "type": "capabilities",
            "capabilities": {
                "authenticated_queries": [
                    "列出所有指标(支持模糊匹配)",
                    "查询指标数据(支持项目组/部门/中心多维度)",
                    "查询用户权限",
                    "查询用户信息"
                ],
                "features": [
                    "自动图表配置(折线图/柱状图)",
                    "字段自动识别(X轴/系列字段)",
                    "权限校验",
                    "支持时间范围筛选"
                ],
                "total_indicators": 87,
                "note": "真实数据需要有效的 coclaw_token 认证"
            },
            "message": "IMAP指标查询技能能力概览"
        }

    def _handle_query_auth(self, query: str, params: Dict):
        """处理权限查询"""
        if REAL_API_AVAILABLE:
            try:
                yxdata_token, _ = self._get_auth_tokens()
                if not yxdata_token:
                    raise Exception("缺少可用的 yxdata-token,无法查询真实权限")

                auth_data = _query_user_auth_api(self.empno, yxdata_token, self.token)

                if not auth_data.get('status'):
                    return {"status": False, "error": auth_data.get('error', '查询权限失败')}

                content = auth_data.get('data', {}).get('content', [])
                grouped: Dict[str, list] = {}
                for item in content:
                    auth_type = item.get('auth_type')
                    key_name = item.get('key_name')
                    if not auth_type or not key_name:
                        continue
                    grouped.setdefault(auth_type, [])
                    grouped[auth_type].append(key_name)

                return {
                    "status": True,
                    "type": "user_auth",
                    "user_permissions": grouped,
                    "total": len(content),
                    "message": f"共获取到 {len(content)} 项权限"
                }
            except Exception as e:
                logger.error(f"权限查询失败,回退示例数据: {e}")

        return {
            "status": True,
            "type": "user_auth",
            "user_permissions": {
                "项目组级": ["ZXUFP", "BVB"],
                "部门级": ["FM开发二部"],
                "分中心级": ["FM产品研发中心"]
            },
            "total": 3,
            "message": "共获取到 3 项权限(示例数据)"
        }

    def _handle_query_user_info(self, query: str, params: Dict):
        """处理用户信息查询"""
        if REAL_API_AVAILABLE:
            try:
                yxdata_token, _ = self._get_auth_tokens()
                if not yxdata_token:
                    raise Exception("缺少可用的 yxdata-token,无法查询真实用户信息")

                user_data = _query_user_info_api(self.empno, yxdata_token, self.token)

                if not user_data.get('status'):
                    return {"status": False, "error": user_data.get('error', '查询用户信息失败')}

                org_info = _extract_user_org_info_api(user_data)
                return {
                    "status": True,
                    "type": "user_info",
                    "user_info": org_info or {},
                    "message": f"用户信息查询成功 ({self.empno})"
                }
            except Exception as e:
                logger.error(f"用户信息查询失败,回退示例数据: {e}")

        return {
            "status": True,
            "type": "user_info",
            "user_info": {
                "employee_id": self.empno or "12345678",
                "name": "吴俊琴",
                "department": "软件平台开发六部",
                "projectGroup": "有线效能工具",
                "subCenter": "软件平台研发中心"
            },
            "message": f"用户: 吴俊琴 ({self.empno or '12345678'})"
        }

    def _handle_query_by_url(self, query: str, params: Dict):
        """处理URL直连查询"""
        url = params.get('url', '')
        is_encoded = params.get('is_encoded', False)
        if REAL_API_AVAILABLE:
            try:
                result = query_by_url(url, is_encoded=is_encoded)
                if not result.get('status'):
                    return {"status": False, "error": result.get('error', 'URL查询失败')}

                raw = result.get('data', {}) or {}
                content = raw.get('content', [])
                x_field = self._detect_x_axis(content)
                series_fields = self._detect_series_fields(content)

                return {
                    "status": True,
                    "type": "url_query",
                    "url": url,
                    "data": {"count": len(content), "content": content},
                    "chart": {"x_field": x_field, "series_fields": series_fields, "rows": content},
                    "message": "URL查询成功"
                }
            except Exception as e:
                logger.error(f"URL查询失败,回退示例数据: {e}")

        return {
            "status": True,
            "type": "url_query",
            "url": url,
            "data": {"count": 10, "content": [{"x": 1, "y": 100}]},
            "chart": {"x_field": "x", "series_fields": ["y"], "rows": [{"x": 1, "y": 100}]},
            "message": "URL查询成功(示例数据)"
        }

    def _handle_query_indicator_detail_by_url(self, query: str, params: Dict):
        """处理单指示器详单数据查询
        
        优化功能：默认展示前10条记录，完整数据保存为Excel
        """
        single_indicator_url = params.get('single_indicator_url', '')
        column_name = params.get('column_name', '')

        if not single_indicator_url:
            return {
                "status": False,
                "error": "缺少必要参数: single_indicator_url",
                "suggestion": "请提供单指示器的URL链接，例如：查询单指示器详单数据，url=..., column_name=..."
            }

        if not column_name:
            return {
                "status": False,
                "error": "缺少必要参数: column_name",
                "suggestion": "请指定需要下钻的字段名称，例如：数量、名称、负责人等"
            }

        # 调用新功能模块
        try:
            result = query_indicator_detail_data_by_url(single_indicator_url, column_name)

            if result.get('status'):
                data_section = result.get('data')
                
                # 处理不同格式的数据
                if isinstance(data_section, dict) and 'content' in data_section:
                    full_data = data_section['content']
                elif isinstance(data_section, list):
                    full_data = data_section
                else:
                    full_data = []
                
                total = len(full_data)
                
                # 保存完整数据到 Excel (仅当数据量 > 0 时)
                excel_path = None
                if total > 0:
                    excel_path = self._save_data_to_excel(full_data, single_indicator_url, column_name)
                
                # 取前10条用于展示
                display_data = full_data[:10]
                
                # 生成Markdown表格（直接呈现，不做数据内容处理）
                formatted_table = self._format_data_as_markdown_table(display_data)
                
                response = {
                    "status": True,
                    "type": "indicator_detail_data",
                    "single_indicator_url": single_indicator_url,
                    "column_name": column_name,
                    "data": display_data,
                    "total": total,
                    "formatted_table": formatted_table,
                    "message": f"详单数据查询成功，共 {total} 条记录，显示前10条"
                }
                
                if excel_path:
                    response["excel_path"] = excel_path
                    response["message"] += f"，完整数据已保存至: {excel_path}"
                
                return response
            else:
                return {
                    "status": False,
                    "error": result.get('error', '详单数据查询失败'),
                    "suggestion": result.get('suggestion'),
                    "raw_response": result.get('raw_response')
                }
        except Exception as e:
            logger.error(f"详单数据查询异常: {e}")
            return {
                "status": False,
                "error": f"详单数据查询异常: {str(e)}",
                "suggestion": "请检查URL和字段名是否正确，或联系系统管理员"
            }

    def _handle_milestone_retrospective(self, query: str, params: Dict):
        """处理里程碑复盘报告数据获取
        
        优化：直接返回API的 result 部分完整内容，不添加任何额外信息
        """
        # 从用户查询中解析标准化输入
        parsed_data = self._parse_standard_input(query)

        if not parsed_data:
            return {
                "status": False,
                "error": "无法解析里程碑复盘报告数据请求",
                "suggestion": "请使用标准格式:返回类型:table;里程碑:里程碑1,里程碑2;指标:指标1,指标2,...;添加内容:添加指标1,添加指标2,...;"
            }

        # 尝试调用里程碑复盘报告API
        try:
            result = self._call_milestone_retrospective_api(parsed_data)
            if result.get('status'):
                # 只返回 API 返回的 result 部分完整内容（在 data 字段中）
                if 'data' in result:
                    return result['data']
                else:
                    return result
            else:
                return result
        except Exception as e:
            logger.error(f"里程碑复盘报告数据获取失败: {e}")
            return {
                "status": False,
                "error": f"获取里程碑复盘报告数据失败: {str(e)}",
                "suggestion": "请检查网络连接和认证信息"
            }

    def _parse_standard_input_strict(self, query: str) -> Optional[Dict[str, Any]]:
        """严格解析标准输入格式（v2.0优化）
        
        要求：
        - 必须以"返回类型："或"返回类型:"开头
        - 必须包含里程碑和指标字段
        - 使用全角/半角冒号和分号
        - 里程碑和指标用半角逗号分隔
        """
        try:
            query = query.strip()
            
            # 严格校验
            if not (query.startswith('返回类型：') or query.startswith('返回类型:')):
                return None
            if '里程碑' not in query or '指标' not in query:
                return None
            
            sep_pattern = r'[:：;；]'
            
            # 提取返回类型
            return_type_match = re.search(r'返回类型\s*' + sep_pattern + r'\s*(\w+)', query)
            if not return_type_match:
                return None
            return_type = return_type_match.group(1)
            
            # 提取里程碑
            milestones_match = re.search(r'里程碑\s*' + sep_pattern + r'\s*([^\s;；:]+)', query)
            if not milestones_match:
                return None
            milestones_str = milestones_match.group(1).strip()
            milestones = [m.strip() for m in re.split(r'[,\uff0c|；]', milestones_str) if m.strip()]
            
            # 提取指标
            indicators_match = re.search(r'指标\s*' + sep_pattern + r'\s*([^\s;；:]+)', query)
            if not indicators_match:
                return None
            indicators_str = indicators_match.group(1).strip()
            indicators = [ind.strip() for ind in re.split(r'[,\uff0c|；]', indicators_str) if ind.strip()]
            
            # 提取添加内容（可选）
            additional = []
            additional_match = re.search(r'添加内容\s*' + sep_pattern + r'\s*([^\s;；:]*)', query)
            if additional_match:
                additional_str = additional_match.group(1).strip()
                if additional_str and additional_str != '无':
                    additional = [item.strip() for item in re.split(r'[,\uff0c|；]', additional_str) if item.strip()]
            
            if not milestones or not indicators:
                return None
            
            return {
                "return_type": return_type,
                "milestones": milestones,
                "indicators": indicators,
                "additional": additional
            }
            
        except Exception as e:
            logger.debug(f"严格解析标准输入失败: {e}")
            return None

    def _parse_standard_input(self, query: str) -> Optional[Dict[str, Any]]:
        """解析标准输入格式"""
        try:
            # 标准格式:返回类型:table;里程碑:名称1,名称2;指标:指标1,指标2,...;添加内容:添加指标1,添加指标2,...;
            query = query.strip()

            # 支持全角/半角冒号和分号
            # 使用 [:\uFF1A;\uFF1B] 匹配半角/全角冒号和分号
            sep_pattern = r'[:\uFF1A;\uFF1B]'

            # 提取返回类型 (兼容全角/半角)
            return_type_match = re.search(r'返回类型\s*' + sep_pattern + r'\s*(\w+)', query)
            return_type = return_type_match.group(1) if return_type_match else 'table'

            # 提取里程碑 (兼容全角/半角)
            milestones_match = re.search(r'里程碑\s*' + sep_pattern + r'\s*([^\s;\uFF1B:]+)', query)
            milestones = []
            if milestones_match:
                milestones_str = milestones_match.group(1).strip()
                milestones = [m.strip() for m in re.split(r'[,\|；]', milestones_str) if m.strip()]

            # 提取指标 (兼容全角/半角)
            indicators_match = re.search(r'指标\s*' + sep_pattern + r'\s*([^\s;\uFF1B:]+)', query)
            indicators = []
            if indicators_match:
                indicators_str = indicators_match.group(1).strip()
                indicators = [ind.strip() for ind in re.split(r'[,\|；]', indicators_str) if ind.strip()]

            # 提取添加内容 (兼容全角/半角)
            additional_match = re.search(r'添加内容\s*' + sep_pattern + r'\s*([^\s;\uFF1B:]+)', query)
            additional = []
            if additional_match:
                additional_str = additional_match.group(1).strip()
                if additional_str and additional_str != '无':
                    additional = [item.strip() for item in re.split(r'[,\|；]', additional_str) if item.strip()]

            if not milestones:
                return None

            return {
                "return_type": return_type,
                "milestones": milestones,
                "indicators": indicators,
                "additional": additional
            }
        except Exception as e:
            logger.error(f"解析标准输入失败: {e}")
            return None

    def _call_milestone_retrospective_api(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """调用里程碑复盘报告API"""
        # 获取认证信息（此接口可选，如果缺失则使用免鉴权方式）
        empno = self.empno or os.getenv('coclaw_empno')
        token = self.token or os.getenv('coclaw_token')

        # 构建请求数据
        # 使用全角冒号和全角分号构建标准格式字符串（按照要求）
        question_parts = [
            f"返回类型：{data['return_type']}；",
            f"里程碑：{','.join(data['milestones'])}；",
            f"指标：{','.join(data['indicators'])}；"
        ]
        if data.get('additional'):
            question_parts.append(f"添加内容：{','.join(data['additional'])}；")
        
        request_data = {
            "question": ''.join(question_parts)
        }

        logger.info(f"里程碑复盘API请求体: {request_data}")

        # 构造请求头（如果提供了认证信息则带上，否则免鉴权）
        headers = {
            'Content-Type': 'application/json'
        }
        if empno and token:
            headers['X-Emp-No'] = empno
            headers['X-Auth-Value'] = token
            logger.info("使用认证信息调用里程碑复盘API")
        else:
            logger.info("使用免鉴权方式调用里程碑复盘API")

        # 发送POST请求
        try:
            import requests
            response = requests.post(
                'https://aip.zte.com.cn/cmtool/api/v1/ai_platform/milestone_retrospective',
                headers=headers,
                json=request_data,
                timeout=30
            )
            response.raise_for_status()
            result = response.json()

            # 返回标准表格结构
            if isinstance(result, dict) and result.get('status'):
                # 提取 data.bo.result 中的实际数据
                data_section = result.get('data', {})
                bo = data_section.get('bo', {})
                actual_result = bo.get('result')
                
                # 如果result有效，优先返回result
                if actual_result is not None:
                    return {
                        "status": True,
                        "type": "milestone_retrospective",
                        "data": actual_result,
                        "doc_id": bo.get('docId'),
                        "chat_uuid": bo.get('chatUuid'),
                        "finish_reason": bo.get('finishReason'),
                        "raw_response": result  # 保留原始响应供调试
                    }
                # 如果result为空但docId存在，提示用户文档已生成
                elif bo.get('docId'):
                    return {
                        "status": True,
                        "type": "milestone_retrospective",
                        "data": {
                            "message": "里程碑复盘报告已生成，请通过文档ID查看",
                            "doc_id": bo.get('docId'),
                            "chat_uuid": bo.get('chatUuid'),
                            "finish_reason": bo.get('finishReason')
                        }
                    }
                # 否则使用data字段或content字段
                else:
                    return {
                        "status": False,
                        "error": "API返回成功但缺少result数据，请检查请求参数或联系系统管理员",
                        "raw_response": result
                    }
            else:
                return {
                    "status": False,
                    "error": result.get('message', 'API调用失败') if isinstance(result, dict) else 'API返回格式错误',
                    "data": result
                }
        except ImportError:
            # 如果没有requests库,返回模拟数据
            logger.warning("requests库未安装,使用模拟数据")
            return {
                "status": True,
                "data": {
                    "content": [
                        {
                            "里程碑": data['milestones'][0] if data['milestones'] else '',
                            "指标": data['indicators'][0] if data['indicators'] else '',
                            "数值": 0,
                            "单位": '个',
                            "备注": '模拟数据'
                        }
                    ],
                    "total": len(data['milestones']) * len(data['indicators'])
                }
            }
        except Exception as e:
            logger.error(f"API调用异常: {e}")
            return {
                "status": False,
                "error": f"API调用失败: {str(e)}",
                "suggestion": "请检查网络连接或稍后重试"
            }

    def _handle_query_all_detail(self, query: str, params: Dict):
        """处理全量详单数据查询（all_api场景）
        
        用户可以使用此场景直接获取指标的完整详单数据并导出为CSV。
        参数：
        - indicator_name: 指标名称
        - query_type: 查询类型（项目组/部门/中心）
        - query_name: 查询名称
        """
        # 检查必要参数
        missing = []
        if not params.get('indicator_name'):
            missing.append('指标名称')
        if not params.get('query_type'):
            missing.append('查询类型')
        if not params.get('query_name'):
            missing.append('查询条件')
        
        if missing:
            return {
                "status": False,
                "error": f"缺少必要参数: {', '.join(missing)}",
                "suggestion": "请提供完整的查询描述,例如: '| 指标详单数据 | 查询ZXUFP项目组的MR交付周期'"
            }
        
        # 调用 _handle_query_indicator 并强制导出CSV
        # 合并参数：除了原参数外，强制设置 export_excel=True
        merged_params = params.copy()
        merged_params['export_excel'] = True
        
        # 重用指标查询逻辑，但只关注导出结果
        result = self._handle_query_indicator(query, merged_params)
        
        # 简化返回：只保留导出信息
        if result.get('status'):
            excel_info = result.get('excel_export')
            if excel_info and excel_info.get('available'):
                return {
                    "status": True,
                    "type": "all_detail_export",
                    "indicator": result.get('indicator', {}),
                    "query_condition": result.get('query_condition', {}),
                    "excel_export": excel_info,
                    "message": f"全量详单数据已导出: {excel_info.get('filename')} (共{excel_info.get('count')}个文件)"
                }
            else:
                return {
                    "status": False,
                    "error": "详单数据导出失败",
                    "reason": excel_info.get('reason') if excel_info else '未知错误',
                    "suggestion": "请确认指标已配置详单信息或all_api接口可用"
                }
        else:
            # 查询失败，返回原错误
            return result

    def _handle_unknown(self, query: str, params: Dict):
        """处理未知意图"""
        return {
            "status": False,
            "error": "无法理解您的查询意图",
            "suggestions": [
                "列出所有指标",
                "查询ZXUFP项目组的MR交付周期",
                "查看我的权限",
                "查询我的用户信息",
                "通过URL链接查询数据",
                "| 指标详单数据 | 查询项目组的指标详单"
            ],
            "help": "请尝试更明确的描述"
        }

    def _format_data_as_markdown_table(self, data: list) -> str:
        """
        将数据列表格式化为Markdown表格
        
        Args:
            data: 数据列表（字典列表）
            
        Returns:
            Markdown格式的表格字符串
        """
        if not data:
            return "无数据可展示"
        
        # 提取表头（取第一条数据的所有键）
        headers = list(data[0].keys()) if isinstance(data[0], dict) else []
        if not headers:
            return "数据格式异常：无法提取字段"
        
        # 构建表格行
        lines = []
        # 表头行
        lines.append("| " + " | ".join(headers) + " |")
        # 分隔行
        lines.append("| " + " | ".join(["---"] * len(headers)) + " |")
        
        # 数据行
        for row in data:
            if not isinstance(row, dict):
                continue
            row_values = []
            for header in headers:
                value = str(row.get(header, ''))
                # 清理换行符，避免破坏表格格式
                value = value.replace('\n', ' ').replace('\r', '')
                row_values.append(value)
            lines.append("| " + " | ".join(row_values) + " |")
        
        return "\n".join(lines)

    def _save_data_to_excel(self, data: list, url: str, column_name: str) -> str:
        """
        将详单数据保存为 CSV 文件（纯标准库实现）
        
        Args:
            data: 完整数据列表
            url: 数据来源URL
            column_name: 下钻字段名
            
        Returns:
            CSV文件保存路径
        """
        try:
            # 创建工作区目录（如果不存在）
            # 使用用户全局工作区：C:\Users\00294192.WIN-EGGBPNLI8OF\.openclaw\workspace
            workspace_dir = os.path.join(os.path.expanduser('~'), '.openclaw', 'workspace')
            os.makedirs(workspace_dir, exist_ok=True)
            
            # 生成文件名：imap_detail_YYYYMMDD_HHMMSS_{column}_{url_part}.xlsx
            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
            # 从URL提取部分标识符
            url_part = ''
            if 'sub-menu/' in url:
                url_part = url.split('sub-menu/')[-1].split('?')[0][:20]
            elif 'dashboard' in url:
                url_part = 'dashboard'
            
            safe_column = column_name.replace('/', '_').replace('\\', '_')[:30]
            base_name = f"imap_detail_{timestamp}_{safe_column}"
            if url_part:
                base_name += f"_{url_part}"
            
            csv_path = os.path.join(workspace_dir, f"{base_name}.csv")
            
            if not data:
                logger.warning("无数据可保存")
                return ''
            
            # 确定 CSV 列和数据行
            first = data[0]
            if isinstance(first, dict):
                columns = list(first.keys())
                rows = [list(d.values()) for d in data]
            else:
                columns = [f'列{i+1}' for i in range(len(first))]
                rows = data
            
            import csv
            with open(csv_path, 'w', newline='', encoding='utf-8-sig') as f:
                writer = csv.writer(f)
                writer.writerow(columns)
                for row in rows:
                    writer.writerow([str(x) if x is not None else '' for x in row])
            
            logger.info(f"详单数据已保存到: {csv_path}")
            return csv_path
            
        except Exception as e:
            logger.error(f"保存 CSV 失败: {e}")
            return ''


def quick_query(user_query: str, empno=None, token=None):
    """快速查询入口"""
    return ImapQueryOrchestrator(empno, token).process_query(user_query)


def list_all_indicators(empno=None, token=None):
    """列出所有可用指标"""
    return quick_query("列出所有指标", empno, token)


def query_by_indicator_name(indicator_name: str, query_type: str, query_name: str,
                           start_date=None, end_date=None, empno=None, token=None):
    """按指标名称查询数据"""
    query = f"查询{query_name}{query_type}的{indicator_name}"
    if start_date and end_date:
        query += f"({start_date}到{end_date})"
    return quick_query(query, empno, token)


def query_indicator(indicator_name: str, query_type: str, query_name: str,
                     start_date: Optional[str] = None, end_date: Optional[str] = None,
                     empno: Optional[str] = None, token: Optional[str] = None) -> Dict[str, Any]:
    """传统API:直接查询指标数据(走完整的接口1-5流程)"""
    orchestrator = ImapQueryOrchestrator(empno=empno, token=token)
    params = {
        "indicator_name": indicator_name,
        "query_type": query_type,
        "query_name": query_name,
        "start_date": start_date,
        "end_date": end_date,
    }
    return orchestrator._handle_query_indicator("", params)


def query_indicator_list_export(keyword: str = "", empno: Optional[str] = None, token: Optional[str] = None) -> Dict[str, Any]:
    """传统API:导出/返回指标列表(走接口1,可带关键词筛选)"""
    orchestrator = ImapQueryOrchestrator(empno=empno, token=token)
    return orchestrator._handle_list_indicators("", {"indicator_keyword": keyword})


def query_by_imap_url(imap_url: str, is_encoded: bool = False, empno: Optional[str] = None, token: Optional[str] = None) -> Dict[str, Any]:
    """传统API:通过明确的 IMAP URL 免鉴权取数(走接口6)"""
    orchestrator = ImapQueryOrchestrator(empno=empno, token=token)
    return orchestrator._handle_query_by_url("", {"url": imap_url, "is_encoded": is_encoded})


def query_indicator_detail_by_url(single_indicator_url: str, column_name: str, empno: Optional[str] = None, token: Optional[str] = None) -> Dict[str, Any]:
    """
    传统API:单指示器详单数据获取（免鉴权接口）

    用户需提供：
    - single_indicator_url: iMAP单指示器的URL链接
    - column_name: 需要详单下钻的字段名称

    调用：GET https://aip.zte.com.cn/imap/api/mcp/get_indicator_detail_data_by_url?single_indicator_url=...&column_name=...
    返回：接口返回中data部分的内容
    """
    orchestrator = ImapQueryOrchestrator(empno=empno, token=token)
    return orchestrator._handle_query_indicator_detail_by_url("", {
        "single_indicator_url": single_indicator_url,
        "column_name": column_name
    })


def query_user_auth(empno: str, auth_token: Optional[str] = None, yxdata_token: Optional[str] = None) -> Dict[str, Any]:
    """传统API:查询用户权限(返回接口2原始结构,供上层再格式化)"""
    # 防御:真实API模块未导入成功时不应该直接调用未定义符号
    if not _query_user_auth_api:
        return {
            "status": False,
            "error": "真实API模块未导入成功(可能未将 scripts/ 加入 sys.path)"
        }
    orchestrator = ImapQueryOrchestrator(empno=empno, token=auth_token)

    # 如果调用方直接传了 yxdata-token 就复用;否则走完整认证链路
    if not yxdata_token:
        yxdata_token, _ = orchestrator._get_auth_tokens()
    if not yxdata_token:
        return {"status": False, "error": "缺少可用的认证信息"}

    result = _query_user_auth_api(empno, yxdata_token, orchestrator.token)  # 调用底层接口2
    return result


def get_user_auth_types_proxy(auth_data: Dict[str, Any]) -> Dict[str, Any]:
    """兼容性:把接口2原始结构转换为按 auth_type 分组的列表"""
    if _get_user_auth_types_api:
        return _get_user_auth_types_api(auth_data)
    # 如果底层不可用则返回空结构
    return {}


# 保持与 server.py / 旧代码的导入兼容:导出同名函数
get_user_auth_types = get_user_auth_types_proxy


def query_user_info(empno: str, auth_token: Optional[str] = None, yxdata_token: Optional[str] = None) -> Dict[str, Any]:
    """传统API:查询用户信息(返回接口3原始结构)"""
    # 防御:真实API模块未导入成功时不应该直接调用未定义符号
    if not _query_user_info_api:
        return {
            "status": False,
            "error": "真实API模块未导入成功(可能未将 scripts/ 加入 sys.path)"
        }
    orchestrator = ImapQueryOrchestrator(empno=empno, token=auth_token)

    if not yxdata_token:
        yxdata_token, _ = orchestrator._get_auth_tokens()
    if not yxdata_token:
        return {"status": False, "error": "缺少可用的认证信息"}

    result = _query_user_info_api(empno, yxdata_token, orchestrator.token)  # 调用底层接口3
    return result


# 保持与 server.py / 旧代码的导入兼容:导出同名函数(兜底不崩)
def extract_user_org_info(user_info: Dict[str, Any]) -> Optional[Dict[str, str]]:
    """从接口3返回结构中提取组织信息(真实模块不可用时返回None)"""
    if _extract_user_org_info_api:
        return _extract_user_org_info_api(user_info)
    return None


def get_milestone_retrospective_data(
    milestones: List[str],
    indicators: List[str],
    additional_metrics: Optional[List[str]] = None,
    return_type: str = "table",
    empno: Optional[str] = None,
    token: Optional[str] = None
) -> Dict[str, Any]:
    """
    里程碑复盘报告数据获取

    标准输入格式:
    返回类型:table;
    里程碑:里程碑1,里程碑2;
    指标:指标1,指标2,...;
    添加内容:添加指标1,添加指标2,...;
    """
    orchestrator = ImapQueryOrchestrator(empno=empno, token=token)

    # 构建标准化查询字符串
    query_parts = [f"返回类型:{return_type};", f"里程碑:{','.join(milestones)};", f"指标:{','.join(indicators)};"]
    if additional_metrics:
        query_parts.append(f"添加内容:{','.join(additional_metrics)};")

    standard_query = ''.join(query_parts)

    # 调用处理器
    params = {
        'return_type': return_type,
        'milestones': milestones,
        'indicators': indicators,
        'additional': additional_metrics or []
    }

    return orchestrator._handle_milestone_retrospective(standard_query, params)


if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("用法: python main.py <自然语言查询>")
        print("示例: python main.py '查询ZXUFP项目组的MR交付周期'")
        print("      python main.py '列出所有指标'")
        print("      python main.py '查看我的权限'")
        sys.exit(1)

    user_query = " ".join(sys.argv[1:])
    print(f"🔍 理解查询: {user_query}\n")

    result = quick_query(user_query)
    print(json.dumps(result, ensure_ascii=False, indent=2))

    if result.get('status'):
        print(f"\n✅ {result.get('message', '查询成功')}")
    else:
        print(f"\n❌ {result.get('error', '查询失败')}")
        if result.get('suggestion'):
            print(f"💡 {result.get('suggestion')}")
