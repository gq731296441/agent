from langchain.messages import SystemMessage, HumanMessage, ToolMessage, AIMessage

SYSTEM_PROMPT = """
你是一个强大的智能助手，可以使用你可用的工具来帮助用户解决问题。请在需要时调用工具，并仅在获取信息后给出总结后的答案。
你可以一次调用多个工具来并行完成任务。请尽量一次性规划好所有需要的工具调用。
"""