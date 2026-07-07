from typing import Optional
from pathlib import Path
from dataclasses import dataclass, field, asdict

from .hardware import (
    CameraConfig,
    ArmConfig,
    HeadConfig,
    ChassisConfig,
    BatteryConfig,
)
from .network import ZMQConfig, LoggingConfig
from .services import SpeechConfig, TTSConfig, LLMConfig, VisionConfig
from .apps import GamepadConfig, HumanFollowConfig


def _default_arms() -> dict:
    """双臂默认配置 (按名索引).

    - left  在 ACM0, 与底盘共线 (left_arm 1-7 + base 8/9/10)
    - right 在 ACM1, 与头部共线 (right_arm 1-7 + head 8/9)
    各臂 service_addr / calibration_path 不同.
    """
    calib_dir = Path(__file__).resolve().parent / "calibration"
    return {
        "left": ArmConfig(
            port="/dev/serial/by-path/platform-3610000.usb-usb-0:2.2.1:1.0",
            service_addr="tcp://*:5557",
            calibration_path=str(calib_dir / "left_arm.json"),
        ),
        "right": ArmConfig(
            port="/dev/serial/by-path/platform-3610000.usb-usb-0:2.2.2:1.0",
            service_addr="tcp://*:5559",
            calibration_path=str(calib_dir / "right_arm.json"),
        ),
    }


def _default_cameras() -> dict:
    """多相机默认配置 (按名索引, path 区分设备)."""
    return {
        "head": CameraConfig(path="/dev/v4l/by-path/platform-3610000.usb-usb-0:2.1:1.0-video-index0"),
        # "left_wrist": CameraConfig(path="/dev/v4l/by-path/platform-3610000.usb-usb-0:2.3:1.0-video-index0"),
        "right_wrist": CameraConfig(path="/dev/v4l/by-path/platform-3610000.usb-usb-0:2.4:1.0-video-index0")
    }


@dataclass
class Config:
    """全局配置"""
    cameras: dict = field(default_factory=_default_cameras)   # {name: CameraConfig}
    arms: dict = field(default_factory=_default_arms)         # {name: ArmConfig} (left/right)
    head: HeadConfig = field(default_factory=HeadConfig)
    chassis: ChassisConfig = field(default_factory=ChassisConfig)
    battery: BatteryConfig = field(default_factory=BatteryConfig)
    zmq: ZMQConfig = field(default_factory=ZMQConfig)
    logging: LoggingConfig = field(default_factory=LoggingConfig)
    human_follow: HumanFollowConfig = field(default_factory=HumanFollowConfig)
    speech: SpeechConfig = field(default_factory=SpeechConfig)
    tts: TTSConfig = field(default_factory=TTSConfig)
    llm: LLMConfig = field(default_factory=LLMConfig)
    vision: VisionConfig = field(default_factory=VisionConfig)
    gamepad: GamepadConfig = field(default_factory=GamepadConfig)

    def to_dict(self) -> dict:
        """转换为字典"""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "Config":
        """从字典创建配置"""
        cameras_data = data.get("cameras")
        cameras = ({n: CameraConfig(**c) for n, c in cameras_data.items()}
                   if cameras_data else _default_cameras())
        arms_data = data.get("arms")
        arms = ({n: ArmConfig(**a) for n, a in arms_data.items()}
                if arms_data else _default_arms())
        return cls(
            cameras=cameras,
            arms=arms,
            head=HeadConfig(**data.get("head", {})),
            chassis=ChassisConfig(**data.get("chassis", {})),
            battery=BatteryConfig(**data.get("battery", {})),
            zmq=ZMQConfig(**data.get("zmq", {})),
            logging=LoggingConfig(**data.get("logging", {})),
            human_follow=HumanFollowConfig(**data.get("human_follow", {})),
            speech=SpeechConfig(**data.get("speech", {})),
            tts=TTSConfig(**data.get("tts", {})),
            llm=LLMConfig(**data.get("llm", {})),
            vision=VisionConfig(**data.get("vision", {})),
            gamepad=GamepadConfig(**data.get("gamepad", {}))
        )


_config_instance: Optional[Config] = None


def get_config() -> Config:
    """获取全局配置实例"""
    global _config_instance
    if _config_instance is None:
        _config_instance = Config()
    return _config_instance


def set_config(config: Config):
    """设置全局配置实例"""
    global _config_instance
    _config_instance = config
