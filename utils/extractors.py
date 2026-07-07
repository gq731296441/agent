"""
流式数据提取器
从 LangChain/LangGraph 的 Agent 流式输出中提取结构化数据

主要组件：
- ThinkTagParser: <think> 标签流式解析器，分离推理内容和正式回答
- ToolCallAccumulator: 工具调用累积器，处理流式工具调用 chunk 的合并与解析
- extract_reasoning_from_chunk: 从 message chunk 的 additional_kwargs 提取推理内容
- extract_tool_result: 从工具执行节点提取工具结果
"""

from typing import Optional, Dict, List, Any, Tuple
import json
from json_repair import repair_json
from config import get_logger

logger = get_logger(__name__)


# ==================== 推理内容提取 ====================

class ThinkTagParser:
    """
    <think> 标签流式解析器
    
    用于从 LLM 流式输出的 content 中分离 <think>...</think> 思考内容和正式回答内容。
    支持标签跨多个流式 chunk 的情况。
    
    状态机：
        init       -> 检测中，缓冲内容判断是否以 <think> 开头
        in_think   -> 在 <think> 内，内容作为推理文本累积
        post_think -> </think> 之后，内容作为正式回答
        no_think   -> 无 <think> 标签，所有 content 视为正式回答
    
    使用示例：
        parser = ThinkTagParser()
        
        # 每次重新进入 model 节点时重置
        parser.reset()
        
        # 如果检测到 additional_kwargs 中有 reasoning_content，禁用 think 解析
        if reasoning_from_kwargs:
            parser.disable()
        
        # 处理每个 content chunk
        reasoning, answer = parser.process_chunk(text_chunk)
        
        # 节点切换或结束时刷新缓冲区
        reasoning, answer = parser.flush()
    """
    
    def __init__(self):
        self.reset()
    
    def reset(self):
        """重置解析状态（每次重新进入 model 节点时调用）"""
        self._state = "init"
        self._buffer = ""
        self._post_think_strip = False  # 刚进入 post_think 时需要剥离前导换行
    
    def disable(self):
        """禁用 <think> 解析（当检测到 additional_kwargs.reasoning_content 时调用）"""
        self._state = "no_think"
        self._buffer = ""
        self._post_think_strip = False
    
    @property
    def is_in_think(self) -> bool:
        """是否在 <think> 块内"""
        return self._state == "in_think"
    
    @property
    def is_detecting(self) -> bool:
        """是否仍在检测阶段"""
        return self._state == "init"
    
    def process_chunk(self, text_chunk: str) -> Tuple[str, str]:
        """处理内容 chunk，分离思考内容和正式回答内容。
        
        Args:
            text_chunk: 流式输出的文本片段
            
        Returns:
            (reasoning_part, answer_part) - 任一部分可能为空字符串
        """
        if self._state == "no_think":
            return "", text_chunk
        
        if self._state == "post_think":
            if self._post_think_strip:
                text_chunk = text_chunk.lstrip("\n")
                if not text_chunk:
                    # 整个 chunk 都是换行，继续等待下一个 chunk
                    return "", ""
                # 遇到实际内容，停止剥离
                self._post_think_strip = False
            return "", text_chunk
        
        self._buffer += text_chunk
        
        if self._state == "init":
            return self._process_init()
        
        if self._state == "in_think":
            return self._process_in_think()
        
        return "", ""
    
    def flush(self) -> Tuple[str, str]:
        """刷新缓冲区残留内容（节点切换或结束时调用）。
        
        Returns:
            (reasoning_part, answer_part)
        """
        if not self._buffer:
            return "", ""
        
        buf = self._buffer
        self._buffer = ""
        
        if self._state == "in_think":
            # 缓冲区内容属于推理（未闭合的 <think> 块）
            return buf, ""
        elif self._state == "init":
            # 仍在检测阶段，视为普通文本
            return "", buf
        
        return "", ""
    
    def _process_init(self) -> Tuple[str, str]:
        """处理 init 状态：判断 content 是否以 <think> 开头"""
        stripped = self._buffer.lstrip()
        if stripped.startswith("<think>"):
            # 检测到 <think> 标签，切换到 in_think 状态
            self._state = "in_think"
            tag_pos = self._buffer.find("<think>")
            self._buffer = self._buffer[tag_pos + 7:]  # len("<think>") = 7
            # 继续处理 in_think
            return self._process_in_think()
        elif stripped and not "<think>".startswith(stripped):
            # 内容明确不以 <think> 开头
            self._state = "no_think"
            result = self._buffer
            self._buffer = ""
            return "", result
        elif len(stripped) >= 7:
            # 有足够字符但不是 <think>
            self._state = "no_think"
            result = self._buffer
            self._buffer = ""
            return "", result
        else:
            # 字符不够，继续缓冲等待判断
            return "", ""
    
    def _process_in_think(self) -> Tuple[str, str]:
        """处理 in_think 状态：寻找 </think> 闭合标签"""
        close_idx = self._buffer.find("</think>")
        if close_idx != -1:
            # 找到 </think> 闭合标签
            reasoning_part = self._buffer[:close_idx]
            rest = self._buffer[close_idx + 8:]  # len("</think>") = 8
            self._state = "post_think"
            self._buffer = ""
            self._post_think_strip = True  # 后续 chunk 继续剥离前导换行
            rest = rest.lstrip("\n")
            if not rest:
                # </think> 后同一 chunk 内没有实际内容，等后续 chunk 继续剥离
                return reasoning_part, ""
            # 同一 chunk 已有实际内容，停止剥离
            self._post_think_strip = False
            return reasoning_part, rest
        else:
            # 尚未闭合，保留末尾可能的 </think> 部分匹配（最多 7 字符）
            safe_len = max(0, len(self._buffer) - 7)
            reasoning_part = self._buffer[:safe_len]
            self._buffer = self._buffer[safe_len:]
            return reasoning_part, ""


def extract_reasoning_from_chunk(message_chunk) -> Optional[str]:
    """从 message chunk 中提取推理内容。
    
    优先读取 additional_kwargs.reasoning_content，
    同时兼容 message_chunk.model_extra.reasoning。
    
    Args:
        message_chunk: LangChain 流式消息 chunk
        
    Returns:
        推理内容字符串，如果没有则返回 None
    """
    additional_kwargs = getattr(message_chunk, "additional_kwargs", None) or {}
    reasoning = additional_kwargs.get("reasoning_content")
    if reasoning:
        return reasoning

    model_extra = getattr(message_chunk, "model_extra", None)
    if isinstance(model_extra, dict):
        reasoning = model_extra.get("reasoning")
        if reasoning:
            return reasoning

    return None


# ==================== 工具调用提取 ====================

class ToolCallAccumulator:
    """
    工具调用累积器
    
    处理 LLM 流式输出中的工具调用 chunk，将分散的 chunk 合并为完整的工具调用，
    并在解析时自动过滤无效调用。
    
    功能：
    - 累积流式工具调用 chunk（按 index 合并 name、args、id）
    - 解析累积的工具调用参数（JSON 字符串 → dict）
    - 过滤不完整的工具调用（缺少 name 或 id）
    - 过滤不在已注册工具列表中的幻觉工具调用
    - 检测并记录 invalid_tool_calls
    
    使用示例：
        accumulator = ToolCallAccumulator(valid_tool_names={"工具A", "工具B"})
        
        # 累积每个 chunk
        accumulator.accumulate_chunks(message_chunk.tool_call_chunks)
        
        # 解析并获取有效的工具调用
        tool_calls = accumulator.parse_and_clear()
        
        # 检测 invalid_tool_calls
        ToolCallAccumulator.check_invalid_tool_calls(message_chunk)
    """
    
    def __init__(self, valid_tool_names: set = None):
        """
        Args:
            valid_tool_names: 已注册的有效工具名称集合，用于过滤幻觉工具
        """
        self._accumulated: Dict[int, Dict[str, Any]] = {}
        self._valid_tool_names = valid_tool_names or set()
    
    @property
    def has_pending(self) -> bool:
        """是否有待处理的累积工具调用"""
        return bool(self._accumulated)
    
    def accumulate_chunk(self, tc_chunk: dict):
        """累积一个工具调用 chunk。
        
        Args:
            tc_chunk: 工具调用 chunk，包含 index, name, args, id 字段
        """
        try:
            index = tc_chunk.get("index")
            if index is None:
                logger.warning(f"【Extractor】工具调用 chunk 缺少 index，已跳过: {tc_chunk}")
                return
            
            if index not in self._accumulated:
                self._accumulated[index] = {
                    "name": tc_chunk.get("name"),
                    "args": tc_chunk.get("args") or "",
                    "id": tc_chunk.get("id")
                }
            else:
                # 合并
                if tc_chunk.get("name"):
                    self._accumulated[index]["name"] = tc_chunk.get("name")
                if tc_chunk.get("id"):
                    self._accumulated[index]["id"] = tc_chunk.get("id")
                if tc_chunk.get("args"):
                    self._accumulated[index]["args"] += tc_chunk.get("args")
        except Exception as e:
            logger.error(f"【Extractor】累积工具调用参数异常: {e}, chunk: {tc_chunk}, 已跳过")
    
    def accumulate_chunks(self, tc_chunks: list):
        """批量累积工具调用 chunks。
        
        Args:
            tc_chunks: 工具调用 chunk 列表
        """
        for tc_chunk in tc_chunks:
            self.accumulate_chunk(tc_chunk)
    
    def parse_and_clear(self) -> List[Dict[str, Any]]:
        """解析所有累积的工具调用并清空缓冲区。
        
        自动过滤：
        1. 不完整（缺少 name 或 id）的无效工具调用
        2. 不在已注册工具列表中的幻觉工具调用
        
        Returns:
            工具调用数据列表，每项包含 tool_name, args, tool_call_id
        """
        if not self._accumulated:
            return []
        
        result = []
        for idx, tc in self._accumulated.items():
            try:
                tool_name = tc.get("name")
                args_str = tc.get("args", "{}")
                tool_id = tc.get("id")
                
                # 过滤掉不完整的工具调用（LLM 产生的 invalid_tool_calls 可能缺少关键字段）
                if not tool_name or not tool_id:
                    logger.warning(f"【Extractor】跳过不完整的工具调用 index={idx}: name={tool_name}, id={tool_id}")
                    continue
                
                # 校验工具是否在已注册的工具列表中，过滤 LLM 幻觉产生的虚假工具
                if self._valid_tool_names and tool_name not in self._valid_tool_names:
                    logger.warning(
                        f"【Extractor】跳过无效工具调用 index={idx}: "
                        f"工具 '{tool_name}' 不在已注册工具列表中，"
                        f"可用工具: {self._valid_tool_names}"
                    )
                    continue
                
                # 解析参数字符串为字典
                args_dict = {}
                if args_str:
                    try:
                        args_dict = json.loads(args_str)
                    except (json.JSONDecodeError, TypeError) as e:
                        # LLM 流式拼接可能产生畸形 JSON（缺括号、缺引号等），
                        # 使用 json_repair 尝试自动修复
                        logger.warning(
                            f"【Extractor】Tool Args 标准解析失败，尝试自动修复: {e}"
                        )
                        try:
                            args_dict = repair_json(args_str, return_objects=True)
                            if not isinstance(args_dict, dict):
                                args_dict = {"raw_args": args_dict}
                            logger.info(
                                f"【Extractor】Tool Args 自动修复成功: {list(args_dict.keys())}"
                            )
                        except Exception as repair_err:
                            logger.error(
                                f"【Extractor】Tool Args 自动修复也失败: {repair_err}, "
                                f"Content: {args_str}"
                            )
                            args_dict = {"raw_args": args_str}

                result.append({
                    "tool_name": tool_name,
                    "args": args_dict,
                    "tool_call_id": tool_id
                })
            except Exception as e:
                logger.error(f"【Extractor】解析工具调用 index={idx} 时异常: {e}, 已跳过")
                continue
        
        self._accumulated.clear()
        return result
    
    def clear(self):
        """清空累积的工具调用"""
        self._accumulated.clear()

    def peek(self, index: int) -> Optional[Dict[str, Any]]:
        """查看指定索引的累积状态（不清空），供流式发送时读取。

        Args:
            index: 工具调用 chunk 的索引

        Returns:
            包含 name, args, id 的字典；若索引不存在则返回 None
        """
        item = self._accumulated.get(index)
        if item is None:
            return None
        return {"name": item["name"], "args": item["args"], "id": item["id"]}

    def peek_all(self) -> Dict[int, Dict[str, Any]]:
        """查看所有累积状态（不清空），返回 index → 状态 的字典。

        Returns:
            {index: {"name": ..., "args": ..., "id": ...}, ...}
        """
        return {
            idx: {"name": item["name"], "args": item["args"], "id": item["id"]}
            for idx, item in self._accumulated.items()
        }

    @staticmethod
    def check_invalid_tool_calls(message_chunk):
        """检测并记录 message_chunk 中的 invalid_tool_calls。
        
        Args:
            message_chunk: LangChain 流式消息 chunk
        """
        # if hasattr(message_chunk, "invalid_tool_calls") and message_chunk.invalid_tool_calls:
        #     for itc in message_chunk.invalid_tool_calls:
        #         logger.warning(
        #             f"【Extractor】检测到 invalid_tool_call: "
        #             f"name={itc.get('name')}, "
        #             f"id={itc.get('id')}, "
        #             f"error={itc.get('error')}"
        #         )


# ==================== 工具结果提取 ====================

def extract_tool_result(message_chunk) -> Optional[Dict[str, Any]]:
    """从工具执行节点的消息中提取工具结果。
    
    Args:
        message_chunk: LangChain 工具消息（ToolMessage/ToolMessageChunk）
        
    Returns:
        工具结果字典，包含 tool_name, tool_result, tool_call_id, artifact（如有）
        如果不是工具消息则返回 None
    """
    if not hasattr(message_chunk, "type") or message_chunk.type != "tool":
        return None
    
    result = {
        "tool_name": message_chunk.name,
        "tool_result": message_chunk.content,
        "tool_call_id": message_chunk.tool_call_id,
    }
    
    # artifact 是可选字段
    if hasattr(message_chunk, "artifact"):
        result["artifact"] = message_chunk.artifact

    return result


# ==================== 流式 JSON 字段提取 ====================

def extract_streaming_json_field(args_str: str, field_name: str) -> str:
    """从一个可能不完整的 JSON 字符串中，按 best-effort 方式提取指定字符串字段的值。

    支持处理 JSON 转义序列（\\\\n, \\\\t, \\\\r, \\\\\", \\\\\\\\, \\\\uXXXX），
    对不完整的 trailing escape（如末尾的反斜杠或不完整的 \\\\u 序列）会停止提取，
    等待后续 chunk 补全后再继续。

    典型使用场景：LLM 流式生成 write_file 工具调用的 args JSON 时，
    逐 chunk 提取 ``content`` 字段的当前值，计算增量后推流到前端。

    Args:
        args_str: 当前累积的 args JSON 字符串（可能不完整）
        field_name: 要提取的字段名，如 ``"content"`` 或 ``"new_string"``

    Returns:
        提取并转义还原后的内容字符串；若未找到字段则返回空字符串。

    Examples:
        >>> extract_streaming_json_field('{"file_path":"/a.md","content":"Hello"}', "content")
        'Hello'
        >>> extract_streaming_json_field('{"content":"Line1\\\\nLine2"}', "content")
        'Line1\\nLine2'
        >>> extract_streaming_json_field('{"file_path":"/a.md","conte', "content")
        ''
    """
    import re

    if not args_str or not field_name:
        return ""

    # 定位字段标记: "field_name": "
    pattern = re.compile(rf'"{re.escape(field_name)}"\s*:\s*"')
    match = pattern.search(args_str)
    if not match:
        return ""

    pos = match.end()
    result_chars: list[str] = []
    i = pos

    while i < len(args_str):
        ch = args_str[i]

        if ch == "\\":
            # 转义序列
            if i + 1 >= len(args_str):
                # 末尾反斜杠，可能是跨 chunk 的转义序列开头，停止等待
                break

            next_ch = args_str[i + 1]
            if next_ch == "n":
                result_chars.append("\n")
                i += 2
            elif next_ch == "t":
                result_chars.append("\t")
                i += 2
            elif next_ch == "r":
                result_chars.append("\r")
                i += 2
            elif next_ch == '"':
                result_chars.append('"')
                i += 2
            elif next_ch == "\\":
                result_chars.append("\\")
                i += 2
            elif next_ch == "u":
                # Unicode 转义 \\uXXXX，需要 4 个 hex 字符
                if i + 5 < len(args_str):
                    try:
                        code_point = int(args_str[i + 2 : i + 6], 16)
                        result_chars.append(chr(code_point))
                        i += 6
                    except (ValueError, IndexError):
                        result_chars.append("\\u")
                        i += 2
                else:
                    # 不完整的 \\u 序列，等待后续 chunk
                    break
            else:
                # 无效转义，保留原样
                result_chars.append("\\")
                result_chars.append(next_ch)
                i += 2

        elif ch == '"':
            # 字符串字段结束
            break

        else:
            result_chars.append(ch)
            i += 1

    return "".join(result_chars)
