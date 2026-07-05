from __future__ import annotations

import logging
from typing import Optional

from homebot.utils.robot_utils import (
    ensure_safe_goal_position,
    load_calibration,
    make_motors_dict,
    save_calibration,
)

from ..joint_driver import JointDriver, PIDGains
from ..motors import Motor
from ..motors.feetech import FeetechMotorsBus

logger = logging.getLogger(__name__)


class ArmDriver(JointDriver):
    """Joint-space arm driver (= JointDriver + gripper).

    Usage:

        bus = FeetechMotorsBus(port=..., motors={...})
        bus.connect()
        arm = ArmDriver(
            bus=bus,
            joint_motors=["shoulder_pan", "shoulder_lift", "elbow_flex",
                          "wrist_flex", "wrist_yaw", "wrist_roll", "gripper"],
            gripper_joint="gripper",
            home_position={...},
        )
        arm.configure()
        arm.write_joints({"shoulder_pan": 30.0, ...})
        arm.move_to(target_positions, duration=2.0)
    """

    def __init__(
        self,
        bus: FeetechMotorsBus,
        joint_motors: list[str],
        *,
        gripper_joint: Optional[str] = None,
        home_position: Optional[dict[str, float]] = None,
        max_relative_target: Optional[float] = 10.0,
        default_pid: Optional[PIDGains] = None,
        pid_gains: Optional[dict[str, PIDGains]] = None,
        gripper_max_torque_limit: int = 500,
        gripper_protection_current: int = 250,
        gripper_overload_torque: int = 25,
    ):
        super().__init__(
            bus,
            joint_motors,
            home_position=home_position,
            max_relative_target=max_relative_target,
            default_pid=default_pid,
            pid_gains=pid_gains,
        )
        if gripper_joint is not None and gripper_joint not in joint_motors:
            raise ValueError(f"gripper_joint '{gripper_joint}' not in joint_motors {joint_motors}")

        self.gripper_joint = gripper_joint
        self.gripper_max_torque_limit = gripper_max_torque_limit
        self.gripper_protection_current = gripper_protection_current
        self.gripper_overload_torque = gripper_overload_torque

    @property
    def arm_motors(self) -> list[str]:
        """Non-gripper joints (keeps joint_motors order). IK solves only these."""
        if self.gripper_joint is None:
            return list(self.joint_motors)
        return [m for m in self.joint_motors if m != self.gripper_joint]

    def _configure_extra(self) -> None:
        """Gripper overload protection (runs inside configure's torque_disabled context)."""
        if self.gripper_joint is not None:
            g = self.gripper_joint
            self.bus.write("Max_Torque_Limit", g, self.gripper_max_torque_limit)
            self.bus.write("Protection_Current", g, self.gripper_protection_current)
            self.bus.write("Overload_Torque", g, self.gripper_overload_torque)


# ── Helpers (thin wrappers over robot_utils; keep hal.arm import paths stable) ──

def make_arm_motors_dict(
    joint_motors: dict[str, int],
    *,
    gripper_joint: Optional[str] = None,
    use_degrees: bool = True,
    model: str = "sts3215",
) -> dict[str, Motor]:
    """``{name: id}`` -> ``{name: Motor}``. gripper_joint uses RANGE_0_100, others per use_degrees."""
    return make_motors_dict(
        joint_motors, gripper_joint=gripper_joint, use_degrees=use_degrees, model=model
    )


# Calibration JSON I/O is subsystem-agnostic; reuse the shared impls.
load_arm_calibration = load_calibration
save_arm_calibration = save_calibration

__all__ = [
    "ArmDriver",
    "PIDGains",
    "ensure_safe_goal_position",
    "make_arm_motors_dict",
    "load_arm_calibration",
    "save_arm_calibration",
]
