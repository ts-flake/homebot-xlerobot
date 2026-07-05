from __future__ import annotations

from typing import Callable, Optional, TypeVar

T = TypeVar("T")


class BusIORetryMixin:
    """Reconnect-and-retry-once wrapper for bus IO shared by the motor drivers.

    Set ``on_comm_error`` to a no-arg callable that returns True if the bus
    recovered (see ``MotorBusManager.reconnect``); a wrapped op that raises
    ``ConnectionError`` (incl. ``DeviceNotConnectedError``) then retries once.
    """

    on_comm_error: Optional[Callable[[], bool]] = None

    def _io_retry(self, fn: Callable[[], T]) -> T:
        try:
            return fn()
        except ConnectionError:
            if self.on_comm_error is None or not self.on_comm_error():
                raise
            return fn()
