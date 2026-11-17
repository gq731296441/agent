#!/usr/bin/env python3
"""
yx-sw-component-manager 统一入口（有线组件清单管理）
融合三个技能的完整功能：
  1. validate  - 组件清单综合校验（合并 wx-sw-component-list-check 与
                 component-manifest-verify-addDir），输出统一审核报告
                 （多sheet Excel + HTML + JSON）
  2. query     - PDM 部件编号查询（来自 pdm-query2report-skill）
  3. backfill  - 按查询结果序号回填部件编号到原 Excel（来自 pdm-query2report-skill）

用法：
  python main.py validate <excel_path> [--output-dir DIR] [--product-model X] ...
  python main.py query <excel_path> [--output-format csv|xlsx|json|html] [--dry-run]
  python main.py backfill <excel_path> --result-file FILE --result-ids 1,2,3 [--dry-run] [--no-prompt]

环境变量：
  coclaw_empno / RDC_EMP_NO - 工号（query 必填）
  coclaw_token / RDC_AUTH_TOKEN - 认证 token（query 必填）
"""

import os
import sys
import json
import argparse
from pathlib import Path

# 将项目根目录加入路径，保证 scripts 包可导入
project_root = Path(__file__).parent
sys.path.insert(0, str(project_root))


def _session_id():
    """从环境变量读取会话ID（跨子命令/步骤关联同一次会话），无则留空"""
    for key in ("SESSION_ID", "COMIND_SESSION_ID", "session_id"):
        v = os.getenv(key)
        if v:
            return str(v).strip()
    return None


# 技能信息（显式传入，避免组件误推断成 skill-usage-monitor 自身）
_SKILL_NAME = "yx-sw-component-manager"
_SKILL_VERSION = "1.1.0"
_SKILL_OWNER = "10306212"  # 洪伟韬


def _report_usage(module, status="success", excel_path=None, output_files=None,
                  error_code=None, error_message=None, start_time=None, end_time=None,
                  duration_ms=None, extra_input=None):
    """
    上报技能使用埋点（静默失败，绝不阻塞主流程）。
    字段约定（埋点模块 scripts/monitor/report_usage.py，源自 skill-usage-monitor 组件）：
      - skill_name/version/owner 显式传入（本技能自包含，不依赖兄弟目录 skill-usage-monitor）
      - module: 按子命令区分（validate/query/backfill）
      - input_summary: 用户输入摘要（组件自动脱敏）
      - output_files: 生成文件数量（非路径列表）
      - session_id: 从环境变量读取
    """
    try:
        from scripts.monitor.report_usage import send_report
        input_summary = excel_path or ""
        if extra_input:
            input_summary = f"{input_summary} {' '.join(extra_input)}" if input_summary else " ".join(extra_input)
        send_report(
            skill_name=_SKILL_NAME,
            skill_version=_SKILL_VERSION,
            skill_owner=_SKILL_OWNER,
            module=module,
            status=status,
            input_summary=input_summary,
            output_files=output_files,
            session_id=_session_id(),
            error_code=error_code,
            error_message=error_message,
            start_time=start_time,
            end_time=end_time,
            duration_ms=duration_ms,
        )
    except Exception:
        # 埋点失败静默，绝不影响技能主流程
        pass


def _count_output_files(result):
    """统计生成的报告文件数量（统计 result 中指向文件的路径字段，排除非文件字段）"""
    if not isinstance(result, dict):
        return None
    file_fields = ('excel_report', 'html_report', 'json_report',
                   'wx_report', 'adddir_report', 'report_file')
    count = 0
    for f in file_fields:
        v = result.get(f)
        if isinstance(v, dict):  # wx_report 是 dict，取其内部文件
            count += _count_output_files(v)
        elif v:
            count += 1
    return count if count > 0 else None


def cmd_validate(args):
    """统一校验子命令"""
    import time
    from scripts.validate.runner import run_validate
    start_ts = time.time()
    extra_input = []
    if args.product_model:
        extra_input.append(f"产品型号={args.product_model}")
    if args.no_path_check:
        extra_input.append("跳过路径校验")
    try:
        result = run_validate(
            args.excel_path,
            config_path=args.config,
            output_dir=args.output_dir,
            product_model=args.product_model,
            work_group_1=args.work_group_1,
            work_group_2=args.work_group_2,
            work_group_3=args.work_group_3,
            enable_path_check=not args.no_path_check,
            log_level=args.log_level,
        )
        # 任务正常完成（status=success，含校验不通过 failed>0）记 success；
        # run_validate 返回 error dict（文件不存在/加载失败/表头失败等任务未真正执行）记 failed
        if result.get('status') == 'success':
            _report_usage(
                module="validate", status="success",
                excel_path=args.excel_path,
                output_files=_count_output_files(result),
                duration_ms=int((time.time() - start_ts) * 1000),
                extra_input=extra_input,
            )
        else:
            _report_usage(
                module="validate", status="failed",
                excel_path=args.excel_path, error_code="EXEC_ERROR",
                error_message=str(result.get('message', '')),
                duration_ms=int((time.time() - start_ts) * 1000),
                extra_input=extra_input,
            )
        # 精简输出
        summary = {k: v for k, v in result.items()
                   if k not in ('errors', 'correction_suggestions_list')}
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return 0 if result.get('status') == 'success' else 1
    except KeyboardInterrupt:
        _report_usage(module="validate", status="cancelled",
                      excel_path=args.excel_path, error_code="INTERRUPT",
                      duration_ms=int((time.time() - start_ts) * 1000))
        raise
    except Exception as e:
        _report_usage(module="validate", status="failed",
                      excel_path=args.excel_path, error_code="EXCEPTION",
                      error_message=str(e),
                      duration_ms=int((time.time() - start_ts) * 1000))
        raise


def cmd_query(args):
    """PDM 查询子命令（函数级调用 run_query，嵌入埋点）"""
    import time
    from scripts.pdm_query.main import run_query
    start_ts = time.time()
    extra_input = [f"输出格式={args.output_format}"]
    if args.dry_run:
        extra_input.append("dry-run")
    try:
        results, no_match_details, output_file, elapsed_time, baseline, top_level_details = run_query(
            args.excel_path, args.output_format)
        # 任务正常完成（含 0 匹配 / dry-run）均记 success
        _report_usage(
            module="query", status="success",
            excel_path=args.excel_path,
            output_files=1 if output_file else None,
            duration_ms=int((time.time() - start_ts) * 1000),
            extra_input=extra_input,
        )
        # 保留原 main() 的核心输出
        print("\n" + "=" * 50)
        print("查询完成！")
        print("=" * 50)
        print(f"📁 文档清单:")
        print(f" 📄 原始文件: {os.path.basename(args.excel_path)}")
        if output_file and not args.dry_run:
            print(f" 📄 查询结果: {os.path.basename(output_file)}")
        print(f"\n📊 查询统计:")
        print(f" 🔢 查询到记录: {len(results)}")
        if results:
            unique_source_rows = sorted(set([r['源行号'] for r in results]))
            print(f" 📍 涉及源行号: {unique_source_rows}")
        print(f" ⚠️ 未匹配行数: {len(no_match_details)}")
        print(f" ⏱️ 耗时: {elapsed_time:.1f} 秒")
        # 展示第一级部件基准（产品/项目），作为多编号回填的筛选依据
        if baseline and (baseline["products"] or baseline["projects"]):
            print(f"\n🎯 第一级部件基准（用于多编号筛选）:")
            if baseline["products"]:
                print(f" 📦 产品: {', '.join(baseline['products'])}")
            if baseline["projects"]:
                print(f" 📁 项目: {', '.join(baseline['projects'])}")
            if baseline["product_nos"]:
                print(f" 🆔 产品编号: {', '.join(baseline['product_nos'])}")
        print("=" * 50)
        return 0
    except KeyboardInterrupt:
        _report_usage(module="query", status="cancelled",
                      excel_path=args.excel_path, error_code="INTERRUPT",
                      duration_ms=int((time.time() - start_ts) * 1000))
        raise
    except Exception as e:
        _report_usage(module="query", status="failed",
                      excel_path=args.excel_path, error_code="EXCEPTION",
                      error_message=str(e),
                      duration_ms=int((time.time() - start_ts) * 1000))
        raise


def cmd_backfill(args):
    """回填子命令（函数级调用回填核心函数，嵌入埋点；无交互确认）"""
    import time
    from scripts.pdm_query.main import (
        read_query_result_file, validate_backfill_operation,
        backfill_by_result_ids, generate_diff_report,
    )
    start_ts = time.time()
    extra_input = [f"结果文件={os.path.basename(args.result_file)}",
                   f"序号={args.result_ids}"]
    if args.dry_run:
        extra_input.append("dry-run")

    def report(status, output_files=None, error_code=None, error_message=None):
        _report_usage(module="backfill", status=status,
                      excel_path=args.excel_path,
                      output_files=output_files,
                      error_code=error_code, error_message=error_message,
                      duration_ms=int((time.time() - start_ts) * 1000),
                      extra_input=extra_input)

    try:
        # 1. 读取查询结果文件
        query_results = read_query_result_file(args.result_file)
        if not query_results:
            print("❌ 查询结果文件为空,无法回填")
            report("failed", error_code="EMPTY_RESULT", error_message="查询结果文件为空")
            return 1
        selected_result_ids = [int(s.strip()) for s in args.result_ids.split(",")]

        # 2. 回填前校验（硬错误则停止）
        errors, warnings, uncovered_empty_rows = validate_backfill_operation(
            query_results=query_results,
            selected_result_ids=selected_result_ids,
            excel_file=args.excel_path,
        )
        if errors:
            # 仅冲突类错误时，尝试按第一级基准（产品/项目）自动筛选解决
            conflict_only = all(e.get('type') == 'conflict' for e in errors)
            unresolved_conflicts = []
            if conflict_only:
                from scripts.pdm_query.main import auto_resolve_conflicts
                resolved_ids, unresolved_conflicts, baseline = auto_resolve_conflicts(
                    query_results, selected_result_ids, args.excel_path)
                if not unresolved_conflicts and set(resolved_ids) != set(selected_result_ids):
                    auto_removed = [rid for rid in selected_result_ids if rid not in resolved_ids]
                    print(f"\n✅ 多编号自动筛选：已按第一级基准（产品/项目）自动匹配，")
                    print(f"   保留序号 {resolved_ids}，剔除冲突序号 {auto_removed}")
                    if baseline and (baseline["products"] or baseline["projects"]):
                        print(f"   基准: 产品={baseline['products']}, 项目={baseline['projects']}")
                    selected_result_ids = resolved_ids
                    # 重新校验（此时冲突应已解决）
                    errors, warnings, uncovered_empty_rows = validate_backfill_operation(
                        query_results=query_results,
                        selected_result_ids=selected_result_ids,
                        excel_file=args.excel_path,
                    )
            if errors:
                # 未解决冲突时，附上各候选的产品/项目信息供用户判断
                if conflict_only and unresolved_conflicts:
                    print("\nℹ️ 以下源行无法按基准自动匹配（0 个或多个候选与第一级产品/项目一致），需人工选择：")
                    for uc in unresolved_conflicts:
                        print(f"  源行号 {uc['source_row']}:")
                        for c in uc.get("candidates", []):
                            print(f"    序号 {c['序号']}: 部件编号 {c['部件编号']}"
                                  f"  产品: {c.get('产品','') or '未知'}  项目: {c.get('项目','') or '未知'}")
                    b = uc.get("baseline", {}) if uc else {}
                    if b and (b.get("products") or b.get("projects")):
                        print(f"  基准: 产品={b.get('products')}, 项目={b.get('projects')}")
                for err in errors:
                    print(f"\n🚫 {err['title']}")
                    print(f" {err['message']}")
                print("\n❌ 回填操作已停止,请解决上述错误后重试。")
                report("failed", error_code="VALIDATE_ERROR",
                       error_message="; ".join(e['title'] for e in errors))
                return 1

        # 3. 构建有效回填序号（部件编号非空）
        id_to_result = {}
        for r in query_results:
            try:
                rid = int(r.get("序号", 0))
                if rid > 0:
                    id_to_result[rid] = r
            except (ValueError, TypeError):
                continue
        valid_ids_for_backfill = [
            rid for rid in selected_result_ids
            if rid in id_to_result and str(id_to_result[rid].get("部件编号", "")).strip()
        ]
        if not valid_ids_for_backfill:
            print("❌ 没有可回填的有效序号（部件编号均为空或序号不存在）")
            report("failed", error_code="NO_VALID_IDS",
                   error_message="没有可回填的有效序号")
            return 1

        # 4. 预览
        print("\n" + "=" * 60)
        print("回填记录预览")
        print("=" * 60)
        print(f"{'序号':<6}{'源行号':<8}{'中文名称':<28}{'部件编号':<16}{'产品':<12}{'项目'}")
        print("-" * 100)
        for rid in valid_ids_for_backfill:
            r = id_to_result[rid]
            product = str(r.get('产品', '') or '')
            project = str(r.get('项目', '') or '')
            print(f"{rid:<6}{r.get('源行号',''):<8}{str(r.get('中文名称',''))[:26]:<28}"
                  f"{r.get('部件编号',''):<16}{product[:11]:<12}{project}")
        print("=" * 60)

        # 5. dry-run：仅预览，不实际回填
        if args.dry_run:
            print("\n[Dry-run模式] 以上为回填预览,不会实际修改文件。")
            report("success", output_files=None)
            return 0

        # 6. 执行回填 + 差异报告
        backfilled_file, backfill_stats = backfill_by_result_ids(
            args.excel_path, query_results, valid_ids_for_backfill)
        diff_file = generate_diff_report(
            args.excel_path, backfilled_file, backfill_stats, valid_ids_for_backfill)

        success_count = backfill_stats['backfilled']
        overwrite_count = backfill_stats['already_filled']
        has_uncovered = len(uncovered_empty_rows) > 0
        print("\n" + "=" * 60)
        if has_uncovered:
            print("🔔 回填操作完成【⚠️ 部分完成】")
        else:
            print("✅ 回填操作完成【全部完成】")
        print("=" * 60)
        print(f" 📄 回填后文件: {os.path.basename(backfilled_file)}")
        print(f" 📄 差异报告: {os.path.basename(diff_file)}")
        print(f" 🔢 有效回填: {success_count + overwrite_count} 条"
              f"（新增 {success_count}，覆盖 {overwrite_count}）")
        if has_uncovered:
            print(f" ⚠️ 未覆盖行数: {len(uncovered_empty_rows)} 行")

        # 任务正常完成（含部分完成/未覆盖）均记 success
        report("success", output_files=2)
        return 0
    except KeyboardInterrupt:
        report("cancelled", error_code="INTERRUPT")
        raise
    except Exception as e:
        report("failed", error_code="EXCEPTION", error_message=str(e))
        raise


def build_parser():
    """构建命令行解析器"""
    parser = argparse.ArgumentParser(
        prog='yx-sw-component-manager',
        description='有线组件清单管理 - 综合校验 / PDM查询 / 编号回填',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
子命令:
  validate  组件清单综合校验（格式/规则/编号/代码库/路径/API一致性），输出统一审核报告
  query     PDM 部件编号查询（对部件编号为空的行）
  backfill  按查询结果序号回填部件编号到原 Excel

示例:
  # 综合校验
  python main.py validate 软件组件清单.xlsx --output-dir ./output
  # PDM查询
  python main.py query 软件组件清单.xlsx --output-format xlsx
  # 回填
  python main.py backfill 软件组件清单.xlsx --result-file 查询结果.xlsx --result-ids 1,2,3
"""
    )
    subparsers = parser.add_subparsers(dest='command', help='子命令')

    # validate
    p_validate = subparsers.add_parser('validate', help='组件清单综合校验，输出统一审核报告')
    p_validate.add_argument('excel_path', help='Excel文件路径')
    p_validate.add_argument('--config', default=None, help='配置文件路径')
    p_validate.add_argument('--output-dir', default=None, help='输出目录（默认 Excel 同目录）')
    p_validate.add_argument('--product-model', default=None, help='产品规格型号（跨产品一致性）')
    p_validate.add_argument('--work-group-1', default=None, help='工作组类1编号（TEAM1）')
    p_validate.add_argument('--work-group-2', default=None, help='工作组类2编号（TEAM2）')
    p_validate.add_argument('--work-group-3', default=None, help='工作组类3编号（TEAM3）')
    p_validate.add_argument('--no-path-check', action='store_true', help='禁用 Gerrit 路径校验')
    p_validate.add_argument('--log-level', default='INFO', help='日志级别')
    p_validate.set_defaults(func=cmd_validate)

    # query
    p_query = subparsers.add_parser('query', help='PDM 部件编号查询')
    p_query.add_argument('excel_path', help='Excel文件路径')
    p_query.add_argument('--output-format', '-f', choices=['csv', 'xlsx', 'json', 'html'],
                         default='xlsx', help='输出格式（默认xlsx）')
    p_query.add_argument('--dry-run', '-d', action='store_true', help='模拟运行')
    p_query.set_defaults(func=cmd_query)

    # backfill
    p_backfill = subparsers.add_parser('backfill', help='按查询结果序号回填部件编号')
    p_backfill.add_argument('excel_path', help='原始Excel文件路径')
    p_backfill.add_argument('--result-file', '-r', required=True, help='查询结果文件路径')
    p_backfill.add_argument('--result-ids', '-i', required=True,
                            help='要回填的查询结果序号，逗号分隔，如 3,4,5')
    p_backfill.add_argument('--dry-run', '-d', action='store_true', help='模拟运行，不实际回填')
    p_backfill.add_argument('--no-prompt', action='store_true', help='跳过交互确认')
    p_backfill.set_defaults(func=cmd_backfill)

    return parser


def main():
    parser = build_parser()
    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        return 1

    # 执行子命令
    try:
        return args.func(args)
    except KeyboardInterrupt:
        print("\n用户中断")
        return 130
    except Exception as e:
        print(f"执行失败: {e}")
        import traceback
        traceback.print_exc()
        return 1


if __name__ == '__main__':
    sys.exit(main())
