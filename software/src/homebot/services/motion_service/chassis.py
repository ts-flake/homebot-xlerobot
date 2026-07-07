import math
import time
from typing import Optional
from threading import Lock

import zmq

from homebot.hal.chassis import (
    ChassisDriver,
    make_chassis_kinematics,
    make_wheel_motors_dict,
    load_chassis_calibration,
)
from homebot.configs import get_config, ChassisConfig
from homebot.common.interfaces.msg import SourcePriority, Command, Velocity, NavigationVelocity
from homebot.common.interfaces.srv import Request, Response, VelocitySrv, decode_request
from homebot.utils.pretty_logging import get_logger, init_logging, make_callout_text

from .motor_bus_manager import MotorBusManager
from .battery import BatteryService
from .arbiter import PriorityArbiter

console_level, logger = get_logger(__name__)


def _build_chassis_driver(config: ChassisConfig) -> ChassisDriver:
    manager = MotorBusManager()
    bus = manager.get_bus(config.port)
    if bus is None:
        motors = make_wheel_motors_dict(config.wheel_motors, model=config.wheel_motor_model)
        calibration = load_chassis_calibration(config.calibration_path)
        if not manager.initialize_bus(config.port, config.baudrate, motors, calibration):
            raise RuntimeError(f"failed to init chassis bus: {config.port}")
        bus = manager.get_bus(config.port)

    return ChassisDriver(
        bus,
        kinematics=make_chassis_kinematics(config.kinematics),
        max_linear_speed=config.max_linear_speed,
        max_angular_speed=config.max_angular_speed,
        on_comm_error=lambda: manager.reconnect(config.port),
    )


class ChassisService:
    """Chassis REP service: Velocity (continuous) or NavigationVelocity (timed move)."""

    TIMEOUT_MS = 500  # deadman: continuous velocity must refresh within this

    def __init__(
        self,
        config: Optional[ChassisConfig] = None,
        *,
        rep_addr: Optional[str] = None,
        pub_addr: Optional[str] = None,
    ):
        self.config = config or get_config().chassis
        self.rep_addr = rep_addr or self.config.service_addr
        self.pub_addr = pub_addr or get_config().battery.pub_addr

        self.chassis = _build_chassis_driver(self.config)
        self.arbiter = PriorityArbiter(self.TIMEOUT_MS, self._stop_hw)
        self._emergency_locked = False

        self._lock = Lock()
        self._context: Optional[zmq.Context] = None
        self._socket: Optional[zmq.Socket] = None
        self._running = False

        self.battery_service = BatteryService(bus=self.chassis.bus, pub_addr=self.pub_addr)

    def _handle(self, req: Request, kind: str) -> Response:
        now = time.time()
        with self._lock:
            self.arbiter.check_timeout(now)

            if req.command is Command.UNLOCK:
                self._emergency_locked = False
                self.arbiter.release()
                return self._resp(True, "emergency released")

            if req.source is SourcePriority.EMERGENCY:
                self._emergency_locked = True
                self.arbiter.release()
                return self._resp(True, "emergency stop, chassis locked")

            if req.command is Command.STOP:
                self.arbiter.release()
                return self._resp(True, "stopped")

            if self._emergency_locked:
                return self._resp(False, "chassis emergency-locked; send UNLOCK")

            if req.command is Command.QUERY:
                return self._resp(True, "query ok")

            if not self.arbiter.can_acquire(req):
                return self._resp(False, f"busy: owned by {self.arbiter.owner.value[0]}")

            vx, vy, vtheta, duration = self._targets(req, kind)
            deadline = (now + duration) if duration else None
            self.arbiter.acquire(req, deadline=deadline)
            self._execute(vx, vy, vtheta, req.source)
            return self._resp(True, "accepted")

    def _targets(self, req: Request, kind: str):
        if kind == "navigation":
            return self._from_navigation(req.data)
        return self._from_velocity(req.data)

    def _from_velocity(self, v: Velocity):
        lin = v.linear or {}
        ang = v.angular or {}
        return float(lin.get("x", 0)), float(lin.get("y", 0)), float(ang.get("z", 0)), v.duration_s

    def _from_navigation(self, nav: NavigationVelocity):
        lin = nav.linear or {}
        ang = nav.angular or {}
        dist, lspeed = float(lin.get("value", 0)), abs(float(lin.get("speed", 0)))
        angle, aspeed = float(ang.get("value", 0)), abs(float(ang.get("speed", 0)))
        vx = vy = vtheta = 0.0
        duration = None
        if dist and lspeed > 0:
            vx = math.copysign(lspeed, dist)
            duration = abs(dist) / lspeed
            if lspeed > self.config.max_linear_speed:
                logger.warning("linear speed %.2f>%.2f m/s clipped; distance will fall short",
                               lspeed, self.config.max_linear_speed)
        elif angle and aspeed > 0:
            vtheta = math.copysign(aspeed, angle)
            duration = abs(angle) / aspeed
            if aspeed > self.config.max_angular_speed:
                logger.warning("angular speed %.2f>%.2f rad/s clipped; angle will fall short",
                               aspeed, self.config.max_angular_speed)
        return vx, vy, vtheta, duration

    def _execute(self, vx, vy, vtheta, source: SourcePriority) -> None:
        try:
            self.chassis.set_velocity(vx, vy, vtheta)
            logger.info("move [%s] vx=%+.2f vy=%+.2f vtheta=%+.2f", source.value[0], vx, vy, vtheta)
        except Exception as e:
            logger.error("execute failed: %s", e)

    def _stop_hw(self) -> None:
        try:
            self.chassis.stop()
        except Exception as e:
            logger.error("stop failed: %s", e)

    def _resp(self, success: bool, message: str) -> Response:
        return Response(
            success=success,
            message=message,
            curr_owner=self.arbiter.owner,
            curr_priority=self.arbiter.priority,
            timestamp_s=time.time(),
        )

    def start(self) -> None:
        logger.info("\n" + make_callout_text(
            "Chassis service",
            content="\n".join([
                f"port: {self.config.port}",
                f"rep: {self.rep_addr}",
                f"max linear: {self.config.max_linear_speed} m/s",
                f"max_angular: {self.config.max_angular_speed} rad/s"
            ]),
            icon="🛞",
        ))

        try:
            self.chassis.configure()
        except Exception as e:
            logger.error("chassis configure failed: %s", e)
            return

        self._context = zmq.Context()
        self._socket = self._context.socket(zmq.REP)
        self._socket.setsockopt(zmq.LINGER, 0)
        self._socket.bind(self.rep_addr)
        self.battery_service.start(self._context)

        self._running = True
        logger.info("chassis service ready at %s", self.rep_addr)

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
                    resp = self._handle(decode_request(raw), raw.get("kind"))
                except Exception as e:
                    logger.error("bad request: %s", e)
                    resp = self._resp(False, "bad request")
                self._socket.send_json(VelocitySrv.encode_response(resp))
        except KeyboardInterrupt:
            logger.info("chassis service stopping...")
        finally:
            self.stop()

    def stop(self) -> None:
        if getattr(self, "_stopped", False):
            return
        self._stopped = True
        self._running = False
        self.battery_service.stop()
        self._stop_hw()
        self.chassis.close()
        if self._socket:
            self._socket.close()
            self._socket = None
        if self._context:
            self._context.term()
            self._context = None
        logger.info("chassis service closed")


def main():
    import argparse

    parser = argparse.ArgumentParser(description="HomeBot chassis service")
    parser.add_argument("--addr", default=None, help="override REP address")
    parser.add_argument("--battery-addr", default=None, help="override battery PUB address")
    args = parser.parse_args()

    init_logging(console_level)

    service = ChassisService(rep_addr=args.addr, pub_addr=args.battery_addr)
    service.start()


if __name__ == "__main__":
    main()
