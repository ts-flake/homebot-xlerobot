from __future__ import annotations

import logging
from typing import Optional

import numpy as np
from scipy.spatial.transform import Rotation

from homebot.common.interfaces.msg import EEDelta

from .kinematics import RobotKinematics

logger = logging.getLogger(__name__)


class ArmEEProcessor:
    """EE delta -> joint targets. Holds the accumulated target EE pose.

    Usage:

        proc = ArmEEProcessor(kinematics=RobotKinematics(urdf_path, ...))
        obs = driver.read_joints()
        targets = proc.step(obs, EEDelta(ee_delta={"x": 0.001}))
        driver.write_joints(targets, safe=True)
    """

    def __init__(
        self,
        kinematics: RobotKinematics,
        *,
        joint_names: Optional[list[str]] = None,
        resync_threshold_deg: float = 10.0,
    ):
        self.kin = kinematics
        # Driver-side (semantic) joint names, aligned by position to kin.joint_names
        # (URDF names). Defaults to kin.joint_names when they already match.
        self.joint_names = list(joint_names) if joint_names is not None else list(kinematics.joint_names)
        if len(self.joint_names) != len(kinematics.joint_names):
            raise ValueError(
                f"joint_names ({len(self.joint_names)}) != kinematics.joint_names "
                f"({len(kinematics.joint_names)}) length mismatch"
            )
        self.resync_threshold_deg = float(resync_threshold_deg)
        self._target_ee: Optional[np.ndarray] = None
        self._q_prev: Optional[np.ndarray] = None

    def reset(self) -> None:
        self._target_ee = None
        self._q_prev = None

    def _extract_q(self, joint_obs_deg: dict[str, float]) -> np.ndarray:
        missing = [n for n in self.joint_names if n not in joint_obs_deg]
        if missing:
            raise ValueError(f"joint_obs missing required joints (joint_names): {missing}")
        return np.array([joint_obs_deg[n] for n in self.joint_names], dtype=float)

    def step(
        self,
        q_dict: dict[str, float],
        delta: EEDelta,
    ) -> dict[str, float]:
        """Convert an EE delta action into a joint action.

        Args:
            q_dict: joint angles, {joint_name: angle_deg}
            delta: EE delta; ee_delta translations x/y/z in m, rotations rx/ry/rz in rad.

        Returns:
            target joint action, excluding grippers. {joint_name: target_deg}
        """
        q_obs = self._extract_q(q_dict)

        # Resync: if the last IK command drifted too far from the observation, snap to obs.
        if (
            self.resync_threshold_deg > 0
            and self._q_prev is not None
            and np.max(np.abs(self._q_prev - q_obs)) > self.resync_threshold_deg
        ):
            max_delta = float(np.max(np.abs(self._q_prev - q_obs)))
            logger.debug(
                "[IK resync] max |q_prev - q_obs| = %.2f deg > %.2f; snapping to observation.",
                max_delta, self.resync_threshold_deg,
            )
            self._q_prev = q_obs.copy()
            self._target_ee = None

        # First call or after reset: init target pose from current FK.
        if self._target_ee is None:
            self._target_ee = np.asarray(
                self.kin.forward_kinematics(q_obs), dtype=float,
            ).copy()

        ee = delta.ee_delta or {}

        # Accumulate translation (world frame).
        self._target_ee[0, 3] += float(ee.get("x", 0.0))
        self._target_ee[1, 3] += float(ee.get("y", 0.0))
        self._target_ee[2, 3] += float(ee.get("z", 0.0))

        # Accumulate rotation (EE body frame, right-multiply); rx/ry/rz are a rad rotvec.
        rx, ry, rz = float(ee.get("rx", 0.0)), float(ee.get("ry", 0.0)), float(ee.get("rz", 0.0))
        if rx or ry or rz:
            dr = Rotation.from_rotvec([rx, ry, rz]).as_matrix()
            self._target_ee[:3, :3] = self._target_ee[:3, :3] @ dr

        # IK warm-start from the last target (or the observation).
        q_seed = self._q_prev if self._q_prev is not None else q_obs
        q_target = np.asarray(
            self.kin.inverse_kinematics(q_seed, self._target_ee), dtype=float,
        )

        self._q_prev = q_target.copy()
        return {name: float(q_target[i]) for i, name in enumerate(self.joint_names)}
