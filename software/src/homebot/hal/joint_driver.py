from __future__ import annotations

import logging
import time
from typing import Optional

import numpy as np

from homebot.common.interfaces.msg import PIDGains
from homebot.utils.robot_utils import (
    ensure_safe_goal_position,
    make_traj_sampler,
    save_calibration,
)

from .bus_recovery import BusIORetryMixin
from .motors import MotorCalibration
from .motors._utils import check_if_not_connected
from .motors.feetech import FeetechMotorsBus, OperatingMode

logger = logging.getLogger(__name__)


class JointDriver(BusIORetryMixin):
    """Shared joint-space driver for multi-joint subsystems (arm / head).

    One instance = a set of joints on one FeetechMotorsBus. Joint naming / DOF
    come entirely from ``joint_motors``. Position mode + PID only (no IK/FK);
    the driver does not open/close the serial port (managed by orchestration).
    Subclasses append config via the ``_configure_extra()`` hook.

    Usage:

        bus = FeetechMotorsBus(port=..., motors={...})
        bus.connect()
        head = JointDriver(bus, joint_motors=["head_yaw", "head_pitch"], home_position={...})
        head.configure()
        head.write_joints({"head_yaw": 30.0})
    """

    def __init__(
        self,
        bus: FeetechMotorsBus,
        joint_motors: list[str],
        *,
        home_position: Optional[dict[str, float]] = None,
        max_relative_target: Optional[float] = 10.0,
        default_pid: Optional[PIDGains] = None,
        pid_gains: Optional[dict[str, PIDGains]] = None,
        on_comm_error=None,
    ):
        if not joint_motors:
            raise ValueError("joint_motors must contain at least one entry.")
        missing = [m for m in joint_motors if m not in bus.motors]
        if missing:
            raise ValueError(
                f"Joint motors not present in bus.motors: {missing}. "
                f"bus has: {list(bus.motors)}"
            )

        pid_gains = pid_gains or {}
        unknown_pid = [m for m in pid_gains if m not in joint_motors]
        if unknown_pid:
            raise ValueError(f"pid_gains has joints not in joint_motors: {unknown_pid}")

        self.bus = bus
        self.joint_motors = list(joint_motors)
        self.home_position = dict(home_position) if home_position else {}
        self.max_relative_target = max_relative_target

        # default_pid applies to joints not overridden in pid_gains (per-joint).
        self.default_pid = default_pid or PIDGains()
        self.pid_gains = dict(pid_gains)

        # Called with no args on a comm failure; returns True if the bus recovered.
        self.on_comm_error = on_comm_error

    # ── Derived properties ────────────────────────────────────────────

    @property
    def is_connected(self) -> bool:
        return self.bus.is_connected

    def pid_for(self, joint: str) -> PIDGains:
        """Effective PID for a joint: per-joint override first, else default_pid."""
        return self.pid_gains.get(joint, self.default_pid)

    # ── Lifecycle ─────────────────────────────────────────────────────

    @check_if_not_connected
    def configure(self) -> None:
        """All joints to POSITION mode + per-joint PID. Subclasses extend via _configure_extra."""
        with self.bus.torque_disabled(self.joint_motors):
            self.bus.configure_motors()
            for name in self.joint_motors:
                gains = self.pid_for(name)
                self.bus.write("Operating_Mode", name, OperatingMode.POSITION.value)
                self.bus.write("P_Coefficient", name, gains.p)
                self.bus.write("I_Coefficient", name, gains.i)
                self.bus.write("D_Coefficient", name, gains.d)
            self._configure_extra()
        logger.info(
            "%s configured %d joints in POSITION mode.",
            type(self).__name__, len(self.joint_motors),
        )

    def _configure_extra(self) -> None:
        """Subclass hook: extra config inside configure's torque_disabled context. No-op by default."""

    @check_if_not_connected
    def enable_torque(self) -> None:
        self.bus.enable_torque(self.joint_motors)

    @check_if_not_connected
    def disable_torque(self) -> None:
        self.bus.disable_torque(self.joint_motors)

    def close(self) -> None:
        """Disable torque. Leaves the port open. Tolerates an already-disconnected bus."""
        if not self.is_connected:
            return
        try:
            self.disable_torque()
        except Exception as e:
            logger.warning("%s close: disable_torque failed: %s", type(self).__name__, e)

    # ── Read / write joints ───────────────────────────────────────────

    def _relative_caps(self, names: list[str]) -> Optional[dict[str, float]]:
        """Per-joint |goal - present| caps for safe writes; None disables clamping."""
        if self.max_relative_target is None:
            return None
        return dict.fromkeys(names, float(self.max_relative_target))

    # No @check_if_not_connected: _io_retry surfaces the bus-level guard so a
    # dropped bus can reconnect+retry instead of hard-failing here.
    def read_joints(self, normalize: bool = True) -> dict[str, float]:
        """Read current joint positions. normalize=True -> deg / RANGE_0_100; False -> raw ticks."""
        return self._io_retry(
            lambda: self.bus.sync_read("Present_Position", self.joint_motors, normalize=normalize)
        )

    # No @check_if_not_connected: see read_joints; _io_retry handles recovery.
    def write_joints(
        self,
        positions: dict[str, float],
        *,
        normalize: bool = True,
        safe: bool = True,
        num_retry: int = 0,
    ) -> dict[str, float]:
        """Write target joint positions.

        Args:
            positions: ``{joint_name: target}``; missing joints are left unchanged.
            normalize: True if inputs are deg / RANGE_0_100; False if raw ticks.
            safe: when True and ``max_relative_target`` is set, clamp each joint's relative move.
            num_retry: retries on comm failure.

        Returns:
            The actually-sent ``{joint_name: value}`` (possibly safe-clamped).
        """
        unknown = [m for m in positions if m not in self.joint_motors]
        if unknown:
            raise ValueError(f"Unknown joints in write request: {unknown}")

        def _write():
            targets = dict(positions)
            caps = self._relative_caps(list(targets)) if safe and normalize else None
            if caps:
                present = self.bus.sync_read("Present_Position", list(targets), normalize=True)
                goal_present = {n: (targets[n], present[n]) for n in targets}
                targets = ensure_safe_goal_position(goal_present, caps)
            self.bus.sync_write("Goal_Position", targets, normalize=normalize, num_retry=num_retry)
            return targets

        return self._io_retry(_write)

    # ── Smooth trajectory ─────────────────────────────────────────────

    @check_if_not_connected
    def move_to(
        self,
        target_positions: dict[str, float],
        *,
        duration: float = 3.0,
        fps: int = 30,
        traj: str = "min_jerk",
    ) -> None:
        """Blocking smooth move to target joint positions.

        Args:
            target_positions: target ``{joint_name: value}`` (normalized units);
                              unspecified joints hold their current position.
            duration: total time (s).
            fps: control rate (Hz).
            traj: 'min_jerk' or 'linear'.
        """
        if duration <= 0:
            raise ValueError("duration must be > 0")

        unknown = [m for m in target_positions if m not in self.joint_motors]
        if unknown:
            raise ValueError(f"Unknown joints in target: {unknown}")

        current = self.read_joints(normalize=True)
        names = self.joint_motors
        start = np.array([current[n] for n in names], dtype=float)
        end = np.array(
            [float(target_positions.get(n, current[n])) for n in names],
            dtype=float,
        )

        sampler = make_traj_sampler(start, end, duration, mode=traj)
        dt = 1.0 / fps
        t = 0.0
        while t < duration:
            step_start = time.perf_counter()
            pos, _, _ = sampler(t)
            self.bus.sync_write(
                "Goal_Position",
                {n: float(v) for n, v in zip(names, pos)},
                normalize=True,
            )
            t += dt
            elapsed = time.perf_counter() - step_start
            if elapsed < dt:
                time.sleep(dt - elapsed)
        self.bus.sync_write("Goal_Position", dict(zip(names, end)), normalize=True)

    @check_if_not_connected
    def move_to_home(self, *, duration: float = 3.0, fps: int = 30, traj: str = "min_jerk") -> None:
        """Move to ``home_position``. Raises if not configured."""
        if not self.home_position:
            raise RuntimeError("home_position not configured.")
        self.move_to(self.home_position, duration=duration, fps=fps, traj=traj)

    # ── Calibration ──────────────────────────────────────────────────

    @check_if_not_connected
    def calibrate(self, save_to: Optional[str] = None) -> dict[str, MotorCalibration]:
        """Interactive calibration: middle position + full range recording.

        Steps:
            1. User moves all joints to the middle; ENTER records half-turn homing offset.
            2. User sweeps all joints through their full range; ENTER to stop.
            3. Calibration written to servo EEPROM (drive_mode=0).
            4. Optionally saved to JSON.
        """
        self.bus.disable_torque(self.joint_motors)
        for name in self.joint_motors:
            self.bus.write("Operating_Mode", name, OperatingMode.POSITION.value)

        input(
            "Move \033[32mall\033[0m joints to the \033[1mmiddle\033[0m of their "
            "range of motion and press ENTER..."
        )
        homing_offsets = self.bus.set_half_turn_homings(self.joint_motors)

        print(
            "Move all joints sequentially through their entire ranges "
            "of motion.\nRecording positions. Press ENTER to stop..."
        )
        range_mins, range_maxes = self.bus.record_ranges_of_motion(self.joint_motors)

        cal: dict[str, MotorCalibration] = {}
        for name in self.joint_motors:
            motor = self.bus.motors[name]
            cal[name] = MotorCalibration(
                id=motor.id,
                drive_mode=0,
                homing_offset=int(homing_offsets[name]),
                range_min=int(range_mins[name]),
                range_max=int(range_maxes[name]),
            )
        self.bus.write_calibration(cal)
        logger.info("%s calibration written for %d joints.", type(self).__name__, len(cal))

        if save_to is not None:
            save_calibration(cal, save_to)
            logger.info("Calibration saved to %s", save_to)
        return cal

    def setup_motors(self) -> None:
        """Interactively assign a motor ID to each joint. One-time during assembly."""
        for motor in reversed(self.joint_motors):
            input(f"Connect the controller board to the '{motor}' motor only and press enter.")
            self.bus.setup_motor(motor)
            print(f"'{motor}' motor id set to {self.bus.motors[motor].id}")
