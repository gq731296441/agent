"""
系统提示词模板模块
定义各种场景下的系统提示词，用于指导 AI 的行为
"""
from langchain.messages import SystemMessage, HumanMessage, ToolMessage, AIMessage
from datetime import datetime

SYSTEM_PROMPTS = {
   "default": """
# Role
你是一个功能强大的智能助手，名称为“轻量化智能助手”。你的任务是协助用户解决问题，并在必要时调用工具。

用户工号：{user_id}，用户Token：{user_token}
当前时间：{current_time}

# Workflow Control (严格执行)
你必须遵循以下输出模式。

- 对于简单问题直接回答，不要解释
- 工具调用前思考为什么要调用这些工具，目标是什么
- 工具调用后解释工具执行结果，并思考是否达到了目标
- 当任务完成或无法继续时，输出最终总结后的答案

# Constraints
- 身份保护：妥善保管用户 Token，严禁泄露。
- 重试机制：针对同一错误最多重试 3 次。
"""
}

def build_system_prompt(
   user_id: str=None,
   user_token: str=None,
   include_time: bool = True
) -> str:

   prompt_template = SYSTEM_PROMPTS["default"]

   # 收集所有要替换的变量
   format_kwargs = {}

   if include_time:
      current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
      format_kwargs["current_time"] = current_time

   if user_id is not None:
      format_kwargs["user_id"] = user_id
   if user_token is not None:
      format_kwargs["user_token"] = user_token

   # 一次性 format
   try:
      prompt = prompt_template.format(**format_kwargs)
   except KeyError as e:
      raise

   return prompt

def get_prompt_with_tools(
   user_id: str=None,
   user_token: str=None,
   include_time: bool = True,
   tool_descriptions: str = ""
) -> str:
   """获取包含工具描述的系统提示词"""
   base_prompt = build_system_prompt(
      user_id=user_id,
      user_token=user_token,
      include_time=include_time
   )

   if tool_descriptions:
      tool_section = f"\n# Available Tools\n{tool_descriptions}\n"
      full_prompt = base_prompt + tool_section
   else:
      full_prompt = base_prompt

   return full_prompt
