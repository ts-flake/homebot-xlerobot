from dataclasses import dataclass, field


@dataclass
class GamepadConfig:
    """游戏手柄控制配置 (控制方案 / 策略, 对齐 lerobot xlerobot_yaw_gamepad).

    只放 app 控制策略: 手柄设备、步长、使能开关、底盘速度档. 不含硬件死区 (keymap
    解码以 0.5 摇杆阈值充当死区), 也不重复 ChassisConfig 的速度上限或 service 地址
    (后者由 app 从 config.chassis / config.arms / config.zmq 推导 connect 地址).
    """

    # ── 手柄设备 / 循环 ──
    fps: int = 30                          # 控制循环频率 (Hz)
    gamepad_id: int = 0                    # 打开哪个手柄 (设备索引)
    backend: str = "sdl"                   # "sdl" (Linux/跨平台) | "xinput" (Windows) | "auto"

    # ── 使能开关 ──
    enable_left_arm: bool = True
    enable_right_arm: bool = True
    enable_head: bool = False
    enable_base: bool = True

    # 机械臂控制模式: "cartesian" 走 ee_delta (service 端 placo IK).
    arm_mode: str = "cartesian"

    # ── 步长 (速度单位: ang=deg/s, pos=m/s, gripper=单位/s); 实际增量 = stepsize*factor*dt ──
    stepsize: dict = field(default_factory=lambda: {"ang": 20.0, "pos": 0.04, "gripper": 30.0})
    stepsize_factors: dict = field(default_factory=lambda: {
        "arm":  {"x": 1, "y": 1, "z": 1, "roll": 2, "pitch": 1, "yaw": 1},
        "head": {"pitch": 1, "yaw": 1},
    })

    # ── 底盘速度档 (back 键循环切换): xy m/s, theta deg/s ──
    base_speed_levels: list = field(default_factory=lambda: [
        {"xy": 0.1, "theta": 30},   # 慢
        {"xy": 0.2, "theta": 60},   # 中
        {"xy": 0.3, "theta": 90},   # 快
    ])


@dataclass
class HumanFollowConfig:
    """人体跟随配置（YOLO26版）"""
    # 模型配置
    model_path: str = "models/yolo26n.onnx"     # YOLO26 nano (~2.4MB)
    conf_threshold: float = 0.5               # 检测置信度阈值

    # 跟踪配置
    max_tracking_age: int = 30                # 最大丢失帧数
    min_iou_threshold: float = 0.3            # IoU匹配阈值
    target_selection: str = "center"          # 目标选择策略: center/largest/closest

    # 推理优化（边缘设备）
    inference_size: int = 320                 # 输入分辨率 320x320
    use_half_precision: bool = False          # FP16半精度推理（需GPU支持）

    # 跟随控制配置
    target_distance: float = 1.0              # 目标距离（米）
    target_width_ratio: float = 0.4          # 1米处人体占画面宽度比例（0.25=25%）
    target_height_ratio: float = 1.0          # 1米处人体占画面高度比例（1.0=100%）
    kp_linear: float = 0.8                    # 线速度P系数（归一化误差后）
    kp_angular: float = 1.5                   # 角速度P系数（归一化误差后）
    max_linear_speed: float = 0.5             # 最大线速度 (m/s)
    max_angular_speed: float = 2.0            # 最大角速度 (rad/s)
    dead_zone_x: float = 0.15                 # 水平死区（比例值，0.15=15%画面宽度）
    dead_zone_area: float = 0.1               # 面积死区（相对值）

    # 安全配置
    timeout_ms: int = 1000                    # 通信超时
    stop_on_lost: bool = True                 # 丢失目标时是否停止
    search_on_lost: bool = False               # 丢失时是否旋转搜索
    lost_patience: int = 30                   # 丢失容忍帧数（约2秒@30fps）

    # ZeroMQ配置
    chassis_service_addr: str = "tcp://localhost:5556"
    vision_sub_addr: str = "tcp://localhost:5560"
