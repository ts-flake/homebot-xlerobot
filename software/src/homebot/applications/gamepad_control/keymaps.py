"""手柄按键映射 (action → 按键组合 DSL) —— 整机 xlerobot: 双臂 + 头部 + 底盘.

app 层的控制方案数据 (绑定与 app dispatch 分离, 便于改键). 迁移自 lerobot gamepad_utils.

方案 (与 xlerobot_yaw_gamepad 一致):
- 左臂: 左摇杆 (x/z) + LB&左摇杆 (y/pitch) + LB&dpad (roll/yaw) + LT (gripper)
- 右臂: 右摇杆 (x/z) + RB&右摇杆 (y/pitch) + RB&abxy (roll/yaw) + RT (gripper)
- 头部: abxy (未按 RB, 与右臂腕部用 RB 错开)
- 底盘: dpad (双摇杆已被两臂占用, 故底盘走数字方向键) + back 键切速

DSL 语法见 hal.gamepad.keymap; 用 get_gamepad_states(gamepad, ALL_KEYMAP) 求各动作 on/off.
"""

# 左臂: 左摇杆 + LB 修饰 + LB&dpad 腕部 + LT 夹爪
LEFT_ARM_KEYMAP = {
    # EE 平移 XZ (左摇杆; 未按 LB)
    'left_arm.x+': '!ls&!rs&!lb&ls_up',
    'left_arm.x-': '!ls&!rs&!lb&ls_down',
    'left_arm.z+': '!ls&!rs&!lb&ls_right',
    'left_arm.z-': '!ls&!rs&!lb&ls_left',
    # shoulder_pan (y) 与 pitch (LB + 左摇杆)
    'left_arm.pitch+': 'lb&ls_down',
    'left_arm.pitch-': 'lb&ls_up',
    'left_arm.y+': 'lb&ls_left',
    'left_arm.y-': 'lb&ls_right',
    # wrist_roll / wrist_yaw (LB + dpad)
    'left_arm.roll+': 'lb&dpad_up',
    'left_arm.roll-': 'lb&dpad_down',
    'left_arm.yaw+': 'lb&dpad_left',
    'left_arm.yaw-': 'lb&dpad_right',
    # 夹爪 (LT)
    'left_arm.gripper+': '!lb&lt',
    'left_arm.gripper-': 'lb&lt',
}

# 右臂: 右摇杆 + RB 修饰 + RB&abxy 腕部 + RT 夹爪
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

# 头部: abxy (未按 RB, 与右臂腕部错开)
HEAD_KEYMAP = {
    'head.yaw+': '!rb&x',
    'head.yaw-': '!rb&b',
    'head.pitch+': '!rb&a',
    'head.pitch-': '!rb&y',
}

# 底盘: dpad (数字方向) + back 键切速
BASE_KEYMAP = {
    'base.forward': '!lb&!rs&dpad_up',
    'base.backward': '!lb&!rs&dpad_down',
    'base.left': 'rs&!lb&dpad_left',
    'base.right': 'rs&!lb&dpad_right',
    'base.rotate_left': '!lb&!rs&dpad_left',
    'base.rotate_right': '!lb&!rs&dpad_right',
    'base.speed_up': 'back',   # Xbox: back; PS5: create/options
}

# 复位 / 退出
RESET_KEYMAP = {
    'back_to_zero': 'start',   # 回零位
    'exit': 'logo',            # 退出遥控
}

ALL_KEYMAP = {
    **LEFT_ARM_KEYMAP,
    **RIGHT_ARM_KEYMAP,
    **HEAD_KEYMAP,
    **BASE_KEYMAP,
    **RESET_KEYMAP,
}
