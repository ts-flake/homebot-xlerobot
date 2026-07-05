import json
from typing import Optional, Any
from enum import Enum
from dataclasses import dataclass, field


class SourcePriority(Enum):
    EMERGENCY = ('emergency', 4)
    GAMEPAD = ('gamepad', 3)
    AUTO = ('auto', 3)
    VOICE = ('voice', 2)
    WEB = ('web', 1)
    UNKNOWN = ('unknown', 0)
    HOME = ('home', 0)


class Command(Enum):
    MOVE = 'move'       # actuate the request payload (default)
    QUERY = 'query'     # read state only, no actuation
    HOME = 'home'       # blocking move to home pose
    STOP = 'stop'       # release control and hold
    UNLOCK = 'unlock'   # clear emergency lock


class CameraStatus(Enum):
    OK = 'ok'
    NO_SIGNAL = 'no_signal'     # capture returned no frame
    RECONNECTING = 'reconnecting'


class BatteryStatus(Enum):
    UNKNOWN = "unknown"
    NORMAL = "normal"
    LOW = "low"
    CRITICAL = "critical"
    CHARGING = "charging"


@dataclass
class PIDGains:
    """Feetech position-loop PID register values, shared by config and driver."""
    p: int = 16
    i: int = 0
    d: int = 32


@dataclass
class BatteryState:
    id: int | str = 0
    voltage: float = 0.0
    percentage: float = 0.0
    status: BatteryStatus = BatteryStatus.UNKNOWN
    temperature: Optional[int] = None  # degree celsius
    timestamp_s: Optional[float] = None

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "voltage": self.voltage,
            "percentage": self.percentage,
            "status": self.status.value,
            "temperature": self.temperature,
            "timestamp_s": self.timestamp_s,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "BatteryState":
        return cls(
            id=d.get("id", 0),
            voltage=d.get("voltage", 0.0),
            percentage=d.get("percentage", 0.0),
            status=BatteryStatus(d.get("status", BatteryStatus.UNKNOWN.value)),
            temperature=d.get("temperature"),
            timestamp_s=d.get("timestamp_s"),
        )


@dataclass
class Velocity:
    timestamp_s: Optional[float] = None
    linear: dict[str, Any] = field(default_factory=lambda: {
        'x': 0.0, # m/s
        'y': 0.0,
        'z': 0.0,
    })
    angular: dict[str, Any] = field(default_factory=lambda: {
        'x': 0.0, # rad/s
        'y': 0.0,
        'z': 0.0,
    })
    duration_s: Optional[float] = None # duration in second


@dataclass
class NavigationVelocity:
    timestamp_s: Optional[float] = None
    linear: dict[str, Any] = field(default_factory=lambda: {
        'value': 0.0, # distance (m), value > 0 moves forward, < 0 moves backward
        'speed': 0.0, # m/s
    })
    angular: dict[str, Any] = field(default_factory=lambda: {
        'value': 0.0, # angle (rad), angle > 0 rotates counter-clockwise, < 0 rotates clockwise
        'speed': 0.0,
    })


@dataclass
class JointAngles:
    timestamp_s: Optional[float] = None
    joint_angles: dict[str, Any] = field(default_factory=dict)


@dataclass
class EEDelta:
    timestamp_s: Optional[float] = None
    ee_delta: dict[str, Any] = field(default_factory=lambda: {
        'x': 0.0, # m
        'y': 0.0,
        'z': 0.0,
        'rx': 0.0, # angle (rad) around x-axis (sign follows right hand rule)
        'ry': 0.0,
        'rz': 0.0,
    })
    extra: Optional[dict[str, Any]] = None # extra info passed here, e.g., gripper state


@dataclass
class Image:
    """Camera frame. data holds raw encoded bytes; wire form is multipart [header, data]."""
    frame_id: int = 0
    timestamp_s: Optional[float] = None
    width: int = 0
    height: int = 0
    encoding: str = 'jpeg'
    status: CameraStatus = CameraStatus.OK
    data: bytes = b''

    def to_multipart(self) -> list[bytes]:
        header = {
            'frame_id': self.frame_id,
            'timestamp_s': self.timestamp_s,
            'width': self.width,
            'height': self.height,
            'encoding': self.encoding,
            'status': self.status.value,
        }
        return [json.dumps(header).encode(), self.data]

    @classmethod
    def from_multipart(cls, parts: list[bytes]) -> "Image":
        header = json.loads(parts[0].decode())
        return cls(
            frame_id=header.get('frame_id', 0),
            timestamp_s=header.get('timestamp_s'),
            width=header.get('width', 0),
            height=header.get('height', 0),
            encoding=header.get('encoding', 'jpeg'),
            status=CameraStatus(header.get('status', CameraStatus.OK.value)),
            data=parts[1] if len(parts) > 1 else b'',
        )



