import time
from typing import Optional, Dict
from threading import Lock

import zmq

from homebot.hal.head import HeadDriver, make_head_motors_dict, load_head_calibration
from homebot.configs import get_config, HeadConfig
from homebot.common.interfaces.msg import Command, JointAngles
from homebot.common.interfaces.srv import Request, Response, JointAnglesSrv, decode_request
from homebot.utils.pretty_logging import get_logger, init_logging, make_callout_text

from .motor_bus_manager import MotorBusManager
from .arbiter import PriorityArbiter

console_level, logger = get_logger(__name__)


def _build_head_driver(config: HeadConfig) -> HeadDriver:
    manager = MotorBusManager()
    bus = manager.get_bus(config.port)
    if bus is None:
        motors = make_head_motors_dict(
            config.joint_motors,
            use_degrees=config.use_degrees,
            model=config.joint_motor_model,
        )
        calibration = load_head_calibration(config.calibration_path)
        if not manager.initialize_bus(config.port, config.baudrate, motors, calibration):
            raise RuntimeError(f"failed to init head bus: {config.port}")
        bus = manager.get_bus(config.port)

    return HeadDriver(
        bus,
        joint_motors=list(config.joint_motors),
        home_position=config.home_position,
        max_relative_target=config.max_relative_target,
        default_pid=config.default_pid,
        pid_gains=config.pid_gains,
        on_comm_error=lambda: manager.reconnect(config.port),
    )


class HeadService:
    """Head REP service: joint space only (no gripper, no IK)."""

    TIMEOUT_MS = 2000

    def __init__(self, config: Optional[HeadConfig] = None, *, rep_addr: Optional[str] = None):
        self.config = config or get_config().head
        self.rep_addr = rep_addr or self.config.service_addr

        self.head = _build_head_driver(self.config)
        self.arbiter = PriorityArbiter(self.TIMEOUT_MS)
        self._initial_pos: Optional[Dict[str, float]] = None

        self._lock = Lock()
        self._context: Optional[zmq.Context] = None
        self._socket: Optional[zmq.Socket] = None
        self._running = False

    def _states(self) -> Dict[str, float]:
        if self.head is None or not self.head.is_connected:
            return {}
        try:
            return self.head.read_joints(normalize=True)
        except Exception as e:
            logger.error("read joints failed: %s", e)
            return {}

    def _handle(self, req: Request) -> Response:
        if req.command is Command.QUERY:
            return self._resp(True, "query ok", JointAngles(joint_angles=self._states()))

        with self._lock:
            self.arbiter.check_timeout()

            if req.command is Command.STOP:
                self.arbiter.release()
                return self._resp(True, "released")

            if not self.arbiter.can_acquire(req):
                return self._resp(False, f"busy: owned by {self.arbiter.owner.value[0]}")

            self.arbiter.acquire(req)
            ok = self._execute(req)
            return self._resp(ok, "accepted" if ok else "execute failed")

    def _execute(self, req: Request) -> bool:
        if req.command is Command.HOME:
            return self._execute_home(req.source)

        angles = (req.data.joint_angles if req.data else None) or {}
        targets = {k: float(v) for k, v in angles.items() if k in self.head.joint_motors}
        unknown = [k for k in angles if k not in self.head.joint_motors]
        if unknown:
            logger.warning("ignoring unknown joints: %s", unknown)
        if not targets:
            return True
        try:
            self.head.write_joints(targets)
            logger.info("joints [%s] %s", req.source.value[0],
                        ", ".join(f"{k}={v:.1f}" for k, v in targets.items()))
            return True
        except Exception as e:
            logger.error("write joints failed: %s", e)
            return False

    def _execute_home(self, source) -> bool:
        try:
            self.head.move_to_home(duration=self.config.home_move_duration)
            logger.info("move_to_home [%s]", source.value[0])
            return True
        except Exception as e:
            logger.error("move_to_home failed: %s", e)
            return False

    def _resp(self, success: bool, message: str, data: Optional[JointAngles] = None) -> Response:
        return Response(
            success=success,
            message=message,
            curr_owner=self.arbiter.owner,
            curr_priority=self.arbiter.priority,
            timestamp_s=time.time(),
            data=data,
        )

    def start(self) -> None:
        logger.info("\n" + make_callout_text(
            "Head service",
            content="\n".join([
                f"port: {self.config.port}",
                f"joints: {list(self.config.joint_motors)}",
                f"rep: {self.rep_addr}",
            ]),
            icon="🤖",
        ))

        try:
            self.head.configure()
        except Exception as e:
            logger.error("head configure failed: %s", e)
            return

        try:
            self._initial_pos = self.head.read_joints(normalize=True)
        except Exception as e:
            logger.error("read initial pose failed: %s", e)
            self._initial_pos = None
        if self.config.home_position:
            try:
                self.head.move_to_home(duration=self.config.home_move_duration)
            except Exception as e:
                logger.error("move_to_home failed: %s", e)

        self._context = zmq.Context()
        self._socket = self._context.socket(zmq.REP)
        self._socket.setsockopt(zmq.LINGER, 0)
        self._socket.bind(self.rep_addr)

        self._running = True
        logger.info("head service ready at %s", self.rep_addr)

        try:
            while self._running:
                try:
                    raw = self._socket.recv_json(flags=zmq.NOBLOCK)
                except zmq.Again:
                    with self._lock:
                        self.arbiter.check_timeout()
                    time.sleep(0.001)
                    continue
                try:
                    resp = self._handle(decode_request(raw))
                except Exception as e:
                    logger.error("bad request: %s", e)
                    resp = self._resp(False, "bad request")
                self._socket.send_json(JointAnglesSrv.encode_response(resp))
        except KeyboardInterrupt:
            logger.info("head service stopping...")
        finally:
            self.stop()

    def stop(self) -> None:
        if getattr(self, "_stopped", False):
            return
        self._stopped = True
        self._running = False
        if self._initial_pos:
            try:
                self.head.move_to(self._initial_pos, duration=self.config.home_move_duration)
            except Exception as e:
                logger.error("return to initial pose failed: %s", e)
        try:
            self.head.close()
        except Exception as e:
            logger.error("close driver failed: %s", e)
        if self._socket:
            self._socket.close()
            self._socket = None
        if self._context:
            self._context.term()
            self._context = None
        logger.info("head service closed")


def main():
    import argparse

    parser = argparse.ArgumentParser(description="HomeBot head service")
    parser.add_argument("--addr", default=None, help="override REP address")
    args = parser.parse_args()

    init_logging(console_level)

    service = HeadService(rep_addr=args.addr)
    service.start()


if __name__ == "__main__":
    main()
