import time
from typing import Optional, List

from homebot.common.interfaces.msg import BatteryState, BatteryStatus
from ..motors.feetech import FeetechMotorsBus, OperatingMode


class BatteryDriver:
    """
    Battery driver. Read motor voltage from the motor bus.
    """
    
    # Default thresholds for a 3S Li-ion pack (11.1V nominal, 12.6V full, 9.0V cutoff)
    _DEFAULT_FULL_VOLTAGE = 12.6    # full (V)
    _DEFAULT_LOW_VOLTAGE = 10.5     # low (V)
    _DEFAULT_CRITICAL_VOLTAGE = 9.5 # critical (V)
    _DEFAULT_MIN_VOLTAGE = 9.0      # min working (V)
    # Feetech Present_Voltage register is in units of 0.1V
    _VOLTAGE_SCALE = 0.1
    
    def __init__(self, 
                 bus: FeetechMotorsBus,
                 *,
                 motor_ids: Optional[List[int]] = None,
                 full_voltage: float = _DEFAULT_FULL_VOLTAGE,
                 low_voltage: float = _DEFAULT_LOW_VOLTAGE,
                 critical_voltage: float = _DEFAULT_CRITICAL_VOLTAGE,
                 min_voltage: float = _DEFAULT_MIN_VOLTAGE):
       
        self._bus = bus
        self._motor_ids = motor_ids or [1]
        self._full_voltage = full_voltage
        self._low_voltage = low_voltage
        self._critical_voltage = critical_voltage
        self._min_voltage = min_voltage
        
        self._last_state: Optional[BatteryStatus] = None
    
    def _voltage_to_percentage(self, voltage: float) -> float:
        if voltage >= self._full_voltage:
            return 100.0
        elif voltage <= self._min_voltage:
            return 0.0
        else:
            percentage = ((voltage - self._min_voltage) / 
                         (self._full_voltage - self._min_voltage)) * 100.0
            return max(0.0, min(100.0, percentage))
    
    def _determine_status(self, voltage: float) -> BatteryStatus:
        if voltage <= 0:
            return BatteryStatus.UNKNOWN
        elif voltage < self._critical_voltage:
            return BatteryStatus.CRITICAL
        elif voltage < self._low_voltage:
            return BatteryStatus.LOW
        else:
            return BatteryStatus.NORMAL

    def _candidate_motor_names(self) -> List[tuple]:
        id2name = {motor.id: name for name, motor in self._bus.motors.items()}
        ordered: List[tuple] = []
        seen = set()
        for sid in self._motor_ids:
            if sid in id2name:
                ordered.append((sid, id2name[sid]))
                seen.add(sid)
        for sid, name in id2name.items():
            if sid not in seen:
                ordered.append((sid, name))
        return ordered

    def read_state(self) -> Optional[BatteryState]:
        if not self._bus.is_connected:
            return

        # All motor share the same battery voltage
        for servo_id, motor_name in self._candidate_motor_names():
            try:
                raw_v = self._bus.read("Present_Voltage", motor_name, normalize=False)
                voltage = float(raw_v) * self._VOLTAGE_SCALE
                if voltage <= 0:
                    continue

                # Motor temperature (optional)
                try:
                    temperature = int(self._bus.read("Present_Temperature", motor_name, normalize=False))
                except Exception:
                    temperature = None

                state = BatteryState(
                    id=servo_id,
                    voltage=voltage,
                    percentage=self._voltage_to_percentage(voltage),
                    status=self._determine_status(voltage),
                    temperature=temperature,
                    timestamp_s=time.time()
                )
                self._last_state = state
                return state

            except Exception:
                # Try next servo motor
                continue

        # All motors fail
        return
    
    def get_last_state(self) -> Optional[BatteryState]:
        return self._last_state
    
    def is_low_battery(self) -> bool:
        return (self._last_state and 
                self._last_state.status in (BatteryStatus.LOW, BatteryStatus.CRITICAL))
    
    def is_critical_battery(self) -> bool:
        return (self._last_state and 
                self._last_state.status == BatteryStatus.CRITICAL)
