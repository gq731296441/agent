#!/usr/bin/env python
"""
打包脚本：将 studio-rdcloud-workitem 目录压缩为 zip 文件到同级别目录。

文件名格式：studio-rdcloud-workitem-{年月日时分秒}.zip

排除规则：
1. 隐藏文件和目录（.git, .ruff_cache 等）
2. docs 目录
3. scripts/tests 目录
4. logs 目录的文件（除了 .md 文件）
5. scripts 里面的 __pycache__ 目录（可能多层级）
6. .gitignore 文件

使用方法：
    python package_zip.py
"""

import os
import sys
import zipfile
from datetime import datetime
from pathlib import Path


def should_exclude(file_path: Path, root_dir: Path) -> bool:
    """
    判断文件是否应该被排除。

    Args:
        file_path: 文件完整路径
        root_dir: 根目录路径

    Returns:
        True 表示应该排除，False 表示应该打包
    """
    # 获取相对于根目录的路径
    try:
        rel_path = file_path.relative_to(root_dir)
    except ValueError:
        return False

    rel_str = str(rel_path).replace(os.sep, "/")
    parts = rel_path.parts

    # 1. 排除隐藏文件和目录（以.开头的）
    for part in parts:
        if part.startswith(".") and part not in [".", ".."]:
            return True

    # 2. 排除 docs 目录
    if parts[0] == "docs":
        return True

    # 3. 排除 scripts/tests 目录
    if len(parts) >= 2 and parts[0] == "scripts" and parts[1] == "tests":
        return True

    # 4. logs 目录只保留 .md 文件
    if parts[0] == "logs":
        if file_path.suffix.lower() != ".md":
            return True

    # 5. 排除所有 __pycache__ 目录（可能多层级）
    if "__pycache__" in parts:
        return True

    # 6. 排除 .gitignore 文件
    if file_path.name == ".gitignore":
        return True

    return False


def package_zip(source_dir: str, output_dir: str = None) -> str:
    """
    将目录打包为 zip 文件。

    Args:
        source_dir: 源目录路径
        output_dir: 输出目录路径（默认与源目录同级）

    Returns:
        生成的 zip 文件路径
    """
    source_path = Path(source_dir).resolve()

    if not source_path.exists():
        raise FileNotFoundError(f"源目录不存在：{source_path}")

    if not source_path.is_dir():
        raise NotADirectoryError(f"源路径不是目录：{source_path}")

    # 确定输出目录（默认为源目录的父目录）
    if output_dir:
        output_path = Path(output_dir).resolve()
    else:
        output_path = source_path.parent

    output_path.mkdir(parents=True, exist_ok=True)

    # 生成文件名：studio-rdcloud-workitem-{年月日时分秒}.zip
    timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
    zip_filename = f"{source_path.name}-{timestamp}.zip"
    zip_file_path = output_path / zip_filename

    # 创建 zip 文件
    excluded_count = 0
    file_count = 0

    # 外层文件夹名称：使用源目录名
    outer_folder_name = source_path.name

    with zipfile.ZipFile(zip_file_path, "w", zipfile.ZIP_DEFLATED) as zipf:
        # 遍历所有文件和目录
        for file_path in source_path.rglob("*"):
            # 跳过目录本身
            if file_path.is_dir():
                continue

            # 检查是否应该排除
            if should_exclude(file_path, source_path):
                excluded_count += 1
                continue

            # 添加到 zip 文件，路径前加上外层文件夹名
            rel_path = file_path.relative_to(source_path)
            arcname = Path(outer_folder_name) / rel_path
            zipf.write(file_path, arcname)
            file_count += 1

    # 输出结果
    print(f"打包完成！")
    print(f"源目录：{source_path}")
    print(f"输出文件：{zip_file_path}")
    print(f"打包文件数：{file_count}")
    print(f"排除文件数：{excluded_count}")
    print(f"文件大小：{zip_file_path.stat().st_size / 1024:.2f} KB")

    return str(zip_file_path)


def main():
    """主函数"""
    # 解析命令行参数
    if len(sys.argv) < 2:
        # 如果没有提供参数，使用当前脚本所在目录
        script_dir = Path(__file__).resolve().parent
        source_dir = script_dir
        output_dir = None
    else:
        # 第一个参数：源目录（必填）
        source_dir = Path(sys.argv[1]).resolve()

        # 第二个参数：输出目录（可选）
        output_dir = None
        if len(sys.argv) >= 3:
            output_dir = Path(sys.argv[2]).resolve()

    print(f"开始打包...")
    print(f"源目录：{source_dir}")
    if output_dir:
        print(f"输出目录：{output_dir}")
    print("-" * 60)

    try:
        zip_path = package_zip(str(source_dir), str(output_dir) if output_dir else None)
        print("-" * 60)
        print(f"成功！zip 文件已生成：{zip_path}")
    except Exception as e:
        print(f"打包失败：{e}")
        raise


if __name__ == "__main__":
    main()
