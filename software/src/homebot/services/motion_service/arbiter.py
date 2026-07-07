import time
from typing import Optional, Callable

from homebot.common.interfaces.msg import SourcePriority
from homebot.common.interfaces.srv import Request


class PriorityArbiter:
    """Deadman + priority control ownership shared by the motion services."""

    def __init__(self, timeout_ms: float, on_release: Optional[Callable[[], None]] = None):
        self.timeout_ms = timeout_ms
        self.owner: SourcePriority = SourcePriority.UNKNOWN
        self.priority: int = 0
        self._last_time: float = 0.0
        self._deadline: Optional[float] = None
        self._on_release = on_release

    @property
    def idle(self) -> bool:
        return self.owner is SourcePriority.UNKNOWN

    def check_timeout(self, now: Optional[float] = None) -> bool:
        """Release control on deadman or timed-move deadline. Return True if released."""
        now = time.time() if now is None else now
        if self.idle:
            return False
        if self._deadline is not None:
            if now >= self._deadline:
                self.release()
                return True
            return False
        if (now - self._last_time) * 1000.0 > self.timeout_ms:
            self.release()
            return True
        return False

    def can_acquire(self, req: Request) -> bool:
        return (self.idle or req.effective_priority >= self.priority) and self._deadline is None

    def acquire(self, req: Request, deadline: Optional[float] = None) -> None:
        self.owner = req.source
        self.priority = req.effective_priority
        self._last_time = time.time()
        self._deadline = deadline

    def release(self) -> None:
        self.owner = SourcePriority.UNKNOWN
        self.priority = 0
        self._last_time = 0.0
        self._deadline = None
        if self._on_release is not None:
            self._on_release()
