"""HomeBot 游戏手柄整机遥控应用.

用一只手柄遥控整台 xlerobot (双臂 Cartesian + 头部 + 底盘), 经统一 send_command
客户端下发给 motion_service. 控制方案见 keymaps.py.

命令行:
    cd software/src
    python -m applications.gamepad_control
"""

from .app import GamepadControlApp

__all__ = ["GamepadControlApp"]
