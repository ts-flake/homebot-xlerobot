import time
from typing import Optional, Dict
from threading import Lock

import zmq

from homebot.hal.arm import ArmDriver, make_arm_motors_dict, load_arm_calibration
from homebot.configs import get_config, ArmConfig
from homebot.common.interfaces.msg import Command, JointAngles
from homebot.common.interfaces.srv import Request, Response, JointAnglesSrv, decode_request
from homebot.utils.pretty_logging import get_logger, init_logging, make_callout_text

from .motor_bus_manager import MotorBusManager
from .arbiter import PriorityArbiter

logger = get_logger(__name__)


def _build_arm_driver(config: ArmConfig) -> ArmDriver:
    manager = MotorBusManager()
    bus = manager.get_bus(config.port)
    if bus is None:
        motors = make_arm_motors_dict(
            config.joint_motors,
            gripper_joint=config.gripper_joint,
            use_degrees=config.use_degrees,
            model=config.joint_motor_model,
        )
        calibration = load_arm_calibration(config.calibration_path)
        if not manager.initialize_bus(config.port, config.baudrate, motors, calibration):
            raise RuntimeError(f"failed to init arm bus: {config.port}")
        bus = manager.get_bus(config.port)

    return ArmDriver(
        bus,
        joint_motors=list(config.joint_motors),
        gripper_joint=config.gripper_joint,
        home_position=config.home_position,
        max_relative_target=config.max_relative_target,
        default_pid=config.default_pid,
        pid_gains=config.pid_gains,
        gripper_max_torque_limit=config.gripper_max_torque_limit,
        gripper_protection_current=config.gripper_protection_current,
        gripper_overload_torque=config.gripper_overload_torque,
        on_comm_error=lambda: manager.reconnect(config.port),
    )


class ArmService:
    """Arm REP service: JointAngles (joint space) or EEDelta (Cartesian IK)."""

    TIMEOUT_MS = 2000

    def __init__(
        self,
        config: Optional[ArmConfig] = None,
        *,
        arm_name: str = "left",
        rep_addr: Optional[str] = None,
    ):
        self.arm_name = arm_name
        self.config = config or get_config().arms[arm_name]
        self.rep_addr = rep_addr or self.config.service_addr

        self.arm = _build_arm_driver(self.config)
        self.arbiter = PriorityArbiter(self.TIMEOUT_MS)
        self._ee_processor = None
        self._initial_pos: Optional[Dict[str, float]] = None

        self._lock = Lock()
        self._context: Optional[zmq.Context] = None
        self._socket: Optional[zmq.Socket] = None
        self._running = False

    def _states(self) -> Dict[str, float]:
        if self.arm is None or not self.arm.is_connected:
            return {}
        try:
            return self.arm.read_joints(normalize=True)
        except Exception as e:
            logger.error("read joints failed: %s", e)
            return {}

    def _get_ee_processor(self):
        if self._ee_processor is not None:
            return self._ee_processor
        try:
            from homebot.hal.arm.kinematics import RobotKinematics
            from homebot.hal.arm.ee_processor import ArmEEProcessor
            ik_joint_names = [self.config.ik_joint_map[n] for n in self.arm.arm_motors]
            kin = RobotKinematics(
                urdf_path=self.config.urdf_path,
                target_frame_name=self.config.ee_frame_name,
                joint_names=ik_joint_names,
            )
            self._ee_processor = ArmEEProcessor(kin, joint_names=self.arm.arm_motors)
            logger.info("ee processor ready (IK joints %s -> %s)", self.arm.arm_motors, ik_joint_names)
        except Exception as e:
            logger.error("ee processor init failed (placo/urdf?): %s", e)
            return None
        return self._ee_processor

    def _warmup_ee_processor(self) -> None:
        """Eager-build the EE processor and run one zero-delta IK solve at startup.

        Placo import, URDF load and the first solver.solve() are all slow; doing
        them here (not on the first ee_delta) avoids a latency spike that would
        queue commands and cause an abrupt catch-up motion.
        """
        proc = self._get_ee_processor()
        if proc is None:
            return
        try:
            from homebot.common.interfaces.msg import EEDelta
            q = self.arm.read_joints(normalize=True)
            proc.step(q, EEDelta())  # FK + IK once to build placo internals
            proc.reset()             # clear warmup state; first real cmd re-inits from FK
            logger.info("ee processor warmed up")
        except Exception as e:
            logger.error("ee processor warmup failed: %s", e)

    def _handle(self, req: Request, kind: str) -> Response:
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
            ok = self._execute(req, kind)
            return self._resp(ok, "accepted" if ok else "execute failed")

    def _execute(self, req: Request, kind: str) -> bool:
        if req.command is Command.HOME:
            return self._execute_home(req.source)
        if kind == "ee_delta":
            return self._execute_ee_delta(req)
        return self._execute_joints(req)

    def _execute_joints(self, req: Request) -> bool:
        angles = (req.data.joint_angles if req.data else None) or {}
        targets = {k: float(v) for k, v in angles.items() if k in self.arm.joint_motors}
        unknown = [k for k in angles if k not in self.arm.joint_motors]
        if unknown:
            logger.warning("ignoring unknown joints: %s", unknown)
        if not targets:
            return True
        try:
            self.arm.write_joints(targets)
            logger.info("joints [%s] %s", req.source.value[0],
                        ", ".join(f"{k}={v:.1f}" for k, v in targets.items()))
            return True
        except Exception as e:
            logger.error("write joints failed: %s", e)
            return False

    def _execute_ee_delta(self, req: Request) -> bool:
        proc = self._get_ee_processor()
        if proc is None:
            return False
        d = req.data  # msg.EEDelta: translations in m, rotations in rad
        try:
            q = self.arm.read_joints(normalize=True)
            targets = proc.step(q, d)
            gripper = (d.extra or {}).get("gripper")
            if gripper is not None and self.arm.gripper_joint is not None:
                targets[self.arm.gripper_joint] = float(gripper)
            self.arm.write_joints(targets)
            ee = d.ee_delta or {}
            logger.info("ee_delta [%s] x=%+.3f y=%+.3f z=%+.3f",
                        req.source.value[0], ee.get("x", 0.0), ee.get("y", 0.0), ee.get("z", 0.0))
            return True
        except Exception as e:
            logger.error("ee_delta failed: %s", e)
            return False

    def _execute_home(self, source) -> bool:
        try:
            self.arm.move_to_home(duration=self.config.home_move_duration)
            if self._ee_processor is not None:
                self._ee_processor.reset()
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
        init_logging()
        logger.info("\n" + make_callout_text(
            f"Arm service [{self.arm_name}]",
            content="\n".join([
                f"port: {self.config.port}",
                f"joints: {list(self.config.joint_motors)}",
                f"rep: {self.rep_addr}",
            ]),
            icon="🦾",
        ))

        try:
            self.arm.configure()
        except Exception as e:
            logger.error("arm configure failed: %s", e)
            return

        try:
            self._initial_pos = self.arm.read_joints(normalize=True)
        except Exception as e:
            logger.error("read initial pose failed: %s", e)
            self._initial_pos = None
        if self.config.home_position:
            try:
                self.arm.move_to_home(duration=self.config.home_move_duration)
            except Exception as e:
                logger.error("move_to_home failed: %s", e)

        # Eager IK init + warmup so the first ee_delta command isn't slow.
        self._warmup_ee_processor()

        self._context = zmq.Context()
        self._socket = self._context.socket(zmq.REP)
        self._socket.setsockopt(zmq.LINGER, 0)
        self._socket.bind(self.rep_addr)

        self._running = True
        logger.info("arm service [%s] ready at %s", self.arm_name, self.rep_addr)

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
                self._socket.send_json(JointAnglesSrv.encode_response(resp))
        except KeyboardInterrupt:
            logger.info("arm service stopping...")
        finally:
            self.stop()

    def stop(self) -> None:
        if getattr(self, "_stopped", False):
            return
        self._stopped = True
        self._running = False
        if self._initial_pos:
            try:
                self.arm.move_to(self._initial_pos, duration=self.config.home_move_duration)
            except Exception as e:
                logger.error("return to initial pose failed: %s", e)
        try:
            self.arm.close()
        except Exception as e:
            logger.error("close driver failed: %s", e)
        if self._socket:
            self._socket.close()
            self._socket = None
        if self._context:
            self._context.term()
            self._context = None
        logger.info("arm service [%s] closed", self.arm_name)


def main():
    import argparse

    parser = argparse.ArgumentParser(description="HomeBot arm service")
    parser.add_argument("--arm", default="left", help="arm name (config.arms key)")
    parser.add_argument("--addr", default=None, help="override REP address")
    args = parser.parse_args()

    service = ArmService(arm_name=args.arm, rep_addr=args.addr)
    service.start()


if __name__ == "__main__":
    main()
