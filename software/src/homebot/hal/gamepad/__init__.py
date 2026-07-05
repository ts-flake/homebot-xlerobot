from .base import Gamepad, SEMANTIC_BUTTONS

# XInput backend (Windows). Importing the class does not load the DLL (loaded in
# __init__), so import is safe on Linux; only instantiation fails there.
from .xinput_driver import (
    XInputDriver,
    XboxController,         # backward-compat alias (= XInputDriver)
    ControllerState,
    StickState,
    ButtonFlags as Button,  # backward-compat
    get_connected_controllers,
    wait_for_connection,
    XINPUT_MAX_CONTROLLERS,
)

# SDL backend (Linux/cross-platform); imports even without pygame (connect() raises).
from .sdl_driver import SDLDriver

# keymap decode engine (data-agnostic)
from .keymap import decode_key, get_gamepad_states, print_decode_keymap

__all__ = [
    "Gamepad",
    "SEMANTIC_BUTTONS",
    "SDLDriver",
    "XInputDriver",
    "XboxController",
    "ControllerState",
    "StickState",
    "Button",
    "get_connected_controllers",
    "wait_for_connection",
    "XINPUT_MAX_CONTROLLERS",
    "decode_key",
    "get_gamepad_states",
    "print_decode_keymap",
]
