from __future__ import annotations

import logging
from typing import Optional

from homebot.utils.robot_utils import (
    load_calibration,
    make_motors_dict,
    save_calibration,
)

from ..joint_driver import JointDriver, PIDGains
from ..motors import Motor

logger = logging.getLogger(__name__)


class HeadDriver(JointDriver):
    """Joint-space head driver.

    Identical to ``JointDriver`` (POSITION mode + per-joint PID, read/write/move_to/
    calibrate); just a semantic class name. Joint naming / count come from ``joint_motors``.

    Usage:

        bus = FeetechMotorsBus(port=..., motors={...})
        bus.connect()
        head = HeadDriver(
            bus=bus,
            joint_motors=["head_yaw", "head_pitch"],
            home_position={"head_yaw": 0.0, "head_pitch": 0.0},
        )
        head.configure()
        head.write_joints({"head_yaw": 30.0})
        head.move_to({"head_pitch": -20.0}, duration=1.0)
    """


# ── Helpers ──

def make_head_motors_dict(
    joint_motors: dict[str, int],
    *,
    use_degrees: bool = True,
    model: str = "sts3215",
) -> dict[str, Motor]:
    """``{name: id}`` -> ``{name: Motor}``. Head has no gripper; all joints use the use_degrees mode."""
    return make_motors_dict(
        joint_motors, gripper_joint=None, use_degrees=use_degrees, model=model
    )


# Calibration JSON I/O is subsystem-agnostic; reuse the shared impls.
load_head_calibration = load_calibration
save_head_calibration = save_calibration

__all__ = [
    "HeadDriver",
    "PIDGains",
    "make_head_motors_dict",
    "load_head_calibration",
    "save_head_calibration",
]
