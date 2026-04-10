from fastmcp import FastMCP
import os
import requests
import aiohttp
import asyncio
import json
import re
import random
import string
from datetime import datetime, timedelta
from typing import List, Optional

# 关闭代理，避免内网干扰
os.environ.pop("http_proxy", None)
os.environ.pop("https_proxy", None)
os.environ.pop("all_proxy", None)

# 创建 MCP 服务器
mcp = FastMCP(
    "mrm单据系统"
)

# ============================================================================
# 📦 随机数据池
# ============================================================================

# 随机人名列表
FIRST_NAMES = ["杨益", "王春风", "李明", "张伟", "刘芳", "陈静", "赵强", "孙丽", "周杰", "吴敏"]
LAST_NAMES = ["10244702", "10001298", "10345678", "10567890", "10123456", "10987654", "10246810", "10135792"]

# 随机物料名称
MATERIALS = [
    "三类超柔导热衬垫",
    "高性能散热硅胶片",
    "电磁屏蔽材料",
    "导热绝缘垫片",
    "柔性电路板",
    "金属屏蔽罩",
    "导热凝胶",
    "相变导热材料",
    "石墨导热片",
    "陶瓷基板"
]

# 随机状态
DOC_STATUSES = ["待处理", "处理中", "已完成", "已取消", "待审核", "审核通过", "审核拒绝"]
CANCEL_STATUSES = ["是", "否"]
PROCESS_STATUSES = ["处理中", "已完成", "待处理", "处理失败", "排队中"]

# 随机部门
DEPARTMENTS = ["研发部", "生产部", "质量部", "采购部", "销售部", "技术部", "运维部"]


# ============================================================================
# 🔧 辅助函数
# ============================================================================

def random_name() -> str:
    """生成随机人名 + 工号"""
    name = random.choice(FIRST_NAMES)
    emp_no = random.choice(LAST_NAMES)
    return f"{name}{emp_no}"

def random_date(days_range: int = 30) -> str:
    """生成随机日期"""
    base_date = datetime.now()
    random_days = random.randint(-days_range, 0)
    target_date = base_date + timedelta(days=random_days)
    return target_date.strftime("%Y-%m-%d %H:%M:%S")

def random_string(length: int = 8) -> str:
    """生成随机字符串"""
    return ''.join(random.choices(string.ascii_uppercase + string.digits, k=length))

def random_id(prefix: str = "DOC") -> str:
    """生成随机单据 ID"""
    return f"{prefix}-{random_string(6)}-{random.randint(1000, 9999)}"


# ============================================================================
# 🛠️ MCP 工具定义
# ============================================================================

@mcp.tool(name="单据筛选")
async def doc_query(id: str) -> str:
    """从 mrm 数据库中获取该 id 对应的具体单据信息，查询单据必须通过'单据筛选'工具进行查询。

    参数:
    - id: 单据 id
    
    返回:
    - 单据详细信息
    """
    # 随机生成单据信息
    creator = random_name()
    handler = random_name()
    material = random.choice(MATERIALS)
    status = random.choice(DOC_STATUSES)
    creator_cancel = random.choice(CANCEL_STATUSES)
    handler_cancel = random.choice(CANCEL_STATUSES)
    create_date = random_date()
    department = random.choice(DEPARTMENTS)
    priority = random.choice(["高", "中", "低", "紧急"])
    
    # 随机生成额外字段
    extra_fields = []
    if random.random() > 0.5:
        extra_fields.append(f"备注：测试单据{random_string(4)}")
    if random.random() > 0.5:
        extra_fields.append(f"项目编号：PRJ-{random_string(6)}")
    if random.random() > 0.5:
        extra_fields.append(f"预计完成时间：{random_date(15)}")
    
    extra_text = ", ".join(extra_fields) if extra_fields else "无额外信息"
    
    return f"""
═══════════════════════════════════════════════════
📋 单据查询结果
═══════════════════════════════════════════════════
单据 ID: {id}
状态：{status}
优先级：{priority}

👤 人员信息:
  - 制单人：{creator}
  - 处理人：{handler}
  - 所属部门：{department}

📦 物料信息:
  - 物料名称：{material}
  - 物料编码：MAT-{random_string(8)}

✅ 取消状态:
  - 制单人是否取消：{creator_cancel}
  - 处理人是否取消：{handler_cancel}

📅 时间信息:
  - 创建时间：{create_date}
  - 更新时间：{random_date(5)}

📝 其他信息:
  {extra_text}
═══════════════════════════════════════════════════
"""


@mcp.tool(name="单据跟催")
async def doc_mail(id: str) -> str:
    """查询输入 id 的单据并发邮件跟催，返回是否成功跟催，跟催单据必须通过'单据跟催'工具进行跟催。

    参数:
    - id: 单据 id
    
    返回:
    - 跟催结果
    """
    # 随机决定是否成功（80% 成功率）
    success = random.random() > 0.2
    
    if success:
        # 随机生成跟催详情
        email_count = random.randint(1, 3)
        recipients = [random_name() for _ in range(random.randint(1, 3))]
        send_time = random_date(0)
        
        return f"""
═══════════════════════════════════════════════════
📧 单据跟催结果
═══════════════════════════════════════════════════
单据 ID: {id}
状态：✅ 跟催成功

📮 邮件发送详情:
  - 发送数量：{email_count} 封
  - 发送时间：{send_time}
  - 收件人：{', '.join(recipients)}
  
⏰ 预计响应时间：{random.randint(1, 24)} 小时内
═══════════════════════════════════════════════════
"""
    else:
        # 随机失败原因
        error_reasons = [
            "收件人邮箱地址无效",
            "邮件服务器暂时不可用",
            "单据状态不允许跟催",
            "发送频率超限，请稍后重试",
            "权限不足，无法执行跟催操作"
        ]
        error = random.choice(error_reasons)
        
        return f"""
═══════════════════════════════════════════════════
❌ 单据跟催结果
═══════════════════════════════════════════════════
单据 ID: {id}
状态：❌ 跟催失败

⚠️ 失败原因：{error}

💡 建议操作:
  1. 检查单据状态是否正常
  2. 确认收件人信息是否正确
  3. {random.randint(5, 30)} 分钟后重试
═══════════════════════════════════════════════════
"""


@mcp.tool(name="单据处理")
async def doc_process(id: str) -> str:
    """查询输入 id 的单据并进行处理，返回是否处理成功，处理单据必须通过'单据处理'工具进行处理。

    参数:
    - id: 单据 id
    
    返回:
    - 处理结果
    """
    # 随机处理状态
    process_status = random.choice(PROCESS_STATUSES)
    progress = random.randint(0, 100)
    handler = random_name()
    start_time = random_date(1)
    
    # 根据状态生成不同详情
    if process_status == "已完成":
        detail = f"""
  - 完成时间：{random_date(0)}
  - 处理结果：审核通过
  - 下一环节：归档"""
    elif process_status == "处理失败":
        errors = ["系统异常", "数据校验失败", "权限不足", "资源锁定", "网络超时"]
        detail = f"""
  - 错误代码：ERR-{random.randint(1000, 9999)}
  - 错误信息：{random.choice(errors)}
  - 建议：联系管理员"""
    else:
        detail = f"""
  - 当前进度：{progress}%
  - 预计完成：{random_date(3)}
  - 排队位置：第{random.randint(1, 10)}位"""
    
    return f"""
═══════════════════════════════════════════════════
⚙️ 单据处理结果
═══════════════════════════════════════════════════
单据 ID: {id}
状态：{process_status}

👤 处理人：{handler}
🕐 开始时间：{start_time}

📊 处理详情:
{detail}

🔗 操作链接：https://i.zte.com.cn/doc/{id}/process
═══════════════════════════════════════════════════
"""


def main():
    mcp.run(transport="http", host="0.0.0.0", port=6002)

if __name__ == "__main__":
    main()

