"""订阅智能体头像色（固定色板，按 agent_id 确定性选取）。"""

_AVATAR_COLORS = [
    "#F47474", "#FD8B8B", "#D08160", "#EE8D56", "#E0A060", "#FDA88B", "#F9C46D",
    "#C8A871", "#F9E86D", "#A0B860", "#C4DB7C", "#AFE58E", "#7CDB80", "#61A861",
    "#47A677", "#97D9C7", "#60A8A0", "#8BDAFF", "#73ADFF", "#6090C0", "#5079A8",
    "#98ABCE", "#F892BB", "#E274AF", "#DBA1F4", "#B49FF3", "#A574C5", "#A07880",
]


def generate_avatar_color() -> str:
    """随机选取一个背景色。"""
    import random

    return random.choice(_AVATAR_COLORS)


def avatar_color_from_seed(seed: str) -> str:
    """无存储色时按名称/ID 稳定回退，从固定色板中确定性选取。"""
    text = (seed or "agent").strip() or "agent"
    acc = 0
    for ch in text:
        acc = ord(ch) + ((acc << 5) - acc)
    return _AVATAR_COLORS[abs(acc) % len(_AVATAR_COLORS)]
