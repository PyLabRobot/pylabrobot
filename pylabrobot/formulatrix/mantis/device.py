"""Formulatrix Mantis liquid dispenser as a PyLabRobot resource."""

import logging
import math
import os
from typing import Any, Dict, Optional, Tuple

from pylabrobot.io.ftdi import FTDI
from pylabrobot.resources import Coordinate, Deck, Resource
from pylabrobot.resources.manipulator import LinkBody
from pylabrobot.resources.resource_holder import ResourceHolder

from .driver.arm_kinematics import (
  LEFT_BASE,
  RIGHT_BASE,
  compute_elbow_positions,
  compute_head_position,
)
from .driver.configuration import RECORDING_MANTIS_V4
from .driver.errors import MantisConfigurationError, MantisMotionError
from .driver.features.arm import Arm
from .driver.features.drop_sensor import DropSensor
from .driver.features.led_bar import LedBar
from .driver.features.plate_clamp import PlateClamp
from .driver.features.pump import (
  INPUT_SOURCES,
  MAXIMUM_AUTO_PRIME_CYCLES,
  PREPARING_PRESSURE_SETTLE_TIMEOUTS,
  InputSource,
  Pump,
  compute_pump_cycle,
)
from .driver.features.rfid import Rfid
from .driver.master import MantisDriver
from .driver.simulator import MantisSimulationDriver, MantisSimulationRfid
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


class MantisDevice(Resource):
  """A Formulatrix Mantis, v3 or v4: its main box, the parts on it and the driver that runs it.

  The main box carries the deck with its plate holder, the two chip racks with a chip spot per input
  slot (``chip_spots``), and the Z column with the arm. The generation is read at setup
  (``driver.configuration.generation``). A chip placed in a chip spot is checked at setup against
  the tag the RFID reader reads in its slot. On a v4 a chip is picked up from its slot and dropped
  back into it (``pick_up_chip``, ``drop_chip``); on the head it is a child of the right long link,
  where the long links meet (``attached_chip``), and primed at the waste station (``prime_chip``).
  """

  # Where ``default_consecutive_drops`` starts: the install's value for every chip.
  _default_consecutive_drops: int = 5

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
    checked before anything moves. The LED bar shows "prime_chip" meanwhile.

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
    chip = self._attached_chip
    if chip is None:
      raise MantisMotionError(f"{self.name}: no chip is on the head; pick_up_chip() first")
    if chip.dispense_type != "standard":
      raise MantisConfigurationError(
        f"{self.name}: {chip.name} is a {chip.chip_type} chip; only a chip that dispenses drops is "
        "primed"
      )
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
      logger.info(
        "[%s] priming %s until %d drops in a row, at most %d cycles",
        self.name,
        chip.name,
        consecutive_drops,
        cycles,
      )
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
      logger.info(
        "[%s] priming %s with %g uL: %d small and %d large drops",
        self.name,
        chip.name,
        volume,
        small_drops,
        large_drops,
      )
    post_prime = pump._get_valve_sequence(chip_type=chip.chip_type, name="post_prime")
    return_moves = arm._get_waste_station_return_moves()
    settle_timeout = PREPARING_PRESSURE_SETTLE_TIMEOUTS.get(
      chip.chip_type, pump.default_settle_timeout
    )
    async with self.driver._show_action(action="prime_chip"):
      await arm.move_to_waste_station()
      await pump._prime(
        chip_pressure=liquid_class.prime_pressure,
        vacuum=liquid_class.prime_vacuum,
        settle_timeout=settle_timeout,
        drops=drops,
        post_prime=post_prime,
        drop_sensor=drop_sensor,
        cycles=cycles,
        consecutive_drops=consecutive_drops,
      )
      await arm._play_queued_move_groups(groups=return_moves)

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
