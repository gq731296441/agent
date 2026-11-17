# -*- coding: utf-8 -*-
"""
软件组件清单完整校验工具
功能：
1. 文档格式检查（模板完整性、必需列、数据完整性）
2. PDM文档详情查询（文档状态、产品信息等）
3. 部件编号校验（通过 PDM 校验接口，仅校验组件和软件模块）
4. 代码库/目录拆分校验（通过 Gerrit 拆分/校验接口）
5. 组件对应的测试代码目录、UT代码目录、FT代码目录的拆分校验
6. 代码路径校验（通过 Gerrit 路径校验接口，检查路径是否存在于代码库中）
7. 将完整校验结果输出到Excel文件

使用方法：
    python script.py <Excel文件路径>
"""

import os
import sys
import re
from datetime import datetime

# 将项目根目录加入路径，保证 scripts 包可导入（独立运行或统一编排均可用）
_project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

import requests
import pandas as pd

# 设置编码
import codecs
if sys.version_info[0] < 3:
    reload(sys)
    sys.setdefaultencoding('utf-8')
    sys.stdout = codecs.getwriter('utf-8')(sys.stdout)

# ==================== 全局配置 ====================


def check_directory_exists(repo_name, directory):
    """
    通过 Gerrit 路径校验接口检查代码库中路径是否存在

    参数:
        repo_name: 代码库名称
        directory: 代码路径（相对路径，可能为空）

    返回:
        (exists: bool, message: str)
    """
    from scripts.api_clients.path_check_api_client import create_path_check_api_client

    # 如果路径为空，视为通过（无路径需要校验）
    if not directory or not directory.strip():
        return True, '路径为空，无需校验'

    client = create_path_check_api_client()
    return client.check_path(repo_name, directory)


def validate_paths_for_split(repo_list, dir_list):
    """
    对拆分后的多个 (repo, dir) 组合进行路径校验

    返回:
        (path_check_result: str, path_check_remarks: str, path_detail: str)
    """
    if not repo_list:
        return '不通过', '无代码库', ''

    # 路径和代码库数量对齐
    dir_list = list(dir_list) if dir_list else []
    if len(dir_list) < len(repo_list):
        dir_list.extend([''] * (len(repo_list) - len(dir_list)))

    all_ok = True
    details = []
    invalid_items = []

    for repo, directory in zip(repo_list, dir_list):
        repo = repo.strip()
        directory = (directory or '').strip()

        if not repo:
            all_ok = False
            details.append(f"[{repo or '空'}] 代码库为空")
            invalid_items.append(f"{repo or '空'}:代码库为空")
            continue

        exists, msg = check_directory_exists(repo, directory)
        if exists:
            details.append(f"[{repo}/{directory}] {msg}")
        else:
            all_ok = False
            details.append(f"[{repo}/{directory}] {msg}")
            invalid_items.append(f"{repo}/{directory}")

    if all_ok:
        return '通过', '所有代码路径均存在于对应代码库中', '; '.join(details)
    else:
        return '不通过', f'以下代码路径不存在: {", ".join(invalid_items)}', '; '.join(details)


# ==================== 第一部分：文档格式检查 ====================

def check_document_format(file_path, doc_no):
    """
    检查文档格式
    返回: (component_doc_check_result, doc_info_dict)
    """
    component_doc_check_result = ""
    doc_info = {}

    if not doc_no:
        component_doc_check_result = "未提供文档编号，跳过PDM文档查询"
        print("提示: 未提供文档编号，将只进行Excel格式检查")

        try:
            excel_data = pd.read_excel(file_path, sheet_name=None)
            sheet_names = list(excel_data.keys())

            if '软件结构' not in sheet_names:
                component_doc_check_result += "未使用模板，无\"软件结构\"sheet"
            else:
                df = pd.read_excel(file_path, sheet_name='软件结构', engine='openpyxl')
                required_columns = ['层次', '部件类别', '中文名称', '英文名称', '英文代号', '部件编号',
                                    '代码库/目录', '是否核心组件/模块', '组件对应的测试代码目录', '组件对应的UT代码目录', '组件对应的FT代码目录',
                                    '是否航天级组件', '组件/模块代码量统计专用分支', ]
                if all(column in df.columns for column in required_columns):
                    error_condition = df['中文名称'].notna() & df['层次'].isna()
                    if error_condition.any():
                        component_doc_check_result += "存在层次为空的部件"

                    df_filtered = df[df['部件类别'].isin(['软件子系统', '软件模块', '组件'])]
                    if df_filtered.empty:
                        component_doc_check_result += "不包含软件子系统、软件模块/组件信息"
                    else:
                        df_modules = df_filtered[df_filtered['部件类别'].isin(['软件模块', '组件'])]
                        if not df_modules.empty:
                            error_condition2 = df_modules['中文名称'].notna() & df_modules['代码库/目录'].isna()
                            if error_condition2.any():
                                component_doc_check_result += "存在组件/软件模块对应\"代码库/目录\"未填写"
                else:
                    component_doc_check_result += "未使用最新模板，缺少关键列。模板获取链接：https://i.zte.com.cn/index/ispace/#/space/6ceb1c0d972d48bb9568b0842f27bd0e/wiki/page/a442ea70abfc48fbbfd5f971ceeced50/view"
        except Exception as e:
            component_doc_check_result += f"读取Excel文件时出错: {str(e)}"
            print(f"读取Excel文件出错: {e}")

        return component_doc_check_result, doc_info

    from scripts.api_clients.pdm_doc_api_client import create_pdm_doc_api_client
    client = create_pdm_doc_api_client()

    try:
        # 查询文档详情（是否存在）
        doc_detail = client.get_doc_detail(doc_no)
        if doc_detail is None:
            component_doc_check_result = "此编号不存在"
            print(f"{doc_no} 此编号不存在，请与项目确认文档信息")
        else:
            # 查询文档详细信息
            doc_info_data = client.get_doc_info(doc_no)
            if doc_info_data:
                doc_info = {
                    'product': doc_info_data.get('product', ''),
                    'team1': doc_info_data.get('team1', ''),
                    'team2': doc_info_data.get('team2', ''),
                    'team3': doc_info_data.get('team3', ''),
                    'projectName': doc_info_data.get('projectName', ''),
                    'lastUpdatedDate': doc_info_data.get('lastUpdatedDate', ''),
                    'nameCn': doc_info_data.get('nameCn', ''),
                    'version': doc_info_data.get('version', ''),
                    'type1': doc_info_data.get('type1', ''),
                    'type2': doc_info_data.get('type2', ''),
                    'type3': doc_info_data.get('type3', ''),
                    'lastUpdatedBy': doc_info_data.get('lastUpdatedBy', '')
                }
            else:
                print(f"警告: 文档 {doc_no} 的详细信息查询结果为空")

            try:
                excel_data = pd.read_excel(file_path, sheet_name=None)
                sheet_names = list(excel_data.keys())

                if '软件结构' not in sheet_names:
                    component_doc_check_result += "未使用模板，无\"软件结构\"sheet"
                else:
                    df = pd.read_excel(file_path, sheet_name='软件结构', engine='openpyxl')

                    if df.empty:
                        component_doc_check_result += "软件结构sheet为空，没有数据"
                    else:
                        required_columns = ['层次', '部件类别', '中文名称', '英文名称', '英文代号', '部件编号',
                                            '代码库/目录', '是否核心组件/模块', '组件对应的测试代码目录', '组件对应的UT代码目录', '组件对应的FT代码目录',
                                            '是否航天级组件', '组件/模块代码量统计专用分支', ]
                        if all(column in df.columns for column in required_columns):
                            error_condition = df['中文名称'].notna() & df['层次'].isna()
                            if error_condition.any():
                                component_doc_check_result += "存在层次为空的部件"

                            df_filtered = df[df['部件类别'].isin(['软件子系统', '软件模块', '组件'])]
                            if df_filtered.empty:
                                component_doc_check_result += "不包含软件子系统、软件模块/组件信息"
                            else:
                                df_modules = df_filtered[df_filtered['部件类别'].isin(['软件模块', '组件'])]
                                if not df_modules.empty:
                                    error_condition2 = df_modules['中文名称'].notna() & df_modules['代码库/目录'].isna()
                                    if error_condition2.any():
                                        component_doc_check_result += "存在组件/软件模块对应\"代码库/目录\"未填写"
                        else:
                            missing_columns = [col for col in required_columns if col not in df.columns]
                            component_doc_check_result += f"未使用最新模板，缺少关键列: {', '.join(missing_columns)}。模板获取链接：https://i.zte.com.cn/index/ispace/#/space/6ceb1c0d972d48bb9568b0842f27bd0e/wiki/page/a442ea70abfc48fbbfd5f971ceeced50/view"
            except Exception as e:
                component_doc_check_result += f"读取Excel文件时出错: {str(e)}"
                print(f"读取Excel文件出错: {e}")

    except Exception as e:
        component_doc_check_result = f"接口调用异常: {str(e)}"
        print(f"调用接口时出错: {e}")

    return component_doc_check_result, doc_info


# ==================== 第二部分：代码库校验 ====================

def process_name(name):
    """
    处理代码库/目录列，分离代码库和代码路径

    通过 Gerrit 拆分接口（替代原 view_gerrit_repo_list 表查询）完成拆分。
    保留外层分隔符（逗号/换行/中文逗号）拆分逻辑，每个条目单独调用接口，
    拼接回逗号分隔的 repo 串和 path 串。

    Args:
        name: 代码库/目录单元格字符串

    Returns:
        (repo_str, path_str) 元组，均为逗号分隔字符串
    """
    if not name or pd.isnull(name):
        return '', ''

    from scripts.api_clients.gerrit_api_client import create_gerrit_api_client
    client = create_gerrit_api_client()

    name_str = str(name)

    has_comma = ',' in name_str
    has_newline = '\n' in name_str
    has_chinese_comma = '，' in name_str

    if has_comma or has_newline or has_chinese_comma:
        if has_comma:
            separator = ','
        elif has_newline:
            separator = '\n'
        else:
            separator = '，'

        if separator == '\n':
            pattern = r'[^a-zA-Z0-9\-_\/.\n]'
        elif separator == '，':
            pattern = r'[^a-zA-Z0-9\-_\/.，]'
        else:
            pattern = r'[^a-zA-Z0-9\-_\/.,]'

        cleaned_name = re.sub(pattern, '', name_str)
        parts = cleaned_name.split(separator)
        a = []
        b = []

        for part in parts:
            if not part or part.strip() == '':
                continue
            repo, path = client.split_repo_path(part)
            a.append(repo)
            b.append(path)
        return ','.join(a), ','.join(b)
    else:
        cleaned_name = re.sub(r'[^a-zA-Z0-9\-_\/.]', '', name_str)
        repo, path = client.split_repo_path(cleaned_name)
        return repo, path


def convert_to_str(value):
    if pd.isnull(value) or value is None:
        return ''
    if isinstance(value, float):
        if value.is_integer():
            return str(int(value))
        else:
            return str(value)
    return str(value)


def match_category(text):
    if pd.isnull(text) or text is None:
        return "False"

    categories = ["外部第三方组件", "软件子系统", "软件模块", "系统", "软件", "组件"]
    text_str = str(text)

    for category in categories:
        if category in text_str:
            return category
    return "False"


def should_validate_component(category):
    if pd.isnull(category) or category is None:
        return False

    category_str = str(category).strip()

    validate_categories = ["组件", "软件模块"]
    skip_categories = ["软件", "软件子系统", "外部第三方组件", "第三方组件"]

    # 先判需要校验的（精确匹配），避免"软件模块"被"软件"误杀
    if category_str in validate_categories:
        return True

    if category_str in skip_categories:
        return False

    return False    


def validate_pdm_numbers(df_excel):
    """
    校验部件编号是否存在（仅校验"组件"和"软件模块"类别）

    通过 PDM 校验接口（替代原 pdm_component 表全量查询）批量校验。

    Args:
        df_excel: Excel 软件结构 DataFrame

    Returns:
        pd.DataFrame: PDM 校验结果
    """
    from scripts.api_clients.pdm_verify_api_client import create_pdm_verify_api_client

    # 仅收集需要校验（组件/软件模块）且非空的部件编号
    part_no_to_index = {}
    for index, row in df_excel.iterrows():
        if '部件类别' in row:
            category = row['部件类别']
            if not should_validate_component(category):
                continue

        component_no = ''
        if '部件编号' in row:
            component_no = convert_to_str(row['部件编号']).strip()

        if component_no:
            part_no_to_index.setdefault(component_no, []).append(index)

    # 批量调用接口，建立 partNo -> exists 映射
    client = create_pdm_verify_api_client()
    exists_map = client.verify_parts(list(part_no_to_index.keys()))

    results = []
    for index, row in df_excel.iterrows():
        if '部件类别' in row:
            category = row['部件类别']
            if not should_validate_component(category):
                continue

        component_no = ''
        if '部件编号' in row:
            component_no = convert_to_str(row['部件编号']).strip()

        chinese_name = ''
        if '中文名称' in row and not pd.isnull(row['中文名称']):
            chinese_name = convert_to_str(row['中文名称']).strip()

        if component_no:
            if exists_map.get(component_no):
                check_result = "通过"
                remarks = "部件编号存在于pdm_component表中，在pdm存在"
            else:
                check_result = "不通过"
                remarks = "部件编号不存在于pdm_component表中，在pdm不存在"
        else:
            check_result = "不通过"
            remarks = "部件编号为空"
            component_no = "空"

        results.append({
            '行号': index + 2,
            '中文名称': chinese_name,
            '部件编号': component_no,
            'PDM校验结果': check_result,
            '校验说明': remarks
        })

    return pd.DataFrame(results)


def validate_code_repos(df_excel, enable_path_check=True):
    """校验代码库拆分结果 + 新增代码路径校验（通过代码库校验接口）"""
    results = []

    from scripts.api_clients.repo_check_api_client import create_repo_check_api_client

    # 第一遍：收集所有拆分出的代码库（去重），统一批量校验
    all_repos = set()
    for index, row in df_excel.iterrows():
        if '部件类别' in row:
            category = row['部件类别']
            if not should_validate_component(category):
                continue

        repo_path = ''
        if '代码库/目录' in row and not pd.isnull(row['代码库/目录']):
            repo_path = convert_to_str(row['代码库/目录']).strip()

        gerrit_repo, _ = process_name(repo_path)
        for repo in [r.strip() for r in gerrit_repo.split(',') if r.strip()]:
            all_repos.add(repo)

    client = create_repo_check_api_client()
    valid_repos_map = client.check_repos(list(all_repos))

    for index, row in df_excel.iterrows():
        if '部件类别' in row:
            category = row['部件类别']
            if not should_validate_component(category):
                continue

        repo_path = ''
        if '代码库/目录' in row and not pd.isnull(row['代码库/目录']):
            repo_path = convert_to_str(row['代码库/目录']).strip()

        chinese_name = ''
        if '中文名称' in row and not pd.isnull(row['中文名称']):
            chinese_name = convert_to_str(row['中文名称']).strip()

        gerrit_repo, gerrit_directory = process_name(repo_path)
        gerrit_repo = gerrit_repo.strip()
        gerrit_directory = gerrit_directory.strip() if gerrit_directory else ''

        # ===== 代码库拆分校验 =====
        if not repo_path:
            check_result = "不通过"
            remarks = "代码库/路径未填写"
        elif not gerrit_repo:
            check_result = "不通过"
            remarks = "代码库不存在或格式不正确"
        else:
            repo_list = [r.strip() for r in gerrit_repo.split(',') if r.strip()]
            all_valid = True
            invalid_repos = []

            for repo in repo_list:
                if not valid_repos_map.get(repo):
                    all_valid = False
                    invalid_repos.append(repo)

            if all_valid:
                check_result = "通过"
                remarks = "所有代码库都存在于数据库中"
            else:
                check_result = "部分不通过"
                remarks = f"以下代码库不存在于数据库中: {', '.join(invalid_repos)}"

        # ===== 拆分结果 =====
        split_repos = []
        split_dirs = []

        if ',' in gerrit_repo:
            split_repos = [r.strip() for r in gerrit_repo.split(',') if r.strip()]
            if gerrit_directory:
                split_dirs = [d.strip() for d in gerrit_directory.split(',')]
            else:
                split_dirs = [''] * len(split_repos)
        elif gerrit_repo:
            split_repos = [gerrit_repo]
            split_dirs = [gerrit_directory] if gerrit_directory else ['']

        if len(split_dirs) < len(split_repos):
            split_dirs.extend([''] * (len(split_repos) - len(split_dirs)))

        if split_repos:
            split_detail = f"代码库: {split_repos}, 路径: {split_dirs}"
        else:
            split_detail = "无"

        # ===== 新增：代码路径校验 =====
        if enable_path_check and split_repos and check_result != "不通过":
            path_check_result, path_check_remarks, path_detail = validate_paths_for_split(
                split_repos, split_dirs
            )
        elif not enable_path_check:
            path_check_result, path_check_remarks, path_detail = '未启用', '代码路径校验未启用', ''
        else:
            path_check_result, path_check_remarks, path_detail = '未校验', '代码库校验未通过，跳过路径校验', ''

        results.append({
            '行号': index + 2,
            '中文名称': chinese_name,
            '原始代码库/目录': repo_path,
            '代码库': gerrit_repo,
            '代码路径': gerrit_directory,
            '拆分后代码库数量': len(split_repos),
            '代码库拆分校验结果': check_result,
            '代码库拆分校验说明': remarks,
            '代码路径校验结果': path_check_result,
            '代码路径校验说明': path_check_remarks,
            '代码路径校验详情': path_detail,
            '拆分详情': split_detail
        })

    return pd.DataFrame(results)


def validate_additional_code_repos(df_excel, enable_path_check=True):
    """校验测试/UT/FT代码目录 + 新增代码路径校验（通过代码库校验接口）"""
    test_code_col = '组件对应的测试代码目录'
    ut_code_col = '组件对应的UT代码目录'
    ft_code_col = '组件对应的FT代码目录'

    columns_to_check = []
    if test_code_col in df_excel.columns:
        columns_to_check.append(test_code_col)
    if ut_code_col in df_excel.columns:
        columns_to_check.append(ut_code_col)
    if ft_code_col in df_excel.columns:
        columns_to_check.append(ft_code_col)

    if not columns_to_check:
        print("提示: Excel中不存在附加代码目录列，跳过附加代码库校验")
        return pd.DataFrame()

    print(f"检测到附加代码库列: {', '.join(columns_to_check)}，开始校验...")

    from scripts.api_clients.repo_check_api_client import create_repo_check_api_client

    # 第一遍：收集所有拆分出的代码库（去重），统一批量校验
    all_repos = set()
    for index, row in df_excel.iterrows():
        if '部件类别' in row:
            category = row['部件类别']
            if not should_validate_component(category):
                continue

        for col_name in columns_to_check:
            repo_path = ''
            if col_name in row and not pd.isnull(row[col_name]):
                repo_path = convert_to_str(row[col_name]).strip()

            gerrit_repo, _ = process_name(repo_path)
            for repo in [r.strip() for r in gerrit_repo.split(',') if r.strip()]:
                all_repos.add(repo)

    client = create_repo_check_api_client()
    valid_repos_map = client.check_repos(list(all_repos))

    results = []

    for index, row in df_excel.iterrows():
        if '部件类别' in row:
            category = row['部件类别']
            if not should_validate_component(category):
                continue

        chinese_name = ''
        if '中文名称' in row and not pd.isnull(row['中文名称']):
            chinese_name = convert_to_str(row['中文名称']).strip()

        for col_name in columns_to_check:
            repo_path = ''
            if col_name in row and not pd.isnull(row[col_name]):
                repo_path = convert_to_str(row[col_name]).strip()

            gerrit_repo, gerrit_directory = process_name(repo_path)
            gerrit_repo = gerrit_repo.strip()
            gerrit_directory = gerrit_directory.strip() if gerrit_directory else ''

            # 代码库拆分校验
            if not repo_path:
                check_result = "不通过"
                remarks = f"{col_name}未填写"
            elif not gerrit_repo:
                check_result = "不通过"
                remarks = f"{col_name}中的代码库不存在或格式不正确"
            else:
                repo_list = [r.strip() for r in gerrit_repo.split(',') if r.strip()]
                all_valid = True
                invalid_repos = []

                for repo in repo_list:
                    if not valid_repos_map.get(repo):
                        all_valid = False
                        invalid_repos.append(repo)

                if all_valid:
                    check_result = "通过"
                    remarks = f"{col_name}中的所有代码库都存在于数据库中"
                else:
                    check_result = "部分不通过"
                    remarks = f"{col_name}中以下代码库不存在于数据库中: {', '.join(invalid_repos)}"

            # 拆分结果
            split_repos = []
            split_dirs = []

            if ',' in gerrit_repo:
                split_repos = [r.strip() for r in gerrit_repo.split(',') if r.strip()]
                if gerrit_directory:
                    split_dirs = [d.strip() for d in gerrit_directory.split(',')]
                else:
                    split_dirs = [''] * len(split_repos)
            elif gerrit_repo:
                split_repos = [gerrit_repo]
                split_dirs = [gerrit_directory] if gerrit_directory else ['']

            if len(split_dirs) < len(split_repos):
                split_dirs.extend([''] * (len(split_repos) - len(split_dirs)))

            if split_repos:
                split_detail = f"代码库: {split_repos}, 路径: {split_dirs}"
            else:
                split_detail = "无"

            # ===== 新增：代码路径校验 =====
            if enable_path_check and split_repos and check_result != "不通过":
                path_check_result, path_check_remarks, path_detail = validate_paths_for_split(
                    split_repos, split_dirs
                )
            elif not enable_path_check:
                path_check_result, path_check_remarks, path_detail = '未启用', '代码路径校验未启用', ''
            else:
                path_check_result, path_check_remarks, path_detail = '未校验', '代码库校验未通过，跳过路径校验', ''

            if col_name == test_code_col:
                col_type = "测试代码目录"
            elif col_name == ut_code_col:
                col_type = "UT代码目录"
            elif col_name == ft_code_col:
                col_type = "FT代码目录"
            else:
                col_type = col_name

            results.append({
                '行号': index + 2,
                '中文名称': chinese_name,
                '校验类型': col_type,
                '原始路径': repo_path,
                '代码库': gerrit_repo,
                '代码路径': gerrit_directory,
                '拆分后代码库数量': len(split_repos),
                '校验结果': check_result,
                '校验说明': remarks,
                '代码路径校验结果': path_check_result,
                '代码路径校验说明': path_check_remarks,
                '代码路径校验详情': path_detail,
                '拆分详情': split_detail
            })

    return pd.DataFrame(results)


def is_core_component(value):
    if pd.isnull(value):
        return 0
    value_str = str(value)
    return 1 if '是' in value_str else 0


# ==================== 第三部分：结果输出 ====================

def save_results_to_excel(pdm_results, code_results, additional_code_results, format_results, output_path):
    try:
        writer = pd.ExcelWriter(output_path, engine='xlsxwriter')

        if not format_results.empty:
            format_results.to_excel(writer, sheet_name='文档格式检查', index=False)

        if not pdm_results.empty:
            pdm_results.to_excel(writer, sheet_name='PDM编号校验', index=False)

        if not code_results.empty:
            code_results.to_excel(writer, sheet_name='代码库拆分校验', index=False)

        if not additional_code_results.empty:
            additional_code_results.to_excel(writer, sheet_name='组件的测试-UT-FT代码目录校验', index=False)

        # 汇总
        summary_data = {
            '校验类型': [],
            '总记录数': [],
            '通过数量': [],
            '不通过数量': [],
            '部分通过数量': [],
            '通过率': [],
            '校验时间': []
        }

        current_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

        if not format_results.empty and '格式检查结果' in format_results.columns:
            pass_count = len(format_results[format_results['格式检查结果'] == '通过'])
            fail_count = len(format_results[format_results['格式检查结果'] != '通过'])
            total_count = len(format_results)
            pass_rate = f"{pass_count/float(total_count)*100:.1f}%" if total_count > 0 else "0%"

            summary_data['校验类型'].append('文档格式检查')
            summary_data['总记录数'].append(total_count)
            summary_data['通过数量'].append(pass_count)
            summary_data['不通过数量'].append(fail_count)
            summary_data['部分通过数量'].append(0)
            summary_data['通过率'].append(pass_rate)
            summary_data['校验时间'].append(current_time)

        if not pdm_results.empty:
            pass_count = len(pdm_results[pdm_results['PDM校验结果'] == '通过'])
            fail_count = len(pdm_results[pdm_results['PDM校验结果'] == '不通过'])
            total_count = len(pdm_results)
            pass_rate = f"{pass_count/float(total_count)*100:.1f}%" if total_count > 0 else "0%"

            summary_data['校验类型'].append('PDM编号校验')
            summary_data['总记录数'].append(total_count)
            summary_data['通过数量'].append(pass_count)
            summary_data['不通过数量'].append(fail_count)
            summary_data['部分通过数量'].append(0)
            summary_data['通过率'].append(pass_rate)
            summary_data['校验时间'].append(current_time)

        if not code_results.empty:
            pass_count = len(code_results[code_results['代码库拆分校验结果'] == '通过'])
            fail_count = len(code_results[code_results['代码库拆分校验结果'] == '不通过'])
            partial_count = len(code_results[code_results['代码库拆分校验结果'] == '部分不通过'])
            total_count = len(code_results)
            pass_rate = f"{pass_count/float(total_count)*100:.1f}%" if total_count > 0 else "0%"

            summary_data['校验类型'].append('代码库拆分校验')
            summary_data['总记录数'].append(total_count)
            summary_data['通过数量'].append(pass_count)
            summary_data['不通过数量'].append(fail_count)
            summary_data['部分通过数量'].append(partial_count)
            summary_data['通过率'].append(pass_rate)
            summary_data['校验时间'].append(current_time)

            # 新增：代码路径校验汇总
            if '代码路径校验结果' in code_results.columns:
                path_checked = code_results[code_results['代码路径校验结果'].isin(['通过', '不通过'])]
                if not path_checked.empty:
                    p_pass = len(path_checked[path_checked['代码路径校验结果'] == '通过'])
                    p_fail = len(path_checked[path_checked['代码路径校验结果'] == '不通过'])
                    p_total = len(path_checked)
                    p_rate = f"{p_pass/float(p_total)*100:.1f}%" if p_total > 0 else "0%"

                    summary_data['校验类型'].append('代码路径校验')
                    summary_data['总记录数'].append(p_total)
                    summary_data['通过数量'].append(p_pass)
                    summary_data['不通过数量'].append(p_fail)
                    summary_data['部分通过数量'].append(0)
                    summary_data['通过率'].append(p_rate)
                    summary_data['校验时间'].append(current_time)

        if not additional_code_results.empty:
            pass_count = len(additional_code_results[additional_code_results['校验结果'] == '通过'])
            fail_count = len(additional_code_results[additional_code_results['校验结果'] == '不通过'])
            partial_count = len(additional_code_results[additional_code_results['校验结果'] == '部分不通过'])
            total_count = len(additional_code_results)
            pass_rate = f"{pass_count/float(total_count)*100:.1f}%" if total_count > 0 else "0%"

            summary_data['校验类型'].append('组件的测试-UT-FT代码目录校验')
            summary_data['总记录数'].append(total_count)
            summary_data['通过数量'].append(pass_count)
            summary_data['不通过数量'].append(fail_count)
            summary_data['部分通过数量'].append(partial_count)
            summary_data['通过率'].append(pass_rate)
            summary_data['校验时间'].append(current_time)

            # 新增：附加代码路径校验汇总
            if '代码路径校验结果' in additional_code_results.columns:
                path_checked = additional_code_results[additional_code_results['代码路径校验结果'].isin(['通过', '不通过'])]
                if not path_checked.empty:
                    p_pass = len(path_checked[path_checked['代码路径校验结果'] == '通过'])
                    p_fail = len(path_checked[path_checked['代码路径校验结果'] == '不通过'])
                    p_total = len(path_checked)
                    p_rate = f"{p_pass/float(p_total)*100:.1f}%" if p_total > 0 else "0%"

                    summary_data['校验类型'].append('附加代码目录路径校验')
                    summary_data['总记录数'].append(p_total)
                    summary_data['通过数量'].append(p_pass)
                    summary_data['不通过数量'].append(p_fail)
                    summary_data['部分通过数量'].append(0)
                    summary_data['通过率'].append(p_rate)
                    summary_data['校验时间'].append(current_time)

        summary_df = pd.DataFrame(summary_data)
        summary_df.to_excel(writer, sheet_name='校验汇总', index=False)

        writer.close()
        print(f"结果文件已保存: {output_path}")
        return True
    except Exception as e:
        print(f"保存结果文件时出错: {str(e)}")
        import traceback
        traceback.print_exc()
        return False


def run_checks(excel_file, enable_path_check=None):
    """
    可复用的完整校验入口（供统一编排模块调用）

    Args:
        excel_file: Excel文件路径
        enable_path_check: 是否启用代码路径校验，默认读取环境变量 ENABLE_PATH_CHECK

    Returns:
        dict: {
            'format_df': 文档格式检查 DataFrame,
            'pdm_df': PDM编号校验 DataFrame,
            'code_df': 代码库拆分校验 DataFrame,
            'additional_df': 组件的测试-UT-FT代码目录校验 DataFrame,
            'format_check_result': 格式检查结果字符串,
            'doc_info': 文档信息字典,
            'doc_no': 文档编号,
            'filename': 文件名,
        }
    """
    if not os.path.exists(excel_file):
        raise FileNotFoundError(f"文件 '{excel_file}' 不存在")

    print(f"开始处理文件: {excel_file}")

    filename = os.path.basename(excel_file)

    digits = re.findall(r'\d', filename)
    doc_no = ''.join(digits[:12]) if digits else ''

    if doc_no and len(doc_no) < 2:
        print(f"提示: 从文件名提取的编号 '{doc_no}' 长度过短，不作为文档编号使用")
        doc_no = ''
    elif doc_no:
        print(f"从文件名提取的文档编号: {doc_no}")
    else:
        print("提示: 文件名中未找到文档编号，将跳过PDM文档查询")

    # 是否启用代码路径校验（可通过环境变量控制，避免无gerrit权限时耗时）
    if enable_path_check is None:
        enable_path_check = os.environ.get("ENABLE_PATH_CHECK", "true").lower() == "true"
    if enable_path_check:
        print("提示: 代码路径校验已启用（通过 Gerrit 校验接口）")
    else:
        print("提示: 代码路径校验已禁用（设置环境变量 ENABLE_PATH_CHECK=true 可启用）")

    result = {
        'format_df': pd.DataFrame(),
        'pdm_df': pd.DataFrame(),
        'code_df': pd.DataFrame(),
        'additional_df': pd.DataFrame(),
        'format_check_result': '',
        'doc_info': {},
        'doc_no': doc_no,
        'filename': filename,
    }

    try:
        print(f"\n{'='*60}")

        print("\n[步骤1] 执行文档格式检查...")
        format_check_result, doc_info = check_document_format(excel_file, doc_no)

        format_result = {
            '文档编号': doc_no if doc_no else '未提供',
            '文档名称': filename,
            '格式检查结果': '通过' if not format_check_result else '不通过',
            '检查详情': format_check_result if format_check_result else '格式检查通过',
            '产品名称': doc_info.get('product', ''),
            '项目名称': doc_info.get('projectName', ''),
            '文档中文名': doc_info.get('nameCn', ''),
            '文档版本': doc_info.get('version', ''),
            '检查时间': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        }
        format_df = pd.DataFrame([format_result])
        result['format_df'] = format_df
        result['format_check_result'] = format_check_result
        result['doc_info'] = doc_info

        print(f"格式检查结果: {format_result['格式检查结果']}")
        if format_check_result:
            print(f"问题详情: {format_check_result}")

        pdm_combined = pd.DataFrame()
        code_combined = pd.DataFrame()
        additional_code_combined = pd.DataFrame()

        skip_db_check = False
        if format_check_result and "未提供文档编号" not in format_check_result:
            skip_db_check = True
            print("⚠ 格式检查不通过，跳过数据库校验")

        if not skip_db_check:
            print("\n[步骤2] 执行校验...")

            try:
                df = pd.read_excel(excel_file, sheet_name='软件结构', engine='openpyxl')
                print(f"成功读取 {len(df)} 行数据")

                df = df.where(pd.notnull(df), '')

                if '是否核心组件/模块' in df.columns:
                    df['是否核心组件/模块'] = df['是否核心组件/模块'].astype(str)
                    df['是否核心组件/模块'] = df['是否核心组件/模块'].apply(is_core_component)

                if '部件类别' in df.columns:
                    df['部件类别'] = df['部件类别'].apply(match_category)

                if '代码库/目录' in df.columns:
                    df['代码库/目录'] = df['代码库/目录'].astype(str)
                    repo_list = []
                    path_list = []
                    for item in df['代码库/目录']:
                        repo, path = process_name(item)
                        repo_list.append(repo)
                        path_list.append(path)
                    df['代码库'] = repo_list
                    df['代码路径'] = path_list

                if '部件编号' in df.columns:
                    df['部件编号'] = df['部件编号'].apply(convert_to_str)

                print("执行PDM编号校验...")
                pdm_results = validate_pdm_numbers(df)
                if not pdm_results.empty:
                    pdm_results.insert(0, '文档编号', doc_no if doc_no else '未提供')
                    pdm_results.insert(1, '文档名称', filename)
                    pdm_combined = pdm_results
                    print(f"PDM编号校验完成: 校验 {len(pdm_results)} 条记录")

                print("执行代码库拆分校验（含代码路径校验）...")
                code_results = validate_code_repos(
                    df, enable_path_check=enable_path_check
                )
                if not code_results.empty:
                    code_results.insert(0, '文档编号', doc_no if doc_no else '未提供')
                    code_results.insert(1, '文档名称', filename)
                    code_combined = code_results
                    print(f"代码库拆分校验完成: 校验 {len(code_results)} 条记录")

                print("\n[步骤3] 执行组件的测试-UT-FT代码库校验（含代码路径校验）...")
                additional_results = validate_additional_code_repos(
                    df, enable_path_check=enable_path_check
                )
                if not additional_results.empty:
                    additional_results.insert(0, '文档编号', doc_no if doc_no else '未提供')
                    additional_results.insert(1, '文档名称', filename)
                    additional_code_combined = additional_results
                    print(f"组件的测试-UT-FT代码目录校验完成: 校验 {len(additional_results)} 条记录")
                else:
                    print("未检测到附加代码库列或无需校验，跳过")

            except Exception as e:
                print(f"读取或处理Excel文件时出错: {e}")
                import traceback
                traceback.print_exc()

        result['pdm_df'] = pdm_combined
        result['code_df'] = code_combined
        result['additional_df'] = additional_code_combined

    except Exception as e:
        print(f"处理文件时出错: {str(e)}")
        import traceback
        traceback.print_exc()
        raise

    return result


def main():
    if len(sys.argv) < 2:
        print("错误: 请提供Excel文件路径")
        print("使用方法: python script.py <Excel文件路径>")
        sys.exit(1)

    excel_file = sys.argv[1]

    if not os.path.exists(excel_file):
        print(f"错误: 文件 '{excel_file}' 不存在")
        sys.exit(1)

    print(f"开始处理文件: {excel_file}")

    filename = os.path.basename(excel_file)

    digits = re.findall(r'\d', filename)
    doc_no = ''.join(digits[:12]) if digits else ''

    if doc_no and len(doc_no) < 2:
        print(f"提示: 从文件名提取的编号 '{doc_no}' 长度过短，不作为文档编号使用")
        doc_no = ''
    elif doc_no:
        print(f"从文件名提取的文档编号: {doc_no}")
    else:
        print("提示: 文件名中未找到文档编号，将跳过PDM文档查询")

    output_dir = "VerifyResult"
    if not os.path.exists(output_dir):
        os.makedirs(output_dir)

    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')

    # 是否启用代码路径校验（可通过环境变量控制，避免无gerrit权限时耗时）
    enable_path_check = os.environ.get("ENABLE_PATH_CHECK", "true").lower() == "true"
    if enable_path_check:
        print("提示: 代码路径校验已启用（通过 Gerrit 校验接口）")
    else:
        print("提示: 代码路径校验已禁用（设置环境变量 ENABLE_PATH_CHECK=true 可启用）")

    try:
        print(f"\n{'='*60}")

        print("\n[步骤1] 执行文档格式检查...")
        format_check_result, doc_info = check_document_format(excel_file, doc_no)

        format_result = {
            '文档编号': doc_no if doc_no else '未提供',
            '文档名称': filename,
            '格式检查结果': '通过' if not format_check_result else '不通过',
            '检查详情': format_check_result if format_check_result else '格式检查通过',
            '产品名称': doc_info.get('product', ''),
            '项目名称': doc_info.get('projectName', ''),
            '文档中文名': doc_info.get('nameCn', ''),
            '文档版本': doc_info.get('version', ''),
            '检查时间': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        }
        format_df = pd.DataFrame([format_result])

        print(f"格式检查结果: {format_result['格式检查结果']}")
        if format_check_result:
            print(f"问题详情: {format_check_result}")

        pdm_combined = pd.DataFrame()
        code_combined = pd.DataFrame()
        additional_code_combined = pd.DataFrame()

        skip_db_check = False
        if format_check_result and "未提供文档编号" not in format_check_result:
            skip_db_check = True
            print("⚠ 格式检查不通过，跳过数据库校验")

        if not skip_db_check:
            print("\n[步骤2] 执行校验...")

            try:
                df = pd.read_excel(excel_file, sheet_name='软件结构', engine='openpyxl')
                print(f"成功读取 {len(df)} 行数据")

                df = df.where(pd.notnull(df), '')

                if '是否核心组件/模块' in df.columns:
                    df['是否核心组件/模块'] = df['是否核心组件/模块'].astype(str)
                    df['是否核心组件/模块'] = df['是否核心组件/模块'].apply(is_core_component)

                if '部件类别' in df.columns:
                    df['部件类别'] = df['部件类别'].apply(match_category)

                if '代码库/目录' in df.columns:
                    df['代码库/目录'] = df['代码库/目录'].astype(str)
                    repo_list = []
                    path_list = []
                    for item in df['代码库/目录']:
                        repo, path = process_name(item)
                        repo_list.append(repo)
                        path_list.append(path)
                    df['代码库'] = repo_list
                    df['代码路径'] = path_list

                if '部件编号' in df.columns:
                    df['部件编号'] = df['部件编号'].apply(convert_to_str)

                print("执行PDM编号校验...")
                pdm_results = validate_pdm_numbers(df)
                if not pdm_results.empty:
                    pdm_results.insert(0, '文档编号', doc_no if doc_no else '未提供')
                    pdm_results.insert(1, '文档名称', filename)
                    pdm_combined = pdm_results
                    print(f"PDM编号校验完成: 校验 {len(pdm_results)} 条记录")

                print("执行代码库拆分校验（含代码路径校验）...")
                code_results = validate_code_repos(
                    df, enable_path_check=enable_path_check
                )
                if not code_results.empty:
                    code_results.insert(0, '文档编号', doc_no if doc_no else '未提供')
                    code_results.insert(1, '文档名称', filename)
                    code_combined = code_results
                    print(f"代码库拆分校验完成: 校验 {len(code_results)} 条记录")

                print("\n[步骤3] 执行组件的测试-UT-FT代码库校验（含代码路径校验）...")
                additional_results = validate_additional_code_repos(
                    df, enable_path_check=enable_path_check
                )
                if not additional_results.empty:
                    additional_results.insert(0, '文档编号', doc_no if doc_no else '未提供')
                    additional_results.insert(1, '文档名称', filename)
                    additional_code_combined = additional_results
                    print(f"组件的测试-UT-FT代码目录校验完成: 校验 {len(additional_results)} 条记录")
                else:
                    print("未检测到附加代码库列或无需校验，跳过")

            except Exception as e:
                print(f"读取或处理Excel文件时出错: {e}")
                import traceback
                traceback.print_exc()

        print(f"\n{'='*60}")
        print("保存校验结果...")

        base_name = os.path.splitext(filename)[0]
        output_filename = f"{base_name}_VerifyResult_{timestamp}.xlsx"
        output_path = os.path.join(output_dir, output_filename)

        if save_results_to_excel(pdm_combined, code_combined, additional_code_combined, format_df, output_path):
            print(f"\n✓ 校验完成！")
            print(f"  - 文档格式检查: {'通过' if not format_check_result else '不通过'}")
            print(f"  - PDM编号校验: {len(pdm_combined)} 条记录")
            print(f"  - 代码库拆分校验: {len(code_combined)} 条记录")
            if not additional_code_combined.empty:
                print(f"  - 附加代码库校验: {len(additional_code_combined)} 条记录")
            print(f"  - 结果文件: {output_path}")
        else:
            print("保存结果文件失败")

    except Exception as e:
        print(f"处理文件时出错: {str(e)}")
        import traceback
        traceback.print_exc()
        sys.exit(1)

    print("\n处理完成！")


if __name__ == "__main__":
    main()