# common/schemas/response_schemas.py
"""
API 响应相关 Pydantic 模型定义
"""

from typing import Any, Dict, Generic, List, Optional, TypeVar
from pydantic import BaseModel, Field

# 泛型类型变量，用于支持不同类型的 data
T = TypeVar("T")


class ApiResponse(BaseModel, Generic[T]):
    """
    通用 API 响应数据模型。
    
    用于统一所有 API 接口的响应格式。
    
    Attributes:
        code: 状态码，0 表示成功，非 0 表示失败
        msg: 响应消息
        data: 响应数据，类型由泛型 T 决定
        status: 请求状态，True 表示成功
    """
    code: int = Field(default=0, description="状态码，0 表示成功")
    msg: str = Field(default="成功", description="响应消息")
    data: Optional[T] = Field(default=None, description="响应数据")
    status: bool = Field(default=True, description="请求状态")
    
    class Config:
        # 允许任意类型，支持泛型
        arbitrary_types_allowed = True
    
    @classmethod
    def success(cls, data: T = None, msg: str = "成功") -> "ApiResponse[T]":
        """创建成功响应的便捷方法"""
        return cls(code=0, msg=msg, data=data, status=True)
    
    @classmethod
    def error(cls, msg: str, code: int = -1, data: T = None) -> "ApiResponse[T]":
        """创建错误响应的便捷方法"""
        return cls(code=code, msg=msg, data=data, status=False)


class ListApiResponse(ApiResponse[List[T]], Generic[T]):
    """
    列表类型的 API 响应数据模型。
    
    data 字段为列表类型。
    """
    data: List[T] = Field(default=[], description="响应数据列表")
    
    @classmethod
    def success(cls, data: List[T] = None, msg: str = "成功") -> "ListApiResponse[T]":
        """创建成功响应的便捷方法"""
        return cls(code=0, msg=msg, data=data or [], status=True)
    
    @classmethod
    def error(cls, msg: str, code: int = -1) -> "ListApiResponse[T]":
        """创建错误响应的便捷方法"""
        return cls(code=code, msg=msg, data=[], status=False)
