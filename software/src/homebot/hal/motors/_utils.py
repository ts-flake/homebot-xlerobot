# Copyright 2024 The HuggingFace Inc. team. All rights reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#
# Vendored / adapted from lerobot.utils.{import_utils,decorators,utils,errors}.
# Kept self-contained so hal/motors has no lerobot dependency.

import importlib.util
import platform
import select
import sys
from functools import wraps


# ── Errors ───────────────────────────────────────────────────────────────

class DeviceNotConnectedError(ConnectionError):
    """Raised when an operation requires a connection but none is open."""

    def __init__(self, message="This device is not connected. Try calling `connect()` first."):
        super().__init__(message)


class DeviceAlreadyConnectedError(ConnectionError):
    """Raised when `connect()` is called on an already-connected device."""

    def __init__(self, message="This device is already connected. Try not calling `connect()` twice."):
        super().__init__(message)


# ── Optional-package detection ───────────────────────────────────────────

_require_package_cache: dict[str, bool] = {}


def _is_package_available(import_name: str) -> bool:
    return importlib.util.find_spec(import_name) is not None


def require_package(pkg_name: str, import_name: str | None = None) -> None:
    """Raise an informative ImportError if an optional dependency is missing.

    Args:
        pkg_name: PyPI distribution name (used in the install hint).
        import_name: Python import name if it differs from `pkg_name`.
    """
    cache_key = import_name or pkg_name
    if cache_key not in _require_package_cache:
        _require_package_cache[cache_key] = _is_package_available(cache_key)
    if not _require_package_cache[cache_key]:
        raise ImportError(
            f"'{pkg_name}' is required but not installed. Install it with: pip install {pkg_name}"
        )


_serial_available = _is_package_available("serial")
_deepdiff_available = _is_package_available("deepdiff")
_feetech_sdk_available = _is_package_available("scservo_sdk")


# ── Connection-state decorators ──────────────────────────────────────────

def check_if_not_connected(func):
    @wraps(func)
    def wrapper(self, *args, **kwargs):
        if not self.is_connected:
            raise DeviceNotConnectedError(
                f"{self.__class__.__name__} is not connected. Run `.connect()` first."
            )
        return func(self, *args, **kwargs)

    return wrapper


def check_if_already_connected(func):
    @wraps(func)
    def wrapper(self, *args, **kwargs):
        if self.is_connected:
            raise DeviceAlreadyConnectedError(f"{self.__class__.__name__} is already connected.")
        return func(self, *args, **kwargs)

    return wrapper


# ── Terminal helpers (used by record_ranges_of_motion) ───────────────────

def enter_pressed() -> bool:
    if platform.system() == "Windows":
        import msvcrt

        if msvcrt.kbhit():
            key = msvcrt.getch()
            return key in (b"\r", b"\n")
        return False
    return bool(select.select([sys.stdin], [], [], 0)[0]) and sys.stdin.readline().strip() == ""


def move_cursor_up(lines: int) -> None:
    """Move the terminal cursor up by `lines` lines."""
    print(f"\033[{lines}A", end="")
