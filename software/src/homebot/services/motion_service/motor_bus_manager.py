from typing import Optional

from homebot.hal.motors import Motor, MotorCalibration
from homebot.hal.motors.feetech import FeetechMotorsBus
from homebot.utils.pretty_logging import get_logger

logger = get_logger(__name__)


class MotorBusManager:
    """Singleton bus registry keyed by serial port: {port: FeetechMotorsBus}.

    One physical bus per port may carry motors from several subsystems
    (e.g. ACM0 = left_arm + head, ACM1 = right_arm + base). Only holds and
    connects/disconnects buses; motor semantics live elsewhere.
    """

    _instance: Optional['MotorBusManager'] = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._buses = {}
            cls._instance._baudrates = {}
        return cls._instance

    def initialize_bus(
        self,
        port: str,
        baudrate: int,
        motors: dict[str, Motor],
        calibration: Optional[dict[str, MotorCalibration]] = None,
    ) -> bool:
        """Create and connect the bus on a port (first call per port wins).

        Args:
            port: serial device path, also the registry key.
            baudrate: bus baudrate.
            motors: all motors to operate on this bus, {name: Motor}.
            calibration: optional {name: MotorCalibration}; without it normalize is unavailable.
        """
        existing = self._buses.get(port)
        if existing is not None and existing.is_connected:
            return True

        self._baudrates[port] = baudrate
        try:
            bus = FeetechMotorsBus(port, motors=motors, calibration=calibration or {})
            bus.connect()
        except Exception as e:
            logger.error("bus connect failed on %s: %s", port, e)
            self._buses.pop(port, None)
            return False

        self._buses[port] = bus
        logger.info("bus initialized: %s @ %dbps (%d motors)", port, baudrate, len(motors))
        return True

    def reconnect(self, port: str) -> bool:
        """Reopen a bus after a USB drop; clears the SDK's stuck ``is_using`` guard."""
        bus = self._buses.get(port)
        if bus is None:
            return False
        try:
            bus.port_handler.is_using = False  # reset guard left set by an interrupted txn
            bus.disconnect(disable_torque=False)
        except Exception as e:
            logger.warning("reconnect: disconnect %s failed: %s", port, e)
        try:
            bus.connect()
        except Exception as e:
            logger.error("reconnect failed on %s: %s", port, e)
            return False
        logger.info("bus reconnected: %s", port)
        return True

    def get_bus(self, port: str) -> Optional[FeetechMotorsBus]:
        """Return the bus on a port, or None if not initialized."""
        return self._buses.get(port)

    def is_bus_ready(self, port: str) -> bool:
        """True if the port's bus is connected."""
        bus = self._buses.get(port)
        return bus is not None and bus.is_connected

    def ports(self) -> list[str]:
        """Initialized ports."""
        return list(self._buses)

    def close(self) -> None:
        """Disconnect all buses."""
        for port, bus in list(self._buses.items()):
            try:
                bus.disconnect()
            except Exception as e:
                logger.warning("error disconnecting %s: %s", port, e)
            logger.info("bus closed: %s", port)
        self._buses.clear()
        self._baudrates.clear()
        MotorBusManager._instance = None


def get_motor_bus(port: str) -> Optional[FeetechMotorsBus]:
    return MotorBusManager().get_bus(port)


def is_bus_ready(port: str) -> bool:
    return MotorBusManager().is_bus_ready(port)
