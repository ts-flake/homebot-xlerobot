"""SDL / pygame 游戏手柄后端 (Linux / 跨平台).

迁移自 lerobot-xlerobot 的 gamepad_utils.SDLDriver. 实现 base.Gamepad 契约.

存在理由: pygame.joystick 暴露的原始按键/轴索引取决于内核 HID 驱动 —— Ubuntu 22
(hid-sony) 与 Ubuntu 24 (hid-playstation) 同一手柄索引布局不同. 本类优先用 SDL2
GameController API (经 SDL_GameControllerDB 给出稳定语义名), 回退到 Joystick API +
名称匹配的布局表. (Windows 用 XInput 后端则无此问题.)

注: pygame 为可选依赖 (见 setup.py extras "gamepad"); 未安装时本模块仍可 import,
仅在 connect() 时报错.
"""
from __future__ import annotations

import logging
import warnings

from .base import SEMANTIC_BUTTONS

logger = logging.getLogger(__name__)

try:
    import pygame
    _PYGAME_AVAILABLE = True
except ImportError:
    pygame = None  # type: ignore
    _PYGAME_AVAILABLE = False

# SDL2 GameController API (首选路径). 经 SDL_GameControllerDB 让语义按键名跨发行版一致.
try:
    from pygame._sdl2 import controller as _sdl_controller  # type: ignore
    _SDL_CONTROLLER_AVAILABLE = True
except ImportError:  # 很旧的 pygame 或未装 pygame; 回退到 Joystick API
    _sdl_controller = None  # type: ignore
    _SDL_CONTROLLER_AVAILABLE = False


# 各语义按键对应的 SDL GameController 按键常量名 (运行时 getattr(pygame, NAME) 解析,
# 兼容未暴露全部常量的 pygame 构建). 扳机走 axis.
_SDL_BUTTON_CONST_NAMES = {
    "a":     "CONTROLLER_BUTTON_A",
    "b":     "CONTROLLER_BUTTON_B",
    "x":     "CONTROLLER_BUTTON_X",
    "y":     "CONTROLLER_BUTTON_Y",
    "lb":    "CONTROLLER_BUTTON_LEFTSHOULDER",
    "rb":    "CONTROLLER_BUTTON_RIGHTSHOULDER",
    "back":  "CONTROLLER_BUTTON_BACK",
    "start": "CONTROLLER_BUTTON_START",
    "logo":  "CONTROLLER_BUTTON_GUIDE",
    "ls":    "CONTROLLER_BUTTON_LEFTSTICK",
    "rs":    "CONTROLLER_BUTTON_RIGHTSTICK",
}

# Joystick API 回退布局 (仅当 SDL GameController API 无法接管设备时使用, PS5 DualSense
# 很少需要). 遇到未识别布局时在此新增条目, 并在 _detect_joystick_layout 里按名称匹配.
_JOYSTICK_LAYOUTS: dict[str, dict] = {
    "sony_legacy": {
        "btn": {
            "a": 0, "b": 1, "y": 2, "x": 3,
            "lb": 4, "rb": 5,
            "start": 8, "back": 9, "logo": 10,
            "ls": 11, "rs": 12,
        },
        "lt_axis": 2,
        "rt_axis": 5,
        "ls_axes": (0, 1),
        "rs_axes": (3, 4),
    },
}

# 把模拟扳机当作数字按键的阈值.
_TRIGGER_THRESHOLD = 0.5
# SDL Controller 轴为有符号 int16 — 归一化到 [-1, 1] / [0, 1].
_SDL_AXIS_DENOM = 32767.0


class SDLDriver:
    """通用 SDL/pygame 手柄包装, 跨 Linux 发行版 / HID 驱动鲁棒. 实现 base.Gamepad 契约.

    公开接口: connect/disconnect/is_connected/update/reset/
    get_button(name)/get_left_stick()/get_right_stick()/get_dpad().
    """

    def __init__(self, id: int = 0):
        self.id = id
        self._mode: str | None = None  # 'controller' or 'joystick'
        self._controller = None
        self._joystick = None
        self._joystick_layout: str | None = None
        self.reset()

    def is_connected(self) -> bool:
        if self._mode == "controller" and self._controller is not None:
            try:
                return bool(self._controller.attached())
            except Exception:
                return False
        if self._mode == "joystick" and self._joystick is not None:
            return bool(self._joystick.get_init())
        return False

    def connect(self) -> None:
        if not _PYGAME_AVAILABLE:
            raise RuntimeError(
                "pygame 未安装, SDLDriver 不可用. 请 pip install pygame (见 setup.py extras 'gamepad')."
            )
        pygame.init()
        pygame.joystick.init()

        # ---- 首选: SDL GameController API (驱动无关映射) ----
        if _SDL_CONTROLLER_AVAILABLE:
            try:
                _sdl_controller.init()
                if (
                    _sdl_controller.get_count() > self.id
                    and _sdl_controller.is_controller(self.id)
                ):
                    self._controller = _sdl_controller.Controller(self.id)
                    self._mode = "controller"
                    name = getattr(self._controller, "name", "<unknown>")
                    logger.info(
                        "SDLDriver: using SDL GameController API "
                        f"(id={self.id}, name={name!r})"
                    )
                    return
                logger.warning(
                    f"SDLDriver: device {self.id} is not registered as an SDL "
                    "GameController; falling back to Joystick API."
                )
            except Exception as e:
                logger.warning(
                    f"SDLDriver: SDL GameController init failed ({e!r}); "
                    "falling back to Joystick API."
                )
        else:
            logger.warning(
                "SDLDriver: pygame._sdl2.controller unavailable; "
                "falling back to Joystick API."
            )

        # ---- 回退: 原始 Joystick API + 布局自动检测 ----
        if pygame.joystick.get_count() <= self.id:
            logger.error("No gamepad detected. Please connect a gamepad and try again.")
            return
        self._joystick = pygame.joystick.Joystick(self.id)
        self._joystick.init()
        self._mode = "joystick"
        self._joystick_layout = self._detect_joystick_layout(self._joystick)
        guid = getattr(self._joystick, "get_guid", lambda: "?")()
        logger.info(
            "SDLDriver: using Joystick API fallback "
            f"(id={self.id}, name={self._joystick.get_name()!r}, "
            f"guid={guid}, layout={self._joystick_layout!r})"
        )

    @staticmethod
    def _detect_joystick_layout(joystick) -> str:
        """按名称选回退布局. 目前只内置 legacy 布局; 需要时扩展 _JOYSTICK_LAYOUTS."""
        name = (joystick.get_name() or "").lower()
        _ = name  # reserved for future name-based detection
        return "sony_legacy"

    def disconnect(self) -> None:
        if self._mode == "controller" and self._controller is not None:
            try:
                self._controller.quit()
            except Exception:
                pass
            self._controller = None
        if self._mode == "joystick" and self._joystick is not None:
            try:
                self._joystick.quit()
            except Exception:
                pass
            self._joystick = None
        if _SDL_CONTROLLER_AVAILABLE:
            try:
                _sdl_controller.quit()
            except Exception:
                pass
        if _PYGAME_AVAILABLE:
            pygame.quit()
        self._mode = None

    def reset(self):
        self._buttons: dict[str, bool] = {k: False for k in SEMANTIC_BUTTONS}
        self._left_stick: tuple[float, float] = (0.0, 0.0)
        self._right_stick: tuple[float, float] = (0.0, 0.0)
        self._dpad: tuple[int, int] = (0, 0)
        self._lt_value: float = 0.0
        self._rt_value: float = 0.0

    def update(self):
        if self._mode is None:
            return  # 未连接; pump 在 pygame.init() 前会失败
        pygame.event.pump()
        if self._mode == "controller":
            self._update_from_controller()
        elif self._mode == "joystick":
            self._update_from_joystick()

    # ---------- internal: SDL GameController path ----------

    def _update_from_controller(self):
        c = self._controller

        for name, const_name in _SDL_BUTTON_CONST_NAMES.items():
            const = getattr(pygame, const_name, None)
            if const is None:
                self._buttons[name] = False
                continue
            try:
                self._buttons[name] = bool(c.get_button(const))
            except Exception:
                self._buttons[name] = False

        lx = self._sdl_axis("CONTROLLER_AXIS_LEFTX")
        ly = self._sdl_axis("CONTROLLER_AXIS_LEFTY")
        rx = self._sdl_axis("CONTROLLER_AXIS_RIGHTX")
        ry = self._sdl_axis("CONTROLLER_AXIS_RIGHTY")
        self._left_stick = (lx / _SDL_AXIS_DENOM, ly / _SDL_AXIS_DENOM)
        self._right_stick = (rx / _SDL_AXIS_DENOM, ry / _SDL_AXIS_DENOM)

        # 扳机: SDL 报告 [0, 32767]; 归一化到 [0, 1].
        self._lt_value = max(0.0, self._sdl_axis("CONTROLLER_AXIS_TRIGGERLEFT") / _SDL_AXIS_DENOM)
        self._rt_value = max(0.0, self._sdl_axis("CONTROLLER_AXIS_TRIGGERRIGHT") / _SDL_AXIS_DENOM)
        self._buttons["lt"] = self._lt_value > _TRIGGER_THRESHOLD
        self._buttons["rt"] = self._rt_value > _TRIGGER_THRESHOLD

        right = self._sdl_btn("CONTROLLER_BUTTON_DPAD_RIGHT")
        left = self._sdl_btn("CONTROLLER_BUTTON_DPAD_LEFT")
        up = self._sdl_btn("CONTROLLER_BUTTON_DPAD_UP")
        down = self._sdl_btn("CONTROLLER_BUTTON_DPAD_DOWN")
        self._dpad = (int(right) - int(left), int(up) - int(down))

    def _sdl_axis(self, const_name: str) -> float:
        const = getattr(pygame, const_name, None)
        if const is None:
            return 0.0
        try:
            return float(self._controller.get_axis(const))
        except Exception:
            return 0.0

    def _sdl_btn(self, const_name: str) -> bool:
        const = getattr(pygame, const_name, None)
        if const is None:
            return False
        try:
            return bool(self._controller.get_button(const))
        except Exception:
            return False

    # ---------- internal: Joystick fallback path ----------

    def _update_from_joystick(self):
        j = self._joystick
        layout = _JOYSTICK_LAYOUTS[self._joystick_layout]
        nb = j.get_numbuttons()
        na = j.get_numaxes()

        def btn(idx):
            return bool(j.get_button(idx)) if idx is not None and idx < nb else False

        def axis(idx):
            return float(j.get_axis(idx)) if idx is not None and idx < na else 0.0

        btn_map = layout["btn"]
        for name in ("a", "b", "x", "y", "lb", "rb",
                     "start", "back", "logo", "ls", "rs"):
            self._buttons[name] = btn(btn_map.get(name))

        # 保留原始扳机语义: raw axis > 0.5 视为按下.
        self._lt_value = axis(layout["lt_axis"])
        self._rt_value = axis(layout["rt_axis"])
        self._buttons["lt"] = self._lt_value > _TRIGGER_THRESHOLD
        self._buttons["rt"] = self._rt_value > _TRIGGER_THRESHOLD

        lx, ly = layout["ls_axes"]
        rx, ry = layout["rs_axes"]
        self._left_stick = (axis(lx), axis(ly))
        self._right_stick = (axis(rx), axis(ry))

        if j.get_numhats() > 0:
            hx, hy = j.get_hat(0)
            self._dpad = (int(hx), int(hy))
        else:
            self._dpad = (0, 0)

        # 设备形状与所选布局差异过大时告警, 快速暴露 "布局不对" 问题.
        if nb < max(btn_map.values()) + 1 or na < max(layout["lt_axis"], layout["rt_axis"]) + 1:
            warnings.warn(
                f"Joystick reports {nb} buttons / {na} axes — does not fit "
                f"layout {self._joystick_layout!r}. Add a matching entry to "
                "_JOYSTICK_LAYOUTS or use the SDL GameController path."
            )

    # ---------- public read API (base.Gamepad 契约) ----------

    def get_button(self, name: str) -> bool:
        if name not in self._buttons:
            raise ValueError(f"Invalid button: {name}")
        return self._buttons[name]

    def get_left_stick(self) -> tuple[float, float]:
        return self._left_stick

    def get_right_stick(self) -> tuple[float, float]:
        return self._right_stick

    def get_dpad(self) -> tuple[int, int]:
        return self._dpad
