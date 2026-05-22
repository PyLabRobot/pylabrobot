"""Per-device parameter database access.

Mirrors ``GeminiAPI.Parameter.ParameterDatabase``. Reads and writes are
pointer-based — to access parameter N you first point ``SUBCMD_PARAM_DB_RD_PTR``
or ``WR_PTR`` at N, then read/write ``SUBCMD_PARAM_DB_VALUE``. If the next
access is to N+1, the pointer auto-increments on the controller side so we can
skip the pointer SET — a ~2× speedup on sweeps like the W-axis 58-parameter
apply.
"""

from __future__ import annotations

import threading

from pylabrobot.liquid_handling.backends.agilent.bravo.protocol.gemini.engine import GeminiEngine
from pylabrobot.liquid_handling.backends.agilent.bravo.protocol.gemini.enums import CommonSubCommands
from pylabrobot.liquid_handling.backends.agilent.bravo.protocol.gemini.packet import InstructionAddress


class ParameterAccess:
    """Pointer-cached parameter read/write for a single device address.

    Each device (or master) has its own ParameterAccess instance; the pointer
    cache is per-device because the pointer is a device-side register.
    """

    _UNSET = -1  # sentinel for "no prior pointer"

    def __init__(self, engine: GeminiEngine, address: InstructionAddress):
        self._engine = engine
        self._address = address
        self._last_read_ptr: int = self._UNSET
        self._last_write_ptr: int = self._UNSET
        self._lock = threading.Lock()

    # --- Single-parameter read/write ----------------------------------------

    def read_uint(self, param_id: int, timeout_ms: int = 5000) -> int:
        with self._lock:
            self._ensure_read_ptr(param_id, timeout_ms)
            value = self._engine.get_value(
                self._address, CommonSubCommands.PARAM_DB_VALUE, timeout_ms
            )
            self._last_read_ptr = param_id
            return value

    def read_float(self, param_id: int, timeout_ms: int = 5000) -> float:
        with self._lock:
            self._ensure_read_ptr(param_id, timeout_ms)
            value = self._engine.get_float(
                self._address, CommonSubCommands.PARAM_DB_VALUE, timeout_ms
            )
            self._last_read_ptr = param_id
            return value

    def write_uint(self, param_id: int, value: int, timeout_ms: int = 5000) -> None:
        with self._lock:
            self._ensure_write_ptr(param_id, timeout_ms)
            self._engine.set_uint(
                self._address, CommonSubCommands.PARAM_DB_VALUE, value, timeout_ms
            )
            self._last_write_ptr = param_id

    def write_float(self, param_id: int, value: float, timeout_ms: int = 5000) -> None:
        with self._lock:
            self._ensure_write_ptr(param_id, timeout_ms)
            self._engine.set_float(
                self._address, CommonSubCommands.PARAM_DB_VALUE, value, timeout_ms
            )
            self._last_write_ptr = param_id

    # --- Database-wide operations -------------------------------------------

    def apply(self, timeout_ms: int = 10_000) -> None:
        """Commit staged parameter writes (SUBCMD_PARAM_DB_APPLY)."""
        self._engine.set_uint(
            self._address, CommonSubCommands.PARAM_DB_APPLY, 1, timeout_ms
        )

    def reset(self, timeout_ms: int = 10_000) -> None:
        """Reset parameters to defaults (SUBCMD_PARAM_DB_RESET)."""
        self._engine.set_uint(
            self._address, CommonSubCommands.PARAM_DB_RESET, 1, timeout_ms
        )

    def save(self, timeout_ms: int = 10_000) -> None:
        """Save parameters to flash (SUBCMD_PARAM_DB_SAVE)."""
        self._engine.set_uint(
            self._address, CommonSubCommands.PARAM_DB_SAVE, 1, timeout_ms
        )

    def load(self, timeout_ms: int = 10_000) -> None:
        """Load parameters from flash (SUBCMD_PARAM_DB_LOAD)."""
        self._engine.set_uint(
            self._address, CommonSubCommands.PARAM_DB_LOAD, 1, timeout_ms
        )

    def count(self, timeout_ms: int = 5000) -> int:
        """Return the controller's count of parameters in its database."""
        return self._engine.get_value(
            self._address, CommonSubCommands.PARAM_DB_COUNT, timeout_ms
        )

    def invalidate_cache(self) -> None:
        """Forget cached pointers. Call after a reboot or reset operation."""
        with self._lock:
            self._last_read_ptr = self._UNSET
            self._last_write_ptr = self._UNSET

    # --- Internals ----------------------------------------------------------

    def _ensure_read_ptr(self, param_id: int, timeout_ms: int) -> None:
        # Mirrors the C# `LastParameterReadPtr + 1 != param.ParameterId` test.
        if self._last_read_ptr + 1 != param_id:
            self._engine.set_uint(
                self._address, CommonSubCommands.PARAM_DB_RD_PTR, param_id, timeout_ms
            )

    def _ensure_write_ptr(self, param_id: int, timeout_ms: int) -> None:
        if self._last_write_ptr + 1 != param_id:
            self._engine.set_uint(
                self._address, CommonSubCommands.PARAM_DB_WR_PTR, param_id, timeout_ms
            )
