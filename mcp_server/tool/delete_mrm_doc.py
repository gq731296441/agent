from langchain.tools import tool
from langgraph.config import get_stream_writer
from pydantic import BaseModel, Field

class delete_mrm_doc(BaseModel):
    """删除mrm单据工具的输入参数说明。"""
    id: str = Field(description="单据id")

@tool(args_schema=delete_mrm_doc)
def delete_mrm_doc(id: str)-> str:
    """从mrm数据库中删除该id对应的单据，删除单据必须通过'单据删除'工具进行查询。"""
    writer = get_stream_writer()  # 必须在 graph 执行上下文中
    writer(f"开始搜索单据{id}...")
    
    writer(f"删除操作完成")
    return f"{id}单据删除成功！"