"""进度事件回调的公共默认实现。

emit(event_type, data) 是贯穿 review/router/cross_validator 各阶段的可选进度回调
（SSE 用，见复盘 D-19）。多处在 emit 为 None 时都需要一个"什么都不做"的默认实现，
此前各自 def 一遍违反 DRY（自审 PR #9 的 finding [5]，见复盘 D-28），这里统一提取。
"""

from __future__ import annotations


def noop_emit(event_type: str, data: dict) -> None:
    """空操作回调：emit 未提供时的默认值。"""
    return None
