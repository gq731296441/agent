"""订阅智能体头像色（低饱和度）。"""

import uuid
from typing import List

_AVATAR_HUES: List[int] = [210, 195, 170, 145, 260, 290, 320, 350, 25, 45, 120, 85]


def generate_avatar_color() -> str:
    """生成随机低饱和度背景色（HSL）。"""
    import uuid

    return avatar_color_from_seed(str(uuid.uuid4()))


def avatar_color_from_seed(seed: str) -> str:
    """无存储色时按名称/ID 稳定回退。"""
    text = (seed or "agent").strip() or "agent"
    acc = 0
    for ch in text:
        acc = ord(ch) + ((acc << 5) - acc)
    h = _AVATAR_HUES[abs(acc) % len(_AVATAR_HUES)]
    return f"hsl({h}, 42%, 62%)"
