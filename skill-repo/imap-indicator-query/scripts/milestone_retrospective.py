"""
里程碑复盘报告数据获取脚本

标准输入格式：
返回类型：table；
里程碑：里程碑1,里程碑2；
指标：指标1,指标2,...；
添加内容：添加指标1,添加指标2,...；
"""

import os
import sys
import json
import argparse
from typing import List, Dict, Any

# 添加父目录到路径以便导入main模块
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

try:
    from main import get_milestone_retrospective_data
except ImportError:
    print("错误: 无法导入 main 模块，请确保在正确的目录中运行", file=sys.stderr)
    sys.exit(1)


def parse_stdin_format(stdin_text: str) -> Dict[str, Any]:
    """解析标准输入格式文本"""
    try:
        stdin_text = stdin_text.strip()

        # 提取返回类型
        return_type = "table"
        if "返回类型：" in stdin_text or "返回类型:" in stdin_text:
            import re
            match = re.search(r'返回类型\s*[:：]\s*(\w+)', stdin_text)
            if match:
                return_type = match.group(1)

        # 提取里程碑
        milestones = []
        if "里程碑：" in stdin_text or "里程碑:" in stdin_text:
            import re
            match = re.search(r'里程碑\s*[:：]\s*([^；；]+)', stdin_text)
            if match:
                milestones_str = match.group(1).strip()
                milestones = [m.strip() for m in milestones_str.replace('，', ',').split(',') if m.strip()]

        # 提取指标
        indicators = []
        if "指标：" in stdin_text or "指标:" in stdin_text:
            import re
            match = re.search(r'指标\s*[:：]\s*([^；；]+)', stdin_text)
            if match:
                indicators_str = match.group(1).strip()
                indicators = [ind.strip() for ind in indicators_str.replace('，', ',').split(',') if ind.strip()]

        # 提取添加内容
        additional = []
        if "添加内容：" in stdin_text or "添加内容:" in stdin_text:
            import re
            match = re.search(r'添加内容\s*[:：]\s*([^；；]*)', stdin_text)
            if match:
                additional_str = match.group(1).strip()
                if additional_str and additional_str != '无':
                    additional = [item.strip() for item in additional_str.replace('，', ',').split(',') if item.strip()]

        if not milestones:
            return {"error": "缺少必需的里程碑参数"}

        return {
            "return_type": return_type,
            "milestones": milestones,
            "indicators": indicators,
            "additional": additional
        }
    except Exception as e:
        return {"error": f"解析失败: {str(e)}"}


def main():
    parser = argparse.ArgumentParser(
        description="里程碑复盘报告数据获取工具",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
标准输入格式示例：
    返回类型：table；
    里程碑：ZXUFPV2.30.30,ZXUFPV1.50.30；
    指标：MR吞吐量,PR吞吐量,成果鉴定前一个月发现缺陷占比；
    添加内容：需求分析引入缺陷数；

命令行示例：
    python milestone_retrospective.py --milestones "ZXUFPV2.30.30,ZXUFPV1.50.30" --indicators "MR吞吐量,PR吞吐量" --additional "需求分析引入缺陷数"
    python milestone_retrospective.py --stdin "返回类型：table；里程碑：ZXUFPV2.30.30；指标：MR吞吐量；"
        """
    )

    parser.add_argument('--milestones', '-m', help='里程碑列表，用逗号分隔')
    parser.add_argument('--indicators', '-i', help='指标列表，用逗号分隔')
    parser.add_argument('--additional', '-a', help='添加的指标列表，用逗号分隔')
    parser.add_argument('--return-type', '-r', default='table', help='返回类型，默认table')
    parser.add_argument('--stdin', '-s', help='从标准输入格式解析（覆盖其他参数）')
    parser.add_argument('--output', '-o', help='输出文件路径，默认输出到标准输出')
    parser.add_argument('--empno', help='工号，默认使用环境变量 coclaw_empno')
    parser.add_argument('--token', help='认证令牌，默认使用环境变量 coclaw_token')

    args = parser.parse_args()

    # 解析参数
    if args.stdin:
        parsed = parse_stdin_format(args.stdin)
        if "error" in parsed:
            print(f"错误: {parsed['error']}", file=sys.stderr)
            sys.exit(1)
        milestones = parsed.get("milestones", [])
        indicators = parsed.get("indicators", [])
        additional = parsed.get("additional", [])
        return_type = parsed.get("return_type", "table")
    else:
        if not args.milestones or not args.indicators:
            parser.error("必须提供 --milestones 和 --indicators 参数，或使用 --stdin 提供标准输入格式")
        milestones = [m.strip() for m in args.milestones.replace('，', ',').split(',') if m.strip()]
        indicators = [i.strip() for i in args.indicators.replace('，', ',').split(',') if i.strip()]
        additional = []
        if args.additional:
            additional = [a.strip() for a in args.additional.replace('，', ',').split(',') if a.strip()]
        return_type = args.return_type

    if not milestones or not indicators:
        print("错误: 里程碑和指标列表不能为空", file=sys.stderr)
        sys.exit(1)

    # 调用API
    try:
        result = get_milestone_retrospective_data(
            milestones=milestones,
            indicators=indicators,
            additional_metrics=additional if additional else None,
            return_type=return_type,
            empno=args.empno,
            token=args.token
        )

        # 格式化输出
        output_json = json.dumps(result, ensure_ascii=False, indent=2)

        if args.output:
            with open(args.output, 'w', encoding='utf-8') as f:
                f.write(output_json)
            print(f"结果已保存到: {args.output}")
        else:
            print(output_json)

        # 返回适当的退出码
        sys.exit(0 if result.get('status') else 1)

    except KeyboardInterrupt:
        print("\n操作已取消", file=sys.stderr)
        sys.exit(130)
    except Exception as e:
        print(f"执行失败: {str(e)}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()