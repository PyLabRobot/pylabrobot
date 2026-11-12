"""Formulatrix Mantis liquid dispenser as a PyLabRobot resource."""

import asyncio
import logging
import math
import os
from typing import Any, Awaitable, Callable, Dict, List, Optional, Sequence, Tuple, Union

from pylabrobot.io.ftdi import FTDI
from pylabrobot.resources import Coordinate, Deck, Plate, Resource, Well
from pylabrobot.resources.manipulator import LinkBody
from pylabrobot.resources.resource_holder import ResourceHolder

from .driver.arm_kinematics import (
  LEFT_BASE,
  RIGHT_BASE,
  compute_elbow_positions,
  compute_head_position,
)
from .driver.configuration import RECORDING_MANTIS_V4
from .driver.errors import (
  MantisConfigurationError,
  MantisCoordinateError,
  MantisMotionError,
)
from .driver.features.arm import MOVE_TIMEOUT, Arm
from .driver.features.drop_sensor import DropSensor
from .driver.features.led_bar import LedBar
from .driver.features.plate_clamp import PlateClamp
from .driver.features.pump import (
  DROP_QUEUE_TIMEOUT,
  INPUT_SOURCES,
  MAXIMUM_AUTO_PRIME_CYCLES,
  PRECISE_PRE_DISPENSE_LARGE_DROPS,
  PRECISE_PRE_DISPENSE_VOLUMES,
  PREPARING_PRESSURE_SETTLE_TIMEOUTS,
  InputSource,
  Pump,
  compute_dispense_drops,
  compute_pump_cycle,
)
from .driver.features.rfid import Rfid
from .driver.master import MantisDriver
from .driver.simulator import MantisSimulationDriver, MantisSimulationRfid
from .driver.stage_transform import PITCH_HEIGHT
from .liquid_classes import MantisLiquidClass, mantis_mapping
from .resource_model import CHIP_SLOT_RACKS, MANIFEST, MantisChip, arm_link, chip_rack, z_column

logger = logging.getLogger(__name__)

# The main box's size, in mm, from the manufacturer's model: up to the top plate's surface.
SIZE_X, SIZE_Y, SIZE_Z = MANIFEST["size"]
# The plate holder on the deck: an SBS footprint pushed into the back-left of the pin nest, in mm.
PLATE_HOLDER_LOCATION = Coordinate(23.62, 15.02, 0.0)
PLATE_HOLDER_SIZE_X, PLATE_HOLDER_SIZE_Y = 127.76, 85.48
# Where the right motor turns, x and y in the device frame, and the arm's height, in mm; the arm
# frame is the device frame moved there and mirrored in y.
ARM_BASE_X, ARM_BASE_Y = MANIFEST["joints"]["base_right"]
ARM_Z = MANIFEST["joints"]["arm_z"]
# The viscosity band of the liquid class a prime takes when given none: water's.
WATER_VISCOSITY_BAND = "1 cP"
# Seconds a dispense waits for its last queued item: a full queue of drops and the moves between.
WELLS_QUEUE_TIMEOUT = DROP_QUEUE_TIMEOUT + MOVE_TIMEOUT
# A queued move: each motor's (position, speed, acceleration), device units.
QueuedMove = List[Tuple[float, float, float]]


class MantisDevice(Resource):
  """A Formulatrix Mantis, v3 or v4: its main box, the parts on it and the driver that runs it.

  The main box carries the deck with its plate holder, the two chip racks with a chip spot per input
  slot (``chip_spots``), and the Z column with the arm. The generation is read at setup
  (``driver.configuration.generation``). A chip placed in a chip spot is checked at setup against
  the tag the RFID reader reads in its slot. On a v4 a chip is picked up from its slot and dropped
  back into it (``pick_up_chip``, ``drop_chip``); on the head it is a child of the right long link,
  where the long links meet (``attached_chip``), primed at the waste station (``prime_chip``) and
  dispensed with into the wells of the wellplate in the plate holder (``dispense``).
  """

  # Where ``default_consecutive_drops`` starts: the install's value for every chip.
  _default_consecutive_drops: int = 5
  # Where the dispense heights start: the unit's pitch height, mm above the wellplate's tallest
  # point, for the nozzle over a well and between wells.
  _default_dispense_height_above_well: float = PITCH_HEIGHT
  _default_nozzle_clearance_above_wellplate: float = PITCH_HEIGHT

  def _update_arm_model(self, *, theta_1: float, theta_2: float) -> None:
    """Turn the arm's links to two link angles, the geometry's own, in degrees.

    Args:
      theta_1: motor 0's link angle; with its offset, the angle the motor reads.
      theta_2: motor 1's link angle; with its offset, the angle the motor reads.
    """
    right_elbow, left_elbow = compute_elbow_positions(theta_1=theta_1, theta_2=theta_2)
    head = compute_head_position(theta_1=theta_1, theta_2=theta_2)
    for side, base, elbow in (
      ("right", RIGHT_BASE, right_elbow),
      ("left", LEFT_BASE, left_elbow),
    ):
      # The device frame mirrors the arm frame in y, so an angle there turns the other way.
      short_angle = -math.degrees(math.atan2(elbow[1] - base[1], elbow[0] - base[0]))
      long_angle = -math.degrees(math.atan2(head[1] - elbow[1], head[0] - elbow[0]))
      short, long = self.arm_links[f"{side}_short_arm"], self.arm_links[f"{side}_long_arm"]
      short.rotate_to(z=short_angle, pivot_coordinate=short.proximal_joint)
      long.rotate_to(z=long_angle - short_angle, pivot_coordinate=long.proximal_joint)

  def __init__(
    self,
    name: str = "Mantis",
    *,
    device_id: Optional[str] = None,
    io: Optional[FTDI] = None,
    rfid_io: Optional[FTDI] = None,
    size_x: float = SIZE_X,
    size_y: float = SIZE_Y,
    size_z: float = SIZE_Z,
    declared_configuration_json: Optional[str] = None,
    oem_config_directory: Optional[str] = None,
    ftdi_library: Optional[str] = None,
    show_actions: bool = True,
    simulation: bool = False,
  ):
    """
    Args:
      name: the resource name.
      device_id: the FTDI serial number. None uses the first Mantis found.
      io: an already-built ``FTDI`` for interface B, used instead of building one.
      rfid_io: an already-built ``FTDI`` for interface A, the RFID reader.
      size_x: width, in mm.
      size_y: depth, in mm.
      size_z: height, in mm.
      declared_configuration_json: a file ``save_configuration`` wrote; setup refuses a device that
        disagrees on what is fitted. Simulated, the configuration; None uses
        ``RECORDING_MANTIS_V4``.
      oem_config_directory: the unit's vendor ``Data/Device/Configs`` folder; where it and the
        declared file both hold a unit value, they must agree.
      ftdi_library: "libftdi" or "d2xx"; None picks D2XX on Windows when FTDI's D2XX library
        loads, else libftdi.
      show_actions: whether the v4's LED bar shows each action's colour, and the idle animation
        between actions.
      simulation: build a simulated Mantis, with no link.

    Raises:
      ValueError: if ``simulation`` is given with ``device_id``, ``io``, ``rfid_io`` or
      ``ftdi_library``.
    """
    super().__init__(
      name=name,
      size_x=size_x,
      size_y=size_y,
      size_z=size_z,
      category="device",
      model="Mantis",
    )
    self.driver: MantisDriver
    if simulation:
      if any(link is not None for link in (device_id, io, rfid_io, ftdi_library)):
        raise ValueError(
          "a simulated Mantis has no link: no device_id, io, rfid_io or ftdi_library"
        )
      self.driver = MantisSimulationDriver(
        declared_configuration_json=declared_configuration_json or RECORDING_MANTIS_V4,
        name=name,
        oem_config_directory=oem_config_directory,
        show_actions=show_actions,
      )
    else:
      self.driver = MantisDriver(
        device_id=device_id,
        io=io,
        rfid_io=rfid_io,
        name=name,
        declared_configuration_json=declared_configuration_json,
        oem_config_directory=oem_config_directory,
        ftdi_library=ftdi_library,
        show_actions=show_actions,
      )
    self.mesh = {
      "path": os.path.join(os.path.dirname(__file__), "resource_model", MANIFEST["base"]),
      "units": "mm",
      "up": "Z",
    }
    measured_at = {
      part: Coordinate(*module["measured_at"]) for part, module in MANIFEST["modules"].items()
    }

    self.deck = Deck(name=f"{name}_deck", size_x=SIZE_X, size_y=SIZE_Y, size_z=0.0)
    self.assign_child_resource(self.deck, location=Coordinate(0.0, 0.0, SIZE_Z))
    self.plate_holder = ResourceHolder(
      name=f"{name}_plate_holder",
      size_x=PLATE_HOLDER_SIZE_X,
      size_y=PLATE_HOLDER_SIZE_Y,
      size_z=0.0,
      model="mantis_plate_holder",
    )
    # The viewer draws the holder in its colour: a step darker than the deck, so the nest shows.
    self.plate_holder.appearance = {"color": 0x8C9198}  # type: ignore[attr-defined]
    self.deck.assign_child_resource(self.plate_holder, location=PLATE_HOLDER_LOCATION)

    # Both chip racks, always: whether a side carries a carousel instead is not read from the
    # device; cb declares it (chip_layout), and carousels are not supported yet.
    self.left_chip_rack = chip_rack(name=f"{name}_left_chip_rack", side="left")
    self.assign_child_resource(self.left_chip_rack, location=measured_at["left_chip_rack"])
    self.right_chip_rack = chip_rack(name=f"{name}_right_chip_rack", side="right")
    self.assign_child_resource(self.right_chip_rack, location=measured_at["right_chip_rack"])
    # Each input slot's chip spot, by slot, as the RFID reader and the chip changer number them.
    self.chip_spots: Dict[int, ResourceHolder] = {}
    for slot, side in CHIP_SLOT_RACKS.items():
      rack = self.left_chip_rack if side == "left" else self.right_chip_rack
      spot = rack.get_resource(f"{rack.name}_spot_{slot}")
      assert isinstance(spot, ResourceHolder)
      self.chip_spots[slot] = spot

    # The Z column and the arm it carries, drawn at idle until setup reads where the arm is. The
    # column is drawn where it stands with Z at 0: how far it travels with Z is not measured.
    self.z_column = z_column(name=f"{name}_z_column")
    self.assign_child_resource(self.z_column, location=measured_at["z_column"])
    self.arm_links: Dict[str, LinkBody] = {}
    for side, base in (("right", RIGHT_BASE), ("left", LEFT_BASE)):
      short = arm_link(name=f"{name}_{side}_short_arm", link=f"{side}_short_arm")
      long = arm_link(name=f"{name}_{side}_long_arm", link=f"{side}_long_arm")
      assert short.distal_joint is not None
      self.z_column.assign_child_resource(
        short,
        location=Coordinate(ARM_BASE_X + base[0], ARM_BASE_Y - base[1], ARM_Z)
        - measured_at["z_column"]
        - short.proximal_joint,
      )
      above = MANIFEST["arm_links"][f"{side}_long_arm"]["above"]
      short.assign_child_resource(
        long,
        location=short.distal_joint - long.proximal_joint + Coordinate(0.0, 0.0, above),
      )
      self.arm_links[f"{side}_short_arm"], self.arm_links[f"{side}_long_arm"] = short, long
    self._update_arm_model(theta_1=0.0, theta_2=0.0)
    # The chip on the head and the slot it came from; and a chip that may be on the head, by slot,
    # until ``confirm_chip_attached``. Held in memory only.
    self._attached_chip: Optional[MantisChip] = None
    self._attached_chip_slot: Optional[int] = None
    self._possibly_attached_chip_slot: Optional[int] = None
    # The chip on the head once a prime of it ended, until it is dropped; a dispense primes another.
    self._primed_chip: Optional[MantisChip] = None

  @property
  def plate_clamp(self) -> Optional[PlateClamp]:
    """The plate clamp, once setup found the LC3 board; None before or without it."""
    return self.driver.plate_clamp

  @property
  def pump(self) -> Optional[Pump]:
    """The pump, once setup found the pressure board; None before or without it."""
    return self.driver.pump

  @property
  def rfid(self) -> Optional[Rfid]:
    """The RFID reader, once setup found it on interface A; None before or without it."""
    return self.driver.rfid

  @property
  def led_bar(self) -> Optional[LedBar]:
    """The LED bar, once setup found board 12 on a v4; None before, without it, or on a v3."""
    return self.driver.led_bar

  @property
  def arm(self) -> Optional[Arm]:
    """The arm and Z, once setup found board 11 (a v4); None before or without it."""
    return self.driver.arm

  @property
  def drop_sensor(self) -> Optional[DropSensor]:
    """The drop sensor, once setup found board 12 on a v4; None before or without it."""
    return self.driver.drop_sensor

  @property
  def default_consecutive_drops(self) -> int:
    """The drops in a row that end a prime that stops on drops, when ``prime_chip`` names none."""
    return self._default_consecutive_drops

  @default_consecutive_drops.setter
  def default_consecutive_drops(self, value: int) -> None:
    if not value >= 1:
      raise ValueError(f"default_consecutive_drops must be 1 or more, got {value}")
    self._default_consecutive_drops = value

  @property
  def default_dispense_height_above_well(self) -> float:
    """The nozzle's height over a well in a dispense, in mm above the wellplate's tallest point."""
    return self._default_dispense_height_above_well

  @default_dispense_height_above_well.setter
  def default_dispense_height_above_well(self, value: float) -> None:
    if not value > 0:
      raise ValueError(f"default_dispense_height_above_well must be above 0, got {value}")
    self._default_dispense_height_above_well = value

  @property
  def default_nozzle_clearance_above_wellplate(self) -> float:
    """How far the nozzle clears the wellplate's tallest point between wells in a dispense, mm."""
    return self._default_nozzle_clearance_above_wellplate

  @default_nozzle_clearance_above_wellplate.setter
  def default_nozzle_clearance_above_wellplate(self, value: float) -> None:
    if not value > 0:
      raise ValueError(f"default_nozzle_clearance_above_wellplate must be above 0, got {value}")
    self._default_nozzle_clearance_above_wellplate = value

  def _get_placed_chips(self) -> Dict[int, MantisChip]:
    """The chip placed in each chip spot, by slot; a spot holding none is left out."""
    return {
      slot: spot.resource
      for slot, spot in self.chip_spots.items()
      if isinstance(spot.resource, MantisChip)
    }

  def _update_simulated_tags(self) -> None:
    """Give a simulated RFID reader the chips placed in the chip spots: the tags it reads."""
    rfid = self.driver.rfid
    if isinstance(rfid, MantisSimulationRfid):
      rfid.chip_types = {slot: chip.chip_type for slot, chip in self._get_placed_chips().items()}

  def _check_tag(self, *, slot: int, chip: MantisChip, tag: Dict[str, Any]) -> None:
    """Raise ``MantisConfigurationError`` unless a tag read in a slot is of the chip type placed
    there; log it."""
    if tag["chip_type"] != chip.chip_type:
      raise MantisConfigurationError(
        f"{self.name}: slot {slot} holds chip {tag['serial']} of type {tag['chip_type']!r} "
        f"(RFID code {tag['chip_type_code']}), but {chip.name} of type {chip.chip_type!r} is "
        "placed there"
      )
    logger.info("[%s] slot %d: chip %s, %s (RFID)", self.name, slot, tag["serial"], chip.chip_type)

  async def _check_chips(self) -> None:
    """Check each chip placed in a chip spot against the tag the RFID reader reads in its slot.

    Nothing is read when no chip is placed; without a reader or its antennas the chips are not
    checked, with a warning. A simulated reader reads the chips placed. One chip placed whose slot
    reads no tag may be on the head, left there by a process that ended: it is taken as possibly
    attached, with a warning, and chip moves are refused until ``confirm_chip_attached``.

    Raises:
      MantisConfigurationError: if a slot with a chip placed has no antenna or reads a tag of
        another chip type or none known, or more slots read no tag than a chip can be on the head.
    """
    self._possibly_attached_chip_slot = None
    placed = self._get_placed_chips()
    if not placed:
      return
    rfid = self.driver.rfid
    if rfid is None or rfid.slot_to_antenna is None:
      logger.warning("[%s] no RFID reader or antennas: the chips placed are not checked", self.name)
      return
    self._update_simulated_tags()
    missing = sorted(set(placed) - set(rfid.slot_to_antenna))
    if missing:
      raise MantisConfigurationError(
        f"{self.name}: chips placed in slots {missing}, which have no antenna"
      )
    tags = await rfid.request_tags()
    found = sorted(slot for slot, tag in tags.items() if tag is not None)
    empty = sorted(slot for slot in placed if tags[slot] is None)
    if len(empty) > (0 if self._attached_chip is not None else 1):
      raise MantisConfigurationError(
        f"{self.name}: chips are placed in slots {empty}, which hold no chip (RFID); chips were "
        f"read in slots {found}"
      )
    for slot, chip in placed.items():
      tag = tags[slot]
      if tag is None:
        logger.warning(
          "[%s] %s is placed in slot %d, which holds no chip (RFID): it may be on the head. Chips "
          "are not moved until confirm_chip_attached()",
          self.name,
          chip.name,
          slot,
        )
        self._possibly_attached_chip_slot = slot
        continue
      self._check_tag(slot=slot, chip=chip, tag=tag)

  async def setup(self, *, skip_arm: bool = False, skip_self_test: bool = False) -> None:
    """Connect, read the device's configuration and check it, then bring the arm and Z up, zero
    the pressure sensors and run the pump's connect self-test; then check the chips placed in the
    chip spots by RFID (``_check_chips``), and stop if they do not match.

    Args:
      skip_arm: do not bring the arm up; nothing moves.
      skip_self_test: skip the connect self-test, on a reconnect after one since power-on.

    Raises:
      MantisConfigurationError: as the driver's setup, or if a chip placed is not the one read.
    """
    await self.driver.setup(skip_arm=skip_arm, skip_self_test=skip_self_test)
    try:
      await self._check_chips()
    except BaseException:
      try:
        await self.driver.stop()
      except Exception:
        logger.warning("[%s] stop after a failed chip check failed", self.name, exc_info=True)
      raise
    arm = self.driver.arm
    if arm is not None and arm.is_initialized:
      theta_1, theta_2 = await arm.request_link_angles()
      self._update_arm_model(theta_1=theta_1, theta_2=theta_2)

  @property
  def attached_chip(self) -> Optional[MantisChip]:
    """The chip on the head, picked up by ``pick_up_chip``; None when there is none."""
    return self._attached_chip

  def _update_chip_location(self, *, chip: MantisChip, slot: Optional[int]) -> None:
    """Move a chip in the resource model: into a slot's chip spot, or with None onto the head,
    centred below where the long links meet."""
    if chip.parent is not None:
      chip.unassign()
    if slot is not None:
      self.chip_spots[slot].assign_child_resource(chip)
      return
    link = self.arm_links["right_long_arm"]
    assert link.distal_joint is not None
    link.assign_child_resource(
      chip,
      location=link.distal_joint
      - Coordinate(chip.get_size_x() / 2, chip.get_size_y() / 2, chip.get_size_z()),
    )

  def _get_arm_and_pump(self) -> Tuple[Arm, Pump]:
    """The arm and the pump a chip is moved with.

    Raises:
      MantisMotionError: if a chip may be on the head, until ``confirm_chip_attached``.
      MantisConfigurationError: without the arm or the pump: before setup, or on a v3, whose chip
        changer is not supported.
    """
    if self._possibly_attached_chip_slot is not None:
      raise MantisMotionError(
        f"{self.name}: the chip of slot {self._possibly_attached_chip_slot} may be on the head; "
        "look, then call confirm_chip_attached()"
      )
    arm, pump = self.driver.arm, self.driver.pump
    if arm is None or pump is None:
      raise MantisConfigurationError(
        f"{self.name}: chips are moved with the arm and the pump, built at setup on a v4; a v3's "
        "chip changer is not supported"
      )
    return arm, pump

  async def _check_chip_in_slot(self, *, slot: int, chip: MantisChip) -> None:
    """Read a slot's tag and raise unless it is of the chip type placed there. A simulated Mantis
    without a reader or its antennas is not checked, with a warning.

    Raises:
      MantisConfigurationError: without a reader or its antennas on a device, or if the slot
        reads no tag, or a tag of another chip type or none known.
    """
    rfid = self.driver.rfid
    if rfid is None or rfid.slot_to_antenna is None:
      if isinstance(self.driver, MantisSimulationDriver):
        logger.warning("[%s] no RFID reader or antennas: slot %d is not checked", self.name, slot)
        return
      raise MantisConfigurationError(
        f"{self.name}: no RFID reader or antennas, so the chip in slot {slot} cannot be checked; "
        "it is not picked up"
      )
    tag = await rfid.request_tag(slot=slot)
    if tag is None:
      raise MantisConfigurationError(
        f"{self.name}: slot {slot} holds no chip (RFID); {chip.name} is placed there"
      )
    self._check_tag(slot=slot, chip=chip, tag=tag)

  async def _check_slot_empty(self, *, slot: int) -> None:
    """Read a slot's tag, if there is a reader with antennas, and raise if it holds a chip.

    Raises:
      MantisConfigurationError: if the slot reads a tag.
    """
    rfid = self.driver.rfid
    if rfid is None or rfid.slot_to_antenna is None:
      logger.warning("[%s] no RFID reader or antennas: slot %d is not checked", self.name, slot)
      return
    tag = await rfid.request_tag(slot=slot)
    if tag is not None:
      raise MantisConfigurationError(
        f"{self.name}: slot {slot} holds chip {tag['serial']} (RFID); a chip is not dropped there"
      )

  async def _check_parked(self, *, arm: Arm) -> None:
    """Raise ``MantisMotionError`` unless the head is at idle (``Arm.request_is_parked``)."""
    if not await arm.request_is_parked():
      raise MantisMotionError(f"{self.name}: a chip is moved from idle only; park() first")

  async def pick_up_chip(self, *, slot: int) -> None:
    """Pick up the chip placed in a slot's chip spot, from idle (v4).

    Before anything moves, the slot's tag must read the chip type placed there. Then the chip's
    pre-attach valve sequence plays and the head goes along the slot's routes onto the chip and back
    to idle (``Arm._get_chip_changer_moves``); the chip then moves onto the head in the resource
    model. The LED bar shows "pick_up_chip" meanwhile. A failure once the head moves leaves the chip
    possibly on the head: chip moves are refused until ``confirm_chip_attached``.

    Args:
      slot: the input slot, a key of ``chip_spots``.

    Raises:
      ValueError: if ``slot`` is not a key of ``chip_spots``, or a target is outside its limits.
      MantisMotionError: if a chip is or may be on the head, the arm is not brought up, the head
        is not at idle, or a motor faults.
      MantisConfigurationError: before setup or on a v3; without the unit's chip changer routes or
        the chip's valve sequences; with no chip placed in the slot; without an RFID reader on a
        device; or if the slot reads no tag or another chip type.
    """
    arm, pump = self._get_arm_and_pump()
    if self._attached_chip is not None:
      raise MantisMotionError(
        f"{self.name}: {self._attached_chip.name} from slot {self._attached_chip_slot} is on the "
        "head; drop_chip() first"
      )
    if slot not in self.chip_spots:
      raise ValueError(f"slot must be one of {tuple(self.chip_spots)}, got {slot}")
    chip = self.chip_spots[slot].resource
    if not isinstance(chip, MantisChip):
      raise MantisConfigurationError(
        f"{self.name}: no chip is placed in slot {slot}; place one in chip_spots[{slot}]"
      )
    groups = arm._get_chip_changer_moves(slot=slot, drop=False)
    pump._get_valve_sequence(chip_type=chip.chip_type, name="pre_attach")
    await self._check_chip_in_slot(slot=slot, chip=chip)
    await self._check_parked(arm=arm)
    async with self.driver._show_action(action="pick_up_chip"):
      await pump._play_valve_sequence(chip_type=chip.chip_type, name="pre_attach")
      try:
        await arm._play_queued_move_groups(groups=groups)
      except BaseException:
        self._possibly_attached_chip_slot = slot
        logger.warning(
          "[%s] the pick-up failed: the chip of slot %d may be on the head", self.name, slot
        )
        raise
      self._update_chip_location(chip=chip, slot=None)
      self._attached_chip, self._attached_chip_slot = chip, slot
      self._update_simulated_tags()

  async def drop_chip(self, *, slot: int) -> None:
    """Drop the chip on the head back into the slot it was picked up from, from idle (v4): the
    chip's pre-detach valve sequence, then the pick-up's moves reversed.

    Before anything moves, a slot that reads a tag refuses: a chip is there. The LED bar shows
    "drop_chip" meanwhile. A failure once the head moves leaves the chip possibly on the head, in
    its chip spot in the resource model: chip moves are refused until ``confirm_chip_attached``.

    Args:
      slot: the slot the chip on the head was picked up from.

    Raises:
      MantisMotionError: if no chip is on the head, it came from another slot, a chip may be on the
        head, the arm is not brought up, the head is not at idle, or a motor faults.
      MantisConfigurationError: before setup or on a v3; without the chip's valve sequences; or if
        the slot reads a tag.
    """
    arm, pump = self._get_arm_and_pump()
    chip = self._attached_chip
    if chip is None:
      raise MantisMotionError(f"{self.name}: no chip is on the head")
    if slot != self._attached_chip_slot:
      raise MantisMotionError(
        f"{self.name}: {chip.name} was picked up from slot {self._attached_chip_slot}; it is "
        f"dropped only there, not into slot {slot}"
      )
    groups = arm._get_chip_changer_moves(slot=slot, drop=True)
    pump._get_valve_sequence(chip_type=chip.chip_type, name="pre_detach")
    await self._check_slot_empty(slot=slot)
    await self._check_parked(arm=arm)
    async with self.driver._show_action(action="drop_chip"):
      await pump._play_valve_sequence(chip_type=chip.chip_type, name="pre_detach")
      try:
        await arm._play_queued_move_groups(groups=groups)
      except BaseException:
        self._possibly_attached_chip_slot = slot
        logger.warning(
          "[%s] the drop failed: the chip of slot %d may be on the head", self.name, slot
        )
        raise
      finally:
        self._update_chip_location(chip=chip, slot=slot)
        self._attached_chip, self._attached_chip_slot = None, None
        self._primed_chip = None
      self._update_simulated_tags()

  async def request_chip_attached(self) -> bool:
    """Whether a chip is on the head, by RFID: whether the slot of the chip on the head, or of the
    one that may be, reads no tag. False, with nothing read, when there is neither.

    The reader reads the slots, not the head: a chip taken out of its slot by hand reads the same.

    Raises:
      MantisConfigurationError: without an RFID reader or its antennas.
    """
    slot = (
      self._attached_chip_slot
      if self._attached_chip_slot is not None
      else self._possibly_attached_chip_slot
    )
    if slot is None:
      return False
    rfid = self.driver.rfid
    if rfid is None or rfid.slot_to_antenna is None:
      raise MantisConfigurationError(f"{self.name}: no RFID reader or antennas")
    return await rfid.request_tag(slot=slot) is None

  def confirm_chip_attached(self, *, attached: bool) -> None:
    """Say whether the chip that may be on the head is there, as seen on the device; chip moves are
    refused until then.

    Attached, it moves onto the head in the resource model and ``drop_chip`` returns it to its
    slot. Not attached, it stays in its slot's chip spot, and a pick-up reads its slot again.

    Args:
      attached: whether the chip is on the head.

    Raises:
      ValueError: if no chip may be on the head.
      MantisConfigurationError: if attached, but no chip is placed in its slot's chip spot.
    """
    slot = self._possibly_attached_chip_slot
    if slot is None:
      raise ValueError(f"{self.name}: no chip may be on the head")
    if not attached:
      self._possibly_attached_chip_slot = None
      return
    chip = self.chip_spots[slot].resource
    if not isinstance(chip, MantisChip):
      raise MantisConfigurationError(
        f"{self.name}: no chip is placed in slot {slot}; place the chip on the head in "
        f"chip_spots[{slot}], then confirm"
      )
    self._possibly_attached_chip_slot = None
    self._update_chip_location(chip=chip, slot=None)
    self._attached_chip, self._attached_chip_slot = chip, slot
    self._primed_chip = None
    self._update_simulated_tags()

  def _get_liquid_class(
    self, *, chip: MantisChip, liquid_class: Optional[MantisLiquidClass]
  ) -> MantisLiquidClass:
    """The liquid class for a chip: the one given, else its type's at ``WATER_VISCOSITY_BAND``.

    Raises:
      ValueError: if the one given is in ``mantis_mapping`` for other chip types only.
      MantisConfigurationError: if none is given and the chip type has none.
    """
    if liquid_class is None:
      default = mantis_mapping.get((WATER_VISCOSITY_BAND, chip.chip_type))
      if default is None:
        raise MantisConfigurationError(
          f"{self.name}: no {WATER_VISCOSITY_BAND} liquid class for {chip.chip_type}; pass one"
        )
      return default
    chip_types = {
      chip_type for (_, chip_type), mapped in mantis_mapping.items() if mapped is liquid_class
    }
    if chip_types and chip.chip_type not in chip_types:
      raise ValueError(
        f"the liquid class is for {sorted(chip_types)}, not {chip.name}'s {chip.chip_type}"
      )
    return liquid_class

  def _get_dispensing_chip(self) -> MantisChip:
    """The chip on the head, which dispenses drops.

    Raises:
      MantisMotionError: if no chip is on the head.
      MantisConfigurationError: for a continuous flow chip.
    """
    chip = self._attached_chip
    if chip is None:
      raise MantisMotionError(f"{self.name}: no chip is on the head; pick_up_chip() first")
    if chip.dispense_type != "standard":
      raise MantisConfigurationError(
        f"{self.name}: {chip.name} is a {chip.chip_type} chip; only a chip that dispenses drops is "
        "primed or dispenses"
      )
    return chip

  def _get_prime(
    self,
    *,
    arm: Arm,
    pump: Pump,
    chip: MantisChip,
    volume: Optional[float],
    liquid_class: Optional[MantisLiquidClass],
    input_source: Optional[InputSource],
    drop_detection: Optional[bool],
    consecutive_drops: Optional[int],
    max_cycles: Optional[int],
  ) -> Callable[[], Awaitable[None]]:
    """Check a prime of the chip on the head, as ``prime_chip`` takes it, and return what runs it;
    nothing is sent.

    Raises:
      ValueError, MantisConfigurationError, MantisMotionError: as ``prime_chip``.
    """
    liquid_class = self._get_liquid_class(chip=chip, liquid_class=liquid_class)
    if not liquid_class.prime_pressure >= 0:
      raise ValueError(
        "the liquid class's prime_pressure must be 0 or above, in psi, got "
        f"{liquid_class.prime_pressure}"
      )
    if not liquid_class.prime_vacuum <= 0:
      raise ValueError(
        "the liquid class's prime_vacuum must be 0 or below, in inHg, got "
        f"{liquid_class.prime_vacuum}"
      )
    drop_sensor = self.driver.drop_sensor
    if drop_detection is None:
      drop_detection = drop_sensor is not None and volume is None and input_source is None
    cycles: Optional[int] = None
    if drop_detection:
      if drop_sensor is None:
        raise MantisConfigurationError(
          f"{self.name}: a prime that stops on drops needs the drop sensor, which a v4 has"
        )
      if volume is not None or input_source is not None:
        raise ValueError("a prime that stops on drops takes no volume and no input_source")
      if consecutive_drops is None:
        consecutive_drops = self.default_consecutive_drops
      cycles = MAXIMUM_AUTO_PRIME_CYCLES[chip.chip_type] if max_cycles is None else max_cycles
      for name, value in (("consecutive_drops", consecutive_drops), ("max_cycles", cycles)):
        if not value >= 1:
          raise ValueError(f"{name} must be 1 or more, got {value}")
      drops = pump._get_drop_states(
        chip_type=chip.chip_type, name="prime_pump", liquid_class=liquid_class, drop="large"
      )
      description = f"until {consecutive_drops} drops in a row, at most {cycles} cycles"
    else:
      if consecutive_drops is not None or max_cycles is not None:
        raise ValueError("consecutive_drops and max_cycles are for a prime that stops on drops")
      if input_source is None:
        input_source = "tube"
      if input_source not in INPUT_SOURCES:
        raise ValueError(f"input_source must be one of {INPUT_SOURCES}, got {input_source!r}")
      if volume is None:
        volume = pump._get_prime_volume(chip_type=chip.chip_type, input_source=input_source)
      if not volume > 0:
        raise ValueError(f"volume must be above 0, got {volume}")
      small, large = pump._get_dispense_sequences(chip_type=chip.chip_type)
      large_drops, small_drops = compute_pump_cycle(
        target_volume=volume,
        large_diaphragm_volume=large[1],
        small_diaphragm_volume=small[1],
        allow_all_zero=False,
      )
      drops = [
        *pump._get_drop_states(
          chip_type=chip.chip_type, name=small[0], liquid_class=liquid_class, drop="small"
        )
        * small_drops,
        *pump._get_drop_states(
          chip_type=chip.chip_type, name=large[0], liquid_class=liquid_class, drop="large"
        )
        * large_drops,
      ]
      description = f"with {volume:g} uL: {small_drops} small and {large_drops} large drops"
    post_prime = pump._get_valve_sequence(chip_type=chip.chip_type, name="post_prime")
    return_moves = arm._get_waste_station_return_moves()
    settle_timeout = PREPARING_PRESSURE_SETTLE_TIMEOUTS.get(
      chip.chip_type, pump.default_settle_timeout
    )
    prime_pressure, prime_vacuum = liquid_class.prime_pressure, liquid_class.prime_vacuum

    async def prime() -> None:
      logger.info("[%s] priming %s %s", self.name, chip.name, description)
      async with self.driver._show_action(action="prime_chip"):
        await arm.move_to_waste_station()
        await pump._prime(
          chip_pressure=prime_pressure,
          vacuum=prime_vacuum,
          settle_timeout=settle_timeout,
          drops=drops,
          post_prime=post_prime,
          drop_sensor=drop_sensor,
          cycles=cycles,
          consecutive_drops=consecutive_drops,
        )
        await arm._play_queued_move_groups(groups=return_moves)

    return prime

  async def prime_chip(
    self,
    *,
    volume: Optional[float] = None,
    liquid_class: Optional[MantisLiquidClass] = None,
    input_source: Optional[InputSource] = None,
    drop_detection: Optional[bool] = None,
    consecutive_drops: Optional[int] = None,
    max_cycles: Optional[int] = None,
  ) -> None:
    """Prime the chip on the head at the waste station, from idle (v4).

    The head goes into the waste station along the unit's route. The pump starts; the vacuum and
    the chip take the liquid class's prime pressures, the bottle off; the drops play, then the
    chip's post-prime; the pump stops on every path. The head comes back along the route, last line
    first, to the idle position, then to idle, as in the recorded v4 prime. By volume, the drops
    are the closest split into small then large drops (``compute_pump_cycle``), and a drop sensor
    that saw none raises. By drops, the chip's prime pump cycle plays until the drop sensor sees
    ``consecutive_drops`` in a row, for at most ``max_cycles`` cycles. Every argument and target is
    checked before anything moves. The LED bar shows "prime_chip" meanwhile. The chip stays primed
    until it is dropped.

    Args:
      volume: uL; None takes the install's prime volume for ``input_source``.
      liquid_class: the pressures and diaphragm delays; None takes the chip type's at
        ``WATER_VISCOSITY_BAND``.
      input_source: by volume, "tube" or "pipette_tip": whose prime volume None takes; None takes
        "tube".
      drop_detection: stop on drops; None does with a drop sensor and no ``volume`` or
        ``input_source``.
      consecutive_drops: by drops, the drops in a row that end it; None takes
        ``default_consecutive_drops``.
      max_cycles: by drops, the most cycles; None takes ``MAXIMUM_AUTO_PRIME_CYCLES``.

    Raises:
      ValueError: if an argument is out of range or not for the kind of prime, or the liquid class
        is another chip type's or its prime pressure or vacuum out of range.
      MantisMotionError: if no chip is on the head, one may be, the head is not at idle, or a
        motor faults.
      MantisConfigurationError: before setup or on a v3; for a continuous flow chip; without the
        unit's waste station or idle position route, the install's valve sequences or volumes; or by
        drops without a drop sensor.
      MantisPressureError: if the vacuum or the chip does not settle.
      MantisPrimeError: if the drop sensor saw no drop, or not ``consecutive_drops`` in a row.
    """
    arm, pump = self._get_arm_and_pump()
    chip = self._get_dispensing_chip()
    prime = self._get_prime(
      arm=arm,
      pump=pump,
      chip=chip,
      volume=volume,
      liquid_class=liquid_class,
      input_source=input_source,
      drop_detection=drop_detection,
      consecutive_drops=consecutive_drops,
      max_cycles=max_cycles,
    )
    self._primed_chip = None
    await prime()
    self._primed_chip = chip

  async def _pre_dispense(
    self,
    *,
    arm: Arm,
    pump: Pump,
    states: List[Tuple[int, int]],
    return_moves: List[List[QueuedMove]],
  ) -> None:
    """Play drops at the waste station, from idle: into it along the unit's route, the drops, then
    back to idle (``Arm._get_waste_station_return_moves``).

    Args:
      arm: the arm.
      pump: the pump, started.
      states: the drops' (state, duration) pairs.
      return_moves: the queued moves back, in their groups.
    """
    await arm.move_to_waste_station()
    await pump._play_drops(states=states)
    await arm._play_queued_move_groups(groups=return_moves)

  async def _play_wells(
    self,
    *,
    arm: Arm,
    pump: Pump,
    wells: List[Tuple[List[QueuedMove], List[Tuple[int, int]]]],
    closing_states: List[Tuple[int, int]],
    end_moves: Tuple[List[QueuedMove], List[QueuedMove]],
    well_ends: List[int],
  ) -> None:
    """Queue each well's moves then its drops on the started sequencer, then ``closing_states`` and
    the first group of ``end_moves``, and wait until the last is played; then play the second group.

    If a queue write or the wait fails, the sequencer is aborted so nothing more plays, and where
    the motors stopped is logged.

    Args:
      arm: the arm.
      pump: the pump, started, its pressures prepared.
      wells: each well's queued moves and its drops' (state, duration) pairs.
      closing_states: (state, duration) pairs played after the last well's drops.
      end_moves: the queued moves after the last well, in two groups waited for in turn.
      well_ends: each well's last queued item is appended to it, as it is queued.

    Raises:
      MantisMotionError: if a motor reports an error.
      MantisTimeoutError: if the last item is not reported played within ``WELLS_QUEUE_TIMEOUT``.
    """
    leaving, to_idle = end_moves
    try:
      item = 0
      for moves, states in wells:
        item = await arm._queue_moves(moves=moves) or item
        item = await pump._queue_valve_states(states=states) or item
        well_ends.append(item)
      item = await pump._queue_valve_states(states=closing_states) or item
      item = await arm._queue_moves(moves=leaving) or item
      await self.driver._wait_for_queue_item(item=item, timeout=WELLS_QUEUE_TIMEOUT)
    except BaseException:
      try:
        await self.driver._unchecked_fw_abort_sequencer()
      except Exception:
        logger.warning("[%s] could not abort the sequencer", self.name, exc_info=True)
      await arm._log_where_it_stopped()
      raise
    await arm._play_queued_moves(moves=to_idle)

  async def _end_dispense(
    self, *, plate_clamp: PlateClamp, pump: Pump, pump_started: bool
  ) -> Optional[BaseException]:
    """Release the clamp, then stop the pump if the dispense started it; each step runs, even on an
    interrupt, and the first fault is logged and returned."""
    fault: Optional[BaseException] = None
    try:
      await plate_clamp.unlock()
    except BaseException as error:
      logger.error("[%s] could not release the plate clamp", self.name, exc_info=True)
      fault = error
    if pump_started:
      try:
        await pump.stop_pump()
      except BaseException as error:
        logger.error("[%s] could not stop the pump", self.name, exc_info=True)
        fault = fault or error
    return fault

  async def dispense(
    self,
    *,
    resource: Union[Plate, List[Well]],
    volume: Union[float, Sequence[float]],
    offset: Optional[Coordinate] = None,
    liquid_class: Optional[MantisLiquidClass] = None,
    dispense_height_above_well: Optional[float] = None,
    prime_volume: Optional[float] = None,
    pre_dispense_volume: Optional[float] = None,
    minimum_small_diaphragm_pumps: int = 0,
    precise: bool = False,
    minimum_traverse_height_start: Optional[float] = None,
    minimum_traverse_height_during: Optional[float] = None,
    minimum_traverse_height_end: Optional[float] = None,
  ) -> None:
    """Dispense into wells with the chip on the head, from idle (v4), as in the recorded v4
    dispense.

    The sequencer restarts, the plate clamp locks and the pump starts. A chip not primed is primed
    (``prime_chip``, by ``prime_volume``); the vacuum and the chip take the liquid class's
    pressures, the bottle off; ``pre_dispense_volume`` plays at the waste station, large drops
    first. Then, in the order given, the head moves over each well (``Arm._get_dispense_moves``)
    and its volume plays as small then large drops, the closest split; a volume below half a small
    drop plays none. After the last well the chip's safe state plays and the head goes back to
    idle. The clamp is released and the pump stopped on every path; a failure leaves the head where
    it stopped, for ``stop`` to park. Precise, every well's small drops play first, then
    ``PRECISE_PRE_DISPENSE_LARGE_DROPS`` large drops at the waste station, then every well's large
    drops. Every argument and target is checked before anything moves. The LED bar shows
    ``dispense_color`` meanwhile, fills as the wells are played and blinks when the last is done.

    Args:
      resource: a wellplate, for all its wells, or wells of one wellplate.
      volume: uL per well, one for all or one per well.
      offset: added to each well's target in the wellplate's frame (x right, y back, z up), mm.
      liquid_class: the pressures and diaphragm delays, the prime's too; None takes the chip
        type's at ``WATER_VISCOSITY_BAND``.
      dispense_height_above_well: the nozzle over a well, mm above the wellplate's tallest point;
        None takes ``default_dispense_height_above_well``.
      prime_volume: uL, for a chip not primed; None primes as ``prime_chip`` does by default.
      pre_dispense_volume: uL, at the waste station before the first well; 0 plays none. None
        takes the install's tube volume, precise ``PRECISE_PRE_DISPENSE_VOLUMES``.
      minimum_small_diaphragm_pumps: the small drops each well takes first, at most as many as fit.
      precise: dispense in two rounds: every well's small drops, then every well's large drops.
      minimum_traverse_height_start: mm above the wellplate's base, Z reaches it at the idle
        position before the first well; None goes straight to the well's height.
      minimum_traverse_height_during: mm above the wellplate's base, the travel height between
        wells; None takes ``default_nozzle_clearance_above_wellplate`` above its tallest point.
      minimum_traverse_height_end: mm above the wellplate's base, Z rises to it after the last
        well; None does not.

    Raises:
      ValueError: if an argument is out of range, the volumes and wells differ in number, the wells
        are on more than one wellplate, a traverse height is below the wellplate's tallest point, a
        target is outside its motor's limits, or the liquid class is another chip type's or its
        pressures out of range.
      MantisMotionError: if no chip is on the head, one may be, the head is not at idle, or a motor
        faults.
      MantisConfigurationError: before setup or on a v3; without the plate clamp; for a continuous
        flow chip; without the unit's stage, waste station or idle position route, or the install's
        valve sequences or volumes; or precise, with no pre-dispense volume known for the chip type.
      MantisCoordinateError: if a well is not on a wellplate, the wellplate is not in the plate
        holder, or it is turned.
      MantisKinematicsError: if a well is out of the arm's reach.
      MantisPressureError, MantisPrimeError: as ``prime_chip``, or if a pressure does not settle.
      PlateClampError: if the clamp does not lock or release.
    """
    arm, pump = self._get_arm_and_pump()
    chip = self._get_dispensing_chip()
    plate_clamp = self.driver.plate_clamp
    if plate_clamp is None:
      raise MantisConfigurationError(
        f"{self.name}: a dispense locks the plate clamp, built on a v4"
      )
    wells = resource.get_all_items() if isinstance(resource, Plate) else list(resource)
    if not wells:
      raise ValueError("there is no well to dispense into")
    volumes = list(volume) if isinstance(volume, (list, tuple)) else [volume] * len(wells)
    if len(volumes) != len(wells):
      raise ValueError(f"{len(wells)} wells but {len(volumes)} volumes")
    if any(not well_volume >= 0 for well_volume in volumes):
      raise ValueError(f"volumes must be 0 or above, got {volumes}")
    if not isinstance(minimum_small_diaphragm_pumps, int) or minimum_small_diaphragm_pumps < 0:
      raise ValueError(
        f"minimum_small_diaphragm_pumps must be an int, 0 or above, got "
        f"{minimum_small_diaphragm_pumps!r}"
      )
    if pre_dispense_volume is not None and not pre_dispense_volume >= 0:
      raise ValueError(f"pre_dispense_volume must be 0 or above, got {pre_dispense_volume}")
    if dispense_height_above_well is None:
      dispense_height_above_well = self.default_dispense_height_above_well
    if not dispense_height_above_well > 0:
      raise ValueError(
        f"dispense_height_above_well must be above 0, got {dispense_height_above_well}"
      )
    wellplate = arm._get_wellplate(resource=wells[0])
    if any(arm._get_wellplate(resource=well) is not wellplate for well in wells):
      raise ValueError("the wells are on more than one wellplate")
    if wellplate.parent is not self.plate_holder:
      raise MantisCoordinateError(
        f"{wellplate.name} is not in {self.plate_holder.name}; a dispense goes to the plate holder"
      )
    top = arm._get_wellplate_top(wellplate=wellplate)
    for name, height in (
      ("minimum_traverse_height_start", minimum_traverse_height_start),
      ("minimum_traverse_height_during", minimum_traverse_height_during),
      ("minimum_traverse_height_end", minimum_traverse_height_end),
    ):
      if height is not None and height < top:
        raise ValueError(
          f"{name} {height} mm is below {wellplate.name}'s tallest point, {top} mm above its base"
        )
    liquid_class = self._get_liquid_class(chip=chip, liquid_class=liquid_class)
    if not liquid_class.chip_pressure >= 0:
      raise ValueError(
        "the liquid class's chip_pressure must be 0 or above, in psi, got "
        f"{liquid_class.chip_pressure}"
      )
    if not liquid_class.vacuum <= 0:
      raise ValueError(
        f"the liquid class's vacuum must be 0 or below, in inHg, got {liquid_class.vacuum}"
      )

    # The drops: each well's, the pre-dispense's and the safe state, each from the install.
    small, large = pump._get_dispense_sequences(chip_type=chip.chip_type)
    small_states = pump._get_drop_states(
      chip_type=chip.chip_type, name=small[0], liquid_class=liquid_class, drop="small"
    )
    large_states = pump._get_drop_states(
      chip_type=chip.chip_type, name=large[0], liquid_class=liquid_class, drop="large"
    )
    safe_state = pump._get_drop_states(
      chip_type=chip.chip_type, name="safe_state", liquid_class=liquid_class, drop="safe_state"
    )
    if pre_dispense_volume is None:
      if not precise:
        pre_dispense_volume = pump._get_input_volume(
          chip_type=chip.chip_type,
          input_source="tube",
          use="pre_dispense",
          argument="pre_dispense_volume",
        )
      elif chip.chip_type in PRECISE_PRE_DISPENSE_VOLUMES:
        pre_dispense_volume = PRECISE_PRE_DISPENSE_VOLUMES[chip.chip_type]
      else:
        raise MantisConfigurationError(
          f"{self.name}: no precise pre-dispense volume is known for {chip.chip_type}; pass "
          "pre_dispense_volume"
        )
    pre_large, pre_small = (
      compute_pump_cycle(
        target_volume=pre_dispense_volume,
        large_diaphragm_volume=large[1],
        small_diaphragm_volume=small[1],
        allow_all_zero=False,
      )
      if pre_dispense_volume > 0
      else (0, 0)
    )
    visits: List[Tuple[int, int, int]] = []
    for index, (well, well_volume) in enumerate(zip(wells, volumes)):
      large_drops, small_drops = compute_dispense_drops(
        volume=well_volume,
        large_diaphragm_volume=large[1],
        small_diaphragm_volume=small[1],
        minimum_small_drops=minimum_small_diaphragm_pumps,
      )
      if large_drops + small_drops == 0:
        if well_volume > 0:
          logger.warning(
            "[%s] %s: %g uL is below half a small drop; nothing is dispensed",
            self.name,
            well.name,
            well_volume,
          )
        continue
      delivered = large_drops * large[1] + small_drops * small[1]
      if abs(delivered - well_volume) > small[1] / 2 + 1e-9:
        logger.warning(
          "[%s] %s: %g uL asked, %g uL dispensed in %d large and %d small drops",
          self.name,
          well.name,
          well_volume,
          delivered,
          large_drops,
          small_drops,
        )
      visits.append((index, small_drops, large_drops))
    if not visits:
      logger.warning("[%s] nothing to dispense: every volume is below half a small drop", self.name)
      return
    # Each round's wells, by index, with their small and large drops.
    rounds: List[List[Tuple[int, int, int]]] = [visits]
    if precise:
      rounds = [
        [(index, small_drops, 0) for index, small_drops, _ in visits if small_drops],
        [(index, 0, large_drops) for index, _, large_drops in visits if large_drops],
      ]
      rounds = [visits_of_round for visits_of_round in rounds if visits_of_round]

    # Where the head goes: each well, and the heights between, in the arm frame.
    offset = offset or Coordinate.zero()
    during = (
      top + self.default_nozzle_clearance_above_wellplate
      if minimum_traverse_height_during is None
      else minimum_traverse_height_during
    )
    positions = [
      arm._get_wellplate_position(wellplate=wellplate, resource=well, offset=offset)
      for well in wells
    ]
    locations = [
      arm._get_arm_frame_location(x=x, y=y, height=top + dispense_height_above_well + offset.z)
      for x, y in positions
    ]
    travel_heights = [arm._get_arm_frame_location(x=x, y=y, height=during).z for x, y in positions]

    def get_height(*, index: int, height: Optional[float]) -> Optional[float]:
      if height is None:
        return None
      x, y = positions[index]
      return arm._get_arm_frame_location(x=x, y=y, height=height).z

    planned: List[
      Tuple[
        List[Tuple[List[QueuedMove], List[Tuple[int, int]]]],
        List[Tuple[int, int]],
        Tuple[List[QueuedMove], List[QueuedMove]],
      ]
    ] = []
    for number, visits_of_round in enumerate(rounds):
      first, last = visits_of_round[0][0], visits_of_round[-1][0]
      moves = arm._get_dispense_moves(
        locations=[locations[index] for index, _, _ in visits_of_round],
        travel_heights=[travel_heights[index] for index, _, _ in visits_of_round],
        start_height=get_height(index=first, height=minimum_traverse_height_start),
      )
      final = number == len(rounds) - 1
      end_moves = arm._get_dispense_end_moves(
        last=locations[last],
        end_height=get_height(index=last, height=minimum_traverse_height_end) if final else None,
      )
      well_drops = [
        (well_moves, small_states * small_drops + large_states * large_drops)
        for well_moves, (_, small_drops, large_drops) in zip(moves, visits_of_round)
      ]
      planned.append((well_drops, safe_state if final else [], end_moves))
    station_return = (
      arm._get_waste_station_return_moves() if pre_large + pre_small or len(rounds) > 1 else []
    )
    prime = (
      None
      if self._primed_chip is chip
      else self._get_prime(
        arm=arm,
        pump=pump,
        chip=chip,
        volume=prime_volume,
        liquid_class=liquid_class,
        input_source=None,
        drop_detection=None,
        consecutive_drops=None,
        max_cycles=None,
      )
    )
    settle_timeout = PREPARING_PRESSURE_SETTLE_TIMEOUTS.get(
      chip.chip_type, pump.default_settle_timeout
    )
    if not await arm.request_is_parked():
      raise MantisMotionError(f"{self.name}: a dispense starts from idle; park() first")

    logger.info(
      "[%s] dispensing into %d wells with %s, %g to %g uL%s",
      self.name,
      len(visits),
      chip.name,
      min(volumes),
      max(volumes),
      ", precise" if precise else "",
    )
    async with self.driver._show_action(action="dispense"):
      await self.driver._unchecked_fw_clear_sequencer()
      await self.driver._unchecked_fw_start_sequencer()
      pump_started = False
      bar = self.driver.led_bar
      # Each well's last queued item: the LED bar fills as the sequencer reports them played.
      well_ends: List[int] = []
      fill: Optional["asyncio.Task[None]"] = None
      try:
        await plate_clamp.lock()
        await pump.start_pump()
        pump_started = True
        if prime is not None:
          self._primed_chip = None
          await prime()
          self._primed_chip = chip
        # A prime just set the same pressures, still regulated: they are not set again.
        prime_pressures = (liquid_class.prime_pressure, liquid_class.prime_vacuum)
        if prime is None or prime_pressures != (liquid_class.chip_pressure, liquid_class.vacuum):
          await pump._prepare_pressures(
            chip_pressure=liquid_class.chip_pressure,
            vacuum=liquid_class.vacuum,
            timeout=settle_timeout,
          )
        if pre_large + pre_small:
          logger.info(
            "[%s] pre-dispensing %g uL: %d large and %d small drops",
            self.name,
            pre_dispense_volume,
            pre_large,
            pre_small,
          )
          await self._pre_dispense(
            arm=arm,
            pump=pump,
            states=large_states * pre_large + small_states * pre_small,
            return_moves=station_return,
          )
        if bar is not None and bar.show_actions:
          total = sum(len(well_drops) for well_drops, _, _ in planned)
          fill = asyncio.create_task(bar._follow_fill(well_ends=well_ends, total=total))
        for number, (well_drops, closing_states, end_moves) in enumerate(planned):
          if number > 0:
            await self._pre_dispense(
              arm=arm,
              pump=pump,
              states=large_states * PRECISE_PRE_DISPENSE_LARGE_DROPS,
              return_moves=station_return,
            )
          await self._play_wells(
            arm=arm,
            pump=pump,
            wells=well_drops,
            closing_states=closing_states,
            end_moves=end_moves,
            well_ends=well_ends,
          )
        if fill is not None and bar is not None:
          fill.cancel()
          await asyncio.gather(fill, return_exceptions=True)
          await bar._show_dispense_done()
      except BaseException:
        if fill is not None:
          fill.cancel()
          await asyncio.gather(fill, return_exceptions=True)
        await self._end_dispense(plate_clamp=plate_clamp, pump=pump, pump_started=pump_started)
        raise
      fault = await self._end_dispense(plate_clamp=plate_clamp, pump=pump, pump_started=True)
      if fault is not None:
        raise fault

  async def stop(self) -> None:
    """Drop a chip on the head back into its slot, parking first; then stop the motors, park an
    arm brought up, turn the pump off and vent, release the clamp and disconnect.

    A chip that cannot be dropped is left on the head, with a warning; the rest still runs.
    """
    arm, slot = self.driver.arm, self._attached_chip_slot
    if slot is not None and arm is not None and arm.is_initialized:
      try:
        if not await arm.request_is_parked():
          await arm.park()
        await self.drop_chip(slot=slot)
      except Exception:
        logger.warning(
          "[%s] the chip from slot %d is left on the head", self.name, slot, exc_info=True
        )
    await self.driver.stop()


def Mantis(
  name: str = "Mantis",
  *,
  device_id: Optional[str] = None,
  io: Optional[FTDI] = None,
  rfid_io: Optional[FTDI] = None,
  size_x: float = SIZE_X,
  size_y: float = SIZE_Y,
  size_z: float = SIZE_Z,
  declared_configuration_json: Optional[str] = None,
  oem_config_directory: Optional[str] = None,
  ftdi_library: Optional[str] = None,
  show_actions: bool = True,
  simulation: bool = False,
) -> MantisDevice:
  """A Formulatrix Mantis, v3 or v4.

  Args:
    name: the resource name.
    device_id: the FTDI serial number. None uses the first Mantis found.
    io: an already-built ``FTDI`` for interface B, used instead of building one.
    rfid_io: an already-built ``FTDI`` for interface A, the RFID reader.
    size_x: width, in mm.
    size_y: depth, in mm.
    size_z: height, in mm.
    declared_configuration_json: a file ``save_configuration`` wrote; setup refuses a device that
      disagrees on what is fitted. Simulated, the configuration; None uses ``RECORDING_MANTIS_V4``.
    oem_config_directory: the unit's vendor ``Data/Device/Configs`` folder; where it and the
      declared file both hold a unit value, they must agree.
    ftdi_library: "libftdi" or "d2xx"; None picks D2XX on Windows when FTDI's D2XX library loads,
      else libftdi.
    show_actions: whether the v4's LED bar shows each action's colour, and the idle animation
      between actions.
    simulation: build a simulated Mantis, with no link.

  Raises:
    ValueError: if ``simulation`` is given with ``device_id``, ``io``, ``rfid_io`` or
      ``ftdi_library``.
  """
  return MantisDevice(
    name=name,
    device_id=device_id,
    io=io,
    rfid_io=rfid_io,
    size_x=size_x,
    size_y=size_y,
    size_z=size_z,
    declared_configuration_json=declared_configuration_json,
    oem_config_directory=oem_config_directory,
    ftdi_library=ftdi_library,
    show_actions=show_actions,
    simulation=simulation,
  )
