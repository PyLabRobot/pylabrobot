"""Per-device parameter database access.

Access to the firmware's parameter database. Reads and writes are
pointer-based -- to access parameter N you first point ``PARAM_DB_RD_PTR``
or ``PARAM_DB_WR_PTR`` at N, then read/write ``PARAM_DB_VALUE``. If the next
access is to N+1, the pointer auto-increments on the controller side so the
pointer SET can be skipped -- roughly a 2x speedup on sweeps like the W-axis
57-parameter apply.
"""

from __future__ import annotations

import threading

from ..protocol.gemini.engine import GeminiEngine
from ..protocol.gemini.enums import CommonSubCommands
from ..protocol.gemini.packet import InstructionAddress


class ParameterAccess:
  """Pointer-cached parameter read/write for a single device address.

  Each device (or the master) has its own :class:`ParameterAccess` instance;
  the pointer cache is per-device because the pointer is a device-side
  register.
  """

  _UNSET = -1  # Sentinel for "no prior pointer".

  def __init__(self, engine: GeminiEngine, address: InstructionAddress):
    """Bind this parameter accessor to one device address.

    Args:
      engine: The Gemini engine to issue reads and writes through.
      address: The controller-tree address of the device to access.
    """
    self._engine = engine
    self._address = address
    self._last_read_ptr: int = self._UNSET
    self._last_write_ptr: int = self._UNSET
    self._lock = threading.Lock()

  # --- Single-parameter read/write ----------------------------------------

  def read_uint(self, param_id: int, timeout: float = 5.0) -> int:
    """Read one parameter as a raw uint32.

    Args:
      param_id: The parameter-database index to read.
      timeout: Maximum time to wait for each wire exchange, in seconds.

    Returns:
      The parameter's raw value.
    """
    with self._lock:
      self._ensure_read_ptr(param_id, timeout)
      value = self._engine.get_value(self._address, CommonSubCommands.PARAM_DB_VALUE, timeout)
      self._last_read_ptr = param_id
      return value

  def read_float(self, param_id: int, timeout: float = 5.0) -> float:
    """Read one parameter as an IEEE 754 float.

    Args:
      param_id: The parameter-database index to read.
      timeout: Maximum time to wait for each wire exchange, in seconds.

    Returns:
      The parameter's decoded float value.
    """
    with self._lock:
      self._ensure_read_ptr(param_id, timeout)
      value = self._engine.get_float(self._address, CommonSubCommands.PARAM_DB_VALUE, timeout)
      self._last_read_ptr = param_id
      return value

  def write_uint(self, param_id: int, value: int, timeout: float = 5.0) -> None:
    """Write one parameter as a raw uint32.

    Args:
      param_id: The parameter-database index to write.
      value: The value to write.
      timeout: Maximum time to wait for each wire exchange, in seconds.
    """
    with self._lock:
      self._ensure_write_ptr(param_id, timeout)
      self._engine.set_uint(self._address, CommonSubCommands.PARAM_DB_VALUE, value, timeout)
      self._last_write_ptr = param_id

  def write_float(self, param_id: int, value: float, timeout: float = 5.0) -> None:
    """Write one parameter as an IEEE 754 float.

    Args:
      param_id: The parameter-database index to write.
      value: The value to write.
      timeout: Maximum time to wait for each wire exchange, in seconds.
    """
    with self._lock:
      self._ensure_write_ptr(param_id, timeout)
      self._engine.set_float(self._address, CommonSubCommands.PARAM_DB_VALUE, value, timeout)
      self._last_write_ptr = param_id

  # --- Database-wide operations -------------------------------------------

  def apply(self, timeout: float = 10.0) -> None:
    """Commit staged parameter writes.

    Args:
      timeout: Maximum time to wait for the wire exchange, in seconds.
    """
    self._engine.set_uint(self._address, CommonSubCommands.PARAM_DB_APPLY, 1, timeout)

  def reset(self, timeout: float = 10.0) -> None:
    """Reset parameters to their firmware defaults.

    Args:
      timeout: Maximum time to wait for the wire exchange, in seconds.
    """
    self._engine.set_uint(self._address, CommonSubCommands.PARAM_DB_RESET, 1, timeout)

  def save(self, timeout: float = 10.0) -> None:
    """Save parameters to flash.

    Args:
      timeout: Maximum time to wait for the wire exchange, in seconds.
    """
    self._engine.set_uint(self._address, CommonSubCommands.PARAM_DB_SAVE, 1, timeout)

  def load(self, timeout: float = 10.0) -> None:
    """Load parameters from flash.

    Args:
      timeout: Maximum time to wait for the wire exchange, in seconds.
    """
    self._engine.set_uint(self._address, CommonSubCommands.PARAM_DB_LOAD, 1, timeout)

  def count(self, timeout: float = 5.0) -> int:
    """Return the controller's count of parameters in its database.

    Args:
      timeout: Maximum time to wait for the wire exchange, in seconds.

    Returns:
      The parameter count reported by the device.
    """
    return self._engine.get_value(self._address, CommonSubCommands.PARAM_DB_COUNT, timeout)

  def invalidate_cache(self) -> None:
    """Forget cached read/write pointers.

    Call this after a reboot or reset operation, since the device-side
    pointer registers no longer match what this accessor last set them to.
    """
    with self._lock:
      self._last_read_ptr = self._UNSET
      self._last_write_ptr = self._UNSET

  # --- Internals ------------------------------------------------------------

  def _ensure_read_ptr(self, param_id: int, timeout: float) -> None:
    """Point the read pointer at ``param_id`` unless it is already there.

    Args:
      param_id: The parameter-database index the next read targets.
      timeout: Maximum time to wait for the wire exchange, in seconds.
    """
    if self._last_read_ptr + 1 != param_id:
      self._engine.set_uint(self._address, CommonSubCommands.PARAM_DB_RD_PTR, param_id, timeout)

  def _ensure_write_ptr(self, param_id: int, timeout: float) -> None:
    """Point the write pointer at ``param_id`` unless it is already there.

    Args:
      param_id: The parameter-database index the next write targets.
      timeout: Maximum time to wait for the wire exchange, in seconds.
    """
    if self._last_write_ptr + 1 != param_id:
      self._engine.set_uint(self._address, CommonSubCommands.PARAM_DB_WR_PTR, param_id, timeout)
