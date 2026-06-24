#!/usr/bin/env python3
"""检查main.py语法"""
import ast
import os

path = r''

try:
    with open(path, 'r', encoding='utf-8') as f:
        content = f.read()
    ast.parse(content)
    print("✅ 语法检查通过")
except SyntaxError as e:
    print(f"❌ 语法错误: {e}")
    print(f"行号: {e.lineno}, 偏移: {e.offset}")
    if e.text:
        print(f"内容: {e.text}")
except Exception as e:
    print(f"其他错误: {e}")
