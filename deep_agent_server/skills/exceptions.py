"""Skill 相关异常（向后兼容 re-export）。"""

from deep_agent_server.skills.selection_guard import SkillSelectionLockedError

__all__ = ["SkillSelectionLockedError"]
