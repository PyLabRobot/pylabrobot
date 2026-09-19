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

import dataclasses
import json
import logging
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

from . import prep_commands as PrepCmd
from .configuration import DeviceConfiguration
from .errors import PREP_ERROR_CODES
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

# PRPAA1087 as it saved itself, on MLPrep Runtime V1.2.2. What a simulated Prep is unless told otherwise.
RECORDING_PREP = os.path.join(_RECORDINGS, "prep_PRPAA1087_v1_2_2.json")
# The same, declared with an 8-channel head. No device with one has been recorded.
RECORDING_PREP_HEAD8 = os.path.join(_RECORDINGS, "prep_PRPAA1087_v1_2_2_head8.json")

# The firmware trees read off two devices. V1.2.2 is what a simulated Prep runs unless told otherwise.
FIRMWARE_TREE_V1_2_2 = os.path.join(_RECORDINGS, "prep_PRPAA1087_v1_2_2_firmware_tree.json")
FIRMWARE_TREE_V3_0_20 = os.path.join(_RECORDINGS, "prep_PRPBD1394_v3_0_20_firmware_tree.json")

# Where PRPAA1087's channels reported themselves after it initialized on 2026-09-13, as (x, y, z) in
# mm, by channel. What a simulated device answers until the resource model holds the channels.
SIMULATED_INITIALIZED_POSITIONS = {
  0: (289.489, 365.0148, 167.499),
  1: (289.489, 345.0123, 167.4954),
}

# The X axis profile PRPAA1087 read at an X speed scale of 100 percent, in mm/s and mm/s2. A simulated
# axis keeps no profile, so setting one changes nothing it answers.
# Each channel's Y drive frame reads deck Y plus this, rear first, as measured on PRPAA1087 (V1.2.2).
SIMULATED_Y_DRIVE_OFFSETS = (112.36, 102.451)
# Each channel's Z drive frame above the reported Z (rear, front), as PRPAA1087's read-only sweep read them.
SIMULATED_Z_DRIVE_OFFSETS = (171.784, 171.091)
# What PRPAA1087's Z drives read for the PWM that limits how hard they push.
SIMULATED_Z_DRIVE_PWM = 125
# What a simulated channel touches with in a cLLD search, in mm: the probes' default
# `stop_disc_diameter`, as simulated channels hold no tips.
SIMULATED_CLLD_PROBE_DIAMETER = 7.0
# The reported X less the X axis's own position, in mm, as on PRPAA1087 (V1.2.2).
SIMULATED_X_AXIS_OFFSET = 0.193
SIMULATED_X_VELOCITY = 400.0
SIMULATED_X_ACCELERATION = 2250.0

# The speed scales PRPAA1087 read, in percent. A simulated device keeps none.
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
    # is still reported after detection stops, as on PRPAA1087.
    self._clld_on: Set[int] = set()
    self._clld_detected: Dict[int, bool] = {}
    self._z_drive_pwm: Dict[int, int] = {}

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
      Where the model has it, or where PRPAA1087 reported it after initializing when nothing models it
      yet.
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

  async def answer(self, request: TCPCommand, path: str, method: str) -> Optional[Tuple[Any, str]]:
    channels = range(self.device.simulated_configuration.num_channels or 0)
    index_of = {int(enum): index for index, enum in enumerate(PrepCmd.channel_order_legacy_prep)}

    if isinstance(request, PrepCmd.PrepGetPositions):
      positions = []
      for channel in channels:
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
      for axis in move.axis_parameters:
        moved = index_of.get(int(axis.channel))
        if moved is not None:
          self._move(moved, move.gantry_x_position, axis.y_position, axis.z_position)
      return None

    if isinstance(request, PrepCmd.PrepMoveYAbsolute):
      for y_target in request.channels:
        moved = index_of.get(int(y_target.channel))
        if moved is not None:
          self._move(moved, None, y_target.y_position, None)
      return None

    if isinstance(request, PrepCmd.PrepMoveZAbsolute):
      for z_target in request.channels:
        moved = index_of.get(int(z_target.channel))
        if moved is not None:
          self._move(moved, None, None, z_target.z_position)
      return None

    if isinstance(request, PrepCmd.PrepMoveZUpToSafe):
      height = self.device.simulated_default_minimum_traverse_height
      for enum in request.channels:
        raised = index_of.get(int(enum))
        if raised is not None and height is not None:
          self._move(raised, None, None, height)
      return None

    if isinstance(request, PrepCmd.PrepZSeekLldPosition):
      # Each channel seeks down toward its floor, stops at the top of the first resource under it,
      # and is left at its final height.
      results = []
      found = False
      for seek in request.seek_parameters:
        seeking = index_of.get(int(seek.channel))
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
          self._move(seeking, None, None, seek.final_position_z)
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

    if isinstance(request, PrepCmd.PrepGetChannelBounds):
      declared = self._declared().channels
      bounds = []
      for channel in channels:
        if channel >= len(declared):
          continue
        c = declared[channel]
        if c.x_range is None or c.y_range is None or c.z_range is None:
          continue
        bounds.append(
          PrepCmd.ChannelBoundsParameters(
            channel=PrepCmd.channel_order_legacy_prep[channel],
            x_min=c.x_range[0],
            x_max=c.x_range[1],
            y_min=c.y_range[0],
            y_max=c.y_range[1],
            z_min=c.z_range[0],
            z_max=c.z_range[1],
          )
        )
      return PrepCmd.PrepGetChannelBounds.Response(bounds=bounds), "the declared channel ranges"

    if isinstance(request, (PrepCmd.PrepYDriveGetPosition, PrepCmd.PrepYAxisSeekCapacitiveLld)):
      owner = self.device.tree.channel_of(request.dest)
      if owner is None or owner >= len(SIMULATED_Y_DRIVE_OFFSETS):
        return None
      offset = SIMULATED_Y_DRIVE_OFFSETS[owner]
      if isinstance(request, PrepCmd.PrepYDriveGetPosition):
        return PrepCmd.PrepYDriveGetPosition.Response(
          position=self._modelled_location(owner)[1] + offset
        ), f"channel {owner}'s modelled Y in its drive frame"
      # The channel searches toward the end of its search, and stops at the first resource in the
      # way.
      x, y, z = self._modelled_location(owner)
      end = request.position - offset
      touched = self._touched_along(owner, 1, y, end, x, z)
      self._move(owner, None, end if touched is None else touched, None)
      return PrepCmd.PrepYAxisSeekCapacitiveLld.Response(
        lld_detected=touched is not None,
        detect_position=0.0 if touched is None else touched + offset,
      ), "the resource model" if touched is not None else "nothing in the way"

    if isinstance(request, PrepCmd.PrepZDriveSetPwm):
      # Nothing is modelled about how hard the drive pushes; the value is accepted and read back.
      self._z_drive_pwm[self.device.tree.channel_of(request.dest) or 0] = int(request.value)
      return None

    if isinstance(request, PrepCmd.PrepZDriveGetPwm):
      owner = self.device.tree.channel_of(request.dest)
      return PrepCmd.PrepZDriveGetPwm.Response(
        value=self._z_drive_pwm.get(owner or 0, SIMULATED_Z_DRIVE_PWM)
      ), "the declared Z drive PWM"

    if isinstance(request, (PrepCmd.PrepZDriveGetPosition, PrepCmd.PrepZAxisSeekObstacle)):
      owner = self.device.tree.channel_of(request.dest)
      if owner is None or owner >= len(SIMULATED_Z_DRIVE_OFFSETS):
        return None
      offset = SIMULATED_Z_DRIVE_OFFSETS[owner]
      x, y, z = self._modelled_location(owner)
      if isinstance(request, PrepCmd.PrepZDriveGetPosition):
        return PrepCmd.PrepZDriveGetPosition.Response(
          position=z + offset
        ), f"channel {owner}'s modelled Z in its drive frame"
      # The channel seeks down from its start, stops at the top of the first resource under it, and
      # is left at its final height.
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
      owner = self.device.tree.channel_of(request.dest)
      if owner is not None:
        if isinstance(request, PrepCmd.PrepChannelStartCLldDetection):
          self._clld_on.add(owner)
          self._clld_detected[owner] = False
        else:
          self._clld_on.discard(owner)
      return None

    if isinstance(request, PrepCmd.PrepCLldGetStatus):
      # Whether the channel touched a resource since its detection was last started; nothing is
      # recorded.
      owner = self.device.tree.channel_of(request.dest)
      detected = owner is not None and self._clld_detected.get(owner, False)
      return PrepCmd.PrepCLldGetStatus.Response(
        detected=[detected], detect_index=[0], length=[0], sample_rate=1
      ), "the resource model" if detected else "nothing in the way"

    if isinstance(request, PrepCmd.PrepZDriveGetAcceleration):
      return PrepCmd.PrepZDriveGetAcceleration.Response(
        value=self._declared().z_drive_acceleration
      ), "the declared Z drive acceleration"

    if isinstance(request, PrepCmd.PrepGetTipDefinitionHeld) or (
      isinstance(request, PrepCmd.PrepProbeRequest) and method == "GetTipDefinitionHeld"
    ):
      # The tip the first channel holding one holds, as the definition it was picked up with. The
      # id and label are PRPAA1087's own answers, empty and holding.
      tip = next((t for t in self.get_mounted_tips() if t is not None), None)
      held = PrepCmd.TipDefinition(
        default_values=tip is None,
        id=0 if tip is None else 255,
        volume=0.0 if tip is None else tip.maximal_volume,
        length=0.0 if tip is None else tip.total_tip_length - tip.fitting_depth,
        tip_type=0 if tip is None else int(PrepCmd.TipTypes.StandardVolume),
        has_filter=False if tip is None else tip.has_filter,
        is_needle=tip is not None and tip.maximal_volume == 0,
        is_tool=False,
        label="No Tip" if tip is None else "Pipettor Custom",
      )
      return HoiParams().add(held, Struct()), "the channels' mounting shafts"

    if isinstance(request, PrepCmd.PrepProbeRequest):
      owner = self.device.tree.channel_of(request.dest)
      if owner is None:
        return None
      if method == "GetNodeVersion":
        declared = self._declared().channels
        version = declared[owner].firmware_version if owner < len(declared) else None
        if version is None:
          return None
        return HoiParams().add(version, Str), f"channel {owner}'s declared firmware"
      if method == "GetTipPresent":
        present = self.get_mounted_tip(owner) is not None
        return HoiParams().add(int(present), U32), f"channel {owner}'s mounting shaft"

    return None


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
      return PrepCmd.PrepXAxisGetVelocity.Response(
        value=SIMULATED_X_VELOCITY
      ), "PRPAA1087's profile"
    if isinstance(request, PrepCmd.PrepXAxisGetAcceleration):
      return (
        PrepCmd.PrepXAxisGetAcceleration.Response(value=SIMULATED_X_ACCELERATION),
        "PRPAA1087's profile",
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

    answer = await self._driver._answer(request, node.path, method["name"])
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
      default_minimum_traverse_height: what the device answers `GetDefaultTraverseHeight` with, and where it
        raises channels to Z safety, in mm. Defaults to what PRPAA1087 (V1.2.2) reports.

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
    who is asked, and answers what the device itself holds.
    """
    for feature in (self.pipettes, self.x_arm):
      if isinstance(feature, _Simulated):
        answered = await feature.answer(request, path, method)
        if answered is not None:
          return answered
    return self._answer_for_device(request, path, method)

  def _answer_for_device(
    self, request: TCPCommand, path: str, method: str
  ) -> Optional[Tuple[Any, str]]:
    """What MLPrep, its deck configuration and its CPU answer, from the declared configuration."""
    c = self.simulated_configuration
    declared = "the declared configuration"

    if isinstance(request, PrepCmd.PrepInitialize):
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
      return type(request).Response(value=SIMULATED_SPEED_SCALE), "PRPAA1087's speed scale"
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
