"""A Prep that answers without being plugged in.

The Prep is reached over TCP, so its simulator stands in at the wire: every
command is built into the frame a device would receive, and answered with a frame the real decoder
takes apart. Everything above the link - path resolution, method lookup by name, discovery, the
configuration each feature resolves - runs exactly as it does against hardware.

What answers comes from two recordings, as the STAR simulator's does:

- a saved configuration (`save_configuration`), which says what device this is and what it carries;
- a firmware tree read off a device, which says which objects and methods its firmware has. A method
  the recorded firmware lacks is refused as that firmware refuses it, so the version is selectable.

Reads of where things are come from the resource model, and moves update it: a simulated device keeps
no positions of its own. A read nothing here answers is refused with an exception frame, which is how
a command that has not been simulated makes itself known.
"""

import asyncio
import dataclasses
import json
import logging
import math
import os
from typing import Any, Dict, List, Optional, Set, Tuple, cast, get_type_hints

from pylabrobot.hamilton.transport.tcp.commands import TCPCommand
from pylabrobot.hamilton.transport.tcp.error_tables import HC_RESULT_PROTOCOL
from pylabrobot.hamilton.transport.tcp.introspection import (
  GetEnumsCommand,
  GetInterfacesCommand,
  GetMethodCommand,
  GetObjectCommand,
  GetStructsCommand,
  GetSubobjectAddressCommand,
)
from pylabrobot.hamilton.transport.tcp.messages import (
  CommandResponse,
  HoiParams,
  hoi_action_code_base,
)
from pylabrobot.hamilton.transport.tcp.packets import Address, HarpPacket, HoiPacket, IpPacket
from pylabrobot.hamilton.transport.tcp.protocol import Hoi2Action
from pylabrobot.hamilton.transport.tcp.session import SessionState, TCPSession
from pylabrobot.hamilton.transport.tcp.tcp import HamiltonTCPClient
from pylabrobot.hamilton.transport.tcp.wire_types import U32, PaddedBool, Str, Struct, wire_type_of
from pylabrobot.io.socket import Socket
from pylabrobot.resources import Coordinate, Resource
from pylabrobot.resources.deck import Deck
from pylabrobot.resources.tip import Tip

from . import prep_commands as PrepCmd
from .configuration import DeviceConfiguration
from .errors import PREP_ERROR_CODES
from .features.core_grippers import JAW_OPEN_EXTRA
from .features.lights import Lights
from .features.pipettes import Pipettes, PipettesConfiguration
from .features.x_arm import XArm
from .master import PrepDriver, _ResolvedPrepCommand
from .prep_commands import MPH_OBJECT_PATH, PrepCommand

logger = logging.getLogger(__name__)


# What stands where a transport's identity would be in the log, so simulated and recorded runs read
# the same way.
SIMULATED_LINK = "[simulation]"

_RECORDINGS = os.path.join(os.path.dirname(__file__), "recordings")

# The recorded device as it saved itself (MLPrep Runtime V1.2.2): a simulated Prep unless told
# otherwise.
RECORDING_PREP = os.path.join(_RECORDINGS, "prep_PRPAA1087_v1_2_2.json")
# The same, declared with an 8-channel head. No device with one has been recorded.
RECORDING_PREP_HEAD8 = os.path.join(_RECORDINGS, "prep_PRPAA1087_v1_2_2_head8.json")

# The firmware trees read off two devices. V1.2.2 is what a simulated Prep runs unless told otherwise.
FIRMWARE_TREE_V1_2_2 = os.path.join(_RECORDINGS, "prep_PRPAA1087_v1_2_2_firmware_tree.json")
FIRMWARE_TREE_V3_0_20 = os.path.join(_RECORDINGS, "prep_PRPBD1394_v3_0_20_firmware_tree.json")

# Where the recorded device's channels reported themselves after initializing, as (x, y, z) in mm,
# by channel: what a simulated device answers until the resource model holds the channels.
SIMULATED_INITIALIZED_POSITIONS = {
  0: (289.489, 365.0148, 167.499),
  1: (289.489, 345.0123, 167.4954),
}

# Each channel's Y drive frame reads deck Y plus this, rear first, as measured on the device.
SIMULATED_Y_DRIVE_OFFSETS = (112.36, 102.451)
# Each channel's Z drive frame above the reported Z (rear, front), as the device's sweep read.
SIMULATED_Z_DRIVE_OFFSETS = (171.784, 171.091)
# What the device's Z drives read for the PWM that limits how hard they push.
SIMULATED_Z_DRIVE_PWM = 125
# What a simulated channel touches with in a cLLD search, in mm: the probes' default
# `stop_disc_diameter`, as simulated channels hold no tips.
SIMULATED_CLLD_PROBE_DIAMETER = 7.0
# The reported X less the X axis's own position, in mm, as on the device (V1.2.2).
SIMULATED_X_AXIS_OFFSET = 0.193
# Each axis's speed in mm/s and acceleration in mm/s2, as timed on the device (X at a speed scale
# of 100 percent). A simulated axis keeps no profile: setting one changes nothing it answers.
SIMULATED_X_SPEED, SIMULATED_X_ACCELERATION = 400.0, 2250.0
SIMULATED_Y_SPEED, SIMULATED_Y_ACCELERATION = 345.0, 950.0
SIMULATED_Z_SPEED, SIMULATED_Z_ACCELERATION = 142.0, 800.0
_PROFILES = (
  (SIMULATED_X_SPEED, SIMULATED_X_ACCELERATION),
  (SIMULATED_Y_SPEED, SIMULATED_Y_ACCELERATION),
  (SIMULATED_Z_SPEED, SIMULATED_Z_ACCELERATION),
)

# Each channel's TADM pressure at rest, in sensor counts (rear, front), as the device read it.
SIMULATED_TADM_PRESSURES = (-4384, -4350)
# The TADM buffer's size in entries and its sample rate, as the device reported them.
SIMULATED_TADM_BUFFER_SIZE, SIMULATED_TADM_SAMPLE_RATE = 20000, 10

# The speed scales the device read, in percent. A simulated device keeps none.
SIMULATED_SPEED_SCALE = 100

# What the deck light reads: off. A simulated device has no light.
SIMULATED_DECK_LIGHT = (0, 0, 0, 0)

# The firmware's result for a method it does not have (HC_RESULT GenericNotSupported).
_NOT_SUPPORTED = next(
  code for code, name in HC_RESULT_PROTOCOL.items() if name == "GenericNotSupported"
)

# Where the 8-channel head's objects are put in a recorded tree, which has none: no head has been
# recorded, so these addresses are not a device's.
_MPH_ROOT_ADDRESS = Address(0xE001, 1, 0xBF00)
_MPH_ADDRESS = Address(0xE001, 1, 0x1000)


def _encode(response: Any) -> HoiParams:
  """Encode a response dataclass field by field, the inverse of `parse_into_struct`.

  Args:
    response: an instance of a command's `Response`, whose fields carry their wire types.

  Returns:
    The parameters a device would answer with.
  """
  params = HoiParams()
  hints = get_type_hints(type(response), include_extras=True)
  for field in dataclasses.fields(response):
    meta = wire_type_of(hints.get(field.name))
    if meta is not None:
      meta.encode_into(getattr(response, field.name), params)
  return params


@dataclasses.dataclass
class _RecordedObject:
  """One object of a recorded firmware tree."""

  path: str
  name: str
  version: str
  address: Address
  interfaces: List[Tuple[int, str]]
  # As recorded: name, interface_id, method_id, call_type, and the full GetMethod answer in hex.
  methods: List[Dict[str, Any]]
  children: List["_RecordedObject"]

  def method(self, interface_id: int, method_id: int) -> Optional[Dict[str, Any]]:
    """The recorded method at these ids, or None when this firmware has none there."""
    return next(
      (m for m in self.methods if (m["interface_id"], m["method_id"]) == (interface_id, method_id)),
      None,
    )


class _RecordedTree:
  """A firmware tree read off a device, which a simulated device answers introspection from."""

  def __init__(self, path: str, head8_installed: bool):
    """
    Args:
      path: a recorded firmware tree.
      head8_installed: whether to add the 8-channel head's objects, which no recorded tree holds.
    """
    with open(path, encoding="utf-8") as f:
      recorded = json.load(f)
    self.firmware_version: Optional[str] = recorded.get("firmware_version")
    self.root = self._read(recorded["tree"])
    if head8_installed and not any(c.name == "MphRoot" for c in self.root.children):
      self.root.children.append(self._head8_objects())
    self._by_address: Dict[Address, _RecordedObject] = {}
    self._index(self.root)

  @classmethod
  def _read(cls, node: Dict[str, Any]) -> _RecordedObject:
    return _RecordedObject(
      path=node["path"],
      name=node["name"],
      version=node["version"],
      address=Address(**node["address"]),
      interfaces=[(i["interface_id"], i["name"]) for i in node["interfaces"]],
      methods=list(node["methods"]),
      children=[cls._read(child) for child in node["children"]],
    )

  def _index(self, node: _RecordedObject) -> None:
    self._by_address[node.address] = node
    for child in node.children:
      self._index(child)

  def _head8_objects(self) -> _RecordedObject:
    """`MphRoot.MPH`, carrying the root's recorded introspection methods and the commands the driver
    sends the head, at the ids it sends them at."""
    introspection = [m for m in self.root.methods if m["interface_id"] == 0]
    commands: Dict[Tuple[int, int], str] = {}
    pending = list(PrepCommand.__subclasses__())
    while pending:
      cls = pending.pop()
      pending.extend(cls.__subclasses__())
      if cls.firmware_path == MPH_OBJECT_PATH and cls.command_id is not None:
        commands[(cls.interface_id, cls.command_id)] = cls.__name__
    methods = introspection + [
      {
        "name": name,
        "interface_id": interface_id,
        "method_id": method_id,
        "call_type": 0,
        "raw_metadata_hex": HoiParams()
        .add(interface_id, PrepCmd.PaddedU8)
        .add(0, PrepCmd.PaddedU8)
        .add(method_id, PrepCmd.U16)
        .add(name, Str)
        .build()
        .hex(),
      }
      for (interface_id, method_id), name in sorted(commands.items())
    ]
    mph = _RecordedObject(
      path=MPH_OBJECT_PATH,
      name="MPH",
      version="",
      address=_MPH_ADDRESS,
      interfaces=list(self.root.interfaces),
      methods=methods,
      children=[],
    )
    return _RecordedObject(
      path=MPH_OBJECT_PATH.rsplit(".", 1)[0],
      name="MphRoot",
      version="",
      address=_MPH_ROOT_ADDRESS,
      interfaces=list(self.root.interfaces),
      methods=introspection,
      children=[mph],
    )

  def get(self, address: Address) -> Optional[_RecordedObject]:
    """The object at an address, or None when the recorded firmware has none there."""
    return self._by_address.get(address)

  def channel_of(self, address: Address) -> Optional[int]:
    """Which pipetting channel an object belongs to, 0-indexed from the back, or None for none.

    Each channel's objects share its node, and the tree lists the channel roots rear first.
    """
    roots = [child for child in self.root.children if child.name == "Channel Root"]
    return next(
      (index for index, root in enumerate(roots) if root.address.node == address.node), None
    )

  def answer_introspection(self, node: _RecordedObject, request: TCPCommand) -> Optional[HoiParams]:
    """What an object answers an interface-0 request with, from the recording.

    Returns:
      The answer, or None for a request this does not know.
    """
    if isinstance(request, GetObjectCommand):
      # Only the children the recording holds are counted, so every slot asked for is one there is.
      return _encode(
        GetObjectCommand.Response(
          name=node.name,
          version=node.version,
          method_count=len(node.methods),
          subobject_count=len(node.children),
        )
      )
    if isinstance(request, GetMethodCommand):
      params = HoiParams()
      params._fragments.append(
        bytes.fromhex(node.methods[request.method_index]["raw_metadata_hex"])
      )
      return params
    if isinstance(request, GetSubobjectAddressCommand):
      child = node.children[request.subobject_index].address
      return _encode(
        GetSubobjectAddressCommand.Response(
          module_id=child.module, node_id=child.node, object_id=child.object
        )
      )
    if isinstance(request, GetInterfacesCommand):
      return _encode(
        GetInterfacesCommand.Response(
          interface_ids=[i for i, _ in node.interfaces],
          interface_names=[name for _, name in node.interfaces],
        )
      )
    # The recordings hold no enums or structs.
    if isinstance(request, GetEnumsCommand):
      return _encode(
        GetEnumsCommand.Response(enum_names=[], value_counts=[], values=[], value_names=[])
      )
    if isinstance(request, GetStructsCommand):
      return _encode(
        GetStructsCommand.Response(
          struct_names=[], field_counts=[], field_type_ids=[], field_names=[]
        )
      )
    return None


# What the device answered while its plate-held latch was set: the Pipettor refuses to drop the
# tools, and MLPrep to initialize, whose own attempt to drop them is refused the same way.
_TOOLS_HELD_BY_A_PLATE = "0xE000.0x0001.0x1000:0x01,0x0010,0x0F04"
_INITIALIZE_WITH_A_PLATE = "0x0001.0x0001.0x2000:0x01,0x0001,0x0F0A;" + _TOOLS_HELD_BY_A_PLATE


class _DeviceRefuses(Exception):
  """A simulated answer that is the device's exception, as its entries."""

  def __init__(self, entries: str):
    super().__init__(entries)
    self.entries = entries


class _Simulated:
  """Reaches the device behind a feature, which for a simulated one is the simulator."""

  _driver: PrepDriver

  @property
  def device(self) -> "PrepSimulationDriver":
    return cast("PrepSimulationDriver", self._driver)

  async def answer(self, request: TCPCommand, path: str, method: str) -> Optional[Tuple[Any, str]]:
    """What this feature would answer a command with, taken from the model.

    The link offers every command to each feature in turn, so a read is built and logged exactly as a
    move is. A move is answered with nothing, and moves the model as the device would have moved.

    Args:
      request: the command, as the driver sent it.
      path: the firmware path of the object it was sent to.
      method: the name the recorded firmware gives the method it was sent to.

    Returns:
      The answer - the command's `Response`, or the parameters of a read by name - and where in the
      model it came from. None when this feature does not answer that command.
    """
    return None


# The commands that move the two CoRe gripper tool channels.
_GRIPPER_MOVES = (
  PrepCmd.PrepPickUpTool,
  PrepCmd.PrepDropTool,
  PrepCmd.PrepPickUpPlate,
  PrepCmd.PrepMovePlate,
  PrepCmd.PrepDropPlate,
  PrepCmd.PrepReleasePlate,
)

# Each firmware channel enum's channel, 0-indexed from the back.
_CHANNEL_INDEX = {int(enum): index for index, enum in enumerate(PrepCmd.channel_order_legacy_prep)}


def _channel_index(enum: Any) -> Optional[int]:
  """The channel a firmware channel enum names, or None for one this device has not."""
  return _CHANNEL_INDEX.get(int(enum))


def _first_contact_along(
  start: float,
  end: float,
  across: float,
  bottom_z: float,
  axis: int,
  boxes: List[Tuple[Resource, Coordinate, Coordinate]],
  radius: float,
) -> Optional[float]:
  """Where a channel moving along one horizontal axis first touches a box.

  Args:
    start: the channel's centre where it starts, in mm.
    end: the channel's centre where it would end, in mm.
    across: the channel's centre on the other horizontal axis, in mm.
    bottom_z: the channel's lowest point, in mm.
    axis: 0 to move along X, 1 along Y.
    boxes: what can be touched, with lower and upper corners on the deck.
    radius: the channel's radius where it touches, in mm.

  Returns:
    The channel's centre at the first touch, in mm, or None.
  """
  other = 1 - axis
  first, last = sorted((start, end))
  contacts: List[float] = []
  for _, low, high in boxes:
    lows, highs = (low.x, low.y), (high.x, high.y)
    if high.z <= bottom_z or not lows[other] - radius <= across <= highs[other] + radius:
      continue
    near, far = lows[axis] - radius, highs[axis] + radius
    if near <= start <= far:
      contacts.append(start)
    elif end < start and first <= far <= last:
      contacts.append(far)
    elif end > start and first <= near <= last:
      contacts.append(near)
  if not contacts:
    return None
  return max(contacts) if end < start else min(contacts)


def _first_contact_below(
  start_z: float,
  floor_z: float,
  x: float,
  y: float,
  boxes: List[Tuple[Resource, Coordinate, Coordinate]],
  radius: float,
) -> Optional[float]:
  """Where a channel's lowest point, moving down, first touches the top of a box.

  Args:
    start_z: where the lowest point starts, in mm.
    floor_z: how low it may go, in mm.
    x: the channel's centre along X, in mm.
    y: the channel's centre along Y, in mm.
    boxes: what can be touched, with lower and upper corners on the deck.
    radius: the channel's radius where it touches, in mm.

  Returns:
    The lowest point's height at the first touch, in mm, or None.
  """
  tops: List[float] = []
  for _, low, high in boxes:
    if not (low.x - radius <= x <= high.x + radius and low.y - radius <= y <= high.y + radius):
      continue
    if low.z < start_z < high.z:
      tops.append(start_z)
    elif floor_z <= high.z <= start_z:
      tops.append(high.z)
  return max(tops) if tops else None


class SimulatedPipettes(_Simulated, Pipettes):
  """The pipetting channels, answering for themselves.

  A cLLD search stops at the first resource on the deck in the channel's way.
  """

  def __init__(self, *args: Any, **kwargs: Any) -> None:
    super().__init__(*args, **kwargs)
    # Channels with continuous cLLD on, and whether each detected since its last start. A detection
    # is still reported after detection stops, as on the device.
    self._clld_on: Set[int] = set()
    self._clld_detected: Dict[int, bool] = {}
    self._z_drive_pwm: Dict[int, int] = {}
    # The pipettor's record of a plate held: set by a finished pick-up whatever the jaws closed on,
    # cleared by a drop or a release.
    self._plate_held = False
    # What a grip leaves for the carry and the let-go (the jaws' offset below the top, how far they
    # closed), and where the tools came from, which is where the firmware returns them.
    self._grip: Optional[Tuple[float, float]] = None
    self._tools_from: Optional[Tuple[float, float, float]] = None

  def _touchable(self) -> List[Tuple[Resource, Coordinate, Coordinate]]:
    """The deck's resources a channel can touch.

    Everything with a volume, apart from the arm and what it carries.

    Returns:
      Each resource with its lower and upper corner on the deck.
    """
    deck = self.device.deck
    if deck is None:
      return []
    arm = self.device.x_arm.resource if self.device.x_arm is not None else None
    carried: Set[int] = set()
    for held in ([arm] if arm is not None else []) + list(self.resources):
      carried.add(id(held))
      carried.update(id(child) for child in held.get_all_children())
    boxes = []
    for resource in deck.get_all_children():
      if id(resource) in carried or resource.location is None:
        continue
      size = Coordinate(
        resource.get_absolute_size_x(),
        resource.get_absolute_size_y(),
        resource.get_absolute_size_z(),
      )
      if min(size.x, size.y, size.z) <= 0:
        continue
      low = resource.get_location_wrt(deck)
      boxes.append((resource, low, low + size))
    return boxes

  def _bottom_offset(self, channel: int) -> float:
    """How far a channel's lowest modelled point is below its reference point.

    Args:
      channel: which channel, 0-indexed from the back.

    Returns:
      The offset in mm, 0 or negative.
    """
    deck = self.device.deck
    point = self.get_reference_point_location(channel)
    if deck is None or point is None or channel >= len(self.resources):
      return 0.0
    held = self.resources[channel]
    lowest = min(
      (
        part.get_location_wrt(deck).z
        for part in [held, *held.get_all_children()]
        if part.location is not None
      ),
      default=point.z,
    )
    return min(lowest - point.z, 0.0)

  def _touched_along(
    self, channel: int, axis: int, start: float, end: float, across: float, z: float
  ) -> Optional[float]:
    """Where a channel searching along X or Y first touches a resource.

    Args:
      channel: which channel, 0-indexed from the back.
      axis: 0 to search along X, 1 along Y.
      start: the channel's centre where it starts, in mm.
      end: the channel's centre where the search ends, in mm.
      across: the channel's centre on the other horizontal axis, in mm.
      z: the channel's reference point height, in mm.

    Returns:
      The channel's centre at the first touch, in mm, or None.
    """
    return _first_contact_along(
      start,
      end,
      across,
      z + self._bottom_offset(channel),
      axis,
      self._touchable(),
      SIMULATED_CLLD_PROBE_DIAMETER / 2,
    )

  def sensed_x_move(self, x_from: float, x_to: float) -> float:
    """Where an X move ends when a channel with detection on touches a resource.

    The detection is recorded on that channel.

    Args:
      x_from: the arm's reference point before the move, in mm.
      x_to: where the move would take it, in mm.

    Returns:
      Where the move ends, in mm.
    """
    for channel in sorted(self._clld_on):
      if self._clld_detected.get(channel):
        continue
      _, y, z = self._modelled_location(channel)
      touched = self._touched_along(channel, 0, x_from, x_to, y, z)
      if touched is not None:
        self._clld_detected[channel] = True
        x_to = touched
    return x_to

  async def _search_x_using_clld(
    self,
    channel_idx: int,
    here: float,
    end: float,
    detect_mode: int,
    sensitivity: int,
  ) -> Optional[float]:
    """Look the whole search up in the resource model, and move the arm once to where it ends.

    Args:
      channel_idx: detecting channel, 0-indexed from the back.
      here: the arm's x where the search starts, in mm.
      end: search end in mm.
      detect_mode: cLLD detect mode; not modelled.
      sensitivity: cLLD sensitivity; not modelled.

    Returns:
      The channel's x where it touches a resource, in mm, or None.
    """
    arm = self._driver.x_arm
    if arm is None:
      raise RuntimeError("no X arm to move; have you called `prep.setup()`?")
    _, y, z = self._modelled_location(channel_idx)
    touched = self._touched_along(channel_idx, 0, here, end, y, z)
    await arm.move_to_x_position(
      end if touched is None else touched, minimum_traverse_height_start=z
    )
    return touched

  def _declared(self) -> PipettesConfiguration:
    """What this device was told its channels are."""
    return self.device.simulated_pipettes or PipettesConfiguration()

  def _modelled_location(self, channel: int) -> Tuple[float, float, float]:
    """Where the model has one channel, as (x, y, z) in mm on the deck.

    Args:
      channel: which channel, 0-indexed from the back.

    Returns:
      Where the model has it, or where the device reported it after initializing while nothing
      models it.
    """
    x, y, z = SIMULATED_INITIALIZED_POSITIONS[channel]
    point = self.get_reference_point_location(channel)
    if point is not None:
      y, z = point.y, point.z
    return self.device.modelled_x(default=x), y, z

  def _move(self, channel: int, x: Optional[float], y: Optional[float], z: Optional[float]) -> None:
    """Record a move on the model: X on the arm, Y and Z on the channel."""
    arm = self.device.x_arm
    if x is not None and arm is not None:
      arm.update_location_by_reference_point(x)
    self.update_location_by_reference_point(channel, y=y, z=z)

  def _gripper_channels(self) -> Tuple[int, int]:
    """The back and front channels the CoRe gripper tools ride: the two front-most."""
    count = self.device.simulated_configuration.num_channels or 0
    return count - 2, count - 1

  def _owner(self, request: TCPCommand) -> Optional[int]:
    """The channel the object a request is sent to belongs to, or None."""
    return self.device.tree.channel_of(request.dest)

  async def answer(self, request: TCPCommand, path: str, method: str) -> Optional[Tuple[Any, str]]:
    # Each group answers its own commands, and none shares one with another.
    for answer in (
      self._answer_grippers,
      self._answer_moves,
      self._answer_seeks,
      self._answer_drive_settings,
      self._answer_probes,
    ):
      answered = answer(request, method)
      if answered is not None:
        return answered
    return None

  def _answer_grippers(self, request: TCPCommand, method: str) -> Optional[Tuple[Any, str]]:
    """The plate-held latch, and where each CoRe gripper command leaves the two tool channels.

    Where the device reported them after each command; Z at the jaws once the tools are on.
    """
    if isinstance(request, PrepCmd.PrepGetPlateHeld):
      return PrepCmd.PrepGetPlateHeld.Response(value=self._plate_held), "the pipettor's record"
    if not isinstance(request, _GRIPPER_MOVES):
      return None
    back, front = self._gripper_channels()
    if isinstance(request, PrepCmd.PrepPickUpTool):
      # Left in the tools, shafts at the seek height; the tools go on the model after.
      self._tools_from = (
        request.tool_position_x,
        request.rear_channel_position_y,
        request.front_channel_position_y,
      )
      self._move(back, request.tool_position_x, request.rear_channel_position_y, request.tool_seek)
      self._move(front, None, request.front_channel_position_y, request.tool_seek)
    elif isinstance(request, PrepCmd.PrepDropTool):
      if self._plate_held:
        raise _DeviceRefuses(_TOOLS_HELD_BY_A_PLATE)
      # Taken home from anywhere, shafts at the traverse height; the tools come off the model after.
      height = self.device.simulated_default_minimum_traverse_height
      if self._tools_from is not None and height is not None:
        x, rear_y, front_y = self._tools_from
        self._move(back, x, rear_y, height - self._mounted_length(back))
        self._move(front, None, front_y, height - self._mounted_length(front))
      self._tools_from = None
    elif isinstance(request, PrepCmd.PrepPickUpPlate):
      # Down to the grip height, jaws closed on the plate; the firmware does not lift it.
      plate_top = request.plate_top_center
      half = (request.plate.width + JAW_OPEN_EXTRA - 2 * request.grip_distance) / 2
      self._grip = (plate_top.z_position - request.grip_height, request.grip_distance)
      self._move(back, plate_top.x_position, plate_top.y_position + half, request.grip_height)
      self._move(front, None, plate_top.y_position - half, request.grip_height)
      self._plate_held = True
    elif isinstance(request, (PrepCmd.PrepMovePlate, PrepCmd.PrepDropPlate)):
      # Both jaws to the top less the grip's offset, spacing kept; a drop then opens them by the
      # clearance and how far they closed.
      plate_top = request.plate_top_center
      half = (self._modelled_location(back)[1] - self._modelled_location(front)[1]) / 2
      z = None if self._grip is None else plate_top.z_position - self._grip[0]
      if isinstance(request, PrepCmd.PrepDropPlate):
        half += request.clearance_y + (0.0 if self._grip is None else self._grip[1])
        self._grip = None
        self._plate_held = False
      self._move(back, plate_top.x_position, plate_top.y_position + half, z)
      self._move(front, None, plate_top.y_position - half, z)
    elif isinstance(request, PrepCmd.PrepReleasePlate):
      # What the jaws do is unmeasured: they are left where they are.
      self._grip = None
      self._plate_held = False
    return None

  def _answer_moves(self, request: TCPCommand, method: str) -> Optional[Tuple[Any, str]]:
    """Where the channels are, and the moves that take them elsewhere."""
    if isinstance(request, PrepCmd.PrepGetPositions):
      positions = []
      for channel in range(self.device.simulated_configuration.num_channels or 0):
        x, y, z = self._modelled_location(channel)
        positions.append(
          PrepCmd.ChannelXYZPositionParameters(
            default_values=False,
            channel=PrepCmd.channel_order_legacy_prep[channel],
            position_x=x,
            position_y=y,
            position_z=z,
          )
        )
      return PrepCmd.PrepGetPositions.Response(
        positions=positions
      ), "where the model has the channels"
    if isinstance(request, (PrepCmd.PrepMoveToPosition, PrepCmd.PrepMoveToPositionViaLane)):
      move = request.move_parameters
      for target in move.axis_parameters:
        self._move_channel(
          target.channel, move.gantry_x_position, target.y_position, target.z_position
        )
    elif isinstance(request, PrepCmd.PrepMoveYAbsolute):
      for y_target in request.channels:
        self._move_channel(y_target.channel, None, y_target.y_position, None)
    elif isinstance(request, PrepCmd.PrepMoveZAbsolute):
      for z_target in request.channels:
        self._move_channel(z_target.channel, None, None, z_target.z_position)
    elif isinstance(request, PrepCmd.PrepMoveZUpToSafe):
      # The shaft's end goes to the traverse height; what it carries reaches below that.
      height = self.device.simulated_default_minimum_traverse_height
      for enum in request.channels:
        raised = _channel_index(enum)
        if raised is not None and height is not None:
          self._move(raised, None, None, height - self._mounted_length(raised))
    return None

  def _move_channel(
    self, enum: Any, x: Optional[float], y: Optional[float], z: Optional[float]
  ) -> None:
    """`_move` for the channel a firmware channel enum names; nothing for one this has not."""
    channel = _channel_index(enum)
    if channel is not None:
      self._move(channel, x, y, z)

  def _answer_seeks(self, request: TCPCommand, method: str) -> Optional[Tuple[Any, str]]:
    """The cLLD and obstacle searches, each stopping at the first resource in the way."""
    if isinstance(request, PrepCmd.PrepZSeekLldPosition):
      # Each channel seeks down toward its floor and ends at the higher of its final height and
      # where it stopped, as the firmware does.
      results = []
      found = False
      for seek in request.seek_parameters:
        seeking = _channel_index(seek.channel)
        touched = None
        if seeking is not None:
          self._move(seeking, seek.seek_position_x, seek.seek_position_y, seek.seek_height)
          offset = self._bottom_offset(seeking)
          top = _first_contact_below(
            seek.seek_height + offset,
            seek.min_seek_height + offset,
            seek.seek_position_x,
            seek.seek_position_y,
            self._touchable(),
            SIMULATED_CLLD_PROBE_DIAMETER / 2,
          )
          touched = None if top is None else top - offset
          stopped = seek.min_seek_height if touched is None else touched
          self._move(seeking, None, None, max(seek.final_position_z, stopped))
        found = found or touched is not None
        results.append(
          PrepCmd.SeekResultParameters(
            default_values=False,
            channel=seek.channel,
            detected=touched is not None,
            position=seek.min_seek_height if touched is None else touched,
          )
        )
      return PrepCmd.PrepZSeekLldPosition.Response(results=results), (
        "the resource model" if found else "nothing in the way"
      )

    if isinstance(request, (PrepCmd.PrepYDriveGetPosition, PrepCmd.PrepYAxisSeekCapacitiveLld)):
      owner = self._owner(request)
      if owner is None or owner >= len(SIMULATED_Y_DRIVE_OFFSETS):
        return None
      offset = SIMULATED_Y_DRIVE_OFFSETS[owner]
      if isinstance(request, PrepCmd.PrepYDriveGetPosition):
        return PrepCmd.PrepYDriveGetPosition.Response(
          position=self._modelled_location(owner)[1] + offset
        ), f"channel {owner}'s modelled Y in its drive frame"
      x, y, z = self._modelled_location(owner)
      end = request.position - offset
      touched = self._touched_along(owner, 1, y, end, x, z)
      self._move(owner, None, end if touched is None else touched, None)
      return PrepCmd.PrepYAxisSeekCapacitiveLld.Response(
        lld_detected=touched is not None,
        detect_position=0.0 if touched is None else touched + offset,
      ), "the resource model" if touched is not None else "nothing in the way"

    if isinstance(
      request,
      (
        PrepCmd.PrepZDriveGetPosition,
        PrepCmd.PrepZAxisSeekObstacle,
        PrepCmd.PrepZAxisSeekCapacitiveLld,
      ),
    ):
      owner = self._owner(request)
      if owner is None or owner >= len(SIMULATED_Z_DRIVE_OFFSETS):
        return None
      offset = SIMULATED_Z_DRIVE_OFFSETS[owner]
      x, y, z = self._modelled_location(owner)
      if isinstance(request, PrepCmd.PrepZDriveGetPosition):
        return PrepCmd.PrepZDriveGetPosition.Response(
          position=z + offset
        ), f"channel {owner}'s modelled Z in its drive frame"
      if isinstance(request, PrepCmd.PrepZAxisSeekCapacitiveLld):
        # Down from where it stands to the first resource under it, and left there.
        bottom = self._bottom_offset(owner)
        end = request.position - offset
        top = _first_contact_below(
          z + bottom, end + bottom, x, y, self._touchable(), SIMULATED_CLLD_PROBE_DIAMETER / 2
        )
        touched = None if top is None else top - bottom
        self._move(owner, None, None, end if touched is None else touched)
        return PrepCmd.PrepZAxisSeekCapacitiveLld.Response(
          lld_detected=touched is not None,
          detect_position=0.0 if touched is None else touched + offset,
        ), "the resource model" if touched is not None else "nothing in the way"
      # Down from its start to the first resource under it, then left at its final height.
      bottom = self._bottom_offset(owner)
      top = _first_contact_below(
        request.start_position - offset + bottom,
        request.end_position - offset + bottom,
        x,
        y,
        self._touchable(),
        SIMULATED_CLLD_PROBE_DIAMETER / 2,
      )
      touched = None if top is None else top - bottom
      self._move(owner, None, None, request.final_position - offset)
      return PrepCmd.PrepZAxisSeekObstacle.Response(
        obstacle_detected=touched is not None,
        position=0.0 if touched is None else touched + offset,
      ), "the resource model" if touched is not None else "nothing in the way"

    if isinstance(
      request, (PrepCmd.PrepChannelStartCLldDetection, PrepCmd.PrepChannelStopCLldDetection)
    ):
      owner = self._owner(request)
      if owner is not None:
        if isinstance(request, PrepCmd.PrepChannelStartCLldDetection):
          self._clld_on.add(owner)
          self._clld_detected[owner] = False
        else:
          self._clld_on.discard(owner)
      return None

    if isinstance(request, PrepCmd.PrepCLldGetStatus):
      # Whether the channel touched a resource since its detection last started.
      owner = self._owner(request)
      detected = owner is not None and self._clld_detected.get(owner, False)
      return PrepCmd.PrepCLldGetStatus.Response(
        detected=[detected], detect_index=[0], length=[0], sample_rate=1
      ), "the resource model" if detected else "nothing in the way"
    return None

  def _answer_drive_settings(self, request: TCPCommand, method: str) -> Optional[Tuple[Any, str]]:
    """The channels' windows, and what their Z drives are set to."""
    if isinstance(request, PrepCmd.PrepGetChannelBounds):
      declared = self._declared().channels
      bounds = []
      for channel in range(self.device.simulated_configuration.num_channels or 0):
        if channel >= len(declared):
          continue
        c = declared[channel]
        if c.x_range is None or c.y_range is None or c.z_range is None:
          continue
        # The Z window is for whatever is attached: it drops by what the channel carries.
        below = self._mounted_length(channel)
        bounds.append(
          PrepCmd.ChannelBoundsParameters(
            channel=PrepCmd.channel_order_legacy_prep[channel],
            x_min=c.x_range[0],
            x_max=c.x_range[1],
            y_min=c.y_range[0],
            y_max=c.y_range[1],
            z_min=c.z_range[0] - below,
            z_max=c.z_range[1] - below,
          )
        )
      return PrepCmd.PrepGetChannelBounds.Response(bounds=bounds), "the declared channel ranges"
    if isinstance(request, PrepCmd.PrepZDriveSetPwm):
      # How hard the drive pushes is not modelled: the value is kept and read back.
      self._z_drive_pwm[self._owner(request) or 0] = int(request.value)
      return None
    if isinstance(request, PrepCmd.PrepZDriveGetPwm):
      return PrepCmd.PrepZDriveGetPwm.Response(
        value=self._z_drive_pwm.get(self._owner(request) or 0, SIMULATED_Z_DRIVE_PWM)
      ), "the declared Z drive PWM"
    if isinstance(request, PrepCmd.PrepZDriveGetAcceleration):
      return PrepCmd.PrepZDriveGetAcceleration.Response(
        value=self._declared().z_drive_acceleration
      ), "the declared Z drive acceleration"
    return None

  def _answer_probes(self, request: TCPCommand, method: str) -> Optional[Tuple[Any, str]]:
    """What the channels say they hold, their TADM reads, and their reads by name."""
    if isinstance(request, PrepCmd.PrepRetrieveTadmData):
      # Pressure is not modelled, so nothing is recorded.
      data = PrepCmd.TadmReturnParameters(
        default_values=False, channel=request.channel, entries=0, error=False, data=[]
      )
      return PrepCmd.PrepRetrieveTadmData.Response(tadm_data=data), "no pressure modelled"
    if isinstance(request, (PrepCmd.PrepTadmGetPressure, PrepCmd.PrepTadmGetStatus)):
      owner = self._owner(request)
      if owner is None or owner >= len(SIMULATED_TADM_PRESSURES):
        return None
      if isinstance(request, PrepCmd.PrepTadmGetPressure):
        return PrepCmd.PrepTadmGetPressure.Response(
          pressure=[SIMULATED_TADM_PRESSURES[owner]]
        ), f"channel {owner}'s pressure at rest"
      return PrepCmd.PrepTadmGetStatus.Response(
        entries=0, buffer_size=SIMULATED_TADM_BUFFER_SIZE, sample_rate=SIMULATED_TADM_SAMPLE_RATE
      ), "no pressure modelled"
    if isinstance(request, PrepCmd.PrepGetTipDefinitionHeld) or (
      isinstance(request, PrepCmd.PrepProbeRequest) and method == "GetTipDefinitionHeld"
    ):
      return HoiParams().add(self._tip_definition_held(), Struct()), "the channels' mounting shafts"
    if isinstance(request, PrepCmd.PrepProbeRequest):
      owner = self._owner(request)
      if owner is None:
        return None
      if method == "GetNodeVersion":
        declared = self._declared().channels
        version = declared[owner].firmware_version if owner < len(declared) else None
        if version is None:
          return None
        return HoiParams().add(version, Str), f"channel {owner}'s declared firmware"
      if method == "GetTipPresent":
        # The sleeve senses what sits in the collar, a tool as much as a tip.
        shaft = self.shaft(owner)
        present = shaft is not None and shaft.has_tip()
        return HoiParams().add(int(present), U32), f"channel {owner}'s mounting shaft"
    return None

  def _tip_definition_held(self) -> PrepCmd.TipDefinition:
    """What the first channel carrying something holds, as the definition it was picked up with.

    The id and label are the device's own answers, empty and holding.
    """
    shafts = (self.shaft(channel) for channel in range(self.num_channels))
    mounted = next((s.tip for s in shafts if s is not None and s.has_tip()), None)
    if mounted is None:
      return PrepCmd.TipDefinition(
        default_values=True,
        id=0,
        volume=0.0,
        length=0.0,
        tip_type=0,
        has_filter=False,
        is_needle=False,
        is_tool=False,
        label="No Tip",
      )
    if isinstance(mounted, Tip):
      return PrepCmd.TipDefinition(
        default_values=False,
        id=255,
        volume=mounted.maximal_volume,
        length=mounted.get_size_z() - mounted.fitting_depth,
        tip_type=int(PrepCmd.TipTypes.StandardVolume),
        has_filter=mounted.has_filter,
        is_needle=mounted.maximal_volume == 0,
        is_tool=False,
        label="Pipettor Custom",
      )
    # A tool is a HeadTool that is not a Tip, and a tool pick-up sends this definition.
    tool = PrepCmd.CO_RE_GRIPPER_TIP_PICKUP_PARAMETERS
    return PrepCmd.TipDefinition(
      default_values=False,
      id=255,
      volume=tool.volume,
      length=tool.length,
      tip_type=int(tool.tip_type),
      has_filter=tool.has_filter,
      is_needle=False,
      is_tool=True,
      label="Pipettor Custom",
    )


class SimulatedXArm(_Simulated, XArm):
  """The X-arm, answering for itself."""

  async def answer(self, request: TCPCommand, path: str, method: str) -> Optional[Tuple[Any, str]]:
    x = self.device.modelled_x(default=SIMULATED_INITIALIZED_POSITIONS[0][0])
    if isinstance(request, PrepCmd.PrepXAxisGetCommandedPosition):
      # The simulated axis counts in its own frame, offset from the reported X.
      return PrepCmd.PrepXAxisGetCommandedPosition.Response(
        value=x - SIMULATED_X_AXIS_OFFSET
      ), "where the model has the arm"
    if isinstance(request, PrepCmd.PrepXAxisGetVelocity):
      return PrepCmd.PrepXAxisGetVelocity.Response(value=SIMULATED_X_SPEED), "the device's profile"
    if isinstance(request, PrepCmd.PrepXAxisGetAcceleration):
      return (
        PrepCmd.PrepXAxisGetAcceleration.Response(value=SIMULATED_X_ACCELERATION),
        "the device's profile",
      )
    if isinstance(request, PrepCmd.PrepXAxisMoveAbsolute):
      # A channel with continuous cLLD on stops the arm where it touches a resource.
      pipettes = self.device.pipettes
      target = request.position + SIMULATED_X_AXIS_OFFSET
      if isinstance(pipettes, SimulatedPipettes):
        target = pipettes.sensed_x_move(x, target)
      self.update_location_by_reference_point(target)
      return None
    if isinstance(request, PrepCmd.PrepXAxisSeekToHomeFlag):
      # No flag is modelled: the seek trips where the arm stands, and nothing moves.
      return PrepCmd.PrepXAxisSeekToHomeFlag.Response(
        value=x - SIMULATED_X_AXIS_OFFSET
      ), "where the model has the arm"
    return None


class _SimulatedSession(TCPSession):
  """A session whose other end is the simulator: requests are built as for TCP, and answered with
  frames the real decoder reads."""

  def __init__(self, io: Socket, driver: "PrepSimulationDriver"):
    super().__init__(io, error_codes=PREP_ERROR_CODES)
    self._driver = driver

  async def exchange(
    self, command: TCPCommand[object], *, read_timeout: Optional[float] = None
  ) -> CommandResponse:
    """Build one request into its frame and answer it, as a device on the link would."""
    self.require_ready()
    if self.client_address is None:
      raise RuntimeError("the simulated session has no client address; call setup first")
    sequence = self.allocate_sequence(command.dest)
    frame = HarpPacket.unpack(IpPacket.unpack(command.build(self.client_address, sequence)).payload)
    hoi = HoiPacket.unpack(frame.payload)
    request = command.request if isinstance(command, _ResolvedPrepCommand) else command
    logger.debug("%s write: %s", SIMULATED_LINK, request)

    status = hoi_action_code_base(hoi.action_code) == Hoi2Action.STATUS_REQUEST
    params, refused = await self._respond(request, command.dest, hoi)
    if refused:
      action = Hoi2Action.STATUS_EXCEPTION if status else Hoi2Action.COMMAND_EXCEPTION
    else:
      action = Hoi2Action.STATUS_RESPONSE if status else Hoi2Action.COMMAND_RESPONSE
    response = HoiPacket(
      interface_id=hoi.interface_id,
      action_id=hoi.action_id,
      params=params,
      action_code=action,
    )
    harp = HarpPacket(
      src=command.dest,
      dst=self.client_address,
      seq=sequence,
      protocol=2,
      action_code=4,
      payload=response.pack(),
    )
    return CommandResponse.from_bytes(IpPacket(protocol=6, payload=harp.pack()).pack())

  async def _respond(
    self, request: TCPCommand, dest: Address, hoi: HoiPacket
  ) -> Tuple[bytes, bool]:
    """What the simulated device answers, and whether it refuses.

    Returns:
      The answer's parameters, and True when they are an exception rather than an answer.
    """
    tree = self._driver.tree
    node = tree.get(dest)
    if node is None:
      return self._refusal(dest, hoi, "no object at that address in the recorded firmware"), True

    if hoi.interface_id == 0:
      answered = tree.answer_introspection(node, request)
      if answered is None:
        return self._refusal(dest, hoi, "introspection request not simulated"), True
      params = answered.build()
      logger.debug("%s read: simulation: %s", SIMULATED_LINK, params.hex())
      return params, False

    method = node.method(hoi.interface_id, hoi.action_id)
    if method is None:
      return self._refusal(
        dest, hoi, f"{node.path} has no such method in the recorded firmware"
      ), True

    try:
      answer = await self._driver._answer(request, node.path, method["name"])
    except _DeviceRefuses as refused:
      logger.debug("%s read: simulation refuses as the device does: %s", SIMULATED_LINK, refused)
      return HoiParams().add(refused.entries, Str).build(), True
    if answer is None:
      if isinstance(request, PrepCmd.PrepProbeRequest) or hasattr(type(request), "Response"):
        return self._refusal(dest, hoi, f"{node.path}.{method['name']} is not simulated"), True
      return b"", False
    value, source = answer
    params = (value if isinstance(value, HoiParams) else _encode(value)).build()
    logger.debug("%s read: simulation: %s from model %s", SIMULATED_LINK, value, source)
    return params, False

  @staticmethod
  def _refusal(dest: Address, hoi: HoiPacket, why: str) -> bytes:
    """The exception a device sends for a method it does not have, logged with why."""
    logger.debug("%s read: simulation refuses: %s", SIMULATED_LINK, why)
    entry = (
      f"0x{dest.module:04X}.0x{dest.node:04X}.0x{dest.object:04X}:"
      f"0x{hoi.interface_id:02X},0x{hoi.action_id:04X},0x{_NOT_SUPPORTED:04X}"
    )
    return HoiParams().add(entry, Str).build()


class _SimulatedIO(HamiltonTCPClient):
  """Stands where the TCP link would be, with the simulator on its other end."""

  def __init__(self, driver: "PrepSimulationDriver"):
    self._driver = driver
    super().__init__(host="simulation", port=0)

  def _create_session(self) -> TCPSession:
    return _SimulatedSession(
      Socket(human_readable_device_name="Hamilton Prep simulation", host="simulation", port=0),
      self._driver,
    )

  async def setup(self) -> None:
    """Open the simulated session and register the recorded root, as the handshake would."""
    if self._session.state is not SessionState.CLOSED:
      raise RuntimeError("the simulated Prep is already set up - call stop() first")
    self._session = self._create_session()
    self._session.state = SessionState.READY
    self._session.client_id = 1
    self._session.client_address = Address(2, 1, 65535)
    root = self._driver.tree.root.address
    self._session.registry.set_root_address(root)
    root_info = await self.introspection.get_object(root)
    root_info.children = {}
    self._session.registry.register(root_info.name, root_info)


class PrepSimulationDriver(PrepDriver):
  """A simulated Prep, driven exactly like the real one."""

  def __init__(
    self,
    deck: Deck,
    declared_configuration_json: Optional[str] = None,
    firmware_tree_json: Optional[str] = None,
    initialized: bool = False,
    default_minimum_traverse_height: float = 167.5,
    simulate_motion_time: bool = False,
    motion_time_scale: float = 0.25,
  ):
    """
    Args:
      deck: the deck to reflect this device into.
      declared_configuration_json: path to a saved configuration, which this device then answers as.
        Defaults to `RECORDING_PREP`.
      firmware_tree_json: path to a recorded firmware tree, whose objects and methods this device
        then has. Defaults to `FIRMWARE_TREE_V1_2_2`.
      initialized: whether the device reports itself already initialized. One that has just been
        switched on does not.
      default_minimum_traverse_height: what the device answers `GetDefaultTraverseHeight` with, and
        where it raises channels to Z safety, in mm. Defaults to what the recorded device reports.
      simulate_motion_time: whether a command that moves the channels takes time at all, so a
        viewer shows each step. Off, every command answers at once.
      motion_time_scale: the share of the device's own time a move takes when it does: a quarter,
        so a step is watched rather than waited for. 1.0 keeps the device's time.

    Raises:
      ValueError: If the declared configuration holds no device.
    """
    super().__init__(
      deck=deck,
      declared_configuration_json=declared_configuration_json or RECORDING_PREP,
      io=_SimulatedIO(self),
    )
    # Its light stands where a real one would, but nobody is looking at it, so setup does not
    # stand at its ready colour. Built here, so setup keeps it rather than making its own.
    self.lights = Lights(self)
    self.lights.default_ready_seconds = 0.0
    configuration = self.declared.get("device")
    if configuration is None:
      raise ValueError(
        "a simulated device has to be told what it is simulating: pass "
        "`declared_configuration_json`, naming a file that records one"
      )
    self.simulated_configuration: DeviceConfiguration = configuration
    self.simulated_pipettes: Optional[PipettesConfiguration] = self.declared.get("pipettes")
    self.firmware_tree_json = firmware_tree_json or FIRMWARE_TREE_V1_2_2
    self.tree = _RecordedTree(
      self.firmware_tree_json, head8_installed=bool(configuration.head8_installed)
    )
    self.initialized = initialized
    self.simulated_default_minimum_traverse_height = default_minimum_traverse_height
    self.simulate_motion_time = simulate_motion_time
    self.motion_time_scale = motion_time_scale

    # The features this device has, each answering for itself. Setup builds only the ones that are
    # not already there, so these stand in for the real ones throughout.
    self.x_arm = SimulatedXArm(self)
    if configuration.num_channels:
      self.pipettes = SimulatedPipettes(self)

  def describe_link(self) -> str:
    return "simulation (no link)"

  def _check_declared_against(self, discovered: DeviceConfiguration) -> None:
    """Nothing to cross-check: a simulated device answers from the declaration.

    Args:
      discovered: what this device answered, which is what it was told to answer.
    """

  def modelled_x(self, default: float) -> float:
    """Where the model has the arm along X, in mm on the deck.

    Args:
      default: what to answer when nothing models the arm yet.
    """
    arm = self.x_arm
    if arm is None or arm.resource is None or arm.resource.location is None or self.deck is None:
      return default
    return arm.resource.get_location_wrt(self.deck).x + arm.configuration.reference_point_from_left

  async def _answer(self, request: TCPCommand, path: str, method: str) -> Optional[Tuple[Any, str]]:
    """What the device would answer, asked of the feature the command is about.

    Each feature answers for its own model, so the logic stays where the model is; this only decides
    who is asked, and answers what the device itself holds. Keeping time, a move is recorded first
    and then waited out, so whatever watches the model sees it for as long as the device takes.
    """
    before = self._where_everything_is() if self.simulate_motion_time else None
    answered = None
    for feature in (self.pipettes, self.x_arm):
      if isinstance(feature, _Simulated):
        answered = await feature.answer(request, path, method)
        if answered is not None:
          break
    if answered is None:
      answered = self._answer_for_device(request, path, method)
    if before is not None:
      seconds = self._motion_time(before, self._where_everything_is()) * self.motion_time_scale
      if seconds > 0:
        await asyncio.sleep(seconds)
    return answered

  def _where_everything_is(self) -> List[Tuple[float, float, float]]:
    """Where the model has each channel, as (x, y, z) in mm: x is the arm's."""
    if not isinstance(self.pipettes, SimulatedPipettes):
      return []
    count = self.simulated_configuration.num_channels or 0
    return [self.pipettes._modelled_location(channel) for channel in range(count)]

  @staticmethod
  def _travel_time(distance: float, speed: float, acceleration: float) -> float:
    """How long a move of `distance` takes, speeding up and slowing down at `acceleration`."""
    distance = abs(distance)
    if distance * acceleration < speed * speed:  # never reaches cruise
      return 2 * math.sqrt(distance / acceleration)
    return distance / speed + speed / acceleration

  def _motion_time(
    self, before: List[Tuple[float, float, float]], after: List[Tuple[float, float, float]]
  ) -> float:
    """How long the device takes from `before` to `after`, the slowest axis setting the time."""
    return max(
      [0.0]
      + [
        self._travel_time(end - start, speed, acceleration)
        for was, now in zip(before, after)
        for start, end, (speed, acceleration) in zip(was, now, _PROFILES)
      ]
    )

  def _answer_for_device(
    self, request: TCPCommand, path: str, method: str
  ) -> Optional[Tuple[Any, str]]:
    """What MLPrep, its deck configuration and its CPU answer, from the declared configuration."""
    c = self.simulated_configuration
    declared = "the declared configuration"

    if isinstance(request, PrepCmd.PrepInitialize):
      if isinstance(self.pipettes, SimulatedPipettes) and self.pipettes._plate_held:
        raise _DeviceRefuses(_INITIALIZE_WITH_A_PLATE)
      self.initialized = True
      return None
    if isinstance(request, PrepCmd.PrepGetIsInitialized):
      return PrepCmd.PrepGetIsInitialized.Response(
        value=self.initialized
      ), "whether it was initialized"
    if isinstance(request, PrepCmd.PrepGetIsEnclosurePresent):
      return PrepCmd.PrepGetIsEnclosurePresent.Response(value=c.has_enclosure), declared
    if isinstance(request, PrepCmd.PrepGetSafeSpeedsEnabled):
      return PrepCmd.PrepGetSafeSpeedsEnabled.Response(value=c.safe_speeds_enabled), declared
    if isinstance(request, PrepCmd.PrepGetDefaultTraverseHeight):
      return PrepCmd.PrepGetDefaultTraverseHeight.Response(
        value=self.simulated_default_minimum_traverse_height
      ), "the height it was told to travel at"
    if isinstance(request, PrepCmd.PrepGetDeckBounds):
      b = c.deck_bounds
      if b is None:
        return None
      return (
        PrepCmd.PrepGetDeckBounds.Response(
          min_x=b.min_x, max_x=b.max_x, min_y=b.min_y, max_y=b.max_y, min_z=b.min_z, max_z=b.max_z
        ),
        declared,
      )
    if isinstance(request, PrepCmd.PrepGetDeckSiteDefinitions):
      sites = [
        PrepCmd._DeckSiteDefinitionWire(
          default_values=False,
          id=s.id,
          left_bottom_front_x=s.left_bottom_front_x,
          left_bottom_front_y=s.left_bottom_front_y,
          left_bottom_front_z=s.left_bottom_front_z,
          length=s.length,
          width=s.width,
          height=s.height,
        )
        for s in c.deck_sites
      ]
      return PrepCmd.PrepGetDeckSiteDefinitions.Response(sites=sites), declared
    if isinstance(request, PrepCmd.PrepGetWasteSiteDefinitions):
      waste = [
        PrepCmd._WasteSiteDefinitionWire(
          default_values=False,
          index=s.index,
          x_position=s.x_position,
          y_position=s.y_position,
          z_position=s.z_position,
          z_seek=s.z_seek,
        )
        for s in c.waste_sites
      ]
      return PrepCmd.PrepGetWasteSiteDefinitions.Response(sites=waste), declared
    if isinstance(request, PrepCmd.PrepGetPresentChannels):
      present = [
        int(PrepCmd.channel_order_legacy_prep[channel]) for channel in range(c.num_channels or 0)
      ]
      if c.head8_installed:
        present.append(int(PrepCmd.ChannelIndex.MPHChannel))
      return PrepCmd.PrepGetPresentChannels.Response(channels=present), declared
    if isinstance(request, (PrepCmd.PrepGetXSpeedScale, PrepCmd.PrepGetZSpeedScale)):
      return type(request).Response(value=SIMULATED_SPEED_SCALE), "the device's speed scale"
    if isinstance(request, PrepCmd.PrepGetDeckLight):
      white, red, green, blue = SIMULATED_DECK_LIGHT
      return (
        PrepCmd.PrepGetDeckLight.Response(white=white, red=red, green=green, blue=blue),
        "no light",
      )
    if isinstance(request, PrepCmd.PrepGetTipAndNeedleDefinitions):
      return PrepCmd.PrepGetTipAndNeedleDefinitions.Response(definitions=[]), "none registered"

    if isinstance(request, PrepCmd.PrepProbeRequest):
      if method == "GetSerialNumber" and c.serial_number is not None:
        return HoiParams().add(c.serial_number, Str), declared
      if method == "GetModuleVersion" and c.firmware_version is not None:
        return HoiParams().add(c.firmware_version, Str), declared
      if method in ("IsParked", "IsSpread"):
        # Neither is modelled: a simulated device's channels are where the model has them.
        return HoiParams().add(False, PaddedBool), "not modelled"
    return None
