"""keymap 解码引擎 (与具体后端 / 机器人无关).

迁移自 lerobot-xlerobot 的 gamepad_utils. 只含 "把按键组合表达式 (DSL) 对一个
base.Gamepad 求布尔值" 的逻辑; 具体的 action→组合 绑定表 (keymap 数据) 属于 app 层
(见 applications/gamepad_control/keymaps.py).

DSL 约定:
- 语义键: ls/rs (摇杆按下), lb/rb, lt/rt, start/back/logo, a/b/x/y.
- 方向: 在 ls/rs/dpad 后加 _up/_down/_left/_right, 如 'ls_up', 'dpad_right'.
- '&' 连接多个条件 (与); '!' 前缀取非. 如 'lb&ls_up', '!ls&!rs&dpad_up'.
"""
from __future__ import annotations


def decode_key(gamepad, code: str) -> bool:
    """对单个动作的按键组合表达式 ``code`` 求值 (针对 gamepad 当前状态)."""
    conditions = code.split('&')
    state = True
    for condition in conditions:
        negate = condition.startswith('!')
        condition = condition.lstrip('!')
        if condition in ['ls', 'rs', 'lb', 'rb', 'lt', 'rt', 'start', 'back', 'logo', 'a', 'b', 'x', 'y']:
            state_i = gamepad.get_button(condition)
        elif condition.startswith('ls') or condition.startswith('rs'):
            stick = gamepad.get_left_stick() if condition.startswith('ls') else gamepad.get_right_stick()
            if condition.endswith('_up'):
                state_i = stick[1] < -0.5
            elif condition.endswith('_down'):
                state_i = stick[1] > 0.5
            elif condition.endswith('_left'):
                state_i = stick[0] < -0.5
            elif condition.endswith('_right'):
                state_i = stick[0] > 0.5
        elif condition.startswith('dpad'):
            dpad = gamepad.get_dpad()
            if condition.endswith('_up'):
                state_i = dpad[1] == 1
            elif condition.endswith('_down'):
                state_i = dpad[1] == -1
            elif condition.endswith('_left'):
                state_i = dpad[0] == -1
            elif condition.endswith('_right'):
                state_i = dpad[0] == 1
        else:
            raise ValueError(f"Invalid key: {condition}")
        if negate:
            state_i = not state_i
        state &= state_i
    return state


def get_gamepad_states(gamepad, keymap: dict[str, str]) -> dict[str, bool]:
    """刷新手柄并返回 keymap 中每个动作的 on/off 字典."""
    gamepad.update()
    states = dict.fromkeys(keymap.keys(), False)
    for action, control in keymap.items():
        states[action] = decode_key(gamepad, control)
    return states


def print_decode_keymap(keymap: dict[str, str]):
    """彩色打印 keymap 帮助 (action -> 人类可读的按键组合)."""
    words = {
        'ls': 'left stick press',
        'rs': 'right stick press',
        'lb': 'left bumper',
        'rb': 'right bumper',
        'lt': 'left trigger',
        'rt': 'right trigger',
        'start': 'start button',
        'back': 'back button',
        'logo': 'logo button',
        'dpad': 'd-pad',
        '!': 'not ',
        '_': ' '
    }
    print("\033[92m")
    print("*-----------------------------*")
    print("*      Control Key Map        *")
    print("*-----------------------------*")
    print("*   [Action] -> Key Mapping   *")
    print("*-----------------------------*")
    print("\033[0m")
    for action, control in keymap.items():
        conditions = control.split('&')
        for i, condition in enumerate(conditions):
            condition = condition.replace('ls_', 'left stick ')
            condition = condition.replace('rs_', 'right stick ')
            for c, n in words.items():
                condition = condition.replace(c, n)
            conditions[i] = condition
        _conn = ' \033[4mand\033[0m '
        print(f"\033[94m[{action:^10}]\033[0m {_conn.join(conditions):^15}")
