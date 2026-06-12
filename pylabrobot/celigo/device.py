"""High-level :class:`Celigo` device facade.

Supports the following operations: setup, status, stage moves by well or by ticks,
Z focus moves, brightfield illumination, and stage open/close (eject/load) choreography.

Everything that physically moves is explicit and polled to completion. Construct with a
serial ``port`` (real board) or a ``transport`` (e.g. a mock) for tests::

    cel = Celigo(port="/dev/ttyUSB3", config_dir=".../Celigo/ConfigFiles")
    cel.setup()
    cel.set_brightfield(True)
    cel.move_to_well("A1")
    cel.move_z(10337)
    cel.close()
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass
from typing import List, Optional, Tuple

from pylabrobot.celigo import ezstepper
from pylabrobot.celigo.config import (
  CalibrationConfig,
  CeligoHardwareConfig,
  HardwareDefaultConfig,
  load_calibration,
  load_hardware_defaults,
)
from pylabrobot.celigo.controller import CeligoController, ControllerStatus
from pylabrobot.celigo.coordinates import CoordinateSystems
from pylabrobot.celigo.ezstepper import EZCommand
from pylabrobot.celigo.navigation import (
  CORNING_3603_96,
  NavigationConfig,
  PlateGeometry,
  load_navigation,
  well_to_encoder_ticks,
)
from pylabrobot.celigo.transport import DEFAULT_BAUDRATE, SerialTransport

# Axis designations: 1=X, 2=Y, 3=Z/focus, 4=filter.
X_AXIS, Y_AXIS, Z_AXIS, FILTER_AXIS = 1, 2, 3, 4

# Brightfield illumination = board DAC channel 0.
BRIGHTFIELD_CHANNEL = 0
BRIGHTFIELD_ON = 3276


@dataclass
class AxisMotion:
  """Velocity / acceleration / current tokens used when commanding an axis move."""

  velocity: int
  acceleration: int
  move_current: Optional[int] = None
  hold_current: Optional[int] = None


# Default motion parameters; override per-axis via Celigo(motion=...).
DEFAULT_MOTION = {
  X_AXIS: AxisMotion(velocity=3543, acceleration=3543, move_current=65),
  Y_AXIS: AxisMotion(velocity=3543, acceleration=3543, move_current=55),
  Z_AXIS: AxisMotion(velocity=5000, acceleration=5000, hold_current=25),
  FILTER_AXIS: AxisMotion(velocity=3543, acceleration=3543),
}


class Celigo:
  """Facade over the native Celigo driver."""

  def __init__(
    self,
    transport=None,
    port: str = "/dev/ttyUSB3",
    baudrate: int = DEFAULT_BAUDRATE,
    config_dir: Optional[str] = None,
    hardware_config: Optional[CeligoHardwareConfig] = None,
    calibration: Optional[CalibrationConfig] = None,
    hardware_defaults: Optional[HardwareDefaultConfig] = None,
    navigation: Optional[NavigationConfig] = None,
    plate: PlateGeometry = CORNING_3603_96,
    motion: Optional[dict] = None,
    move_timeout_s: float = 30.0,
  ):
    self._owns_transport = transport is None
    self.transport = transport if transport is not None else SerialTransport(port, baudrate)
    self.controller: Optional[CeligoController] = None
    self.config_dir = config_dir
    self.hardware_config = hardware_config
    self.calibration = calibration
    self.hardware_defaults = hardware_defaults
    self.navigation = navigation
    self.plate = plate
    self.coords: Optional[CoordinateSystems] = None
    self.motion = {**DEFAULT_MOTION, **(motion or {})}
    self.move_timeout_s = move_timeout_s
    self.motors: List = []

  # -- lifecycle -------------------------------------------------------------

  def setup(self) -> ControllerStatus:
    """Open the link, load configs, build coordinate systems, read board state."""
    if self._owns_transport and not getattr(self.transport, "is_open", False):
      self.transport.open()
    self.controller = CeligoController(self.transport)
    if self.config_dir is not None:
      self._load_configs(self.config_dir)
    if self.calibration is not None and self.hardware_defaults is not None:
      self.coords = CoordinateSystems.from_config(self.calibration, self.hardware_defaults)
    self.motors = self.controller.get_motor_configuration()
    status, _ = self.controller.get_status()
    return status

  def close(self) -> None:
    if self._owns_transport:
      self.transport.close()

  def __enter__(self) -> "Celigo":
    self.setup()
    return self

  def __exit__(self, *exc) -> None:
    self.close()

  def _load_configs(self, config_dir: str) -> None:
    if self.hardware_config is None:
      self.hardware_config = CeligoHardwareConfig.from_install(config_dir)
    self.calibration = self.calibration or _maybe(
      load_calibration, config_dir, "CalibrationConfig.xml"
    )
    self.hardware_defaults = self.hardware_defaults or _maybe(
      load_hardware_defaults, config_dir, "HardwareDefaultConfig.xml"
    )
    self.navigation = self.navigation or _maybe(load_navigation, config_dir, "NavigationConfig.xml")

  # -- status ----------------------------------------------------------------

  def get_status(self) -> Tuple[ControllerStatus, int]:
    return self._ctrl().get_status()

  def read_encoders(self) -> dict:
    """Current encoder position of each axis."""
    c = self._ctrl()
    return {
      "x": c.get_encoder_position(X_AXIS),
      "y": c.get_encoder_position(Y_AXIS),
      "z": c.get_encoder_position(Z_AXIS),
      "filter": c.get_encoder_position(FILTER_AXIS),
    }

  def wait_ready(self, axis: int, timeout: Optional[float] = None) -> int:
    """Poll an axis until its EZStepper status reports ready; return final encoder pos."""
    c = self._ctrl()
    deadline = time.time() + (timeout if timeout is not None else self.move_timeout_s)
    while time.time() < deadline:
      resp = c.send_ezstepper(ezstepper.single_command(EZCommand.QUERY_STATUS, None, axis))
      if resp.ready:
        return c.get_encoder_position(axis)
      time.sleep(0.1)
    raise TimeoutError(f"axis {axis} not ready within timeout")

  # -- motion ----------------------------------------------------------------

  def move_axis_to(self, axis: int, ticks: int, wait: bool = True) -> Optional[int]:
    """Absolute move of an axis to an encoder-tick target (with V/L from motion params)."""
    tokens = self._move_tokens(axis, EZCommand.MOVE_ABSOLUTE, ticks)
    resp = self._ctrl().send_ezstepper(ezstepper.multi_command(tokens, axis))
    if not resp.ok:
      raise RuntimeError(f"axis {axis} move error: {resp.error.name}")
    return self.wait_ready(axis) if wait else None

  def move_z(self, ticks: int, wait: bool = True) -> Optional[int]:
    """Move the Z/focus axis to an absolute encoder target."""
    return self.move_axis_to(Z_AXIS, ticks, wait=wait)

  def move_to_well(self, well: str, wait: bool = True) -> Tuple[int, int]:
    """Move the stage so the named well center is under the optics.

    Requires calibration + hardware-config (loaded in :meth:`setup`).
    """
    if self.coords is None or self.hardware_config is None:
      raise RuntimeError("move_to_well needs calibration + hardware config (set config_dir).")
    x_ax, y_ax = self.hardware_config.x_axis, self.hardware_config.y_axis
    if x_ax is None or y_ax is None:
      raise RuntimeError("hardware config is missing X/Y axis definitions.")
    xt, yt = well_to_encoder_ticks(self.plate, well, self.coords, x_ax, y_ax)
    self.move_axis_to(X_AXIS, xt, wait=wait)
    self.move_axis_to(Y_AXIS, yt, wait=wait)
    return xt, yt

  # -- door / plate load-unload (the stage eject/load choreography) -----------

  def open_door(self, eject_steps: int = 25000) -> None:
    """Drive the stage out to the eject station (limit-protected relative moves)."""
    c = self._ctrl()
    self._set_move_current(X_AXIS)
    self._set_move_current(Y_AXIS)
    c.send_ezstepper(
      ezstepper.multi_command(
        self._move_tokens(X_AXIS, EZCommand.MOVE_NEGATIVE, eject_steps), X_AXIS
      )
    )
    self.wait_ready(X_AXIS)
    c.send_ezstepper(
      ezstepper.multi_command(
        self._move_tokens(Y_AXIS, EZCommand.MOVE_POSITIVE, eject_steps), Y_AXIS
      )
    )
    self.wait_ready(Y_AXIS)

  def close_door(self, load_position: Tuple[int, int, int] = (-136, 5335, 4502)) -> None:
    """Move the stage back under the optics (Y, X, then settle Y)."""
    x, y_in, y_settle = load_position
    self.move_axis_to(Y_AXIS, y_in)
    self.move_axis_to(X_AXIS, x)
    self.move_axis_to(Y_AXIS, y_settle)

  load_plate = close_door
  unload_plate = open_door

  # -- illumination ----------------------------------------------------------

  def set_brightfield(self, on: bool = True, value: int = BRIGHTFIELD_ON) -> int:
    """Turn the brightfield LED on/off (board DAC ch0); return the readback value."""
    c = self._ctrl()
    c.write_dac_raw(BRIGHTFIELD_CHANNEL, value if on else 0)
    return c.read_dac_raw(BRIGHTFIELD_CHANNEL)

  # -- internals -------------------------------------------------------------

  def _move_tokens(self, axis: int, move_cmd: EZCommand, arg: int):
    m = self.motion[axis]
    tokens = []
    if m.hold_current is not None:
      tokens.append((EZCommand.SET_HOLD_CURRENT, m.hold_current))
    tokens.append((EZCommand.SET_VELOCITY, m.velocity))
    tokens.append((EZCommand.SET_ACCELERATION, m.acceleration))
    tokens.append((move_cmd, arg))
    return tokens

  def _set_move_current(self, axis: int) -> None:
    m = self.motion[axis]
    if m.move_current is not None:
      self._ctrl().send_ezstepper(
        ezstepper.single_command(EZCommand.SET_MOVE_CURRENT, m.move_current, axis)
      )

  def _ctrl(self) -> CeligoController:
    if self.controller is None:
      raise RuntimeError("Celigo not set up; call setup() first.")
    return self.controller


def _maybe(loader, config_dir: str, filename: str):
  """Best-effort: load `filename` from config_dir if present, else None."""
  path = (
    config_dir if os.path.basename(config_dir) == filename else os.path.join(config_dir, filename)
  )
  return loader(path) if os.path.isfile(path) else None
