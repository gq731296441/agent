#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
编码检测脚本 - 检查目录下所有文件的编码
"""

import os
import sys
import chardet
from pathlib import Path


def detect_encoding(file_path):
    """检测文件编码"""
    try:
        with open(file_path, "rb") as f:
            raw_data = f.read(10000)  # 读取前10KB用于检测
            result = chardet.detect(raw_data)
            encoding = result["encoding"] if result["encoding"] else "unknown"
            confidence = result["confidence"]
            return encoding, confidence
    except Exception as e:
        return f"error: {str(e)}", 0


def is_utf8(encoding):
    """判断是否为UTF-8编码"""
    if not encoding:
        return False
    encoding = encoding.lower()
    return encoding in ["utf-8", "utf8", "utf_8"]


def scan_directory(root_dir):
    """扫描目录并检测文件编码"""
    total_files = 0
    utf8_files = 0
    non_utf8_files = []

    # 要跳过的文件扩展名（二进制文件）
    skip_extensions = {
        ".exe",
        ".dll",
        ".so",
        ".dylib",
        ".pyc",
        ".pyo",
        ".class",
        ".jar",
        ".png",
        ".jpg",
        ".jpeg",
        ".gif",
        ".bmp",
        ".ico",
        ".svg",
        ".mp3",
        ".mp4",
        ".avi",
        ".mov",
        ".wav",
        ".flac",
        ".zip",
        ".tar",
        ".gz",
        ".bz2",
        ".rar",
        ".7z",
        ".pdf",
        ".doc",
        ".docx",
        ".xls",
        ".xlsx",
        ".ppt",
        ".pptx",
    }

    # 要跳过的目录
    skip_dirs = {".git", "__pycache__", ".ruff_cache", "node_modules", "venv", ".venv"}

    root_path = Path(root_dir)

    for file_path in root_path.rglob("*"):
        if file_path.is_file():
            # 跳过二进制文件
            if file_path.suffix.lower() in skip_extensions:
                continue

            # 跳过某些目录
            if any(part in skip_dirs for part in file_path.parts):
                continue

            total_files += 1
            file_str_path = str(file_path)

            # 检测编码
            encoding, confidence = detect_encoding(file_path)

            if is_utf8(encoding):
                utf8_files += 1
            else:
                non_utf8_files.append(
                    {
                        "path": file_str_path,
                        "encoding": encoding,
                        "confidence": confidence,
                    }
                )

    return total_files, utf8_files, non_utf8_files


def generate_report(root_dir):
    """生成编码检测报告"""
    print("=" * 80)
    print("文件编码检测报告")
    print("=" * 80)
    print(f"扫描目录: {root_dir}")
    print("-" * 80)

    total_files, utf8_files, non_utf8_files = scan_directory(root_dir)

    print(f"总文件数: {total_files}")
    print(f"UTF-8 编码文件数: {utf8_files}")
    print(f"非 UTF-8 编码文件数: {len(non_utf8_files)}")
    print()

    if non_utf8_files:
        print("非 UTF-8 编码文件列表:")
        print("-" * 80)
        for i, file_info in enumerate(non_utf8_files, 1):
            print(f"{i}. 文件: {file_info['path']}")
            print(f"   检测编码: {file_info['encoding']}")
            print(f"   置信度: {file_info['confidence']:.2%}")
            print()
        print("=" * 80)
        print("警告: 发现非 UTF-8 编码的文件！")
    else:
        print("=" * 80)
        print("检查完成：所有文本文件均为 UTF-8 编码！")

    return len(non_utf8_files) == 0


if __name__ == "__main__":
    target_dir = "/home/0668001219/.openclaw/workspace/skills/studio-rdcloud-workitem"

    # 检查目录是否存在
    if not os.path.exists(target_dir):
        print(f"错误: 目录 '{target_dir}' 不存在")
        sys.exit(1)

    success = generate_report(target_dir)
    sys.exit(0 if success else 1)
