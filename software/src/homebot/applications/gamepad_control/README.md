# HomeBot 游戏手柄整机遥控

用一只手柄遥控整台 xlerobot：**双臂**（Cartesian EE 增量，IK 在 service 端）+ **头部**（关节）+ **底盘**（速度）。
经统一 `send_command` 客户端下发给 `motion_service`，与 lerobot `xlerobot_yaw_gamepad` 控制方案一致。

## 系统要求

- **Linux**（SDL 后端，默认）或 **Windows**（XInput 后端）
- 已连接手柄（Xbox / PS5 / 任意 SDL 可识别的手柄）
- 已启动 `motion_service`（底盘 + 双臂，可选头部）

### 依赖

```bash
cd <repo root>
pip install -e .[gamepad]      # pygame (SDL 手柄后端)
pip install -e .[kinematics]   # placo (双臂 Cartesian IK; arm_mode=cartesian 必需)
```

> 未装 pygame：手柄 `connect()` 报错。未装 placo：`ArmService` 的 ee_delta 路径优雅降级（手臂不动并提示）。

## 启动

```bash
# 终端 1: 运动服务 (all = chassis + 双臂 + head; 也可 --service arms / chassis)
cd software/src
python -m services.motion_service --service all

# 终端 2: 手柄遥控
cd software/src
python -m applications.gamepad_control
#   指定后端 / 设备索引:
python -m applications.gamepad_control --backend sdl --gamepad-id 0
```

启动时会打印当前按键映射（`print_decode_keymap`）。

## 控制映射

> 手臂是 **Cartesian 增量**（按住即持续移动，速度由 `stepsize` 决定）；夹爪 / 头部为 app 本地累加的绝对量。

### 左臂（左摇杆 + LB）

| 输入 | 动作 |
|---|---|
| 左摇杆 ↑ / ↓ | EE 前 / 后（x±） |
| 左摇杆 → / ← | EE 上 / 下（z±） |
| LB + 左摇杆 ← / → | EE 侧移（y±） |
| LB + 左摇杆 ↓ / ↑ | pitch ± |
| LB + 方向键 ↑ / ↓ | roll ± |
| LB + 方向键 ← / → | yaw ± |
| LT | 夹爪开 |
| LB + LT | 夹爪合 |

### 右臂（右摇杆 + RB）

| 输入 | 动作 |
|---|---|
| 右摇杆 ↑ / ↓ | EE 前 / 后（x±） |
| 右摇杆 → / ← | EE 上 / 下（z±） |
| RB + 右摇杆 ← / → | EE 侧移（y±） |
| RB + 右摇杆 ↓ / ↑ | pitch ± |
| RB + Y / A | roll ± |
| RB + X / B | yaw ± |
| RT | 夹爪开 |
| RB + RT | 夹爪合 |

### 头部（abxy，默认**关闭**）

| 输入 | 动作 |
|---|---|
| X / B | head_yaw ± |
| A / Y | head_pitch ± |

> 默认 `enable_head=False`，在 `GamepadConfig` 打开。注意 abxy 在按住 RB 时归右臂腕部，未按 RB 时归头部。

### 底盘（方向键）

| 输入 | 动作 |
|---|---|
| 方向键 ↑ / ↓ | 前进 / 后退 |
| 方向键 ← / → | 左转 / 右转（旋转） |
| 按下右摇杆(R3) + 方向键 ← / → | 左 / 右平移 |
| Back | 切换速度档（慢 / 中 / 快） |

> 双摇杆已被两臂占用，故底盘走数字方向键。LB 区分底盘（无 LB）与左臂腕部（LB + 方向键）。

### 系统

| 输入 | 动作 |
|---|---|
| Start | 回零位（双臂 / 头部归 home，底盘停） |
| Logo（Xbox 键） | 退出遥控 |

## 架构

```
手柄 → hal.gamepad (SDLDriver + keymap 引擎) → keymaps.py 绑定
     → 每帧增量 (dt·stepsize·factor)
     → 双臂  ArmArbiterClient.send_command(ee_delta=…, gripper=…)   # IK 在 ArmService(placo)
       头部  HeadArbiterClient.send_command(joints=…)               # 本地累加绝对角
       底盘  ChassisArbiterClient.send_command(vx, vy, vtheta)
```

控制源 `source="gamepad"`（优先级 3）。

## 配置（`configs/config.py` → `GamepadConfig`）

```python
fps: int = 30                         # 控制循环频率
gamepad_id: int = 0                   # 手柄设备索引
backend: str = "sdl"                  # "sdl" | "xinput"
enable_left_arm / right_arm / head / base: bool
arm_mode: str = "cartesian"           # ee_delta + 服务端 IK
stepsize: dict = {"ang": 20.0, "pos": 0.04, "gripper": 60.0}   # deg/s, m/s, /s
stepsize_factors: dict = {"arm": {...}, "head": {...}}
base_speed_levels: list = [{"xy": 0.1, "theta": 30}, ...]      # Back 键循环切换
```

服务地址不在此处：从 `config.chassis` / `config.arms[name]` / `config.head` 的 `service_addr` 推导。

## 改键

编辑 `keymaps.py` 里的 `LEFT_ARM_KEYMAP` / `RIGHT_ARM_KEYMAP` / `HEAD_KEYMAP` / `BASE_KEYMAP`。
组合 DSL 语法见 `hal.gamepad.keymap`（`&` 与、`!` 非、`ls_up`/`dpad_left` 等）。

## 故障排除

| 现象 | 处理 |
|---|---|
| 手柄连不上 | `pip install -e .[gamepad]`；确认系统识别手柄（`ls /dev/input/js*`、SDL_GameControllerDB） |
| 手臂不动并提示 placo | `pip install -e .[kinematics]` |
| 某子系统无响应 | 确认对应 service 已启动（`--service all`），地址 / 端口一致 |

## 文件结构

```
applications/gamepad_control/
├── __init__.py      # 包初始化 (导出 GamepadControlApp)
├── __main__.py      # 启动入口 (委托 app.main())
├── app.py           # 主循环 + 各子系统 dispatch
├── keymaps.py       # 控制方案 (改键在这)
└── README.md        # 本文档
```
