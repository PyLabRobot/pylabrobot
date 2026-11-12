"""Formulatrix Mantis liquid dispenser as a PyLabRobot resource."""

import logging
import math
import os
from typing import Dict, Optional

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
from .driver.errors import MantisConfigurationError
from .driver.features.arm import Arm
from .driver.features.led_bar import LedBar
from .driver.features.plate_clamp import PlateClamp
from .driver.features.pump import Pump
from .driver.features.rfid import Rfid
from .driver.master import MantisDriver
from .driver.simulator import MantisSimulationDriver, MantisSimulationRfid
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


class MantisDevice(Resource):
  """A Formulatrix Mantis, v3 or v4: its main box, the parts on it and the driver that runs it.

  The main box carries the deck with its plate holder, the two chip racks with a chip spot per input
  slot (``chip_spots``), and the Z column with the arm. The generation is read at setup
  (``driver.configuration.generation``). A chip placed in a chip spot is checked at setup against
  the tag the RFID reader reads in its slot.
  """

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

  def _get_placed_chips(self) -> Dict[int, MantisChip]:
    """The chip placed in each chip spot, by slot; a spot holding none is left out."""
    return {
      slot: spot.resource
      for slot, spot in self.chip_spots.items()
      if isinstance(spot.resource, MantisChip)
    }

  async def _check_chips(self) -> None:
    """Check each chip placed in a chip spot against the tag the RFID reader reads in its slot.

    Nothing is read when no chip is placed; without a reader or its antennas the chips are not
    checked, with a warning. A simulated reader reads the chips placed.

    Raises:
      MantisConfigurationError: if a slot with a chip placed has no antenna, reads no tag, or reads
        a tag of another chip type or none known.
    """
    placed = self._get_placed_chips()
    if not placed:
      return
    rfid = self.driver.rfid
    if rfid is None or rfid.slot_to_antenna is None:
      logger.warning("[%s] no RFID reader or antennas: the chips placed are not checked", self.name)
      return
    if isinstance(rfid, MantisSimulationRfid):
      rfid.chip_types = {slot: chip.chip_type for slot, chip in placed.items()}
    missing = sorted(set(placed) - set(rfid.slot_to_antenna))
    if missing:
      raise MantisConfigurationError(
        f"{self.name}: chips placed in slots {missing}, which have no antenna"
      )
    tags = await rfid.request_tags()
    found = sorted(slot for slot, tag in tags.items() if tag is not None)
    for slot, chip in placed.items():
      tag = tags[slot]
      if tag is None:
        raise MantisConfigurationError(
          f"{self.name}: {chip.name} is placed in slot {slot}, which holds no chip (RFID); chips "
          f"were read in slots {found}"
        )
      if tag["chip_type"] != chip.chip_type:
        raise MantisConfigurationError(
          f"{self.name}: slot {slot} holds chip {tag['serial']} of type {tag['chip_type']!r} "
          f"(RFID code {tag['chip_type_code']}), but {chip.name} of type {chip.chip_type!r} is "
          "placed there"
        )
      logger.info(
        "[%s] slot %d: chip %s, %s (RFID)", self.name, slot, tag["serial"], chip.chip_type
      )

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

  async def stop(self) -> None:
    """Stop the motors, park an arm brought up, turn the pump off and vent, release the clamp and
    disconnect."""
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
