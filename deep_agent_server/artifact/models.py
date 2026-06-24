"""Artifact metadata 模型。"""

from dataclasses import dataclass, asdict
from typing import Any, Dict, Optional


@dataclass
class ArtifactMetadata:
    ref_id: str
    tool_call_id: str
    tool_name: str
    virtual_path: str
    storage_path: str
    size_bytes: int
    summary: str
    mime_type: str = "text/plain"
    conversation_id: Optional[str] = None
    created_at: Optional[str] = None

    def to_store_value(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class SpillResult:
    content: str
    summary: str
    truncated: bool
    spilled: bool = False
    ref_id: Optional[str] = None
    virtual_path: Optional[str] = None
    tool_call_id: Optional[str] = None
