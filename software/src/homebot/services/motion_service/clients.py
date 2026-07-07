import time
from typing import Optional

import zmq

from homebot.common.interfaces.msg import SourcePriority, Command
from homebot.common.interfaces.srv import (
    Request,
    Response,
    VelocitySrv,
    NavigationSrv,
    JointAnglesSrv,
    EEDeltaSrv,
)

__all__ = ["ChassisClient", "ArmClient", "HeadClient"]


class MotionClient:
    """REQ client with strict send->recv recovery on timeout."""

    def __init__(self, service_addr: str, timeout_ms: int = 1000):
        self.service_addr = service_addr
        self.timeout_ms = timeout_ms
        self._context = zmq.Context.instance()
        self._socket = self._new_socket()

    def _new_socket(self):
        sock = self._context.socket(zmq.REQ)
        sock.setsockopt(zmq.RCVTIMEO, self.timeout_ms)
        sock.setsockopt(zmq.LINGER, 0)
        sock.connect(self.service_addr)
        return sock

    def _send_request(self, payload: dict, *, timeout_ms: Optional[int] = None) -> Optional[dict]:
        sock = self._socket
        try:
            if timeout_ms is not None:
                sock.setsockopt(zmq.RCVTIMEO, timeout_ms)
            sock.send_json(payload)
            return sock.recv_json()
        except Exception:
            try:
                sock.close()
            except Exception:
                pass
            self._socket = self._new_socket()  # REQ state machine broke; recreate
            return None
        finally:
            if timeout_ms is not None and self._socket is sock:
                sock.setsockopt(zmq.RCVTIMEO, self.timeout_ms)

    def close(self):
        if self._socket is not None:
            try:
                self._socket.close()
            except Exception:
                pass
            self._socket = None


class ChassisClient(MotionClient):

    def __init__(self, service_addr: str = "tcp://127.0.0.1:5556", timeout_ms: int = 1000):
        super().__init__(service_addr, timeout_ms)

    def send_velocity(
        self, vx: float = 0.0, vy: float = 0.0, vtheta: float = 0.0, *,
        duration: Optional[float] = None,
        source: SourcePriority = SourcePriority.WEB,
        priority: Optional[int] = None,
    ) -> Optional[Response]:
        req = VelocitySrv.request()
        req.source, req.priority, req.timestamp_s = source, priority, time.time()
        req.data.linear["x"], req.data.linear["y"] = vx, vy
        req.data.angular["z"] = vtheta
        req.data.duration_s = duration
        d = self._send_request(VelocitySrv.encode_request(req))
        return VelocitySrv.decode_response(d) if d else None

    def send_navigation(
        self, *, distance: float = 0.0, speed: float = 0.0,
        angle: float = 0.0, angle_speed: float = 0.0,
        source: SourcePriority = SourcePriority.WEB,
        priority: Optional[int] = None,
    ) -> Optional[Response]:
        req = NavigationSrv.request()
        req.source, req.priority, req.timestamp_s = source, priority, time.time()
        req.data.linear["value"], req.data.linear["speed"] = distance, speed
        req.data.angular["value"], req.data.angular["speed"] = angle, angle_speed
        d = self._send_request(NavigationSrv.encode_request(req))
        return NavigationSrv.decode_response(d) if d else None

    def stop(self, source: SourcePriority = SourcePriority.WEB) -> Optional[Response]:
        return self._send_cmd(Command.STOP, source)

    def unlock(self, source: SourcePriority = SourcePriority.EMERGENCY) -> Optional[Response]:
        return self._send_cmd(Command.UNLOCK, source)

    def _send_cmd(self, command: Command, source: SourcePriority) -> Optional[Response]:
        req = VelocitySrv.request()
        req.source, req.command, req.timestamp_s = source, command, time.time()
        d = self._send_request(VelocitySrv.encode_request(req))
        return VelocitySrv.decode_response(d) if d else None


class ArmClient(MotionClient):

    HOME_TIMEOUT_MS = 15000

    def __init__(self, service_addr: str = "tcp://127.0.0.1:5557", timeout_ms: int = 1000):
        super().__init__(service_addr, timeout_ms)

    def send_joints(
        self, joints: dict, *,
        source: SourcePriority = SourcePriority.WEB, priority: Optional[int] = None,
    ) -> Optional[Response]:
        req = JointAnglesSrv.request()
        req.source, req.priority, req.timestamp_s = source, priority, time.time()
        req.data.joint_angles = dict(joints)
        d = self._send_request(JointAnglesSrv.encode_request(req))
        return JointAnglesSrv.decode_response(d) if d else None

    def send_ee_delta(
        self, *, x: float = 0.0, y: float = 0.0, z: float = 0.0,
        rx: float = 0.0, ry: float = 0.0, rz: float = 0.0,
        gripper: Optional[float] = None,
        source: SourcePriority = SourcePriority.WEB, priority: Optional[int] = None,
    ) -> Optional[Response]:
        req = EEDeltaSrv.request()
        req.source, req.priority, req.timestamp_s = source, priority, time.time()
        req.data.ee_delta = {"x": x, "y": y, "z": z, "rx": rx, "ry": ry, "rz": rz}
        if gripper is not None:
            req.data.extra = {"gripper": gripper}
        d = self._send_request(EEDeltaSrv.encode_request(req))
        return EEDeltaSrv.decode_response(d) if d else None

    def home(self, *, source: SourcePriority = SourcePriority.WEB, priority: Optional[int] = None) -> Optional[Response]:
        req = JointAnglesSrv.request()
        req.source, req.priority, req.command, req.timestamp_s = source, priority, Command.HOME, time.time()
        d = self._send_request(JointAnglesSrv.encode_request(req), timeout_ms=self.HOME_TIMEOUT_MS)
        return JointAnglesSrv.decode_response(d) if d else None

    def query(self, *, source: SourcePriority = SourcePriority.WEB) -> Optional[Response]:
        req = JointAnglesSrv.request()
        req.source, req.command, req.timestamp_s = source, Command.QUERY, time.time()
        d = self._send_request(JointAnglesSrv.encode_request(req))
        return JointAnglesSrv.decode_response(d) if d else None


class HeadClient(MotionClient):

    HOME_TIMEOUT_MS = 15000

    def __init__(self, service_addr: str = "tcp://127.0.0.1:5558", timeout_ms: int = 1000):
        super().__init__(service_addr, timeout_ms)

    def send_joints(
        self, joints: dict, *,
        source: SourcePriority = SourcePriority.WEB, priority: Optional[int] = None,
    ) -> Optional[Response]:
        req = JointAnglesSrv.request()
        req.source, req.priority, req.timestamp_s = source, priority, time.time()
        req.data.joint_angles = dict(joints)
        d = self._send_request(JointAnglesSrv.encode_request(req))
        return JointAnglesSrv.decode_response(d) if d else None

    def home(self, *, source: SourcePriority = SourcePriority.WEB, priority: Optional[int] = None) -> Optional[Response]:
        req = JointAnglesSrv.request()
        req.source, req.priority, req.command, req.timestamp_s = source, priority, Command.HOME, time.time()
        d = self._send_request(JointAnglesSrv.encode_request(req), timeout_ms=self.HOME_TIMEOUT_MS)
        return JointAnglesSrv.decode_response(d) if d else None

    def query(self, *, source: SourcePriority = SourcePriority.WEB) -> Optional[Response]:
        req = JointAnglesSrv.request()
        req.source, req.command, req.timestamp_s = source, Command.QUERY, time.time()
        d = self._send_request(JointAnglesSrv.encode_request(req))
        return JointAnglesSrv.decode_response(d) if d else None
