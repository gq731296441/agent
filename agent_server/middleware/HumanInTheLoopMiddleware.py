# agent_server/middleware/human_in_the_loop.py
from langchain.agents.middleware import HumanInTheLoopMiddleware
from typing import Dict, Any

def create_hitl_middleware():
    """
    创建支持人工审批的中间件
    支持三种决策：approve / reject / edit
    """
    return HumanInTheLoopMiddleware(
        interrupt_on={
            # 你想人工审批的工具（可以是所有，也可以只列敏感工具）
            # "*" 表示所有工具都需要审批
            # "*": {"allowed_decisions": ["approve", "reject", "edit"]}
            "delete_mrm_doc": {"allowed_decisions": ["approve", "edit", "reject"]},
            # 或者只对某些工具开启：
            # "write_file": {"allowed_decisions": ["approve", "reject"]},
            # "send_email": {"allowed_decisions": ["approve", "reject", "edit"]},
        }
    )