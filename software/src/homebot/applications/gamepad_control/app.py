import sys
import math
import time
import signal
import threading
from typing import Optional

from homebot.configs import get_config, GamepadConfig
from homebot.common.interfaces.msg import SourcePriority
from homebot.hal.gamepad import get_gamepad_states, print_decode_keymap
from homebot.services.motion_service.clients import ChassisClient, ArmClient, HeadClient
from homebot.utils.pretty_logging import get_logger

from .keymaps import ALL_KEYMAP

logger = get_logger(__name__)

SOURCE = SourcePriority.GAMEPAD


def _connect_addr(bind_addr: str) -> str:
    """Service bind address (tcp://*:port) -> client connect address."""
    return bind_addr.replace("*", "127.0.0.1")


def _make_gamepad(backend: str, gamepad_id: int):
    if backend == "xinput":
        from homebot.hal.gamepad import XInputDriver
        return XInputDriver(gamepad_id)
    from homebot.hal.gamepad import SDLDriver
    return SDLDriver(gamepad_id)


class GamepadControlApp:
    """One gamepad drives the whole xlerobot (dual arms + head + base).

    Per frame, key states become increments (dt * stepsize * factor) sent via the
    typed motion clients: arms as Cartesian EE deltas (IK on the service side),
    head as absolute joint targets, base as velocities. Head and gripper are
    absolute, so the app keeps local accumulated targets; arm EE is a true delta.
    """

    def __init__(self, config: Optional[GamepadConfig] = None):
        self.config = config or get_config().gamepad
        gcfg = get_config()

        self.gamepad = _make_gamepad(self.config.backend, self.config.gamepad_id)

        self._arm_names = []
        if self.config.enable_left_arm and "left" in gcfg.arms:
            self._arm_names.append("left")
        if self.config.enable_right_arm and "right" in gcfg.arms:
            self._arm_names.append("right")

        # Clients; connect addresses derived from each service's config.
        self.chassis_client = ChassisClient(_connect_addr(gcfg.chassis.service_addr))
        self.arm_clients = {
            name: ArmClient(_connect_addr(gcfg.arms[name].service_addr))
            for name in self._arm_names
        }
        self.head_client = (
            HeadClient(_connect_addr(gcfg.head.service_addr))
            if self.config.enable_head else None
        )

        # Local absolute targets: gripper [0,100], head joint angles (deg).
        self._gripper = {name: 50.0 for name in self._arm_names}
        self._head = dict(gcfg.head.home_position)
        self._arm_home = {name: dict(gcfg.arms[name].home_position) for name in self._arm_names}
        self._head_home = dict(gcfg.head.home_position)

        self._base_speed_idx = 0
        self._base_moving = False
        self._last_speed_up = 0.0

        self._prev_ts = None
        self._prev_zero = False  # rising-edge trigger for back_to_zero
        self._running = False

    # ── Lifecycle ─────────────────────────────────────────────────────

    def initialize(self) -> bool:
        logger.info("connecting gamepad (backend=%s, id=%d)...",
                    self.config.backend, self.config.gamepad_id)
        try:
            self.gamepad.connect()
        except Exception as e:
            logger.error("gamepad connect failed: %s", e)
            return False
        if not self.gamepad.is_connected():
            logger.error("no gamepad detected")
            return False

        print_decode_keymap(ALL_KEYMAP)
        self._sync_local_state()
        logger.info("gamepad control ready: arms=%s head=%s base=%s",
                    self._arm_names, bool(self.head_client), self.config.enable_base)
        return True

    def _sync_local_state(self):
        """Query current states to seed the local accumulated targets (gripper / head)."""
        for name, client in self.arm_clients.items():
            resp = client.query(source=SOURCE)
            if resp and resp.data and "gripper" in resp.data.joint_angles:
                self._gripper[name] = resp.data.joint_angles["gripper"]
        if self.head_client:
            resp = self.head_client.query(source=SOURCE)
            if resp and resp.data:
                for j in self._head:
                    if j in resp.data.joint_angles:
                        self._head[j] = resp.data.joint_angles[j]

    # ── Main loop ─────────────────────────────────────────────────────

    def run(self):
        if not self._running:
            self._running = True
        period = 1.0 / max(1, self.config.fps)
        logger.info("control loop @ %dHz (Ctrl+C to exit)", self.config.fps)
        try:
            while self._running:
                t0 = time.perf_counter()
                self._tick()
                dt_left = period - (time.perf_counter() - t0)
                if dt_left > 0:
                    time.sleep(dt_left)
        except KeyboardInterrupt:
            logger.info("interrupted, exiting...")
        finally:
            self.stop()

    def _tick(self):
        states = get_gamepad_states(self.gamepad, ALL_KEYMAP)

        if states.get("exit"):
            logger.info("exit requested")
            self._running = False
            return
        # back_to_zero: rising edge only; blocks until motion completes.
        zero = bool(states.get("back_to_zero"))
        if zero and not self._prev_zero:
            self._prev_zero = True
            self._back_to_zero()
            return
        self._prev_zero = zero

        now = time.time()
        dt = (1.0 / self.config.fps) if self._prev_ts is None else (now - self._prev_ts)
        self._prev_ts = now

        for name in self._arm_names:
            self._dispatch_arm(name, states, dt)
        if self.head_client:
            self._dispatch_head(states, dt)
        if self.config.enable_base:
            self._dispatch_base(states, now)

    # ── Per-subsystem dispatch ────────────────────────────────────────

    def _dispatch_arm(self, name, states, dt):
        prefix = f"{name}_arm"
        ss = self.config.stepsize
        sf = self.config.stepsize_factors["arm"]
        dpos = ss["pos"] * dt                      # m
        drot = math.radians(ss["ang"] * dt)        # interface EE rotations are rad
        ee = {"x": 0.0, "y": 0.0, "z": 0.0, "rx": 0.0, "ry": 0.0, "rz": 0.0}

        def axis(pos_act, neg_act, key, mag):
            if states.get(f"{prefix}.{pos_act}"):
                ee[key] = mag
            elif states.get(f"{prefix}.{neg_act}"):
                ee[key] = -mag

        axis("x+", "x-", "x", dpos * sf.get("x", 1))
        axis("y+", "y-", "y", dpos * sf.get("y", 1))
        axis("z+", "z-", "z", dpos * sf.get("z", 1))
        axis("roll+", "roll-", "rx", drot * sf.get("roll", 1))
        axis("pitch+", "pitch-", "ry", drot * sf.get("pitch", 1))
        axis("yaw+", "yaw-", "rz", drot * sf.get("yaw", 1))

        # Gripper accumulates an absolute target.
        dg = ss["gripper"] * dt
        grip_changed = False
        if states.get(f"{prefix}.gripper+"):
            self._gripper[name] = min(100.0, self._gripper[name] + dg)
            grip_changed = True
        elif states.get(f"{prefix}.gripper-"):
            self._gripper[name] = max(0.0, self._gripper[name] - dg)
            grip_changed = True

        if any(v != 0.0 for v in ee.values()) or grip_changed:
            self.arm_clients[name].send_ee_delta(
                **ee, gripper=self._gripper[name], source=SOURCE
            )

    def _dispatch_head(self, states, dt):
        ss = self.config.stepsize
        sf = self.config.stepsize_factors["head"]
        dpitch = ss["ang"] * dt * sf.get("pitch", 1)
        dyaw = ss["ang"] * dt * sf.get("yaw", 1)
        changed = False
        if states.get("head.pitch+"):
            self._head["head_pitch"] += dpitch; changed = True
        elif states.get("head.pitch-"):
            self._head["head_pitch"] -= dpitch; changed = True
        if states.get("head.yaw+"):
            self._head["head_yaw"] += dyaw; changed = True
        elif states.get("head.yaw-"):
            self._head["head_yaw"] -= dyaw; changed = True
        if changed:
            self.head_client.send_joints(dict(self._head), source=SOURCE)

    def _dispatch_base(self, states, now):
        # Speed level cycling (back key, 0.2s debounce).
        if states.get("base.speed_up") and (now - self._last_speed_up) > 0.2:
            self._base_speed_idx = (self._base_speed_idx + 1) % len(self.config.base_speed_levels)
            self._last_speed_up = now
            lvl = self.config.base_speed_levels[self._base_speed_idx]
            logger.info("base speed level -> %d (xy=%.2f m/s, theta=%.0f deg/s)",
                        self._base_speed_idx, lvl["xy"], lvl["theta"])

        lvl = self.config.base_speed_levels[self._base_speed_idx]
        xy = lvl["xy"]
        wtheta = math.radians(lvl["theta"])
        vx = vy = vtheta = 0.0
        if states.get("base.forward"):
            vx += xy
        if states.get("base.backward"):
            vx -= xy
        if states.get("base.left"):
            vy += xy
        if states.get("base.right"):
            vy -= xy
        if states.get("base.rotate_left"):
            vtheta += wtheta
        if states.get("base.rotate_right"):
            vtheta -= wtheta

        moving = (vx != 0.0 or vy != 0.0 or vtheta != 0.0)
        if moving:
            self.chassis_client.send_velocity(vx, vy, vtheta, source=SOURCE)
            self._base_moving = True
        elif self._base_moving:
            # Just stopped: send one zero velocity, then let the deadman release.
            self.chassis_client.send_velocity(0.0, 0.0, 0.0, source=SOURCE)
            self._base_moving = False

    # ── Reset ─────────────────────────────────────────────────────────

    def _back_to_zero(self):
        """Stop the base, then blocking smooth home on arms + head in parallel."""
        logger.info("homing (blocking)...")
        self.chassis_client.stop(source=SOURCE)
        self._base_moving = False

        # Each client has its own socket, so parallel blocking home calls are safe.
        threads = [
            threading.Thread(target=client.home, kwargs={"source": SOURCE})
            for client in self.arm_clients.values()
        ]
        if self.head_client:
            threads.append(threading.Thread(target=self.head_client.home,
                                            kwargs={"source": SOURCE}))
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # Sync local accumulated targets to home, else the next increment jumps.
        for name in self._arm_names:
            home = self._arm_home[name]
            if "gripper" in home:
                self._gripper[name] = home["gripper"]
        if self.head_client:
            self._head = dict(self._head_home)

        # The blocking home skewed dt; reset the baseline.
        self._prev_ts = None
        logger.info("homing done")

    # ── Shutdown ──────────────────────────────────────────────────────

    def stop(self):
        logger.info("stopping gamepad control...")
        try:
            self.chassis_client.stop(source=SOURCE)
        except Exception:
            pass
        for client in [self.chassis_client, self.head_client, *self.arm_clients.values()]:
            if client is not None:
                try:
                    client.close()
                except Exception:
                    pass
        try:
            self.gamepad.disconnect()
        except Exception:
            pass


def main():
    import argparse
    parser = argparse.ArgumentParser(description="HomeBot gamepad control")
    parser.add_argument("--backend", default=None, help="gamepad backend sdl/xinput (override)")
    parser.add_argument("--gamepad-id", type=int, default=None, help="gamepad device index")
    args = parser.parse_args()

    config = get_config().gamepad
    if args.backend:
        config.backend = args.backend
    if args.gamepad_id is not None:
        config.gamepad_id = args.gamepad_id

    app = GamepadControlApp(config)
    signal.signal(signal.SIGINT, lambda *_: setattr(app, "_running", False))

    if not app.initialize():
        sys.exit(1)
    app.run()


if __name__ == "__main__":
    main()
