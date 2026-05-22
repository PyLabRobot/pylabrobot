"""PyLabRobot backend for the Agilent Bravo liquid handler.

The Agilent Bravo is a fixed 96- or 384-channel pipetting head on an X/Y/Z
gantry, with a W (plunger) axis and an optional integrated plate gripper
(G/Zg axes). This backend drives it through the vendored, pure-Python Bravo
control core in :mod:`.bravo` — reverse-engineered from the vendor protocol,
covering four hardware generations: Agile, Agile 7612, Agile SRT, and Darwin
(Gemini).

Because the head is fixed, the Bravo maps to PyLabRobot's 96-channel
operations (``aspirate96`` / ``dispense96`` / ``pick_up_tips96`` /
``drop_tips96``); the per-channel methods raise :class:`NotImplementedError`.
The integrated gripper maps to the resource-move methods.

The control core is synchronous (blocking sockets); this adapter wraps
controller calls in :func:`asyncio.to_thread` to fit PyLabRobot's async model.
"""

from __future__ import annotations

import asyncio
from typing import List, Optional, Union

from pylabrobot.liquid_handling.backends.backend import LiquidHandlerBackend
from pylabrobot.liquid_handling.standard import (
  Drop,
  DropTipRack,
  MultiHeadAspirationContainer,
  MultiHeadAspirationPlate,
  MultiHeadDispenseContainer,
  MultiHeadDispensePlate,
  Pickup,
  PickupTipRack,
  ResourceDrop,
  ResourceMove,
  ResourcePickup,
  SingleChannelAspiration,
  SingleChannelDispense,
)
from pylabrobot.resources import Tip

from pylabrobot.liquid_handling.backends.agilent.bravo.controllers.agile import AgileController
from pylabrobot.liquid_handling.backends.agilent.bravo.controllers.agile_7612 import (
  Agile7612Controller,
)
from pylabrobot.liquid_handling.backends.agilent.bravo.controllers.agile_srt import (
  AgileSrtController,
)
from pylabrobot.liquid_handling.backends.agilent.bravo.controllers.base import BravoController
from pylabrobot.liquid_handling.backends.agilent.bravo.controllers.simulation import (
  SimulationController,
)
from pylabrobot.liquid_handling.backends.agilent.bravo.darwin.controller import DarwinController
from pylabrobot.liquid_handling.backends.agilent.bravo.types import Axis

# controller_type -> controller class. Mirrors OpenBravo's Bravo.connect() factory.
_CONTROLLERS = {
  "agile": AgileController,
  "agile_7612": Agile7612Controller,
  "agile_srt": AgileSrtController,
  "darwin": DarwinController,
  "simulation": SimulationController,
}

_NOT_YET = (
  "is not yet implemented for the Agilent Bravo backend. The control core "
  "(connection, homing) is in place; liquid-handling operations are being "
  "ported from the OpenBravo task layer."
)


class AgilentBravoBackend(LiquidHandlerBackend):
  """PyLabRobot ``LiquidHandlerBackend`` for the Agilent Bravo family."""

  def __init__(
    self,
    controller_type: str = "agile_7612",
    address: Optional[str] = None,
    serial_port: Optional[str] = None,
    num_channels: int = 96,
    profile: Optional[object] = None,
    home_on_setup: bool = True,
  ):
    """Create an Agilent Bravo backend.

    Args:
      controller_type: Which hardware protocol to use — one of ``agile``,
        ``agile_7612``, ``agile_srt``, ``darwin``, ``simulation``.
      address: TCP address of the robot (Ethernet models).
      serial_port: Serial port (legacy ``agile`` over RS-232).
      num_channels: 96 or 384, matching the installed head.
      profile: Optional Bravo calibration profile (axis ranges, homing
        offsets, speeds). Required by the Agile 7612 / SRT / Darwin
        controllers for correct motion.
      home_on_setup: Home all axes during :meth:`setup`.
    """
    super().__init__()
    if controller_type not in _CONTROLLERS:
      raise ValueError(
        f"Unknown controller_type {controller_type!r}; expected one of {sorted(_CONTROLLERS)}"
      )
    self._controller_type = controller_type
    self._address = address
    self._serial_port = serial_port
    self._num_channels = num_channels
    self._profile = profile
    self._home_on_setup = home_on_setup
    self._controller: Optional[BravoController] = None
    self._num_arms = 1
    self._head96_installed = True

  # -- connection ------------------------------------------------------------

  def _make_controller(self) -> BravoController:
    """Construct the concrete controller for the configured hardware."""
    cls = _CONTROLLERS[self._controller_type]
    if self._controller_type in ("agile_7612", "agile_srt"):
      return cls(profile=self._profile)
    if self._controller_type == "darwin":
      return cls(profile=self._profile, address=self._address)
    return cls()

  def _open(self, controller: BravoController) -> None:
    """Open the hardware connection (runs in a worker thread)."""
    if self._controller_type == "agile" and self._serial_port:
      controller.open_serial(self._serial_port)
    elif self._controller_type == "simulation":
      controller.open_tcp("simulation")
    else:
      if not self._address:
        raise ValueError(f"controller_type {self._controller_type!r} requires an address")
      controller.open_tcp(self._address)

  def _home_all(self) -> None:
    """Home every axis the controller supports (runs in a worker thread)."""
    assert self._controller is not None
    axes = [Axis.X, Axis.Y, Axis.Z, Axis.W]
    if getattr(self._controller, "HAS_GRIPPER", True):
      axes += [Axis.G, Axis.Zg]
    self._controller.home_axes(axes)

  async def setup(self):
    await super().setup()
    controller = self._make_controller()
    await asyncio.to_thread(self._open, controller)
    self._controller = controller
    if self._home_on_setup:
      await asyncio.to_thread(self._home_all)
    self.setup_finished = True

  async def stop(self):
    if self._controller is not None:
      await asyncio.to_thread(self._controller.close)
    self._controller = None
    self.setup_finished = False

  def serialize(self) -> dict:
    return {
      **super().serialize(),
      "controller_type": self._controller_type,
      "address": self._address,
      "serial_port": self._serial_port,
      "num_channels": self._num_channels,
      "home_on_setup": self._home_on_setup,
    }

  @property
  def num_channels(self) -> int:
    return self._num_channels

  @property
  def controller(self) -> BravoController:
    """The underlying Bravo controller. Available after :meth:`setup`."""
    if self._controller is None:
      raise RuntimeError("Backend not set up; call setup() first.")
    return self._controller

  def can_pick_up_tip(self, channel_idx: int, tip: Tip) -> bool:
    return True

  # -- per-channel operations: unsupported (the Bravo head is fixed) ---------

  async def pick_up_tips(self, ops: List[Pickup], use_channels: List[int]):
    raise NotImplementedError(
      "The Agilent Bravo has a fixed multi-channel head; use the 96-channel "
      "operation pick_up_tips96 instead of per-channel pick_up_tips."
    )

  async def drop_tips(self, ops: List[Drop], use_channels: List[int]):
    raise NotImplementedError("The Agilent Bravo has a fixed multi-channel head; use drop_tips96.")

  async def aspirate(self, ops: List[SingleChannelAspiration], use_channels: List[int]):
    raise NotImplementedError("The Agilent Bravo has a fixed multi-channel head; use aspirate96.")

  async def dispense(self, ops: List[SingleChannelDispense], use_channels: List[int]):
    raise NotImplementedError("The Agilent Bravo has a fixed multi-channel head; use dispense96.")

  # -- 96-channel head operations -------------------------------------------

  async def pick_up_tips96(self, pickup: PickupTipRack):
    raise NotImplementedError(f"pick_up_tips96 {_NOT_YET}")

  async def drop_tips96(self, drop: DropTipRack):
    raise NotImplementedError(f"drop_tips96 {_NOT_YET}")

  async def aspirate96(
    self, aspiration: Union[MultiHeadAspirationPlate, MultiHeadAspirationContainer]
  ):
    raise NotImplementedError(f"aspirate96 {_NOT_YET}")

  async def dispense96(self, dispense: Union[MultiHeadDispensePlate, MultiHeadDispenseContainer]):
    raise NotImplementedError(f"dispense96 {_NOT_YET}")

  # -- integrated gripper (plate moves) -------------------------------------

  async def pick_up_resource(self, pickup: ResourcePickup):
    if not getattr(self._controller, "HAS_GRIPPER", True):
      raise NotImplementedError("This Bravo model (SRT) has no gripper.")
    raise NotImplementedError(f"pick_up_resource {_NOT_YET}")

  async def move_picked_up_resource(self, move: ResourceMove):
    if not getattr(self._controller, "HAS_GRIPPER", True):
      raise NotImplementedError("This Bravo model (SRT) has no gripper.")
    raise NotImplementedError(f"move_picked_up_resource {_NOT_YET}")

  async def drop_resource(self, drop: ResourceDrop):
    if not getattr(self._controller, "HAS_GRIPPER", True):
      raise NotImplementedError("This Bravo model (SRT) has no gripper.")
    raise NotImplementedError(f"drop_resource {_NOT_YET}")
