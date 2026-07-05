from __future__ import annotations

import json
import logging
import os
from pprint import pformat
from typing import TYPE_CHECKING, Optional

import numpy as np

# Imported lazily inside the functions that build motor objects: a top-level
# import would pull in homebot.hal, whose package init imports the drivers that
# import this module, creating a circular import.
if TYPE_CHECKING:
    from homebot.hal.motors import Motor, MotorCalibration

logger = logging.getLogger(__name__)


def ensure_safe_goal_position(
    goal_present_pos: dict[str, tuple[float, float]],
    max_relative_target: float | dict[str, float],
) -> dict[str, float]:
    """Clamp each goal so |goal - present| stays within max_relative_target."""
    if isinstance(max_relative_target, (int, float)):
        diff_cap = dict.fromkeys(goal_present_pos, float(max_relative_target))
    elif isinstance(max_relative_target, dict):
        if set(goal_present_pos) != set(max_relative_target):
            raise ValueError("max_relative_target keys must match goal_present_pos keys.")
        diff_cap = max_relative_target
    else:
        raise TypeError(max_relative_target)

    safe_goal_positions: dict[str, float] = {}
    warnings_dict: dict[str, dict[str, float]] = {}
    for key, (goal_pos, present_pos) in goal_present_pos.items():
        diff = goal_pos - present_pos
        max_diff = diff_cap[key]
        safe_diff = max(-max_diff, min(diff, max_diff))
        safe_goal = present_pos + safe_diff
        safe_goal_positions[key] = safe_goal
        if abs(safe_goal - goal_pos) > 1e-4:
            warnings_dict[key] = {"original": goal_pos, "safe": safe_goal}

    if warnings_dict:
        logger.warning(
            "Relative goal position clamped:\n%s", pformat(warnings_dict, indent=4)
        )
    return safe_goal_positions


def clip(x: float, limit: float) -> float:
    """Clamp x to [-limit, limit]; return 0.0 when limit <= 0."""
    if limit <= 0:
        return 0.0
    return max(-limit, min(limit, x))


def make_motors_dict(
    motor_ids: dict[str, int],
    *,
    gripper_joint: Optional[str] = None,
    use_degrees: bool = True,
    model: str = "sts3215",
) -> dict[str, Motor]:
    """Build {name: Motor} from {name: id} for FeetechMotorsBus.

    gripper_joint uses RANGE_0_100; other motors use DEGREES or RANGE_M100_100
    per use_degrees. Pass gripper_joint=None for gripperless subsystems (head, wheels).
    """
    from homebot.hal.motors import Motor, MotorNormMode

    body_mode = MotorNormMode.DEGREES if use_degrees else MotorNormMode.RANGE_M100_100
    motors: dict[str, Motor] = {}
    for name, mid in motor_ids.items():
        norm = MotorNormMode.RANGE_0_100 if name == gripper_joint else body_mode
        motors[name] = Motor(id=mid, model=model, norm_mode=norm)
    return motors


def load_calibration(path: str) -> dict[str, MotorCalibration]:
    """Load a JSON calibration file; return {} if it does not exist."""
    from homebot.hal.motors import MotorCalibration

    if not os.path.exists(path):
        return {}
    with open(path, "r") as f:
        raw = json.load(f)
    return {name: MotorCalibration(**fields) for name, fields in raw.items()}


def save_calibration(cal: dict[str, MotorCalibration], path: str) -> None:
    """Serialize calibration to a JSON file, creating parent dirs as needed."""
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    serializable = {
        name: {
            "id": c.id,
            "drive_mode": c.drive_mode,
            "homing_offset": c.homing_offset,
            "range_min": c.range_min,
            "range_max": c.range_max,
        }
        for name, c in cal.items()
    }
    with open(path, "w") as f:
        json.dump(serializable, f, indent=2)


def make_traj_sampler(
    start: np.ndarray,
    end: np.ndarray,
    duration: float,
    *,
    mode: str = "min_jerk",
):
    """Return a sampler f(t) -> (pos, vel, acc) for the given trajectory mode."""
    if mode == "linear":
        return _linear_sampler(start, end, duration)
    if mode == "min_jerk":
        return _min_jerk_sampler(start, end, duration)
    raise ValueError(f"Unsupported trajectory mode: {mode}")


def _linear_sampler(start: np.ndarray, end: np.ndarray, duration: float):
    delta = end - start

    def _s(t: float):
        tau = np.clip(t / duration, 0.0, 1.0)
        return start + delta * tau, delta / duration, np.zeros_like(start)
    return _s


def _min_jerk_sampler(start: np.ndarray, end: np.ndarray, duration: float):
    delta = end - start

    def _s(t: float):
        tau = np.clip(t / duration, 0.0, 1.0)
        pos = start + delta * (10 * tau**3 - 15 * tau**4 + 6 * tau**5)
        vel = delta * (30 * tau**2 - 60 * tau**3 + 30 * tau**4) / duration
        acc = delta * (60 * tau - 180 * tau**2 + 120 * tau**3) / (duration**2)
        return pos, vel, acc
    return _s
