import time
from typing import Optional
from threading import Thread

import zmq

from homebot.hal.motors.feetech import FeetechMotorsBus
from homebot.hal.battery.driver import BatteryDriver
from homebot.common.interfaces.msg import BatteryStatus
from homebot.configs import get_config, BatteryConfig
from homebot.utils.pretty_logging import get_logger, init_logging, make_callout_text
from homebot.utils.robot_utils import make_motors_dict, load_calibration

from .motor_bus_manager import MotorBusManager

console_level, logger = get_logger(__name__)


def _build_battery_driver(config: BatteryConfig, bus: Optional[FeetechMotorsBus] = None) -> BatteryDriver:
    # Reuse a shared bus when given (chassis); otherwise get/create one on config.port.
    if bus is None:
        manager = MotorBusManager()
        bus = manager.get_bus(config.port)
        if bus is None:
            motors = make_motors_dict(config.motors, model=config.motor_model)
            calibration = load_calibration(config.calibration_path)
            if not manager.initialize_bus(config.port, config.baudrate, motors, calibration):
                raise RuntimeError(f"failed to init battery bus: {config.port}")
            bus = manager.get_bus(config.port)
            logger.warning(f"Battery bus creates a new bus: {config.port}.")
    
    return BatteryDriver(
        bus,
        motor_ids=config.motor_ids,
        full_voltage=config.full_voltage,
        low_voltage=config.low_voltage,
        critical_voltage=config.critical_voltage,
        min_voltage=config.min_voltage,
    )


class BatteryService:
    """Publish BatteryState on a PUB socket from a background thread."""

    def __init__(
        self,
        config: Optional[BatteryConfig] = None,
        bus: Optional[FeetechMotorsBus] = None,
        *,
        pub_addr: Optional[str] = None,
    ):
        self.config = config or get_config().battery
        self.pub_addr = pub_addr or self.config.pub_addr
        self.pub_interval_s = self.config.pub_interval_s

        self.battery = _build_battery_driver(self.config, bus)

        self._socket: Optional[zmq.Socket] = None
        self._own_context: Optional[zmq.Context] = None
        self._thread: Optional[Thread] = None
        self._running = False
        self._last_pub_time = 0.0

    def start(self, context: Optional[zmq.Context] = None) -> None:
        """Bind the PUB socket and publish in a daemon thread (non-blocking)."""
        ctx = context or zmq.Context()
        self._own_context = None if context else ctx
        self._socket = ctx.socket(zmq.PUB)
        self._socket.setsockopt(zmq.LINGER, 0)
        self._socket.bind(self.pub_addr)
        self._running = True
        self._thread = Thread(target=self._loop, daemon=True)
        self._thread.start()

        logger.info("\n" + make_callout_text(
            f"Arm service [{self.arm_name}]",
            content="\n".join([
                f"port: {self.config.port}",
                f"pub: {self.pub_addr}",
            ]),
            icon="🔋",
        ))

    def _loop(self) -> None:
        self._publish(force=True)
        while self._running:
            self._publish()
            time.sleep(0.05)

    def _publish(self, force: bool = False) -> None:
        now = time.time()
        if not force and now - self._last_pub_time < self.pub_interval_s:
            return
        state = self.battery.read_state()
        if state is None or self._socket is None:
            return
        self._last_pub_time = now
        try:
            self._socket.send_json(state.to_dict(), flags=zmq.NOBLOCK)
        except zmq.Again:
            return
        if force or state.status in (BatteryStatus.LOW, BatteryStatus.CRITICAL):
            logger.info("battery %.1fV (%.0f%%) %s", state.voltage, state.percentage, state.status.value)

    def stop(self) -> None:
        if not self._running:
            return
        self._running = False
        if self._thread:
            self._thread.join(timeout=1.0)
            self._thread = None
        if self._socket:
            self._socket.close()
            self._socket = None
        if self._own_context:
            self._own_context.term()
            self._own_context = None
        logger.info("battery service stopped")


def main():
    import argparse

    parser = argparse.ArgumentParser(description="HomeBot battery service")
    parser.add_argument("--port", default=None, help="serial port override")
    parser.add_argument("--addr", default=None, help="override battery PUB address")
    args = parser.parse_args()

    config = get_config().battery
    if args.port:
        config.port = args.port
    
    init_logging(console_level)

    service = BatteryService(config=config, pub_addr=args.addr)
    service.start()
    try:
        while True:
            time.sleep(1.0)
    except KeyboardInterrupt:
        pass
    finally:
        service.stop()
    MotorBusManager().close()


if __name__ == "__main__":
    main()
