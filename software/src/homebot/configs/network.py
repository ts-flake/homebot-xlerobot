from dataclasses import dataclass


@dataclass
class ZMQConfig:
    """ZeroMQ网络配置 (非运动子系统地址; 运动服务地址在各自 config 的 service_addr)."""
    # legacy: 运动服务地址已迁到 ChassisConfig / ArmConfig / HeadConfig.service_addr;
    # 下面两项仅留给待迁移的 speech mcp_server, 勿在新代码引用.
    chassis_service_addr: str = "tcp://*:5556"
    arm_service_addr: str = "tcp://*:5557"
    vision_pub_addr: str = "tcp://*:5560"
    speech_service_addr: str = "tcp://*:5570"   # 语音服务地址（备用）
    wakeup_pub_addr: str = "tcp://*:5571"       # 唤醒+ASR PUB地址


@dataclass
class LoggingConfig:
    """日志配置"""
    level: str = "INFO"
