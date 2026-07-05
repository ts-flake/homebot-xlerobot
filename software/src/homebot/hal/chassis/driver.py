from __future__ import annotations

import logging
import math
import time
from typing import Optional

from homebot.utils.robot_utils import (
    clip,
    load_calibration,
    make_motors_dict,
    save_calibration,
)

from ..bus_recovery import BusIORetryMixin
from ..motors import Motor, MotorCalibration
from ..motors._utils import check_if_not_connected
from ..motors.feetech import FeetechMotorsBus, OperatingMode
from .kinematics import BaseChassisKinematics

logger = logging.getLogger(__name__)


class ChassisDriver(BusIORetryMixin):
    """Chassis driver class, allowing a shared motor bus and various chassis kinematics.

    The driver is responsible of wheel motor configure / enable or disable torque / calibrate.
    And read / write chassis velocity.

    NOTE: Motor bus connection is handed over to a bus manager.
    """

    def __init__(
        self,
        bus: FeetechMotorsBus,
        kinematics: BaseChassisKinematics,
        *,
        max_linear_speed: float = 0.2, # m/s
        max_angular_speed: float = math.pi / 2, # rad/s
        on_comm_error=None,
    ):
        # For a shared bus, only a subset of motors is for chassis motion.
        # Take the motor names from chassis kinematics instance.
        self.wheel_motors = list(kinematics.wheel_motors)

        for m in self.wheel_motors:
            if m not in bus.motors:
                raise ValueError(f"Motor '{m}' from kinematics not present in bus.motors {list(bus.motors)}")

        self.bus = bus
        self.kin = kinematics
        self.max_linear_speed = max_linear_speed
        self.max_angular_speed = max_angular_speed

        self._last_vx = 0.0
        self._last_vy = 0.0
        self._last_vtheta = 0.0

        # Called with no args on a comm failure; returns True if the bus recovered.
        self.on_comm_error = on_comm_error

    @property
    def is_connected(self) -> bool:
        return self.bus.is_connected

    @check_if_not_connected
    def configure(self) -> None:
        with self.bus.torque_disabled(self.wheel_motors):
            self.bus.configure_motors()
            for name in self.wheel_motors:
                self.bus.write("Operating_Mode", name, OperatingMode.VELOCITY.value)
        logger.info("ChassisDriver configured %d wheels in VELOCITY mode.", len(self.wheel_motors))

    @check_if_not_connected
    def stop(self) -> None:
        """Stop motion, set velocity to zero."""
        self._last_vx = 0.0
        self._last_vy = 0.0
        self._last_vtheta = 0.0
        self.bus.sync_write(
            "Goal_Velocity",
            dict.fromkeys(self.wheel_motors, 0),
            normalize=False,
            num_retry=3,
        )

    def close(self) -> None:
        """Stop and disable torque."""
        if not self.is_connected:
            return
        try:
            self.stop()
            time.sleep(0.05)
        finally:
            self.bus.disable_torque(self.wheel_motors)

    # ── Control API ───────────────────────────────────────────────────

    # No @check_if_not_connected: _io_retry surfaces the bus-level guard so a
    # dropped bus can reconnect+retry instead of hard-failing here.
    def set_velocity(self, vx: float, vy: float, vtheta: float) -> None:
        """Set chassis velocity in m/s and rad/s. Velocity clipping applies."""
        vx = clip(vx, self.max_linear_speed)
        vy = clip(vy, self.max_linear_speed)
        vtheta = clip(vtheta, self.max_angular_speed)
        self._last_vx, self._last_vy, self._last_vtheta = vx, vy, vtheta

        wheel_radps = self.kin.inverse({
            'vx': vx,
            'vy': vy,
            'vtheta': vtheta
        })

        # rad/s to raw ticks
        raw_by_motor = {m: self._radps_to_raw(m, radps) for m,radps in wheel_radps.items()}

        self._io_retry(lambda: self.bus.sync_write("Goal_Velocity", raw_by_motor, normalize=False))

    def get_velocity(self) -> tuple[float]:
        """Read chassis velocity, (vx, vy, vtheta)."""
        raw = self._io_retry(
            lambda: self.bus.sync_read("Present_Velocity", self.wheel_motors, normalize=False)
        )
        wheel_radps = {m: self._raw_to_radps(m, raw[m]) for m in self.wheel_motors}
        base_vel = self.kin.forward(wheel_radps)
        return (base_vel['vx'], base_vel['vy'], base_vel['vtheta'])

    def get_last_velocity(self) -> tuple[float]:
        """Get the last (vx, vy, vtheta)."""
        return self._last_vx, self._last_vy, self._last_vtheta

    # ── Calibration ────────────────────────────────────────────────

    @check_if_not_connected
    def calibrate(self, save_to: Optional[str] = None) -> dict[str, MotorCalibration]:
        """Wheel calibration all set [0, 4095] range + drive_mode=0.

        NOTE: If a motor is installed in the opposite driving direction,
        modify MotorCalibration.drive_mode=1.
        """
        cal: dict[str, MotorCalibration] = {}
        for name in self.wheel_motors:
            motor = self.bus.motors[name]
            cal[name] = MotorCalibration(
                id=motor.id,
                drive_mode=0,
                homing_offset=0,
                range_min=0,
                range_max=self.bus.model_resolution_table[motor.model] - 1,
            )
        self.bus.write_calibration(cal)
        logger.info("Wheel calibration written for %d motors.", len(cal))

        if save_to is not None:
            save_chassis_calibration(cal, save_to)
            logger.info("Wheel calibration saved to %s", save_to)
        return cal

    # ── Helpers ─────────────────────────────────────

    def _radps_to_raw(self, motor_name: str, radps: float) -> int:
        model = self.bus.motors[motor_name].model
        resolution = self.bus.model_resolution_table[model]
        raw = int(round(radps * resolution / (2.0 * math.pi)))
        return raw

    def _raw_to_radps(self, motor_name: str, raw: int) -> float:
        model = self.bus.motors[motor_name].model
        resolution = self.bus.model_resolution_table[model]
        return raw * (2.0 * math.pi) / resolution


# Chassis-named wrappers over the shared robot_utils helpers, kept so the
# hal.chassis import paths stay unchanged.

load_chassis_calibration = load_calibration
save_chassis_calibration = save_calibration


def make_wheel_motors_dict(
    wheel_ids: dict[str, int],
    model: str = "sts3215",
) -> dict[str, Motor]:
    """Build {name: Motor} for wheels: no gripper, all RANGE_M100_100."""
    return make_motors_dict(wheel_ids, gripper_joint=None, use_degrees=False, model=model)
