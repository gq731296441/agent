import json
import time
from typing import List, Dict, Any
from tokenizers import Tokenizer

class TokenizerJSONCalculator:
    """基于 tokenizer.json 的 Token 计算器"""
    
    def __init__(self, tokenizer_path: str):
        """
        初始化计算器
        
        Args:
            tokenizer_path: tokenizer.json 文件路径
        """
        self.tokenizer = Tokenizer.from_file(tokenizer_path)
        print(f"✅ 加载 tokenizer.json: {tokenizer_path}")
        print(f"   词表大小：{self.tokenizer.get_vocab_size()}")
    
    def count_tokens(self, text: str) -> int:
        """计算文本 Token 数"""
        return len(self.tokenizer.encode(text).ids)
    
    def count_text_tokens(self, text: str) -> int:
        """兼容你现有代码的方法名"""
        return self.count_tokens(text)
    
    def stringify_content(self, content: Any) -> str:
        """序列化内容（兼容你现有代码）"""
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts = []
            for item in content:
                if isinstance(item, dict) and item.get("type") == "text" and "text" in item:
                    parts.append(str(item["text"]))
                elif isinstance(item, (dict, list)):
                    parts.append(json.dumps(item, ensure_ascii=False, default=str))
                else:
                    parts.append(str(item))
            return "\n".join(part for part in parts if part)
        if isinstance(content, dict):
            return json.dumps(content, ensure_ascii=False, default=str)
        return str(content)
    
    def stringify_message_like(self, message: Any) -> str:
        """序列化消息对象（兼容你现有代码）"""
        from langchain_core.messages import BaseMessage, ToolMessage
        
        if isinstance(message, BaseMessage):
            # 映射为 API 标准 role
            role_map = {"human": "user", "ai": "assistant", "system": "system"}
            msg_type = getattr(message, "type", message.__class__.__name__)
            role = role_map.get(msg_type, msg_type)
            
            payload: Dict[str, Any] = {
                "role": role,
                "content": self.stringify_content(getattr(message, "content", "")),
            }
            
            if getattr(message, "name", None):
                payload["name"] = message.name
            
            # ⚠️ 排除 artifact（本地元数据，不发送给 API）
            # if getattr(message, "artifact", None) is not None:
            #     payload["artifact"] = message.artifact
            
            if hasattr(message, "tool_calls") and getattr(message, "tool_calls", None):
                payload["tool_calls"] = getattr(message, "tool_calls")
            
            if isinstance(message, ToolMessage):
                if getattr(message, "tool_call_id", None):
                    payload["tool_call_id"] = message.tool_call_id
            
            return json.dumps(payload, ensure_ascii=False, default=str)
        
        if isinstance(message, dict):
            return json.dumps(message, ensure_ascii=False, default=str)
        
        return str(message)
    
    def count_messages_tokens(self, messages: List[Any]) -> int:
        """计算消息列表 Token 数（兼容你现有代码）"""
        total = 0
        overhead_per_message = 4  # 每条消息的固定开销
        
        for message in messages:
            serialized = self.stringify_message_like(message)
            total += self.count_text_tokens(serialized)
            total += overhead_per_message
        
        return total
    
    def test_system_prompt(self, system_prompt: str) -> Dict:
        """测试系统提示词 Token"""
        tokens = self.count_tokens(system_prompt)
        return {
            "char_count": len(system_prompt),
            "token_count": tokens,
            "ratio": round(len(system_prompt) / max(1, tokens), 2)
        }
    
    def test_full_request(self, messages: List[Dict], tools: List[Dict] = None) -> Dict:
        """测试完整请求 Token（含工具定义）"""
        messages_tokens = self.count_messages_tokens(messages)
        tools_tokens = 0
        
        if tools:
            tools_text = json.dumps(tools, ensure_ascii=False)
            tools_tokens = self.count_tokens(tools_text)
        
        total = messages_tokens + tools_tokens
        
        return {
            "messages_tokens": messages_tokens,
            "tools_tokens": tools_tokens,
            "total_tokens": total,
            "estimated_cost_cny": round(total * 0.04 / 1000, 4)  # 假设 ¥0.04/1K
        }


# ============================================================================
# 🚀 使用示例
# ============================================================================

if __name__ == "__main__":
    # 1. 初始化（替换为你的 tokenizer.json 路径）
    calculator = TokenizerJSONCalculator("/home/10344649@zte.intra/10344649/project/agent/tiktoken_cache/tokenizer.json")
    
    # 2. 测试系统提示词
    system_prompt = """# Role
你是一个功能强大的智能助手，名称为"智搭"，负责协助用户解决问题。

用户工号：10344649，用户 Token：[PROTECTED]
当前时间：2026-03-20 17:30:00

# Workflow (ReAct Pattern)
1. Thought: 分析问题
2. Action: 调用工具
3. Observation: 分析结果
4. Final Answer: 输出回答
"""
    
    result = calculator.test_system_prompt(system_prompt)
    print(f"\n📝 系统提示词测试:")
    print(f"   字符数：{result['char_count']}")
    print(f"   Token 数：{result['token_count']}")
    print(f"   字符:Token 比例：{result['ratio']}")
    
    # 3. 测试消息列表（基于你提供的文件内容）
    messages = [
        {"role": "user", "content": "有什么工具可以使用？"},
        {"role": "user", "content": "你好"},
        {"role": "assistant", "content": "您好！有什么我可以帮您的吗？"},
        {"role": "user", "content": "几点额"},
        {"role": "system", "content": system_prompt},
    ]
    
    # 4. 测试工具定义（基于你提供的 11 个工具）
    tools = [
        {"name": "单据筛选", "description": "从 mrm 数据库中获取单据信息"},
        {"name": "单据跟催", "description": "查询单据并发送邮件跟催"},
        # ... 其他工具
    ]
    
    result = calculator.test_full_request(messages, tools)
    print(f"\n📝 完整请求测试:")
    print(f"   消息 Token: {result['messages_tokens']}")
    print(f"   工具 Token: {result['tools_tokens']}")
    print(f"   总 Token: {result['total_tokens']}")
    print(f"   预估成本：¥{result['estimated_cost_cny']}")
    
    # 5. 性能测试
    print(f"\n⚡ 性能测试:")
    test_text = "这是一个测试文本。" * 100
    start = time.time()
    for _ in range(100):
        calculator.count_tokens(test_text)
    end = time.time()
    print(f"   100 次编码耗时：{(end-start)*1000:.2f} ms")
    print(f"   平均每次：{(end-start)*10:.2f} ms")