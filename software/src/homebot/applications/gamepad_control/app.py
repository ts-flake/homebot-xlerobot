"""
游戏手柄整机遥控 app —— 控制整台 xlerobot (双臂 + 头部 + 底盘).

架构 (对齐 lerobot xlerobot_yaw_gamepad, 但落到 homebot 的 service 架构):
- 读手柄: hal.gamepad SDLDriver + keymap 引擎 (get_gamepad_states) + keymaps.py 绑定表
- 每帧把按键状态换算成增量 (dt*stepsize*factor), 通过统一 send_command 客户端下发:
    * 双臂 → ArmArbiterClient(left/right).send_command(ee_delta=..., gripper=绝对值)
            IK 在 service 端 (ArmService 持 ArmEEProcessor)
    * 头部 → HeadArbiterClient.send_command(joints=绝对值)
    * 底盘 → ChassisArbiterClient.send_command(vx, vy, vtheta)
- 头部 / 夹爪是绝对量, 故 app 维护本地目标并累加增量; 仅手臂 Cartesian 是真增量 (service 累积)
"""
import os
import sys
import math
import time
import signal
import threading

# 添加项目根目录到路径
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))

from homebot.utils.pretty_logging import get_logger
from configs import get_config, GamepadConfig
from hal.gamepad import get_gamepad_states, print_decode_keymap
from software.src.homebot.services.motion_service.clients import (
    ChassisClient,
    ArmClient,
    HeadClient,
)
from .keymaps import ALL_KEYMAP

logger = get_logger(__name__)

SOURCE = "gamepad"


def _connect_addr(bind_addr: str) -> str:
    """service 的 bind 地址 (tcp://*:port) → client connect 地址."""
    return bind_addr.replace("*", "127.0.0.1")


def _make_gamepad(backend: str, gamepad_id: int):
    """按 backend 选手柄后端 (sdl=Linux/跨平台, xinput=Windows)."""
    if backend == "xinput":
        from hal.gamepad import XInputDriver
        return XInputDriver(gamepad_id)
    from hal.gamepad import SDLDriver
    return SDLDriver(gamepad_id)


class GamepadControlApp:
    """整机手柄遥控应用."""

    def __init__(self, config: GamepadConfig = None):
        self.config = config or get_config().gamepad
        gcfg = get_config()

        # 手柄
        self.gamepad = _make_gamepad(self.config.backend, self.config.gamepad_id)

        # 启用哪些臂 (config.arms 的 key; 默认 left/right 由 enable_* 开关控制)
        self._arm_names = []
        if self.config.enable_left_arm and "left" in gcfg.arms:
            self._arm_names.append("left")
        if self.config.enable_right_arm and "right" in gcfg.arms:
            self._arm_names.append("right")

        # 客户端 (connect 地址从各 service 配置推导)
        self.chassis_client = ChassisClient(_connect_addr(gcfg.chassis.service_addr))
        self.arm_clients = {
            name: ArmClient(_connect_addr(gcfg.arms[name].service_addr))
            for name in self._arm_names
        }
        self.head_client = (
            HeadClient(_connect_addr(gcfg.head.service_addr))
            if self.config.enable_head else None
        )

        # 本地目标状态 (绝对量): 夹爪 [0,100], 头部关节角 (deg)
        self._gripper = {name: 50.0 for name in self._arm_names}
        self._head = dict(gcfg.head.home_position)  # {head_yaw, head_pitch}
        self._arm_home = {name: dict(gcfg.arms[name].home_position) for name in self._arm_names}
        self._head_home = dict(gcfg.head.home_position)

        # 底盘速度档 / 运动状态
        self._base_speed_idx = 0
        self._base_moving = False
        self._last_speed_up = 0.0

        self._prev_ts = None
        self._prev_zero = False   # back_to_zero 上一帧状态 (上升沿触发, 一次按下一次回零)
        self._running = False

    # ── 生命周期 ──────────────────────────────────────────────────────

    def initialize(self) -> bool:
        logger.info("连接手柄 (backend=%s, id=%d)...", self.config.backend, self.config.gamepad_id)
        try:
            self.gamepad.connect()
        except Exception as e:
            logger.error("手柄连接失败: %s", e)
            return False
        if not self.gamepad.is_connected():
            logger.error("未检测到手柄")
            return False

        print_decode_keymap(ALL_KEYMAP)
        self._sync_local_state()
        logger.info("整机手柄遥控就绪: arms=%s head=%s base=%s",
                    self._arm_names, bool(self.head_client), self.config.enable_base)
        return True

    def _sync_local_state(self):
        """从各 service 查询当前状态, 初始化本地累加目标 (夹爪 / 头部)."""
        for name, client in self.arm_clients.items():
            resp = client.send_command(query=True, source=SOURCE)
            if resp and resp.joint_states and "gripper" in resp.joint_states:
                self._gripper[name] = resp.joint_states["gripper"]
        if self.head_client:
            resp = self.head_client.send_command(query=True, source=SOURCE)
            if resp and resp.joint_states:
                for j in self._head:
                    if j in resp.joint_states:
                        self._head[j] = resp.joint_states[j]

    # ── 主循环 ────────────────────────────────────────────────────────

    def run(self):
        if not self._running:
            self._running = True
        period = 1.0 / max(1, self.config.fps)
        logger.info("开始遥控循环 @ %dHz (Ctrl+C 退出)", self.config.fps)
        try:
            while self._running:
                t0 = time.perf_counter()
                self._tick()
                dt_left = period - (time.perf_counter() - t0)
                if dt_left > 0:
                    time.sleep(dt_left)
        except KeyboardInterrupt:
            logger.info("收到中断, 退出...")
        finally:
            self.stop()

    def _tick(self):
        states = get_gamepad_states(self.gamepad, ALL_KEYMAP)

        # 复位 / 退出
        if states.get("exit"):
            logger.info("退出遥控")
            self._running = False
            return
        # 回零: 上升沿触发 (按一次回一次, 按住不重复); 阻塞直到运动完成
        zero = bool(states.get("back_to_zero"))
        if zero and not self._prev_zero:
            self._prev_zero = True
            self._back_to_zero()
            return
        self._prev_zero = zero

        # dt
        now = time.time()
        dt = (1.0 / self.config.fps) if self._prev_ts is None else (now - self._prev_ts)
        self._prev_ts = now

        # 双臂
        for name in self._arm_names:
            self._dispatch_arm(name, states, dt)

        # 头部
        if self.head_client:
            self._dispatch_head(states, dt)

        # 底盘
        if self.config.enable_base:
            self._dispatch_base(states, now)

    # ── 各子系统 dispatch ─────────────────────────────────────────────

    def _dispatch_arm(self, name, states, dt):
        prefix = f"{name}_arm"
        ss = self.config.stepsize
        sf = self.config.stepsize_factors["arm"]
        dpos = ss["pos"] * dt
        dang = ss["ang"] * dt
        ee = {"dx": 0.0, "dy": 0.0, "dz": 0.0, "droll": 0.0, "dpitch": 0.0, "dyaw": 0.0}

        def axis(pos_act, neg_act, key, mag):
            if states.get(f"{prefix}.{pos_act}"):
                ee[key] = mag
            elif states.get(f"{prefix}.{neg_act}"):
                ee[key] = -mag

        axis("x+", "x-", "dx", dpos * sf.get("x", 1))
        axis("y+", "y-", "dy", dpos * sf.get("y", 1))
        axis("z+", "z-", "dz", dpos * sf.get("z", 1))
        axis("roll+", "roll-", "droll", dang * sf.get("roll", 1))
        axis("pitch+", "pitch-", "dpitch", dang * sf.get("pitch", 1))
        axis("yaw+", "yaw-", "dyaw", dang * sf.get("yaw", 1))

        # 夹爪 (累加绝对量)
        dg = ss["gripper"] * dt
        grip_changed = False
        if states.get(f"{prefix}.gripper+"):
            self._gripper[name] = min(100.0, self._gripper[name] + dg)
            grip_changed = True
        elif states.get(f"{prefix}.gripper-"):
            self._gripper[name] = max(0.0, self._gripper[name] - dg)
            grip_changed = True

        if any(v != 0.0 for v in ee.values()) or grip_changed:
            self.arm_clients[name].send_command(
                ee_delta=ee, gripper=self._gripper[name], source=SOURCE
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
            self.head_client.send_command(joints=dict(self._head), source=SOURCE)

    def _dispatch_base(self, states, now):
        # 切速档 (back 键, 0.2s 去抖)
        if states.get("base.speed_up") and (now - self._last_speed_up) > 0.2:
            self._base_speed_idx = (self._base_speed_idx + 1) % len(self.config.base_speed_levels)
            self._last_speed_up = now
            lvl = self.config.base_speed_levels[self._base_speed_idx]
            logger.info("底盘速度档 -> %d (xy=%.2f m/s, theta=%.0f deg/s)",
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
            self.chassis_client.send_command(vx, vy, vtheta, source=SOURCE)
            self._base_moving = True
        elif self._base_moving:
            # 刚停: 发一次 0 速 (之后不再刷, 让 deadman 自然释放)
            self.chassis_client.send_command(0.0, 0.0, 0.0, source=SOURCE)
            self._base_moving = False

    # ── 复位 ──────────────────────────────────────────────────────────

    def _back_to_zero(self):
        """一键回零: 底盘停, 双臂 + 头部并行阻塞式平滑回 home, 全部到位后才返回."""
        logger.info("回零位 (阻塞, 各子系统平滑回 home)...")
        # 底盘先停 (非阻塞)
        self.chassis_client.send_command(0.0, 0.0, 0.0, source=SOURCE)
        self._base_moving = False

        # 双臂 + 头部并行下发 home (各自独立 socket, 互不冲突; send_command 阻塞至运动完成)
        threads = [
            threading.Thread(target=client.send_command,
                             kwargs={"command": "home", "source": SOURCE})
            for client in self.arm_clients.values()
        ]
        if self.head_client:
            threads.append(threading.Thread(target=self.head_client.send_command,
                                            kwargs={"command": "home", "source": SOURCE}))
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # 把本地累加目标同步到 home (夹爪 / 头部), 否则下一帧增量会从旧值跳变
        for name in self._arm_names:
            home = self._arm_home[name]
            if "gripper" in home:
                self._gripper[name] = home["gripper"]
        if self.head_client:
            self._head = dict(self._head_home)

        # 刚阻塞了数秒, 复位 dt 基准, 否则下一帧 dt 巨大 → 增量暴冲
        self._prev_ts = None
        logger.info("回零完成")

    # ── 关闭 ──────────────────────────────────────────────────────────

    def stop(self):
        logger.info("停止遥控, 清理...")
        try:
            self.chassis_client.send_command(0.0, 0.0, 0.0, source=SOURCE)
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
    parser = argparse.ArgumentParser(description="HomeBot 整机手柄遥控")
    parser.add_argument("--backend", default=None, help="手柄后端 sdl/xinput (覆盖配置)")
    parser.add_argument("--gamepad-id", type=int, default=None, help="手柄设备索引")
    args = parser.parse_args()

    config = get_config().gamepad
    if args.backend:
        config.backend = args.backend
    if args.gamepad_id is not None:
        config.gamepad_id = args.gamepad_id

    app = GamepadControlApp(config)

    # 优雅退出
    signal.signal(signal.SIGINT, lambda *_: setattr(app, "_running", False))

    if not app.initialize():
        sys.exit(1)
    app.run()


if __name__ == "__main__":
    main()
