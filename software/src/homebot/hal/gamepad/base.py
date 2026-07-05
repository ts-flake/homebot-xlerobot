"""游戏手柄统一接口 (语义层契约).

不同后端 (SDL / XInput) 都实现这个 ``Gamepad`` 协议, 这样 keymap 引擎与 app
与具体后端解耦, 按平台选驱动即可.

约定 (沿用 SDL GameController):
- 语义按键名: a/b/x/y, lb/rb, lt/rt, start/back/logo, ls/rs (摇杆按下).
- 摇杆 ``get_left_stick()/get_right_stick()`` 返回 ``(x, y)``:
    x 正 = 右; y 正 = 下 (SDL 轴方向). 故 "上" 判定为 ``y < -0.5``.
- 方向键 ``get_dpad()`` 返回 ``(x, y)``: x 正 = 右; y 正 = 上. 故 "上" 判定为 ``y == 1``.
  (注: 摇杆与 dpad 的 y 方向约定不同, 与上游 keymap 解码逻辑保持一致.)
"""
from __future__ import annotations

from typing import Protocol, runtime_checkable


# keymap 解码会用到的语义按键名 (decode_key 中的合法 condition)
SEMANTIC_BUTTONS = (
    "a", "b", "x", "y",
    "lb", "rb", "lt", "rt",
    "start", "back", "logo",
    "ls", "rs",
)


@runtime_checkable
class Gamepad(Protocol):
    """所有手柄后端对外暴露的统一只读接口.

    生命周期: connect() → (循环: update() 后读 get_*) → disconnect().
    """

    def connect(self) -> None: ...

    def disconnect(self) -> None: ...

    def is_connected(self) -> bool: ...

    def update(self) -> None:
        """刷新内部缓存的输入状态 (每个控制周期调用一次)."""

    def get_button(self, name: str) -> bool:
        """语义按键是否按下 (name ∈ SEMANTIC_BUTTONS)."""

    def get_left_stick(self) -> tuple[float, float]:
        """左摇杆 (x, y); x 正=右, y 正=下, 范围约 [-1, 1]."""

    def get_right_stick(self) -> tuple[float, float]:
        """右摇杆 (x, y); 约定同左摇杆."""

    def get_dpad(self) -> tuple[int, int]:
        """方向键 (x, y); 每分量 ∈ {-1, 0, 1}; x 正=右, y 正=上."""
