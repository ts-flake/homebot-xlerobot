# Gamepad keymaps (action -> key-combo DSL) for the full xlerobot: dual arms + head + base.
# Binding data only; dispatch lives in app.py. DSL syntax: see homebot.hal.gamepad.keymap.
# Evaluate with get_gamepad_states(gamepad, ALL_KEYMAP).

# Left arm: left stick (x/z), LB+stick (y/pitch), LB+dpad (roll/yaw), LT gripper.
LEFT_ARM_KEYMAP = {
    # EE translation XZ (left stick, LB not held)
    'left_arm.x+': '!ls&!rs&!lb&ls_up',
    'left_arm.x-': '!ls&!rs&!lb&ls_down',
    'left_arm.z+': '!ls&!rs&!lb&ls_right',
    'left_arm.z-': '!ls&!rs&!lb&ls_left',
    # y and pitch (LB + left stick)
    'left_arm.pitch+': 'lb&ls_down',
    'left_arm.pitch-': 'lb&ls_up',
    'left_arm.y+': 'lb&ls_left',
    'left_arm.y-': 'lb&ls_right',
    # wrist roll / yaw (LB + dpad)
    'left_arm.roll+': 'lb&dpad_up',
    'left_arm.roll-': 'lb&dpad_down',
    'left_arm.yaw+': 'lb&dpad_left',
    'left_arm.yaw-': 'lb&dpad_right',
    # gripper (LT)
    'left_arm.gripper+': '!lb&lt',
    'left_arm.gripper-': 'lb&lt',
}

# Right arm: right stick (x/z), RB+stick (y/pitch), RB+abxy (roll/yaw), RT gripper.
RIGHT_ARM_KEYMAP = {
    'right_arm.x+': '!ls&!rs&!rb&rs_up',
    'right_arm.x-': '!ls&!rs&!rb&rs_down',
    'right_arm.z+': '!ls&!rs&!rb&rs_right',
    'right_arm.z-': '!ls&!rs&!rb&rs_left',
    'right_arm.pitch+': 'rb&rs_down',
    'right_arm.pitch-': 'rb&rs_up',
    'right_arm.y+': 'rb&rs_left',
    'right_arm.y-': 'rb&rs_right',
    'right_arm.roll+': 'rb&y',
    'right_arm.roll-': 'rb&a',
    'right_arm.yaw+': 'rb&x',
    'right_arm.yaw-': 'rb&b',
    'right_arm.gripper+': '!rb&rt',
    'right_arm.gripper-': 'rb&rt',
}

# Head: abxy without RB (RB+abxy belongs to the right wrist).
HEAD_KEYMAP = {
    'head.yaw+': '!rb&x',
    'head.yaw-': '!rb&b',
    'head.pitch+': '!rb&a',
    'head.pitch-': '!rb&y',
}

# Base: dpad (sticks are taken by the arms) + back key to cycle speed.
BASE_KEYMAP = {
    'base.forward': '!lb&!rs&dpad_up',
    'base.backward': '!lb&!rs&dpad_down',
    'base.left': 'rs&!lb&dpad_left',
    'base.right': 'rs&!lb&dpad_right',
    'base.rotate_left': '!lb&!rs&dpad_left',
    'base.rotate_right': '!lb&!rs&dpad_right',
    'base.speed_up': 'back',   # Xbox: back; PS5: create/options
}

# Reset / exit
RESET_KEYMAP = {
    'back_to_zero': 'start',
    'exit': 'logo',
}

ALL_KEYMAP = {
    **LEFT_ARM_KEYMAP,
    **RIGHT_ARM_KEYMAP,
    **HEAD_KEYMAP,
    **BASE_KEYMAP,
    **RESET_KEYMAP,
}
