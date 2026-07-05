"""HomeBot 游戏手柄整机遥控 —— 启动入口.

委托给 app.main() (CLI 参数 / 控制方案见 app.py 与 README.md).

    cd software/src
    python -m applications.gamepad_control [--backend sdl|xinput] [--gamepad-id 0]
"""
from .app import main

if __name__ == "__main__":
    main()
