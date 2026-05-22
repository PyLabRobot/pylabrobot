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

Coordinate frame
----------------
PyLabRobot hands the backend deck-frame coordinates (mm). The Bravo's axis
frame is related to the deck by a fixed affine transform that depends on how
the deck is mounted — see :class:`BravoDeckCalibration`. Supply a calibration
measured for your machine (e.g. derived from VWorks teachpoints); the default
is an identity transform and must be calibrated before use on hardware.

This backend's motion sequences are structurally complete but the per-machine
calibration and clearance/speed values require bench validation.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import List, Optional, Tuple, Union

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
from pylabrobot.resources import Coordinate, Resource, Tip

from pylabrobot.liquid_handling.backends.agilent.bravo.controllers.agile import AgileController
from pylabrobot.liquid_handling.backends.agilent.bravo.controllers.agile_7612 import (
  Agile7612Controller,
)
from pylabrobot.liquid_handling.backends.agilent.bravo.controllers.agile_srt import (
  AgileSrtController,
)
from pylabrobot.liquid_handling.backends.agilent.bravo.controllers.base import (
  AxisMoveInfo,
  BravoController,
  JogParams,
)
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


@dataclass
class BravoDeckCalibration:
  """Affine transform from PyLabRobot deck coordinates (mm) to Bravo axes (mm).

  ``bravo_axis = origin + sign * deck_coordinate``. The Bravo Z axis is
  inverted relative to PyLabRobot (a larger Z value is physically lower), so
  ``z_sign`` defaults to ``-1``. Measure ``*_origin`` for your machine from a
  known reference (e.g. a VWorks teachpoint).
  """

  x_origin: float = 0.0
  y_origin: float = 0.0
  z_origin: float = 0.0
  x_sign: float = 1.0
  y_sign: float = 1.0
  z_sign: float = -1.0

  def to_axes(self, c: Coordinate) -> Tuple[float, float, float]:
    """Map a deck-frame coordinate to (X, Y, Z) Bravo axis positions in mm."""
    return (
      self.x_origin + self.x_sign * c.x,
      self.y_origin + self.y_sign * c.y,
      self.z_origin + self.z_sign * c.z,
    )


@dataclass
class BravoMotionDefaults:
  """Tunable clearances and currents for the Bravo motion sequences (mm, A)."""

  safe_z: float = 0.0  # Bravo Z parking/traverse height
  aspirate_clearance: float = 1.0  # mm above the well bottom to pipette at
  tip_pickup_overtravel: float = 6.0  # mm of force-controlled Z descent into tips
  tip_pickup_current: float = 0.6  # A — peak current for a full 96-tip pickup
  tip_jog_velocity: float = 10.0  # mm/s
  tip_jog_acceleration: float = 100.0  # mm/s^2
  tip_jog_tolerance: float = 0.5  # mm


class AgilentBravoBackend(LiquidHandlerBackend):
  """PyLabRobot ``LiquidHandlerBackend`` for the Agilent Bravo family."""

  def __init__(
    self,
    controller_type: str = "agile_7612",
    address: Optional[str] = None,
    serial_port: Optional[str] = None,
    num_channels: int = 96,
    profile: Optional[object] = None,
    calibration: Optional[BravoDeckCalibration] = None,
    motion: Optional[BravoMotionDefaults] = None,
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
      calibration: Deck→axis affine transform; see :class:`BravoDeckCalibration`.
      motion: Tunable clearances/currents; see :class:`BravoMotionDefaults`.
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
    self._calibration = calibration or BravoDeckCalibration()
    self._motion = motion or BravoMotionDefaults()
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
    if self._has_gripper:
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

  @property
  def _has_gripper(self) -> bool:
    return bool(getattr(self._controller, "HAS_GRIPPER", True))

  def can_pick_up_tip(self, channel_idx: int, tip: Tip) -> bool:
    return True

  # -- motion helpers --------------------------------------------------------

  def _move(self, *moves: AxisMoveInfo) -> None:
    """Issue a coordinated controller move (runs in a worker thread)."""
    self.controller.move(list(moves))

  async def _amove(self, *moves: AxisMoveInfo) -> None:
    await asyncio.to_thread(self._move, *moves)

  async def _traverse_xy(self, x: float, y: float) -> None:
    """Retract Z to the safe height, then move X/Y."""
    await self._amove(AxisMoveInfo(axis=Axis.Z, position=self._motion.safe_z))
    await self._amove(
      AxisMoveInfo(axis=Axis.X, position=x),
      AxisMoveInfo(axis=Axis.Y, position=y),
    )

  @staticmethod
  def _reference(resource: Resource, deck, offset: Coordinate) -> Coordinate:
    """Absolute deck-frame coordinate of a resource's A1 anchor + op offset.

    Uses front-left-bottom of the resource; itemised resources (plates, tip
    racks) anchor their A1 item there.
    """
    return resource.get_location_wrt(deck, x="l", y="f", z="b") + offset

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

  async def _pipette96(
    self,
    op: Union[
      MultiHeadAspirationPlate,
      MultiHeadAspirationContainer,
      MultiHeadDispensePlate,
      MultiHeadDispenseContainer,
    ],
    aspirate: bool,
  ) -> None:
    """Shared 96-head aspirate/dispense move sequence.

    Positions the head over the plate/container, descends Z to the pipetting
    depth, moves the W (plunger) axis by the requested volume, and retracts.
    The W axis is commanded in µL; the controller converts to motor units.
    """
    resource: Resource = op.wells[0].parent if hasattr(op, "wells") else op.container  # type: ignore[attr-defined]
    ref = self._reference(resource, self.deck, op.offset)
    x, y, z_bottom = self._calibration.to_axes(ref)
    z_pipette = z_bottom - self._motion.aspirate_clearance * self._calibration.z_sign

    await self._traverse_xy(x, y)
    await self._amove(AxisMoveInfo(axis=Axis.Z, position=z_pipette))
    # Aspirate draws the plunger (+volume); dispense expels it (-volume).
    delta = op.volume if aspirate else -op.volume
    await self._amove(AxisMoveInfo(axis=Axis.W, position=delta, absolute=False))
    await self._amove(AxisMoveInfo(axis=Axis.Z, position=self._motion.safe_z))

  async def aspirate96(
    self, aspiration: Union[MultiHeadAspirationPlate, MultiHeadAspirationContainer]
  ):
    await self._pipette96(aspiration, aspirate=True)

  async def dispense96(self, dispense: Union[MultiHeadDispensePlate, MultiHeadDispenseContainer]):
    await self._pipette96(dispense, aspirate=False)

  async def pick_up_tips96(self, pickup: PickupTipRack):
    """Pick up a full rack of tips with the 96/384 head.

    Positions the head over the tip rack and performs a force-controlled Z
    jog so every channel seats its tip without over-pressing.
    """
    ref = self._reference(pickup.resource, self.deck, pickup.offset)
    x, y, z_top = self._calibration.to_axes(ref)
    await self._traverse_xy(x, y)
    jog = JogParams(
      axis=Axis.Z,
      velocity=self._motion.tip_jog_velocity,
      acceleration=self._motion.tip_jog_acceleration,
      max_position=z_top - self._motion.tip_pickup_overtravel * self._calibration.z_sign,
      tolerance=self._motion.tip_jog_tolerance,
      peak_current=self._motion.tip_pickup_current,
    )
    await asyncio.to_thread(self.controller.jog, jog)
    await self._amove(AxisMoveInfo(axis=Axis.Z, position=self._motion.safe_z))

  async def drop_tips96(self, drop: DropTipRack):
    """Eject the tips from the 96/384 head over the target resource."""
    ref = self._reference(drop.resource, self.deck, drop.offset)
    x, y, z_top = self._calibration.to_axes(ref)
    await self._traverse_xy(x, y)
    await self._amove(AxisMoveInfo(axis=Axis.Z, position=z_top))
    # Drive the plunger to its eject end-stop to strip the tips.
    await self._amove(AxisMoveInfo(axis=Axis.W, position=0.0, absolute=True))
    await self._amove(AxisMoveInfo(axis=Axis.Z, position=self._motion.safe_z))

  # -- integrated gripper (plate moves) -------------------------------------

  def _require_gripper(self) -> None:
    if not self._has_gripper:
      raise NotImplementedError("This Bravo model (SRT) has no gripper.")

  async def pick_up_resource(self, pickup: ResourcePickup):
    """Grip a plate/lid with the integrated gripper."""
    self._require_gripper()
    center = pickup.resource.get_location_wrt(self.deck, x="c", y="c", z="b") + pickup.offset
    x, y, z_bottom = self._calibration.to_axes(center)
    grip_z = (
      z_bottom
      - (pickup.resource.get_absolute_size_z() - pickup.pickup_distance_from_top)
      * self._calibration.z_sign
    )
    await self._traverse_xy(x, y)
    await asyncio.to_thread(self.controller.open_gripper)
    await self._amove(AxisMoveInfo(axis=Axis.Zg, position=grip_z))
    plate_width = pickup.resource.get_absolute_size_x()
    from pylabrobot.liquid_handling.backends.agilent.bravo.types import SpeedLevel

    await asyncio.to_thread(self.controller.grip, SpeedLevel.MED, plate_width)
    await self._amove(AxisMoveInfo(axis=Axis.Zg, position=self._motion.safe_z))

  async def move_picked_up_resource(self, move: ResourceMove):
    """Move a gripped resource to a new X/Y location."""
    self._require_gripper()
    x, y, _ = self._calibration.to_axes(move.location + move.offset)
    await self._amove(
      AxisMoveInfo(axis=Axis.X, position=x),
      AxisMoveInfo(axis=Axis.Y, position=y),
    )

  async def drop_resource(self, drop: ResourceDrop):
    """Release a gripped resource at its destination."""
    self._require_gripper()
    dest = drop.destination + drop.offset
    x, y, z_bottom = self._calibration.to_axes(dest)
    grip_z = (
      z_bottom
      - (drop.resource.get_absolute_size_z() - drop.pickup_distance_from_top)
      * self._calibration.z_sign
    )
    await self._traverse_xy(x, y)
    await self._amove(AxisMoveInfo(axis=Axis.Zg, position=grip_z))
    await asyncio.to_thread(self.controller.open_gripper)
    await self._amove(AxisMoveInfo(axis=Axis.Zg, position=self._motion.safe_z))
