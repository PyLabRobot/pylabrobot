"""Formulatrix Mantis liquid dispenser as a PyLabRobot resource."""

import math
import os
from typing import Any, Dict, Optional, Tuple, Union, cast

from pylabrobot.io.ftdi import FTDI
from pylabrobot.resources import Coordinate, Deck, Resource
from pylabrobot.resources.formulatrix.mantis_chips import MantisChip
from pylabrobot.resources.manipulator import LinkBody
from pylabrobot.resources.resource_holder import ResourceHolder

from .driver.arm_kinematics import (
  LEFT_BASE,
  RIGHT_BASE,
  MantisArmCoordinate,
  compute_elbow_positions,
  compute_head_position,
  compute_joint_angles,
)
from .driver.configuration import RECORDING_MANTIS_V4
from .driver.errors import MantisConfigurationError
from .driver.features.arm import CHIP_CHANGER_ROUTE, Arm
from .driver.features.led_bar import LedBar
from .driver.features.plate_clamp import PlateClamp
from .driver.features.pump import Pump
from .driver.features.rfid import Rfid
from .driver.master import MantisDriver
from .driver.simulator import MantisSimulationDriver
from .liquid_classes import MantisLiquidClass
from .resource_model import (
  ARM_BASE_X,
  ARM_BASE_Y,
  ARM_Z,
  CHIP_SLOT_RACKS,
  MANIFEST,
  PLATE_HOLDER_LOCATION,
  PLATE_HOLDER_SIZE_X,
  PLATE_HOLDER_SIZE_Y,
  SIZE_X,
  SIZE_Y,
  SIZE_Z,
  arm_link,
  chip_rack,
  z_column,
)


class MantisDevice(Resource):
  """A Formulatrix Mantis, v3 or v4: its main box, the parts on it and the driver that runs it.

  The main box carries the deck with its plate holder, the two chip racks with a chip spot per
  input slot (``chip_spots``), and the Z column with the arm. The generation is read at setup
  (``driver.configuration.generation``).
  """

  @staticmethod
  def _get_link_rotations(*, theta_1: float, theta_2: float) -> Dict[str, float]:
    """Each arm link's rotation about z in the device frame, degrees, by link, for two link angles,
    the geometry's own.

    Args:
      theta_1: motor 0's link angle.
      theta_2: motor 1's link angle.
    """
    right_elbow, left_elbow = compute_elbow_positions(theta_1=theta_1, theta_2=theta_2)
    head = compute_head_position(theta_1=theta_1, theta_2=theta_2)
    rotations: Dict[str, float] = {}
    for side, base, elbow in (
      ("right", RIGHT_BASE, right_elbow),
      ("left", LEFT_BASE, left_elbow),
    ):
      base_x, base_y = cast(float, base.x), cast(float, base.y)
      elbow_x, elbow_y = cast(float, elbow.x), cast(float, elbow.y)
      # The device frame mirrors the arm frame in y, so an angle there turns the other way.
      rotations[f"{side}_short_arm"] = -math.degrees(math.atan2(elbow_y - base_y, elbow_x - base_x))
      rotations[f"{side}_long_arm"] = -math.degrees(
        math.atan2(cast(float, head.y) - elbow_y, cast(float, head.x) - elbow_x)
      )
    return rotations

  def _update_arm_model(self, *, theta_1: float, theta_2: float, z: float) -> None:
    """Turn the arm's links to two link angles, the geometry's own, in degrees, and raise or lower
    the Z column with the arm on it to a height.

    Args:
      theta_1: motor 0's link angle; with its offset, the angle the motor reads.
      theta_2: motor 1's link angle; with its offset, the angle the motor reads.
      z: Z's height, in mm above its home flag, up positive.
    """
    column_x, column_y, column_z = MANIFEST["modules"]["z_column"]["measured_at"]
    self.z_column.location = Coordinate(x=column_x, y=column_y, z=column_z + z)
    rotations = self._get_link_rotations(theta_1=theta_1, theta_2=theta_2)
    for side in ("right", "left"):
      short_angle, long_angle = rotations[f"{side}_short_arm"], rotations[f"{side}_long_arm"]
      short, long = self.arm_links[f"{side}_short_arm"], self.arm_links[f"{side}_long_arm"]
      short.rotate_to(z=short_angle, pivot_coordinate=short.proximal_joint)
      long.rotate_to(z=long_angle - short_angle, pivot_coordinate=long.proximal_joint)
    self._state_updated()  # the arm's five-bar, in this state, moved

  def _get_right_long_arm_pose_at_attach_line(self, *, slot: int) -> Tuple[Coordinate, float]:
    """Where the right long link's proximal joint stands relative to the device, and the link's
    rotation about z, with the head at a chip slot's attach line (``Arm._get_attach_line``).

    Args:
      slot: the chip slot.
    """
    arm = cast(Arm, self.driver.arm)
    path = arm._get_chip_slot_path(slot=slot)
    attach_line = arm._get_attach_line(path=path)
    # A line holds an axis it leaves None; the path starts where the chip changer route ends.
    line = CHIP_CHANGER_ROUTE[-1]
    x, y, z = cast(float, line.x), cast(float, line.y), cast(float, line.z)
    for line in path[: attach_line + 1]:
      if line.x is not None and line.y is not None:
        x, y = line.x, line.y
      if line.z is not None:
        z = line.z
    theta_1, theta_2 = compute_joint_angles(location=MantisArmCoordinate(x=x, y=y, z=None))
    rotations = self._get_link_rotations(theta_1=theta_1, theta_2=theta_2)
    short = self.arm_links["right_short_arm"]
    # The short link turns about its proximal joint, the arm's base, which rises and falls with Z
    # (z is Z's position, down from its flag); the long link sits ``above`` its distal joint.
    span = cast(Coordinate, short.distal_joint) - short.proximal_joint
    turned = math.radians(rotations["right_short_arm"])
    base_x, base_y = ARM_BASE_X + cast(float, RIGHT_BASE.x), ARM_BASE_Y - cast(float, RIGHT_BASE.y)
    joint = Coordinate(
      x=base_x + span.x * math.cos(turned) - span.y * math.sin(turned),
      y=base_y + span.x * math.sin(turned) + span.y * math.cos(turned),
      z=ARM_Z - z + span.z + MANIFEST["arm_links"]["right_long_arm"]["above"],
    )
    return joint, rotations["right_long_arm"]

  def _update_chip_model(self, *, slot: int, on_head: bool) -> None:
    """Move a chip slot's chip onto the right long link once it is on the head, keeping the pose
    relative to the link it had in its spot with the head at the slot's attach line, and back into
    its spot once placed. A slot without a chip resource changes nothing.

    Args:
      slot: the chip slot picked up from or placed into.
      on_head: True after a pick-up's attach line, False after a placement's.
    """
    spot, long = self.chip_spots[slot], self.arm_links["right_long_arm"]
    joint, angle = self._get_right_long_arm_pose_at_attach_line(slot=slot)
    turned = math.radians(angle)
    if on_head:
      chip = spot.resource
      if not isinstance(chip, MantisChip):
        return
      offset = chip.get_location_wrt(self) - joint
      location = long.proximal_joint + Coordinate(
        x=offset.x * math.cos(turned) + offset.y * math.sin(turned),
        y=-offset.x * math.sin(turned) + offset.y * math.cos(turned),
        z=offset.z,
      )
      rotation = chip.get_absolute_rotation().z - self.get_absolute_rotation().z - angle
      spot.unassign_child_resource(resource=chip)
      chip.rotation.z = rotation % 360
      long.assign_child_resource(resource=chip, location=location)
      return
    chips = [child for child in long.children if isinstance(child, MantisChip)]
    if not chips or spot.resource is not None:
      return
    chip = chips[0]
    rotation = (
      chip.rotation.z + angle - (spot.get_absolute_rotation().z - self.get_absolute_rotation().z)
    )
    long.unassign_child_resource(resource=chip)
    # A spot takes a chip at a quarter turn only (``ResourceHolder``), as it had there.
    chip.rotation.z = round(rotation / 90) * 90 % 360
    spot.assign_child_resource(resource=chip)

  def serialize_state(self) -> Dict[str, Any]:
    """The resource state, with the arm's five-bar once it has a pose (``Arm._get_five_bar``)."""
    state = super().serialize_state()
    arm = self.driver.arm
    five_bar = arm._get_five_bar(links=self.arm_links) if arm is not None else None
    if five_bar is not None:
      state["five_bar"] = five_bar
    return state

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
    # column is drawn where it stands with Z at 0 and moves with Z, the arm on it.
    self.z_column = z_column(name=f"{name}_z_column")
    self.assign_child_resource(self.z_column, location=measured_at["z_column"])
    self.arm_links: Dict[str, LinkBody] = {}
    for side, base in (("right", RIGHT_BASE), ("left", LEFT_BASE)):
      short = arm_link(name=f"{name}_{side}_short_arm", link=f"{side}_short_arm")
      long = arm_link(name=f"{name}_{side}_long_arm", link=f"{side}_long_arm")
      assert short.distal_joint is not None
      self.z_column.assign_child_resource(
        short,
        location=Coordinate(
          ARM_BASE_X + cast(float, base.x), ARM_BASE_Y - cast(float, base.y), ARM_Z
        )
        - measured_at["z_column"]
        - short.proximal_joint,
      )
      above = MANIFEST["arm_links"][f"{side}_long_arm"]["above"]
      short.assign_child_resource(
        long,
        location=short.distal_joint - long.proximal_joint + Coordinate(0.0, 0.0, above),
      )
      self.arm_links[f"{side}_short_arm"], self.arm_links[f"{side}_long_arm"] = short, long
    self._update_arm_model(theta_1=0.0, theta_2=0.0, z=0.0)

  @property
  def plate_clamp(self) -> Optional[PlateClamp]:
    """The plate clamp, on board 12 or on a pressure-board output; None before discovery or
    without one."""
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
    """The v3 or v4 arm and Z after discovery; None before. Setup initializes it unless skipped."""
    return self.driver.arm

  @property
  def attached_chip(self) -> Optional[MantisChip]:
    """The chip on the arm's head, or None; updated by ``pick_up_chip`` and ``place_chip``."""
    return None if self.driver.arm is None else self.driver.arm.attached_chip

  @property
  def current_chip(self) -> Optional[int]:
    """The chip slot the chip on the arm's head came from, or None."""
    return None if self.driver.arm is None else self.driver.arm.current_chip

  async def pick_up_chip(self, chip_number: Union[int, MantisChip]) -> None:
    """Pick up a chip onto the arm's head.

    Args:
      chip_number: the chip slot, or the chip in one, as ``Arm.pick_up_chip`` takes it.

    Raises:
      MantisConfigurationError: before discovery, or as ``Arm.pick_up_chip``.
    """
    if self.driver.arm is None:
      raise MantisConfigurationError(f"{self.name}: no arm before setup")
    await self.driver.arm.pick_up_chip(chip_number=chip_number)

  async def place_chip(
    self,
    chip_number: int,
    recover_liquid: bool = False,
    *,
    liquid_class: Optional[MantisLiquidClass] = None,
  ) -> None:
    """Place the chip on the arm's head back into its chip slot.

    Args:
      chip_number: the chip slot, as ``Arm.place_chip`` takes it.
      recover_liquid: as ``Arm.place_chip`` takes it.
      liquid_class: as ``Arm.place_chip`` takes it.

    Raises:
      MantisConfigurationError: before discovery, or as ``Arm.place_chip``.
    """
    if self.driver.arm is None:
      raise MantisConfigurationError(f"{self.name}: no arm before setup")
    await self.driver.arm.place_chip(
      chip_number=chip_number, recover_liquid=recover_liquid, liquid_class=liquid_class
    )

  async def setup(self, *, skip_arm: bool = False) -> None:
    """Connect, read the device's configuration and check it, then bring the arm and Z up.

    Args:
      skip_arm: do not bring the arm up; nothing moves.
    """
    await self.driver.setup(skip_arm=skip_arm)
    arm = self.driver.arm
    if arm is not None:
      # Every move from here on passes where it ended to the model; the bring-up's is read here.
      arm._pose_observer = self._update_arm_model
      arm._chip_observer = self._update_chip_model
      arm._chip_spots = self.chip_spots  # a picked-up chip is the one in its spot
      if arm.is_initialized:
        await arm._report_pose()

  async def stop(self, accept_chip_left_on_head: bool = False) -> None:
    """Stop the motors, park an arm brought up, release the clamp and disconnect.

    Args:
      accept_chip_left_on_head: stop with a chip on the head instead of refusing.

    Raises:
      MantisMotionError: if a chip is on the head, unless ``accept_chip_left_on_head``; every motor
        is stopped, the arm stays where it stopped, brought up, and the device stays connected for
        ``place_chip``.
    """
    await self.driver.stop(accept_chip_left_on_head=accept_chip_left_on_head)


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
