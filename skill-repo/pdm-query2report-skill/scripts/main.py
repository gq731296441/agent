#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
PDM部件查询汇总工具 - 完整功能版
版本:v5.14.0 (优化Excel读取性能，解决16,384列卡死问题 + 完善--no-prompt支持)
功能:
1. 查询PDM并生成查询结果文件(支持CSV/XLSX/JSON/HTML)
2. 根据已有的查询结果文件回填部件编号
3. 智能冲突检测与交互式修正
4. 循环重试机制(MAX_RETRY=3)
5. 错误/警告分级处理
6. 生成差异对比报告
"""

import os
import sys
import json
import argparse
import logging
import csv
import time
from datetime import datetime
from typing import List, Dict, Any, Optional, Tuple
from collections import defaultdict

# 配置UTF-8编码
def configure_stdio_utf8():
    """配置标准输入输出编码为UTF-8"""
    if hasattr(sys.stdin, 'reconfigure'):
        sys.stdin.reconfigure(encoding='utf-8', errors='replace')
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    if hasattr(sys.stderr, 'reconfigure'):
        sys.stderr.reconfigure(encoding='utf-8', errors='replace')

configure_stdio_utf8()

# 日志配置
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[logging.StreamHandler(sys.stderr)]
)
logger = logging.getLogger(__name__)

# 检查依赖
try:
    import requests
    from openpyxl import load_workbook, Workbook
    from openpyxl.styles import Font, Alignment, PatternFill
except ImportError as e:
    logger.error(f"缺少依赖包: {e}")
    logger.error("请安装: pip install openpyxl requests")
    sys.exit(1)

# 尝试导入tqdm
try:
    from tqdm import tqdm
    HAS_TQDM = True
except ImportError:
    HAS_TQDM = False

# ==================== Excel读取模块（v5.14.0 性能优化版） ====================
def read_excel_with_openpyxl(file_path: str, sheet_name: str = None) -> Tuple[List[Dict[str, str]], List[str], List[int]]:
    """
    使用openpyxl读取Excel文件，只读取有内容的列，解决16,384列性能问题
    v5.14.0: 使用read_only模式 + 智能列范围检测
    """
    try:
        wb = load_workbook(file_path, data_only=True, read_only=True)

        if sheet_name and sheet_name in wb.sheetnames:
            ws = wb[sheet_name]
            logger.info(f"使用工作表: {sheet_name}")
        else:
            ws = wb.active
            if sheet_name:
                logger.warning(f"未找到工作表'{sheet_name}'，使用默认工作表: {ws.title}")
            else:
                logger.info(f"使用工作表: {ws.title}")

        # ==================== 快速检测有内容的列 ====================
        logger.info("正在检测有内容的列...")

        max_col_with_data = 0
        rows_scanned = 0
        # 检测前100行确定列范围
        for row in ws.iter_rows(max_row=100, values_only=True):
            rows_scanned += 1
            for col_idx, cell_value in enumerate(row, 1):
                if cell_value is not None and str(cell_value).strip() != "":
                    if col_idx > max_col_with_data:
                        max_col_with_data = col_idx

        # 如果前100行未检测到数据，再扫描全表（但限制检测行数）
        if max_col_with_data == 0:
            logger.info("前100行未检测到数据，正在扩大扫描范围...")
            scan_limit = min(ws.max_row, 1000) # 最多扫描1000行
            for row in ws.iter_rows(max_row=scan_limit, values_only=True):
                rows_scanned += 1
                for col_idx, cell_value in enumerate(row, 1):
                    if cell_value is not None and str(cell_value).strip() != "":
                        if col_idx > max_col_with_data:
                            max_col_with_data = col_idx
                if max_col_with_data > 50:
                    break

        # 如果还是没有数据，返回空
        if max_col_with_data == 0:
            logger.warning("未检测到有效数据列")
            wb.close()
            return [], [], []

        # 限制最大读取列数（避免性能问题）
        read_cols = min(max_col_with_data + 5, 200) # 最多读取200列
        logger.info(f"检测到有内容的列数: {max_col_with_data}，实际读取列数: {read_cols}")

        # ==================== 读取有内容的列 ====================
        rows = list(ws.iter_rows(values_only=True, max_col=read_cols))
        if not rows:
            wb.close()
            return [], [], []

        # 第一行作为表头
        headers = []
        for cell_value in rows[0]:
            if cell_value is None:
                headers.append("")
            else:
                headers.append(str(cell_value).strip())

        # 清理空表头
        headers = [h if h else f"Column_{idx}" for idx, h in enumerate(headers)]

        # 读取数据行，记录实际行号
        data_rows = []
        source_rows = []
        for row_idx, row in enumerate(rows[1:], start=2):
            # 跳过完全空的行
            if all(cell is None or str(cell).strip() == "" for cell in row):
                continue

            row_dict = {}
            for col_idx, cell_value in enumerate(row):
                if col_idx < len(headers) and headers[col_idx]:
                    if cell_value is None:
                        row_dict[headers[col_idx]] = ""
                    elif hasattr(cell_value, 'strftime'):
                        row_dict[headers[col_idx]] = cell_value.strftime("%Y-%m-%d")
                    else:
                        row_dict[headers[col_idx]] = str(cell_value).strip()

            if row_dict:
                data_rows.append(row_dict)
                source_rows.append(row_idx)

        wb.close()

        if source_rows:
            logger.info(f"成功读取 {len(data_rows)} 行数据，有效列数: {len([h for h in headers if h])}")
        else:
            logger.info(f"成功读取 {len(data_rows)} 行数据，有效列数: {len([h for h in headers if h])}")

        return data_rows, headers, source_rows

    except Exception as e:
        logger.error(f"读取Excel失败: {e}")
        raise


# ==================== 查询结果文件读取模块 ====================
def read_query_result_file(file_path: str) -> List[Dict[str, Any]]:
    """
    读取查询结果文件(支持 CSV、XLSX、JSON、HTML 格式)
    """
    if not os.path.exists(file_path):
        raise FileNotFoundError(f"查询结果文件不存在: {file_path}")

    file_ext = os.path.splitext(file_path)[1].lower()

    if file_ext == '.csv':
        return read_query_result_csv(file_path)
    elif file_ext == '.xlsx':
        return read_query_result_xlsx(file_path)
    elif file_ext == '.json':
        return read_query_result_json(file_path)
    elif file_ext == '.html':
        return read_query_result_html(file_path)
    else:
        raise ValueError(f"不支持的文件格式: {file_ext},请使用 CSV、XLSX、JSON 或 HTML 格式")

def read_query_result_csv(file_path: str) -> List[Dict[str, Any]]:
    """读取CSV格式的查询结果文件"""
    results = []
    with open(file_path, 'r', encoding='utf-8-sig') as f:
        reader = csv.DictReader(f)
        for row in reader:
            result = {}
            for key, value in row.items():
                # 清理字段名中的BOM和空格
                clean_key = key.strip().replace('\ufeff', '')
                result[clean_key] = value
            results.append(result)
    logger.info(f"成功读取CSV查询结果文件: {len(results)} 条记录")
    return results

def read_query_result_xlsx(file_path: str) -> List[Dict[str, Any]]:
    """读取XLSX格式的查询结果文件"""
    results = []
    wb = load_workbook(file_path, data_only=True)
    ws = wb.active

    # 获取表头
    headers = []
    for cell in ws[1]:
        if cell.value:
            headers.append(str(cell.value).strip())
        else:
            headers.append("")

    # 读取数据
    for row_idx in range(2, ws.max_row + 1):
        row_dict = {}
        for col_idx, header in enumerate(headers, 1):
            if header:
                cell_value = ws.cell(row=row_idx, column=col_idx).value
                if cell_value is None:
                    row_dict[header] = ""
                else:
                    row_dict[header] = str(cell_value).strip()

        if any(row_dict.values()):
            results.append(row_dict)

    wb.close()
    logger.info(f"成功读取Excel查询结果文件: {len(results)} 条记录")
    return results

def read_query_result_json(file_path: str) -> List[Dict[str, Any]]:
    """读取JSON格式的查询结果文件"""
    with open(file_path, 'r', encoding='utf-8') as f:
        data = json.load(f)

    # 支持多种JSON结构
    if isinstance(data, list):
        results = data
    elif isinstance(data, dict):
        if 'results' in data:
            results = data['results']
        elif 'data' in data:
            results = data['data']
        else:
            results = [data]
    else:
        raise ValueError("JSON文件格式不正确,应为数组或包含results/data字段的对象")

    logger.info(f"成功读取JSON查询结果文件: {len(results)} 条记录")
    return results

def read_query_result_html(file_path: str) -> List[Dict[str, Any]]:
    """读取HTML格式的查询结果文件(从表格中提取数据)"""
    try:
        from bs4 import BeautifulSoup
    except ImportError:
        raise ImportError("读取HTML文件需要安装beautifulsoup4,请运行: pip install beautifulsoup4")

    results = []

    with open(file_path, 'r', encoding='utf-8') as f:
        soup = BeautifulSoup(f.read(), 'html.parser')

    # 查找表格
    table = soup.find('table')
    if not table:
        raise ValueError("HTML文件中未找到表格数据")

    # 获取表头
    headers = []
    header_row = table.find('tr')
    if header_row:
        for th in header_row.find_all(['th', 'td']):
            headers.append(th.get_text().strip())

    if not headers:
        raise ValueError("HTML表格中未找到表头")

    # 获取数据行
    rows = table.find_all('tr')[1:]  # 跳过表头行
    for row in rows:
        cells = row.find_all(['td', 'th'])
        if cells:
            row_dict = {}
            for idx, cell in enumerate(cells):
                if idx < len(headers):
                    row_dict[headers[idx]] = cell.get_text().strip()

            if any(row_dict.values()):
                results.append(row_dict)

    logger.info(f"成功读取HTML查询结果文件: {len(results)} 条记录")
    return results

# ==================== PDM认证模块 ====================
class PDMAuth:
    """PDM认证管理"""

    def __init__(self):
        self.emp_no = os.getenv("coclaw_empno") or os.getenv("RDC_EMP_NO")
        self.token = os.getenv("coclaw_token") or os.getenv("RDC_AUTH_TOKEN")
        self.system_code = os.getenv("X_SYSTEM_CODE", "PDM_QUERY_TOOL")
        self.ms_name = os.getenv("MS_NAME", "pdmgbomquery")

        if not self.emp_no or not self.token:
            raise ValueError(
                "缺少认证信息。请设置环境变量:\n"
                " - coclaw_empno / RDC_EMP_NO: 工号 (必填)\n"
                " - coclaw_token / RDC_AUTH_TOKEN: 认证token (必填)"
            )

        self.session = self._create_session()

    def _create_session(self):
        from requests.adapters import HTTPAdapter
        from urllib3.util.retry import Retry

        session = requests.Session()
        retry = Retry(total=2, backoff_factor=0.5, status_forcelist=[500, 502, 503, 504])
        adapter = HTTPAdapter(max_retries=retry, pool_connections=10, pool_maxsize=20)
        session.mount('http://', adapter)
        session.mount('https://', adapter)
        return session

    def get_headers(self) -> Dict[str, str]:
        return {
            "Content-Type": "application/json;charset=UTF-8",
            "X-Emp-No": self.emp_no,
            "X-Auth-Value": self.token,
            "X-System-Code": self.system_code,
            "X-Lang-Id": "zh_CN"
        }

    def close(self):
        if hasattr(self, 'session'):
            self.session.close()

# ==================== 验证模块(v5.7.0 职责分离) ====================

def validate_backfill_operation(
    query_results: List[Dict[str, Any]],
    selected_result_ids: List[int],
    excel_file: str
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], List[Dict[str, Any]]]:
    """
    v5.7.0: 验证逻辑模块化 - 执行完整的回填前验证

    返回: (errors, warnings, uncovered_empty_rows)
    - errors: 严重错误(必须解决才能继续)
    - warnings: 警告(建议关注,不影响继续)
    - uncovered_empty_rows: 未被覆盖的空行信息
    """
    errors = []
    warnings = []
    uncovered_empty_rows = []

    # 建立序号到查询结果的映射
    id_to_result = {}
    for result in query_results:
        try:
            result_id = int(result.get("序号", 0))
            if result_id > 0:
                id_to_result[result_id] = result
        except (ValueError, TypeError):
            continue

    # ==================== 检查1:用户指定的序号是否存在 ====================
    missing_ids = [rid for rid in selected_result_ids if rid not in id_to_result]
    if missing_ids:
        errors.append({
            "type": "missing_ids",
            "title": "❌ 错误1:序号不存在",
            "message": f"以下序号在查询结果文件中不存在: {missing_ids}",
            "details": [f"序号 {rid} 不存在于查询结果文件中" for rid in missing_ids],
            "required_action": "请移除不存在的序号后重新执行命令"
        })

    # ==================== 检查2:冲突检测(同一源行号多个不同部件编号) ====================
    source_row_to_partnos = defaultdict(set)
    source_row_to_ids = defaultdict(list)

    for result_id in selected_result_ids:
        if result_id in id_to_result:
            result = id_to_result[result_id]
            try:
                source_row = int(result.get("源行号", 0))
                part_no = result.get("部件编号", "")
                if part_no:
                    source_row_to_partnos[source_row].add(part_no)
                    source_row_to_ids[source_row].append(result_id)
            except (ValueError, TypeError):
                continue

    conflicts = []
    for source_row, partnos in source_row_to_partnos.items():
        if len(partnos) > 1:
            conflicts.append({
                "source_row": source_row,
                "partnos": list(partnos),
                "result_ids": source_row_to_ids[source_row]
            })

    if conflicts:
        conflict_details = []
        for conflict in conflicts:
            for rid in conflict['result_ids']:
                conflict_details.append(f"序号 {rid} (源行号 {conflict['source_row']}) 与同源行号的其他部件编号冲突")
        errors.append({
            "type": "conflict",
            "title": "❌ 错误2:回填冲突",
            "message": f"检测到 {len(conflicts)} 个源行号对应多个不同的部件编号",
            "details": conflict_details,
            "required_action": "请选择同一源行号的其中一个序号进行回填,不要同时选择多个"
        })

    # ==================== 检查3:部件编号为空 ====================
    empty_partno_ids = []
    empty_partno_details = []
    for result_id in selected_result_ids:
        if result_id in id_to_result:
            result = id_to_result[result_id]
            part_no = result.get("部件编号", "")
            if not part_no or str(part_no).strip() == "":
                empty_partno_ids.append(result_id)
                empty_partno_details.append(f"序号 {result_id}: 部件编号为空 (中文名称: {result.get('中文名称', '')[:30]})")

    if empty_partno_ids:
        errors.append({
            "type": "empty_partno",
            "title": "❌ 错误3:部件编号为空",
            "message": f"以下序号对应的部件编号为空: {empty_partno_ids}",
            "details": empty_partno_details,
            "required_action": "请检查查询结果文件,确保所选序号有有效的部件编号"
        })

    # ==================== 检查4:原始文件中未被覆盖的空行(警告) ====================
    total_empty_rows = 0
    selected_source_rows = set()

    try:
        data_rows, headers, source_rows = read_excel_with_openpyxl(excel_file, sheet_name="软件结构")

        empty_partno_rows = []
        for idx, row in enumerate(data_rows):
            part_no = row.get("部件编号", "")
            if not part_no or part_no.strip() == "":
                empty_partno_rows.append({
                    "row_num": source_rows[idx],
                    "chinese_name": row.get("中文名称", ""),
                    "english_name": row.get("英文名称", "")
                })

        total_empty_rows = len(empty_partno_rows)

        # 获取用户选中的有效序号对应的源行号
        for result_id in selected_result_ids:
            if result_id in id_to_result and result_id not in empty_partno_ids:
                try:
                    source_row = int(id_to_result[result_id].get("源行号", 0))
                    if source_row > 0:
                        selected_source_rows.add(source_row)
                except (ValueError, TypeError):
                    continue

        # 找出未被选中的空行(警告,不强制)
        uncovered_empty_rows = [item for item in empty_partno_rows if item['row_num'] not in selected_source_rows]

        if uncovered_empty_rows:
            uncovered_details = []
            for item in uncovered_empty_rows[:10]:
                uncovered_details.append(f"第{item['row_num']}行: {item['chinese_name'][:30]}")
            if len(uncovered_empty_rows) > 10:
                uncovered_details.append(f"... 还有 {len(uncovered_empty_rows) - 10} 行")
            warnings.append({
                "type": "uncovered",
                "title": "⚠️ 警告:未覆盖的空行",
                "message": f"您选中的有效序号将覆盖 {len(selected_source_rows)} 行,但还有 {len(uncovered_empty_rows)} 行【部件编号】为空未被覆盖",
                "details": uncovered_details,
                "suggestion": "这些行将保持为空,如需回填请补充对应的序号"
            })
    except Exception as e:
        warnings.append({
            "type": "read_error",
            "title": "⚠️ 警告:读取原始文件异常",
            "message": f"读取原始文件时出现异常: {e}",
            "details": [],
            "suggestion": "请检查原始文件是否完整可读"
        })

    return errors, warnings, uncovered_empty_rows


# ==================== 回填功能模块 ====================
PDM_BASE_URL = os.getenv('PDM_BASE_URL', 'https://pdmweb.zte.com.cn')
PDM_ENDPOINT = '/zte-plm-iproduct-gbom/gbom/v1/basicquery'

QUERY_TIMEOUT = 15
MAX_RETRIES = 2

def build_query_condition(
    name_cn: Optional[str] = None,
    name_en: Optional[str] = None,
    en_code: Optional[str] = None,
    team1: Optional[str] = None,
    team2: Optional[str] = None,
    team3: Optional[str] = None,
    lifecycle_status: Optional[List[str]] = None
) -> Dict[str, Any]:
    """构建G0001查询条件"""
    query_condition = {"queryType": "G0001", "queryVer": "v1"}

    if name_cn and str(name_cn).strip():
        query_condition["nameCn"] = str(name_cn).strip()
    if name_en and str(name_en).strip():
        query_condition["nameEn"] = str(name_en).strip()
    if en_code and str(en_code).strip():
        query_condition["enCode"] = str(en_code).strip()
    if team1 and str(team1).strip():
        query_condition["team1"] = str(team1).strip()
    if team2 and str(team2).strip():
        query_condition["team2"] = str(team2).strip()
    if team3 and str(team3).strip():
        query_condition["team3"] = str(team3).strip()

    if lifecycle_status is None:
        lifecycle_status = ["审批完成"]
    query_condition["lifecycleStatus"] = lifecycle_status

    return query_condition

def query_pdm_gbom(query_condition: Dict[str, Any], auth: Optional[PDMAuth] = None) -> Dict[str, Any]:
    """查询PDM GBOM基础信息"""
    if auth is None:
        auth = PDMAuth()

    all_data = []
    current_page = 1
    page_size = 100

    while True:
        request_body = {
            "msName": auth.ms_name,
            "pageNo": current_page,
            "pageSize": page_size,
            "queryCondition": query_condition
        }

        url = PDM_BASE_URL + PDM_ENDPOINT

        for attempt in range(MAX_RETRIES + 1):
            try:
                response = auth.session.post(url, json=request_body, headers=auth.get_headers(), timeout=QUERY_TIMEOUT)
                response.raise_for_status()
                result = response.json()

                code_info = result.get("code", {})
                if isinstance(code_info, dict):
                    code = str(code_info.get("code", ""))
                    msg = code_info.get("msg", "")
                else:
                    code = str(code_info)
                    msg = result.get("message", "")

                if code != "0000":
                    if attempt < MAX_RETRIES:
                        time.sleep(0.5 * (attempt + 1))
                        continue
                    return {"code": code, "message": msg, "total": 0, "data": []}

                bo = result.get("bo", {})
                data = bo.get("data", [])
                total = bo.get("total", 0)

                all_data.extend(data)

                if len(all_data) >= total or len(data) < page_size:
                    return {"code": code, "message": msg, "total": total, "data": all_data}

                current_page += 1
                break

            except Exception as e:
                if attempt < MAX_RETRIES:
                    time.sleep(0.5 * (attempt + 1))
                    continue
                return {"code": "ERROR", "message": str(e), "total": 0, "data": all_data}

        return {"code": "FAILED", "message": "查询失败", "total": 0, "data": all_data}

def deduplicate_by_part_no(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """按部件编号去重,保留最高版本"""
    version_priority = {}
    for item in items:
        part_no = item.get("PART_NO") or item.get("partNo")
        if not part_no:
            continue

        version = item.get("VERSION") or item.get("version", "")

        def compare_versions(v1, v2):
            if not v1:
                return -1
            if not v2:
                return 1
            try:
                return int(v1) - int(v2)
            except:
                pass
            if v1 > v2:
                return 1
            elif v1 < v2:
                return -1
            return 0

        if part_no not in version_priority:
            version_priority[part_no] = item
        else:
            existing_version = version_priority[part_no].get("VERSION") or version_priority[part_no].get("version", "")
            if compare_versions(version, existing_version) > 0:
                version_priority[part_no] = item

    return list(version_priority.values())

def smart_query_strategy(row: Dict[str, Any], auth) -> Tuple[List[Dict[str, Any]], str]:
    """智能多策略查询,返回所有匹配结果(去重后)"""
    conditions = {}

    chinese_name = row.get("中文名称", "")
    english_name = row.get("英文名称", "")
    en_code = row.get("英文代号", "")

    if chinese_name and str(chinese_name).strip():
        conditions["name_cn"] = str(chinese_name).strip()
    if english_name and str(english_name).strip():
        conditions["name_en"] = str(english_name).strip()
    if en_code and str(en_code).strip():
        conditions["en_code"] = str(en_code).strip()

    if not conditions:
        return [], "无有效查询条件"

    all_results = []
    used_strategy = ""

    # 策略1: 多条件组合查询
    try:
        query_condition = build_query_condition(
            name_cn=conditions.get("name_cn"),
            name_en=conditions.get("name_en"),
            en_code=conditions.get("en_code")
        )
        result = query_pdm_gbom(query_condition, auth=auth)
        if result.get("code") == "0000" and result.get("data"):
            all_results.extend(result["data"])
            used_strategy = "多条件组合"
    except Exception as e:
        logger.debug(f"策略1异常: {e}")

    # 如果多条件查询没有结果,尝试单条件查询
    if not all_results:
        priority_order = ["name_cn", "name_en", "en_code"]
        for field_type in priority_order:
            field_value = conditions.get(field_type)
            if field_value:
                try:
                    if field_type == "name_cn":
                        query_condition = build_query_condition(name_cn=field_value)
                    elif field_type == "name_en":
                        query_condition = build_query_condition(name_en=field_value)
                    else:
                        query_condition = build_query_condition(en_code=field_value)

                    result = query_pdm_gbom(query_condition, auth=auth)
                    if result.get("code") == "0000" and result.get("data"):
                        all_results.extend(result["data"])
                        used_strategy = f"单条件查询({field_type})"
                        break
                except Exception as e:
                    logger.debug(f"单条件查询异常 ({field_type}): {e}")

    # 去重:相同部件编号只保留最新版本
    if all_results:
        unique_results = deduplicate_by_part_no(all_results)
        return unique_results, used_strategy

    return [], "所有策略均失败"

def get_pdm_field(result: Dict[str, Any], field_names: List[str], default: str = "") -> str:
    """安全获取PDM返回字段"""
    for field_name in field_names:
        value = result.get(field_name)
        if value is not None and str(value).strip():
            return str(value).strip()
    return default

# ==================== 回填功能模块 ====================
def backfill_by_result_ids(
    original_file: str,
    query_results: List[Dict[str, Any]],
    valid_ids_for_backfill: List[int]
) -> Tuple[str, Dict[str, Any]]:
    """执行实际回填操作(精简版)

    参数:
    original_file: 原始Excel文件路径
    query_results: 查询结果列表(包含序号、源行号、部件编号等)
    valid_ids_for_backfill: 已验证的、需要回填的查询结果序号列表

    返回:
    (新文件路径, 回填统计信息)
    """
    # 建立序号到查询结果的映射
    id_to_result = {}
    for result in query_results:
        try:
            result_id = int(result.get("序号", 0))
            if result_id > 0:
                id_to_result[result_id] = result
        except (ValueError, TypeError):
            continue

    # 加载原始Excel文件
    wb = load_workbook(original_file)
    ws = wb.active

    # 找到"部件编号"列的索引
    part_no_col = None
    for col_idx, cell in enumerate(ws[1], 1):
        if cell.value and str(cell.value).strip() == "部件编号":
            part_no_col = col_idx
            break

    if part_no_col is None:
        raise ValueError("未找到'部件编号'列")

    backfill_stats = {
        "total_selected": len(valid_ids_for_backfill),
        "backfilled": 0,
        "not_found": 0,
        "already_filled": 0,
        "details": []
    }

    # 执行回填
    for result_id in valid_ids_for_backfill:
        if result_id not in id_to_result:
            backfill_stats["details"].append({
                "序号": result_id,
                "源行号": None,
                "状态": "未找到对应查询结果",
                "原部件编号": "",
                "新部件编号": ""
            })
            backfill_stats["not_found"] += 1
            continue

        result = id_to_result[result_id]

        try:
            source_row = int(result.get("源行号", 0))
            part_no = result.get("部件编号", "")
            chinese_name = result.get("中文名称", "")

            if source_row < 1 or source_row > ws.max_row:
                logger.warning(f"源行号 {source_row} 超出工作表范围,跳过")
                continue

            current_value = ws.cell(row=source_row, column=part_no_col).value
            current_value_str = str(current_value) if current_value else ""

            detail = {
                "序号": result_id,
                "源行号": source_row,
                "中文名称": chinese_name,
                "原部件编号": current_value_str,
                "新部件编号": part_no
            }

            if part_no:
                ws.cell(row=source_row, column=part_no_col, value=part_no)

                if not current_value or current_value_str.strip() == "":
                    backfill_stats["backfilled"] += 1
                    detail["状态"] = "已回填"
                else:
                    backfill_stats["already_filled"] += 1
                    detail["状态"] = "已覆盖"
            else:
                backfill_stats["not_found"] += 1
                detail["状态"] = "部件编号为空"

            backfill_stats["details"].append(detail)

        except (ValueError, TypeError) as e:
            logger.warning(f"处理序号 {result_id} 时出错: {e}")
            continue

    # 生成新文件
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    base_name = os.path.splitext(os.path.basename(original_file))[0]
    new_file = f"{base_name}_回填后_{timestamp}.xlsx"

    # 高亮显示回填的单元格
    if backfill_stats["backfilled"] > 0 or backfill_stats["already_filled"] > 0:
        fill = PatternFill(start_color="FFFF00", end_color="FFFF00", fill_type="solid")
        for detail in backfill_stats["details"]:
            source_row = detail.get("源行号")
            if source_row and 1 <= source_row <= ws.max_row:
                cell = ws.cell(row=source_row, column=part_no_col)
                cell.fill = fill

    wb.save(new_file)
    wb.close()

    return new_file, backfill_stats


def generate_diff_report(
    original_file: str,
    backfilled_file: str,
    backfill_stats: Dict[str, Any],
    selected_result_ids: List[int]
) -> str:
    """生成原文件和回填文件的对比差异报告"""
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    base_name = os.path.splitext(os.path.basename(original_file))[0]

    diff_file = f"{base_name}_差异对比_{timestamp}.xlsx"
    wb = Workbook()

    ws_diff = wb.active
    ws_diff.title = "差异详情"

    headers = ["查询序号", "源行号", "中文名称", "原部件编号", "新部件编号", "状态"]
    for col, header in enumerate(headers, 1):
        cell = ws_diff.cell(row=1, column=col, value=header)
        cell.font = Font(bold=True)
        cell.fill = PatternFill(start_color="4CAF50", end_color="4CAF50", fill_type="solid")
        cell.alignment = Alignment(horizontal="center")

    for row_idx, detail in enumerate(backfill_stats["details"], 2):
        ws_diff.cell(row=row_idx, column=1, value=detail.get("序号", ""))
        ws_diff.cell(row=row_idx, column=2, value=detail.get("源行号", ""))
        ws_diff.cell(row=row_idx, column=3, value=detail.get("中文名称", ""))
        ws_diff.cell(row=row_idx, column=4, value=detail.get("原部件编号", ""))
        ws_diff.cell(row=row_idx, column=5, value=detail.get("新部件编号", ""))

        status_cell = ws_diff.cell(row=row_idx, column=6, value=detail["状态"])
        if "已回填" in detail["状态"]:
            status_cell.fill = PatternFill(start_color="90EE90", end_color="90EE90", fill_type="solid")
        elif "已覆盖" in detail["状态"]:
            status_cell.fill = PatternFill(start_color="FFD700", end_color="FFD700", fill_type="solid")
        else:
            status_cell.fill = PatternFill(start_color="FFB6C1", end_color="FFB6C1", fill_type="solid")

    ws_summary = wb.create_sheet("统计汇总")

    summary_data = [
        ["项目", "数量", "说明"],
        ["选中查询序号总数", backfill_stats["total_selected"], ""],
        ["成功回填数", backfill_stats["backfilled"], "原为空,已回填部件编号"],
        ["已覆盖数", backfill_stats["already_filled"], "原已有值,已覆盖"],
        ["未找到数", backfill_stats["not_found"], "查询结果中无对应记录"],
        ["", "", ""],
        ["选中的查询序号", ", ".join(map(str, selected_result_ids)), ""],
        ["", "", ""],
        ["生成时间", datetime.now().strftime("%Y-%m-%d %H:%M:%S"), ""],
        ["原始文件", original_file, ""],
        ["回填后文件", backfilled_file, ""]
    ]

    for row_idx, row_data in enumerate(summary_data, 1):
        for col_idx, value in enumerate(row_data, 1):
            cell = ws_summary.cell(row=row_idx, column=col_idx, value=value)
            if row_idx == 1:
                cell.font = Font(bold=True)
                cell.fill = PatternFill(start_color="4472C4", end_color="4472C4", fill_type="solid")
                cell.font = Font(bold=True, color="FFFFFF")

    for ws in [ws_diff, ws_summary]:
        for column in ws.columns:
            max_length = 0
            column_letter = column[0].column_letter
            for cell in column:
                try:
                    if cell.value and len(str(cell.value)) > max_length:
                        max_length = len(str(cell.value))
                except:
                    pass
            adjusted_width = min(max_length + 2, 50)
            ws.column_dimensions[column_letter].width = adjusted_width

    wb.save(diff_file)
    wb.close()

    csv_diff_file = f"{base_name}_差异对比_{timestamp}.csv"
    with open(csv_diff_file, 'w', newline='', encoding='utf-8-sig') as f:
        writer = csv.writer(f)
        writer.writerow(headers)
        for detail in backfill_stats["details"]:
            writer.writerow([
                detail.get("序号", ""),
                detail.get("源行号", ""),
                detail.get("中文名称", ""),
                detail.get("原部件编号", ""),
                detail.get("新部件编号", ""),
                detail["状态"]
            ])

    logger.info(f"差异报告已生成: {diff_file}")
    return diff_file

# ==================== 报告生成模块 ====================
def generate_html_report(results: List[Dict[str, Any]], no_match_details: List[Dict[str, Any]], timestamp: str) -> str:
    """生成HTML报告"""
    html = f"""<!DOCTYPE html>
<html>
<head>
 <meta charset="UTF-8">
 <title>PDM查询结果报告</title>
 <style>
 body {{ font-family: Arial, sans-serif; margin: 20px; }}
 h1 {{ color: #333; }}
 h2 {{ color: #555; margin-top: 30px; }}
 table {{ border-collapse: collapse; width: 100%; margin-top: 20px; }}
 th, td {{ border: 1px solid #ddd; padding: 8px; text-align: left; }}
 th {{ background-color: #4CAF50; color: white; }}
 tr:nth-child(even) {{ background-color: #f2f2f2; }}
 .no-match {{ background-color: #ffcccc; }}
 .summary {{ margin-bottom: 20px; padding: 10px; background-color: #e7f3ff; border-radius: 5px; }}
 </style>
</head>
<body>
 <h1>PDM部件查询结果报告</h1>
 <div class="summary">
 <p><strong>生成时间:</strong> {timestamp}</p>
 <p><strong>查询结果数:</strong> {len(results)}</p>
 <p><strong>未匹配记录数:</strong> {len(no_match_details)}</p>
 </div>

 <h2>查询结果</h2>
 <table>
 <thead>
 <tr>
 <th>序号</th><th>源行号</th><th>中文名称</th><th>英文名称</th><th>英文代号</th>
 <th>部件编号</th><th>版本</th><th>生命周期状态</th><th>部件类型</th>
 <th>所属产品</th><th>工作组类1</th><th>工作组类2</th><th>工作组类3</th><th>查询策略</th>
 </tr>
 </thead>
 <tbody>
"""
    for result in results[:100]:
        html += f"""
 <tr>
 <td>{result.get('序号', '')}</td>
 <td>{result.get('源行号', '')}</td>
 <td>{result.get('中文名称', '')}</td>
 <td>{result.get('英文名称', '')}</td>
 <td>{result.get('英文代号', '')}</td>
 <td>{result.get('部件编号', '')}</td>
 <td>{result.get('版本', '')}</td>
 <td>{result.get('生命周期状态', '')}</td>
 <td>{result.get('部件类型', '')}</td>
 <td>{result.get('所属产品', '')}</td>
 <td>{result.get('工作组类1', '')}</td>
 <td>{result.get('工作组类2', '')}</td>
 <td>{result.get('工作组类3', '')}</td>
 <td>{result.get('查询策略', '')}</td>
 </tr>
"""
    html += """
 </tbody>
 </table>

 <h2>未匹配记录</h2>
 <table>
 <thead>
 <tr><th>序号</th><th>源行号</th><th>中文名称</th><th>英文名称</th><th>英文代号</th><th>原因</th></tr>
 </thead>
 <tbody>
"""
    for detail in no_match_details:
        html += f"""
 <tr class="no-match">
 <td>{detail.get('序号', '')}</td>
 <td>{detail.get('源行号', '')}</td>
 <td>{detail.get('中文名称', '')}</td>
 <td>{detail.get('英文名称', '')}</td>
 <td>{detail.get('英文代号', '')}</td>
 <td>{detail.get('原因', '')}</td>
 </tr>
"""
    html += """
 </tbody>
 </table>
</body>
</html>
"""
    return html

def generate_query_report(results: List[Dict[str, Any]], no_match_details: List[Dict[str, Any]], excel_path: str, output_format: str) -> str:
    """生成查询结果报告(支持CSV/XLSX/JSON/HTML)"""
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    base_name = os.path.splitext(os.path.basename(excel_path))[0]
    output_file = f"{base_name}_查询结果_{timestamp}.{output_format}"

    # 字段顺序:序号 → 源行号 → 中文名称 → 英文名称 → 英文代号 → 部件编号
    # → 版本 → 生命周期状态 → 部件类型 → 所属产品
    # → 工作组类1 → 工作组类2 → 工作组类3 → 查询策略
    output_fields = [
        "序号", "源行号", "中文名称", "英文名称", "英文代号", "部件编号",
        "版本", "生命周期状态", "部件类型", "所属产品",
        "工作组类1", "工作组类2", "工作组类3", "查询策略"
    ]

    if output_format.lower() == "csv":
        with open(output_file, 'w', newline='', encoding='utf-8-sig') as f:
            writer = csv.writer(f)
            writer.writerow(output_fields)
            for result in results:
                row = []
                for field in output_fields:
                    if field == "部件编号":
                        value = result.get(field, "")
                        if value:
                            row.append(f'="{value}"')
                        else:
                            row.append("")
                    else:
                        row.append(result.get(field, ""))
                writer.writerow(row)

    elif output_format.lower() == "xlsx":
        wb = Workbook()
        ws = wb.active
        ws.title = "查询结果"

        for col, header in enumerate(output_fields, 1):
            cell = ws.cell(row=1, column=col, value=header)
            cell.font = Font(bold=True)
            cell.fill = PatternFill(start_color="4CAF50", end_color="4CAF50", fill_type="solid")
            cell.alignment = Alignment(horizontal="center")

        for row_idx, result in enumerate(results, 2):
            for col_idx, field in enumerate(output_fields, 1):
                value = result.get(field, "")
                cell = ws.cell(row=row_idx, column=col_idx, value=str(value) if value else "")
                if field == "部件编号":
                    cell.number_format = '@'

        for column in ws.columns:
            max_length = 0
            column_letter = column[0].column_letter
            for cell in column:
                try:
                    if cell.value and len(str(cell.value)) > max_length:
                        max_length = len(str(cell.value))
                except:
                    pass
            adjusted_width = min(max_length + 2, 50)
            ws.column_dimensions[column_letter].width = adjusted_width

        if no_match_details:
            ws2 = wb.create_sheet("未匹配记录")
            headers2 = ["序号", "源行号", "中文名称", "英文名称", "英文代号", "原因"]
            for col, header in enumerate(headers2, 1):
                cell = ws2.cell(row=1, column=col, value=header)
                cell.font = Font(bold=True)
                cell.fill = PatternFill(start_color="FF5722", end_color="FF5722", fill_type="solid")

            for row_idx, detail in enumerate(no_match_details, 2):
                ws2.cell(row=row_idx, column=1, value=detail.get("序号", ""))
                ws2.cell(row=row_idx, column=2, value=detail.get("源行号", ""))
                ws2.cell(row=row_idx, column=3, value=detail.get("中文名称", ""))
                ws2.cell(row=row_idx, column=4, value=detail.get("英文名称", ""))
                ws2.cell(row=row_idx, column=5, value=detail.get("英文代号", ""))
                ws2.cell(row=row_idx, column=6, value=detail.get("原因", ""))

        wb.save(output_file)

    elif output_format.lower() == "json":
        with open(output_file, 'w', encoding='utf-8') as f:
            json.dump({
                "summary": {
                    "total_results": len(results),
                    "no_match_count": len(no_match_details),
                    "generated_at": timestamp
                },
                "results": results,
                "no_match_details": no_match_details
            }, f, ensure_ascii=False, indent=2)

    elif output_format.lower() == "html":
        with open(output_file, 'w', encoding='utf-8') as f:
            f.write(generate_html_report(results, no_match_details, timestamp))

    logger.info(f"查询结果已生成: {output_file}")
    return output_file

# ==================== 主查询功能 ====================
def run_query(
    excel_path: str,
    output_format: str = "xlsx"
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], str]:
    """执行查询,返回查询结果"""
    logger.info(f"开始处理文件: {excel_path}")
    start_time = time.time()

    try:
        data_rows, headers, source_rows = read_excel_with_openpyxl(excel_path, sheet_name="软件结构")
    except Exception as e:
        logger.error(f"读取Excel失败: {e}")
        raise

    required_cols = ["部件编号", "中文名称", "英文名称", "英文代号"]
    missing_cols = [col for col in required_cols if col not in headers]
    if missing_cols:
        raise ValueError(f"缺少必要列: {missing_cols}")

    # 收集需要查询的行(部件编号为空的行)
    target_info = []
    for idx, row in enumerate(data_rows):
        part_no = row.get("部件编号", "")
        if not part_no or part_no.strip() == "":
            target_info.append({
                "source_row": source_rows[idx],
                "row_data": row
            })

    total_rows = len(target_info)
    logger.info(f"发现 {total_rows} 行部件编号为空")

    if total_rows == 0:
        logger.info("无需查询")
        return [], [], None

    results = []
    no_match_details = []
    auth = PDMAuth()
    global_counter = 0

    try:
        iterator = tqdm(target_info, desc="查询PDM进度", unit="行") if HAS_TQDM else target_info

        for item in iterator:
            source_row = item["source_row"]
            row = item["row_data"]

            query_results, strategy = smart_query_strategy(row, auth)

            if query_results:
                for result in query_results:
                    global_counter += 1
                    part_no = get_pdm_field(result, ["PART_NO", "partNo"])

                    output_row = {
                        "序号": global_counter,
                        "源行号": source_row,
                        "中文名称": row.get("中文名称", ""),
                        "英文名称": row.get("英文名称", ""),
                        "英文代号": row.get("英文代号", ""),
                        "部件编号": part_no,
                        "版本": get_pdm_field(result, ["VERSION", "version"]),
                        "生命周期状态": get_pdm_field(result, ["LIFECYCLE_STATUS", "lifecycleStatus"]),
                        "部件类型": get_pdm_field(result, ["PART_TYPE", "partType"]),
                        "所属产品": get_pdm_field(result, ["PRODUCT_MODEL", "productModel", "PRODUCT_NO"]),
                        "工作组类1": get_pdm_field(result, ["TEAM1", "team1"]),
                        "工作组类2": get_pdm_field(result, ["TEAM2", "team2"]),
                        "工作组类3": get_pdm_field(result, ["TEAM3", "team3"]),
                        "查询策略": strategy
                    }
                    results.append(output_row)

                match_count = len(query_results)
                if match_count > 1:
                    logger.debug(f"源行号 {source_row}: 找到 {match_count} 个匹配的部件编号")
            else:
                no_match_details.append({
                    "序号": global_counter + 1 if results else 1,
                    "源行号": source_row,
                    "中文名称": row.get("中文名称", ""),
                    "英文名称": row.get("英文名称", ""),
                    "英文代号": row.get("英文代号", ""),
                    "原因": "未找到匹配结果"
                })
                logger.debug(f"源行号 {source_row}: 未找到匹配结果")

    finally:
        auth.close()

    elapsed_time = time.time() - start_time
    logger.info(f"查询完成,耗时 {elapsed_time:.1f} 秒,共获得 {len(results)} 条记录")

    output_file = generate_query_report(results, no_match_details, excel_path, output_format)

    return results, no_match_details, output_file, elapsed_time

# ==================== 主函数 ====================
def main():
    parser = argparse.ArgumentParser(
        description="PDM部件查询汇总工具 - 完整功能版",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
 # 查询模式 - 生成查询结果文件
 %(prog)s query 软件组件清单.xlsx
 %(prog)s query 软件组件清单.xlsx --output-format json
 %(prog)s query 软件组件清单.xlsx --output-format html

 # 回填模式 - 根据已有的查询结果文件回填
 %(prog)s backfill 软件组件清单.xlsx --result-file 查询结果_20241215_143022.xlsx --result-ids 3,4,5
 %(prog)s backfill 软件组件清单.xlsx --result-file 查询结果.json --result-ids 3,4,5
 %(prog)s backfill 软件组件清单.xlsx --result-file 查询结果.html --result-ids 3,4,5

 # 模拟运行
 %(prog)s backfill 软件组件清单.xlsx --result-file 查询结果.xlsx --result-ids 3,4,5 --dry-run

环境变量:
 coclaw_empno / RDC_EMP_NO - 工号 (必填,仅查询时需要)
 coclaw_token / RDC_AUTH_TOKEN - 认证token (必填,仅查询时需要)
 """
    )

    subparsers = parser.add_subparsers(dest="command", help="子命令")

    # 查询命令
    query_parser = subparsers.add_parser("query", help="查询PDM,生成查询结果文件")
    query_parser.add_argument("excel_file", help="Excel文件路径")
    query_parser.add_argument("--output-format", "-f", choices=["csv", "xlsx", "json", "html"],
                              default="xlsx", help="输出格式(默认xlsx)")
    query_parser.add_argument("--dry-run", "-d", action="store_true", help="模拟运行")

    # 回填命令
    backfill_parser = subparsers.add_parser("backfill", help="根据查询结果文件回填部件编号")
    backfill_parser.add_argument("excel_file", help="原始Excel文件路径")
    backfill_parser.add_argument("--result-file", "-r", required=True,
                                 help="查询结果文件路径(支持 CSV、XLSX、JSON、HTML 格式)")
    backfill_parser.add_argument("--result-ids", "-i", required=True,
                                 help="要回填的查询结果序号,用逗号分隔,如: 3,4,5")
    backfill_parser.add_argument("--dry-run", "-d", action="store_true", help="模拟运行,不实际回填")
    backfill_parser.add_argument("--no-prompt", action="store_true", help="跳过交互确认,自动继续回填(即使有空行未被覆盖)")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(1)

    try:
        if args.command == "query":
            # 查询模式
            if args.dry_run:
                print("\n[Dry-run模式] 仅模拟查询,不会生成文件")

            results, no_match_details, output_file, elapsed_time = run_query(args.excel_file, args.output_format)

            print("\n" + "=" * 50)
            print("查询完成！")
            print("=" * 50)

            # ==================== 文件清单 ====================
            print(f"\n📁 文档清单:")
            print(f" 📄 原始文件: {os.path.basename(args.excel_file)}")
            if output_file and not args.dry_run:
                print(f" 📄 查询结果: {os.path.basename(output_file)}")

            # ==================== 查询统计 ====================
            print(f"\n📊 查询统计:")
            print(f" 🔢 查询到记录: {len(results)}")
            if results:
                unique_source_rows = sorted(set([r['源行号'] for r in results]))
                print(f" 📍 涉及源行号: {unique_source_rows}")
            print(f" ⚠️ 未匹配行数: {len(no_match_details)}")
            print(f" ⏱️ 耗时: {elapsed_time:.1f} 秒")

            # ==================== 关键信息 ====================
            if results:
                # 统计多匹配情况
                from collections import Counter
                serial_counts = Counter([r['序号'] for r in results])
                multi_serial = {s: c for s, c in serial_counts.items() if c > 1}

                total_empty_rows = len([r for r in results if r.get('部件编号')])
                print(f"\n🎯 关键信息:")
                if len(no_match_details) == 0:
                    print(f" ✅ 所有行都找到了匹配")
                if multi_serial:
                    print(f" ⚠️ 存在多匹配情况：")
                    for source_row, count in multi_serial.items():
                        print(f" 源行号{source_row}：{count}个匹配")
                    print(f" 📍 使用序号回填：可使用查询结果中的'序号'列精确选择要回填的匹配项")

                # ==================== 后续操作建议 ====================
                if results and not args.dry_run:
                    print(f"\n💡 后续操作建议")
                    print(f" 如需回填部件编号到原文件，请使用命令：")
                    sample_ids = [str(r['序号']) for r in results[:3]]
                    print(f" python {sys.argv[0]} backfill {args.excel_file} --result-file {os.path.basename(output_file)} --result-ids {','.join(sample_ids)}")

            print("=" * 50)

        elif args.command == "backfill":
            # 回填模式(v5.7.0 - 职责分离)
            selected_result_ids = [int(s.strip()) for s in args.result_ids.split(",")]
            print(f"\n将要回填的查询结果序号: {selected_result_ids}")
            print(f"使用查询结果文件: {args.result_file}")

            # ==================== 第一步:读取查询结果文件 ====================
            try:
                query_results = read_query_result_file(args.result_file)
                if not query_results:
                    print("❌ 查询结果文件为空,无法回填")
                    sys.exit(1)
            except Exception as e:
                print(f"❌ 读取查询结果文件失败: {e}")
                sys.exit(1)

            # 建立序号到查询结果的映射( reuse)
            id_to_result = {}
            for result in query_results:
                try:
                    result_id = int(result.get("序号", 0))
                    if result_id > 0:
                        id_to_result[result_id] = result
                except (ValueError, TypeError):
                    continue

            # ==================== 循环检测(职责分离) ====================
            retry_count = 0
            MAX_RETRY = 3

            while True:
                retry_count += 1
                if retry_count > MAX_RETRY:
                    print(f"\n❌ 已达到最大重试次数 ({MAX_RETRY}),回填操作终止。")
                    sys.exit(1)

                print(f"\n{'='*60}")
                print(f"第 {retry_count} 次检查")
                print(f"{'='*60}")

                # ==================== 使用验证模块(v5.7.0 职责分离) ====================
                errors, warnings, uncovered_empty_rows = validate_backfill_operation(
                    query_results=query_results,
                    selected_result_ids=selected_result_ids,
                    excel_file=args.excel_file
                )

                # ==================== 准备后续统计所需的数据 ====================
                # 从验证结果中提取部件编号为空的ID列表
                empty_partno_ids = []
                for error in errors:
                    if error.get('type') == 'empty_partno':
                        for detail in error.get('details', []):
                            # detail 格式: "序号 {id}: 部件编号为空 (中文名称: ...)"
                            if detail.startswith('序号 '):
                                try:
                                    rid = int(detail.split(':')[0].replace('序号 ', ''))
                                    empty_partno_ids.append(rid)
                                except:
                                    pass

                # 获取用户选中的有效序号对应的源行号(用于统计覆盖率)
                selected_source_rows = set()
                for result_id in selected_result_ids:
                    if result_id in id_to_result and result_id not in empty_partno_ids:
                        try:
                            source_row = int(id_to_result[result_id].get("源行号", 0))
                            if source_row > 0:
                                selected_source_rows.add(source_row)
                        except (ValueError, TypeError):
                            continue

                # 获取原始文件中的空行总数
                total_empty_rows = 0
                try:
                    data_rows, headers, source_rows = read_excel_with_openpyxl(args.excel_file, sheet_name="软件结构")
                    for row in data_rows:
                        part_no = row.get("部件编号", "")
                        if not part_no or part_no.strip() == "":
                            total_empty_rows += 1
                except Exception:
                    pass



                # ==================== 显示所有错误和警告 ====================
                if errors:
                    print("\n" + "=" * 70)
                    print("🚫 回填被阻止 - 存在以下必须解决的错误")
                    print("=" * 70)

                    for i, error in enumerate(errors, 1):
                        print(f"\n{error['title']}")
                        print(f" {error['message']}")
                        if error['details']:
                            print(" 详情:")
                            for detail in error['details'][:5]:
                                print(f" - {detail}")
                            if len(error['details']) > 5:
                                print(f" ... 还有 {len(error['details']) - 5} 项")
                        print(f" 🔧 解决方案: {error['required_action']}")

                    print("\n" + "=" * 70)
                    print("❌ 回填操作已停止,请解决上述错误后重试。")
                    print("=" * 70)

                    # ==================== 检查是否有必须停止的错误 ====================
                    has_empty_partno_error = any(e.get('type') == 'empty_partno' for e in errors)

                    # 如果有部件编号为空的错误,必须停止(无论是否--no-prompt)
                    if has_empty_partno_error:
                        print("\n⚠️ 注意:存在【部件编号】为空的序号,必须解决。")
                        print(" 请重新选择有效的序号(部件编号不能为空)")

                        # 在--no-prompt模式下自动退出
                        if args.no_prompt:
                            print("\n[自动模式] 由于存在必须解决的错误,回填操作终止。")
                            print("请修正错误后重新执行命令。")
                            sys.exit(1)
                        else:
                            # 非自动模式，询问是否重新输入序号
                            user_input = input("\n是否重新输入要回填的序号?(Y=重新输入 / N=退出): ").strip().upper()

                            if user_input == 'Y':
                                new_ids_input = input("请输入新的序号(用逗号分隔,如: 3,4,5): ").strip()
                                if new_ids_input:
                                    try:
                                        selected_result_ids = [int(s.strip()) for s in new_ids_input.split(",")]
                                        print(f"\n✅ 已更新要回填的序号: {selected_result_ids}")
                                        continue  # 重新开始检查
                                    except ValueError:
                                        print("❌ 序号格式错误,请输入数字并用逗号分隔")
                                        sys.exit(1)
                                else:
                                    print("❌ 未输入新序号,回填终止")
                                    sys.exit(1)
                            else:
                                print("❌ 回填操作已终止")
                                sys.exit(1)
                    else:
                        # 其他类型的错误（序号不存在、冲突等）
                        # 在--no-prompt模式下自动退出
                        if args.no_prompt:
                            print("\n[自动模式] 检测到错误,回填操作终止。")
                            print("请修正错误后重新执行命令。")
                            sys.exit(1)
                        else:
                            # 非自动模式，询问是否重新输入序号
                            user_input = input("\n是否重新输入要回填的序号?(Y=重新输入 / N=退出): ").strip().upper()

                            if user_input == 'Y':
                                new_ids_input = input("请输入新的序号(用逗号分隔,如: 3,4,5): ").strip()
                                if new_ids_input:
                                    try:
                                        selected_result_ids = [int(s.strip()) for s in new_ids_input.split(",")]
                                        print(f"\n✅ 已更新要回填的序号: {selected_result_ids}")
                                        continue  # 重新开始检查
                                    except ValueError:
                                        print("❌ 序号格式错误,请输入数字并用逗号分隔")
                                        sys.exit(1)
                                else:
                                    print("❌ 未输入新序号,回填终止")
                                    sys.exit(1)
                            else:
                                print("❌ 回填操作已终止")
                                sys.exit(1)

                # 如果有警告但没有错误,显示警告并询问是否继续
                if warnings and not errors:
                    print("\n" + "=" * 70)
                    print("⚠️ 检测到以下警告(不影响回填,但请注意)")
                    print("=" * 70)

                    for warning in warnings:
                        print(f"\n{warning['title']}")
                        print(f" {warning['message']}")
                        if warning['details']:
                            print(" 详情:")
                            for detail in warning['details'][:5]:
                                print(f" - {detail}")
                            if len(warning['details']) > 5:
                                print(f" ... 还有 {len(warning['details']) - 5} 项")
                        print(f" 💡 建议: {warning['suggestion']}")

                    print("\n" + "=" * 70)

                    # ==================== 使用 --no-prompt 参数 ====================
                    if args.no_prompt:
                        print("\n[自动模式] 跳过警告确认,继续回填...")
                    else:
                        while True:
                            user_choice = input("\n是否继续执行回填?(Y=继续 / N=退出 / V=查看警告详情): ").strip().upper()

                            if user_choice == 'Y':
                                print("\n✅ 用户确认继续回填...")
                                break
                            elif user_choice == 'N':
                                print("\n❌ 用户取消回填操作。")
                                sys.exit(0)
                            elif user_choice == 'V':
                                print("\n" + "=" * 70)
                                print("警告详情")
                                print("=" * 70)
                                for warning in warnings:
                                    print(f"\n{warning['title']}")
                                    for detail in warning['details']:
                                        print(f" - {detail}")
                                print("\n" + "=" * 70)
                            else:
                                print("❌ 无效输入,请输入 Y、N 或 V")

                # 没有错误,可以继续
                if not errors:
                    print("\n" + "=" * 60)
                    print("✅ 所有错误已解决,通过验证!")
                    print("=" * 60)
                    print(f"将回填 {len([rid for rid in selected_result_ids if rid in id_to_result and rid not in empty_partno_ids])} 个有效序号。")
                    if total_empty_rows > 0:
                        covered_count = len(selected_source_rows)
                        print(f"原始文件中共有 {total_empty_rows} 行【部件编号】为空,本次将覆盖 {covered_count} 行。")
                        if uncovered_empty_rows:
                            print(f"⚠️ 仍有 {len(uncovered_empty_rows)} 行未被覆盖,将保持为空。")
                    print("=" * 60)

                    # ==================== 最终确认(使用 --no-prompt 参数) ====================
                    if args.no_prompt:
                        print("\n[自动模式] 跳过最终确认,开始执行回填...")
                    else:
                        print("\n请确认是否执行回填操作?")
                        while True:
                            confirm = input("(Y=确认回填 / N=取消回填): ").strip().upper()
                            if confirm == 'Y':
                                print("\n✅ 用户确认,开始执行回填...")
                                break
                            elif confirm == 'N':
                                print("\n❌ 用户取消回填操作。")
                                sys.exit(0)
                            else:
                                print("❌ 无效输入,请输入 Y 或 N")
                    break  # 退出循环,执行回填

            # ==================== 显示回填记录预览 ====================
            print("\n" + "=" * 60)
            print("回填记录预览")
            print("=" * 60)
            print(f"{'序号':<8}{'源行号':<10}{'中文名称':<35}{'部件编号':<20}")
            print("-" * 80)

            valid_ids_for_backfill = []
            for result_id in selected_result_ids:
                if result_id in id_to_result:
                    result = id_to_result[result_id]
                    part_no = result.get("部件编号", "")
                    # 只显示有效的(非空部件编号)
                    if part_no and str(part_no).strip():
                        source_row = result.get("源行号", "")
                        chinese_name = result.get("中文名称", "")[:33]
                        print(f"{result_id:<8}{source_row:<10}{chinese_name:<35}{part_no:<20}")
                        valid_ids_for_backfill.append(result_id)

            print("=" * 60)

            # ==================== Dry-run 判断 ====================
            if args.dry_run:
                print("\n[Dry-run模式] 以上为回填预览,不会实际修改文件。")
                print("如需实际执行回填,请去掉 --dry-run 参数重新运行。")
                return

            # ==================== 执行回填 ====================
            try:
                # 执行回填
                backfilled_file, backfill_stats = backfill_by_result_ids(
                    args.excel_file, query_results, valid_ids_for_backfill
                )

                # 生成差异报告
                diff_file = generate_diff_report(
                    args.excel_file, backfilled_file, backfill_stats, valid_ids_for_backfill
                )

                # 保存完成状态标志(用于后续判断)
                has_uncovered = len(uncovered_empty_rows) > 0
                total_selected = len(valid_ids_for_backfill)
                success_count = backfill_stats['backfilled']
                overwrite_count = backfill_stats['already_filled']

                # ==================== 判断回填完成状态（使用保存的标志） ====================
                # 使用保存的标志
                if has_uncovered:
                    # ==================== 部分完成 - 醒目显示 ====================
                    print("\n" + "=" * 60)
                    print("🔔 " + "=" * 57)
                    print("🔔 回填操作完成【⚠️ 部分完成】")
                    print("🔔 " + "=" * 57)
                    print("=" * 60)
                    completion_status = "⚠️ 部分完成"
                else:
                    # ==================== 全部完成 ====================
                    print("\n" + "=" * 60)
                    print("✅ " + "=" * 57)
                    print("✅ 回填操作完成【全部完成】")
                    print("✅ " + "=" * 57)
                    print("=" * 60)
                    completion_status = "✅ 全部完成"

                # ==================== 操作统计 ====================
                print(f"\n📊 操作统计")
                print("-" * 40)
                print(f" 📄 查询结果文件: {os.path.basename(args.result_file)}")
                print(f" 🔢 回填序号: {', '.join(map(str, selected_result_ids))}")
                print(f" ✅ 有效回填: {success_count + overwrite_count} 条", end="")
                if success_count > 0 and overwrite_count == 0:
                    print("（均为新增）")
                elif overwrite_count > 0 and success_count == 0:
                    print("（均为覆盖）")
                elif success_count > 0 and overwrite_count > 0:
                    print(f"（新增 {success_count} 条，覆盖 {overwrite_count} 条）")
                else:
                    print()
                if has_uncovered:
                    print(f" ⚠️ 未覆盖行数: {len(uncovered_empty_rows)} 行")
                print(f" 📊 回填状态: {completion_status}")

                # ==================== 生成文件列表 ====================
                print(f"\n📄 生成文件")
                print("-" * 40)
                print(f" 📄 回填后文件: {os.path.basename(backfilled_file)}")
                print(f" 📄 差异报告: {os.path.basename(diff_file)}")

                # ==================== 未覆盖信息（醒目显示） ====================
                if has_uncovered:
                    # 获取未覆盖的序号列表
                    missing_serials = []
                    missing_rows_info = []
                    for item in uncovered_empty_rows:
                        for rid, result in id_to_result.items():
                            try:
                                if int(result.get("源行号", 0)) == item['row_num']:
                                    missing_serials.append(rid)
                                    missing_rows_info.append(f"第{item['row_num']}行（序号 {rid}）")
                                    break
                            except:
                                pass

                    print(f"\n⚠️" + "-" * 58)
                    print(f"⚠️ 【未覆盖警告】仍有 {len(uncovered_empty_rows)} 行【部件编号】为空未被覆盖")
                    print(f"⚠️" + "-" * 58)
                    for item in uncovered_empty_rows[:5]:
                        print(f" ⚠️ {item['chinese_name'][:35]} (第{item['row_num']}行)")
                    if len(uncovered_empty_rows) > 5:
                        print(f" ⚠️ ... 还有 {len(uncovered_empty_rows) - 5} 行")

                    # ==================== 补充建议（醒目显示） ====================
                    if missing_serials:
                        print(f"\n💡" + "-" * 58)
                        print(f"💡 【补充建议】如需完成全部回填，请执行以下命令：")
                        print(f"💡" + "-" * 58)
                        print(f"\n python {sys.argv[0]} backfill {args.excel_file} \\")
                        print(f" --result-file {args.result_file} \\")
                        print(f" --result-ids {','.join(map(str, missing_serials))}")
                        if args.no_prompt:
                            print(f" --no-prompt")
                        print()

                        # 同时显示简要说明
                        print(f" 📌 说明：上述命令将补充回填序号 {', '.join(map(str, missing_serials))}")
                        print(f" 对应行：{', '.join(missing_rows_info)}")
                else:
                    # 全部完成时的提示
                    total_empty_rows = 0
                    try:
                        data_rows, headers, source_rows = read_excel_with_openpyxl(args.excel_file, sheet_name="软件结构")
                        for row in data_rows:
                            part_no = row.get("部件编号", "")
                            if not part_no or part_no.strip() == "":
                                total_empty_rows += 1
                    except Exception:
                        total_empty_rows = 0

                    if total_empty_rows > 0:
                        print(f"\n🎉 完美覆盖: 原始文件中共有 {total_empty_rows} 行【部件编号】为空，本次回填已全部覆盖！")

                    print("=" * 60)

            except ValueError as e:
                print(f"\n❌ 回填执行失败: {e}")
                sys.exit(1)

            except ValueError as e:
                print(f"\n❌ 回填执行失败: {e}")
                sys.exit(1)
    except KeyboardInterrupt:
        print("\n用户中断")
        sys.exit(1)
    except Exception as e:
        logger.error(f"处理失败: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
