import asyncio
from dataclasses import dataclass
from typing import Any, List, Optional, Tuple

from pylabrobot.capabilities.capability import BackendParams
from pylabrobot.device import Driver


class XArm6Error(Exception):
  """Error raised when the xArm SDK returns a non-zero code."""

  def __init__(self, code: int, message: str):
    self.code = code
    super().__init__(f"XArm6Error {code}: {message}")


class XArm6Driver(Driver):
  """Driver for the UFACTORY xArm 6 robotic arm.

  Owns the ``XArmAPI`` SDK instance and handles the connection lifecycle,
  error recovery, and the shared motion-profile defaults used by the
  capability backend.  Exposes the SDK via :attr:`_arm` plus the
  :meth:`call` / :meth:`check` helpers, which the backend uses to issue
  motion and gripper commands.

  All lengths are millimeters and all angles are degrees, matching PLR
  conventions.

  Args:
    ip: IP address of the xArm controller.
    tcp_offset: Optional TCP offset (x, y, z, roll, pitch, yaw) for the end
      effector, in mm and degrees.
    tcp_load: Optional payload config as (mass_kg, [cx, cy, cz]).
  """

  def __init__(
    self,
    ip: str,
    tcp_offset: Optional[Tuple[float, float, float, float, float, float]] = None,
    tcp_load: Optional[Tuple[float, List[float]]] = None,
  ):
    super().__init__()
    self._ip = ip
    self._arm: Any = None
    self._tcp_offset = tcp_offset
    self._tcp_load = tcp_load

  # -- SDK helpers -----------------------------------------------------------

  async def _call_sdk(
    self, func, *args, op: str = "", num_retries: int = 0, **kwargs
  ):
    """Run a synchronous xArm SDK call in a thread, check the return code, and
    return any data payload.

    The xArm SDK uses two return conventions: command methods (``set_position``,
    ``set_gripper_position`` etc.) return a single ``int`` status code, while
    query methods (``get_position``, ``get_servo_angle`` etc.) return a
    ``(code, data)`` tuple.  This helper handles both: it raises
    :class:`XArm6Error` on a non-zero code and returns the data payload
    (unwrapped) for query methods, or ``None`` for command methods.

    If ``num_retries`` > 0, failed attempts trigger :meth:`clear_errors` and
    the call is retried up to that many additional times before propagating
    the last error.

    Args:
      func: Bound xArm SDK method to invoke.
      op: Short operation name used in error messages.
      num_retries: Number of retry attempts after :meth:`clear_errors` on
        failure.  Zero means "no retry".
      *args, **kwargs: Forwarded to ``func``.
    """
    code: Any = None
    for attempt in range(num_retries + 1):
      if attempt > 0:
        await self.clear_errors()
      result = await asyncio.to_thread(func, *args, **kwargs)
      if isinstance(result, (tuple, list)):
        code, *data_parts = result
      else:
        code, data_parts = result, []
      if code is None or code == 0:
        if not data_parts:
          return None
        return data_parts[0] if len(data_parts) == 1 else tuple(data_parts)
    raise XArm6Error(code, f"Failed during {op}" if op else "SDK call failed")

  # -- Lifecycle -------------------------------------------------------------

  @dataclass
  class SetupParams(BackendParams):
    """xArm-specific parameters for ``setup``.

    Args:
      skip_gripper_init: If True, skip gripper mode/enable during setup.
    """

    skip_gripper_init: bool = False

  async def clear_errors(self) -> None:
    """Clear errors/warnings and re-enable the robot for motion.

    This runs the full recovery sequence: clean errors, clean warnings,
    re-enable motion, set position control mode, and set ready state.
    Call this when the robot enters an error/protection state (e.g. code 9).
    """
    await self._call_sdk(self._arm.clean_error, op="clean_error")
    await self._call_sdk(self._arm.clean_warn, op="clean_warn")
    await self._call_sdk(self._arm.motion_enable, True, op="motion_enable")
    await self._call_sdk(self._arm.set_mode, 0, op="set_mode")
    await self._call_sdk(self._arm.set_state, 0, op="set_state")

  async def setup(self, backend_params: Optional[BackendParams] = None) -> None:
    """Connect to the xArm and initialize for position control."""
    if not isinstance(backend_params, XArm6Driver.SetupParams):
      backend_params = XArm6Driver.SetupParams()

    from xarm.wrapper import XArmAPI  # type: ignore[import-not-found]

    self._arm = XArmAPI(self._ip)
    await self.clear_errors()

    if self._tcp_offset is not None:
      await self._call_sdk(
        self._arm.set_tcp_offset, list(self._tcp_offset), op="set_tcp_offset"
      )
    if self._tcp_load is not None:
      await self._call_sdk(
        self._arm.set_tcp_load, self._tcp_load[0], self._tcp_load[1], op="set_tcp_load"
      )

    if not backend_params.skip_gripper_init:
      await self._call_sdk(self._arm.set_gripper_mode, 0, op="set_gripper_mode")
      await self._call_sdk(self._arm.set_gripper_enable, True, op="set_gripper_enable")

  async def stop(self) -> None:
    """Disconnect from the xArm."""
    if self._arm is not None:
      await self._call_sdk(self._arm.disconnect, op="disconnect")
      self._arm = None
