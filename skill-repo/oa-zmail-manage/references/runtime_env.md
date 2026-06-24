# 运行环境与解释器选择（必读）

> 调用本技能脚本**前必须**：
> 1. **确认当前运行环境**（Windows / Linux / macOS）；
> 2. 按下方「各平台解释器与安装」表**选择对应解释器**。
>
> 文档中示例统一写 `python3`，**实际执行须按平台**使用：Windows 用 `python` 或 `py -3`，Linux/macOS 用 `python3`。**禁止照抄示例中的 `python3`**，否则在 Windows 下可能无法执行。

## Python 版本

- **版本要求**：Python **3.6+**，兼容 Windows / Linux / macOS。
- **Windows 编码**：脚本在 Windows 下会强制将 stdout/stderr 设为 UTF-8，避免 GBK 编码错误；调用方可设 `PYTHONIOENCODING=utf-8` 与之一致。

## 各平台解释器与安装

| 系统 | 解释器（优先级从高到低） |
|------|--------------------------|
| **Windows** | `python` → `py -3` |
| **Linux** | `python3` |
| **macOS** | `python3` |

### 安装依赖（requirements.txt）

- Windows：`python -m pip install -r requirements.txt`
- Linux/macOS：`pip3 install -r requirements.txt`

