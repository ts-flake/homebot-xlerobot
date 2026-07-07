from typing import Optional
from dataclasses import dataclass, field
from pathlib import Path

from homebot.common.interfaces.msg import PIDGains


@dataclass
class CameraConfig:
    """单个摄像头配置.

    用设备 ``path`` (如 /dev/video0) 而非数字 index: 重启后更稳定, 多相机时不易错位.
    OpenCV 的 VideoCapture 同时接受路径字符串和整数索引.

    fourcc 默认 MJPG (压缩): UVC 相机默认多为 YUYV 未压缩, 高分辨率会超 USB 带宽,
    导致 V4L2 ``select() timeout`` / 取不到帧. 需要原始帧时再改回 "YUYV".
    """
    path: str = "/dev/video0"
    width: int = 640
    height: int = 480
    fps: int = 30
    fourcc: str = "MJPG"

    # 本相机 vision service 的 PUB bind 地址; None 回退到 zmq.vision_pub_addr (主相机用).
    pub_addr: Optional[str] = None


@dataclass
class ArmConfig:
    """机械臂配置 (lerobot motors bus aligned).

    支持任意 DOF: 关节命名与 ID 通过 ``joint_motors`` 字典声明, 不写死在 driver.
    单实例 = 单只机械臂; 双臂在 Config.arms dict 里实例化两份 (left/right),
    各自 port / service_addr / calibration_path 不同.
    """

    # ── 硬件 (默认值; 具体 port 由 Config.arms 里 left/right 覆盖) ──
    port: str = "/dev/serial/by-path/platform-3610000.usb-usb-0:2.2.1:1.0"
    baudrate: int = 1_000_000

    # ── 电机 ──
    # {joint_name: servo_id}; 顺序定义 driver sync_read/write 默认遍历顺序.
    # 默认对齐 lerobot SO-101 yaw arm (7 关节 + gripper).
    joint_motors: dict = field(default_factory=lambda: {
        "shoulder_pan":  1,
        "shoulder_lift": 2,
        "elbow_flex":    3,
        "wrist_flex":    4,
        "wrist_yaw":     5,
        "wrist_roll":    6,
        "gripper":       7,
    })
    joint_motor_model: str = "sts3215"

    # 夹爪关节名; None 表示无 gripper. gripper 用 RANGE_0_100 normalization
    # (0=完全关闭, 100=完全打开).
    gripper_joint: Optional[str] = "gripper"

    # 非 gripper 关节的 normalization mode 选择.
    # True  -> DEGREES (单位: 度)
    # False -> RANGE_M100_100 (单位: 归一化 [-100, 100])
    use_degrees: bool = True

    # 休息位置 {joint_name: value}. 单位跟随 use_degrees / gripper RANGE_0_100.
    home_position: dict = field(default_factory=lambda: {
        "shoulder_pan":  0.0,
        "shoulder_lift": -90.0,
        "elbow_flex":    85.0,
        "wrist_flex":    25.0,
        "wrist_yaw":     0.0,
        "wrist_roll":    -90.0,
        "gripper":       0.0,
    })

    # 单次 write 相对当前位置的最大允许偏移 (度). None 关闭安全限幅.
    max_relative_target: Optional[float] = 10.0

    # 关节 PID. default_pid 是未覆盖关节的统一默认 (沿用 lerobot xlerobot_yaw 推荐值);
    # pid_gains 是 per-joint 覆盖 {joint_name: PIDGains}.
    default_pid: PIDGains = field(default_factory=PIDGains)
    pid_gains: dict = field(default_factory=lambda: {"shoulder_pan": PIDGains(p=8, i=0, d=32)})

    # Gripper 过载保护 (Feetech 寄存器值).
    gripper_max_torque_limit: int = 500     # 50% of max
    gripper_protection_current: int = 250
    gripper_overload_torque: int = 25

    # ── Calibration ──
    calibration_path: str = str(Path(__file__).resolve().parent / "calibration/arm.json")

    # ── ZMQ ──
    service_addr: str = "tcp://*:5557"

    # ── Placo IK (Tier 2 ee_delta 用; driver 本身不依赖) ──
    urdf_path: str = str(Path(__file__).resolve().parent.parent / "hal/arm/kinematics/so101_yaw")
    ee_frame_name: str = "gripper_back"

    # 语义关节名 → URDF 关节名 (driver 用语义名, placo 用 URDF 名). 仅非夹爪关节;
    # IK 顺序由 arm_motors (= joint_motors 去掉 gripper) 决定, 两者按此 map 对应.
    ik_joint_map: dict = field(default_factory=lambda: {
        "shoulder_pan":  "joint1",
        "shoulder_lift": "joint2",
        "elbow_flex":    "joint3",
        "wrist_flex":    "joint4",
        "wrist_yaw":     "joint5",
        "wrist_roll":    "joint6",
    })

    # 启动时缓慢回 home / 退出前返回上电初始位姿的运动时长 (s). 越大越慢越平稳.
    home_move_duration: float = 4.0


@dataclass
class HeadConfig:
    """头部配置 (关节空间, 无夹爪 / 无 IK).

    一般 2 关节 (head_yaw / head_pitch), 但 DOF 与命名由 ``joint_motors`` 决定.
    默认与 left_arm 同一条总线 (ACM0 = left_arm + head); 同 port 时 MotorBusManager
    会自动共享 bus. head ids 8/9 与 left_arm 1-7 不冲突.
    """

    # ── 硬件 (默认 = left_arm 总线 ACM0) ──
    port: str = "/dev/serial/by-path/platform-3610000.usb-usb-0:2.2.1:1.0"
    baudrate: int = 1_000_000

    # ── 电机 {joint_name: servo_id}; 参考 xlerobot head_yaw=8, head_pitch=9 ──
    joint_motors: dict = field(default_factory=lambda: {
        "head_yaw":   8,
        "head_pitch": 9,
    })
    joint_motor_model: str = "sts3215"

    # 非夹爪关节: True→DEGREES, False→RANGE_M100_100
    use_degrees: bool = True

    # 休息位置 {joint_name: deg}
    home_position: dict = field(default_factory=lambda: {
        "head_yaw":   0.0,
        "head_pitch": 35.0,
    })

    # 单次 write 相对当前位置的最大允许偏移 (度). None 关闭安全限幅.
    max_relative_target: Optional[float] = 10.0

    # 关节 PID. 参考 xlerobot head 推荐值 (P=18, I=0, D=36); pid_gains 为 per-joint 覆盖.
    default_pid: PIDGains = field(default_factory=lambda: PIDGains(p=18, i=0, d=36))
    pid_gains: dict = field(default_factory=dict)

    # 回零 (command="home") 时缓慢平滑运动到 home 的时长 (s).
    home_move_duration: float = 3.0

    # ── Calibration ──
    calibration_path: str = str(Path(__file__).resolve().parent / "calibration/head.json")

    # ── ZMQ ──
    service_addr: str = "tcp://*:5558"


@dataclass
class ChassisConfig:
    """底盘配置（重构版, 对齐 lerobot motors bus）。

    - 硬件: port / baudrate, 由 ``motor_bus_manager`` 用来打开共享 ``FeetechMotorsBus``
    - 电机: wheel_motors 是 ``{name: servo_id}``, driver 用 name, bus 用 id
    - 几何: kinematics 字段 {'type': ...(+ 参数)}, 由 hal.make_chassis_kinematics 解析
    - calibration: 持久化到 calibration_path (JSON)
    """

    # ── 硬件 (默认 = right_arm 总线 ACM1) ──
    port: str = "/dev/serial/by-path/platform-3610000.usb-usb-0:2.2.2:1.0"
    baudrate: int = 1_000_000

    # Motor name: id (same as those in calibration)
    wheel_motors: dict = field(default_factory=lambda: {
        "base_left_wheel":  8,
        "base_back_wheel":  9,
        "base_right_wheel": 10,
    })
    wheel_motor_model: str = "sts3215"

    # Kinematics topology + params; 'type' selects the hal kinematics class, the
    # remaining keys are forwarded to it. Wheel names must match wheel_motors.
    kinematics: dict = field(default_factory=lambda: {
        'type': 'omni',
        'wheel_radius': 0.05,   # m
        'base_radius': 0.125,   # m, wheel center to base center
        'wheel_motors': ['base_left_wheel', 'base_back_wheel', 'base_right_wheel'],
        'wheel_mounting_angles_deg': {
            'base_left_wheel': 240.0,
            'base_back_wheel': 0.0,
            'base_right_wheel': 120.0,
        },
    })

    # ── 控制约束 (SI, 喂给 HAL driver) ──
    max_linear_speed: float = 0.5      # m/s
    max_angular_speed: float = 3.14159  # rad/s (≈180 deg/s)

    # ── Calibration ──
    calibration_path: str = str(Path(__file__).resolve().parent / "calibration/chassis.json")

    # ── ZeroMQ ──
    service_addr: str = "tcp://*:5556"


@dataclass
class BatteryConfig:
    """电池监测配置"""
    # Serial bus for standalone runs; defaults match the chassis bus (battery rides a shared bus).
    port: str = "/dev/serial/by-path/platform-3610000.usb-usb-0:2.2.2:1.0"
    baudrate: int = 1_000_000
    motors: dict = field(default_factory=lambda: {
        "base_left_wheel": 8,
        "base_back_wheel": 9,
        "base_right_wheel": 10,
    })
    motor_model: str = "sts3215"
    calibration_path: str = str(Path(__file__).resolve().parent / "calibration/battery.json")

    # 用于读取电压的舵机ID列表（按优先级排序）
    motor_ids: list = field(default_factory=lambda: [8])

    # 电压阈值配置 (3S锂电池)
    full_voltage: float = 12.6     # 满电电压 (V)
    low_voltage: float = 10.5      # 低电量阈值 (V)
    critical_voltage: float = 9.5  # 严重低电量阈值 (V)
    min_voltage: float = 9.0       # 最低工作电压 (V)

    # 发布配置
    pub_interval_s: float = 5.0  # 电压信息发布间隔 (秒)
    pub_addr: str = "tcp://*:5555"  # 电池状态PUB地址
