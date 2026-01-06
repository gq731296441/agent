from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException
import os
import uuid
from typing import Dict, Set

# ============= 代理配置 ============
os.environ.pop("http_proxy", None)
os.environ.pop("https_proxy", None)
os.environ.pop("all_proxy", None)

class WebsocketConnectionManager:
    """
    WebSocket 连接管理器 - 支持一个 ws 处理多个 session
    
    架构设计：
    - 一个物理 WebSocket 连接可以处理多个 session_id
    - session_id → WebSocket 的多对一映射
    - 支持动态切换/创建 session
    
    工作流程：
    1. 前端连接 → WebSocket 对象直接使用，不生成临时 ID
    2. 每条消息携带 user_id 和 session_id
    3. 后端检查 session_id，如果存在则复用 Agent，否则新建 session
    4. session_id → ws 的映射动态维护
    """
    
    def __init__(self):
        # session_id → WebSocket 的映射（多个 session 可指向同一个 ws, 用于通过sessionid查询ws发送消息）
        self.session_to_ws: Dict[str, WebSocket] = {}
        
        # WebSocket 对象 → set(session_ids) 的反向映射（追踪该 ws 处理的所有 session，用于断开时查找需要清理的会话）
        self.ws_to_sessions: Dict[WebSocket, Set[str]] = {}

    async def connect(self, ws: WebSocket) -> WebSocket:
        """
        接受新的 WebSocket 连接
        
        Args:
            ws: WebSocket 连接
            
        Returns:
            WebSocket 对象本身（用于后续识别该连接）
        """
        await ws.accept()
        # 初始化该 ws 对应的 session 集合
        if ws not in self.ws_to_sessions:
            self.ws_to_sessions[ws] = set()
        return ws

    def bind_session(self, ws: WebSocket, session_id: str) -> bool:
        """
        将 session_id 绑定到 WebSocket 对象
        
        支持一个 ws 绑定多个 session_id，实现多对一映射
        
        Args:
            ws: WebSocket 连接
            session_id: 真实的 session_id
            
        Returns:
            成功返回 True，失败返回 False
        """
        if ws not in self.ws_to_sessions:
            # 防御性编程：如果连接在绑定前意外丢失
            return False
        self.session_to_ws[session_id] = ws
        self.ws_to_sessions[ws].add(session_id)

        return True

    def disconnect(self, ws: WebSocket) -> Set[str]:
        """
        断开连接或移除 session 映射
        
        Args:
            session_id: 要移除的 session_id（移除单个 session）
            ws: 要断开的 WebSocket 对象（断开该 ws 处理的所有 session）
        """
        # 获取关联的所有会话
        related_sessions = self.ws_to_sessions.pop(ws, set())
        
        # 清理反向映射
        for sid in related_sessions:
            self.session_to_ws.pop(sid, None)
            
        return related_sessions

    async def send(self, msg: dict, session_id: str):
        """
        发送消息到指定的 session_id 对应的 WebSocket
        
        Args:
            msg: 消息字典
            session_id: session_id（对应的 ws 会接收此消息）
        """
        ws = self.session_to_ws.get(session_id)
        if ws:
            try:
                await ws.send_json(msg)
            except Exception as e:
                print(f"[WS] 发送失败，会话 {session_id} 可能已关闭: {e}")
                # 此处不主动 disconnect，由循环中的 WebSocketDisconnect 统一处理

    def get_sessions_by_ws(self, ws: WebSocket) -> list:
        """
        获取 WebSocket 对象处理的所有 session_id
        
        Args:
            ws: WebSocket 对象
            
        Returns:
            该 ws 处理的 session_id 列表，如果不存在返回空列表
        """
        return self.ws_to_sessions.get(ws, set()).copy()

    def get_connection_status(self) -> dict:
        """
        获取连接管理器的统计信息
        
        Returns:
            包含连接数等信息的字典
        """
        return {
            "active_websockets": len(self.ws_to_sessions),
            "active_sessions": len(self.session_to_ws),
            "total_mappings": sum(len(s) for s in self.ws_to_sessions.values())
        }

