from deepagents import create_deep_agent
from agent_server.llm import create_agent_llm
# System prompt to steer the agent to be an expert researcher
research_instructions = """You are an expert researcher. Your job is to conduct thorough research and then write a polished report.

You have access to an internet search tool as your primary means of gathering information.

## `internet_search`

Use this to run an internet search for a given query. You can specify the max number of results to return, the topic, and whether raw content should be included.
"""

agent = create_deep_agent(
    model=create_agent_llm(),
    tools=[],
    system_prompt=research_instructions,
)

result = agent.invoke({"messages": [{"role": "user", "content": "当前目录位置在哪？有什么文件？"}]})

# Print the agent's response
print(result["messages"][-1].content)

claude --resume f09556e2-4bb4-4c34-a20b-7dcadb36d3b6