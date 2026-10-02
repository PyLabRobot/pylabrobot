import inspect
import logging
import math
import re
import uuid
from typing import Any, Dict, List, Literal, Optional, Sequence, Tuple, Union, cast

from pylabrobot import utils
from pylabrobot.io import LOG_LEVEL_IO
from pylabrobot.legacy.liquid_handling.backends.backend import (
  LiquidHandlerBackend,
)
from pylabrobot.legacy.liquid_handling.errors import NoChannelError
from pylabrobot.legacy.liquid_handling.standard import (
  Drop,
  DropTipRack,
  MultiHeadAspirationContainer,
  MultiHeadAspirationPlate,
  MultiHeadDispenseContainer,
  MultiHeadDispensePlate,
  Pickup,
  PickupTipRack,
  PipettingOp,
  ResourceDrop,
  ResourceMove,
  ResourcePickup,
  SingleChannelAspiration,
  SingleChannelDispense,
)
from pylabrobot.resources import (
  Coordinate,
  Plate,
  Resource,
  Tip,
  Trash,
)
from pylabrobot.resources.itemized_resource import ItemizedResource
from pylabrobot.resources.opentrons import OTDeck
from pylabrobot.resources.tip_rack import TipRack

try:
  import ot_api

  USE_OT = True
except ImportError as e:
  USE_OT = False
  _OT_IMPORT_ERROR = e


# https://github.com/Opentrons/opentrons/issues/14590
# https://labautomation.io/t/connect-pylabrobot-to-ot2/2862/18
_OT_DECK_IS_ADDRESSABLE_AREA_VERSION = "7.1.0"

logger = logging.getLogger(__name__)


def _opentrons_version_components(version: str) -> Tuple[int, int, int]:
  """Parse the numeric release components from an Opentrons server version.

  Raises:
    ValueError: If the version does not start with major, minor, and patch numbers.
  """
  match = re.match(r"^(\d+)\.(\d+)\.(\d+)", version)
  if match is None:
    raise ValueError(
      f"Opentrons server version must start with major, minor, and patch numbers: {version!r}."
    )
  major, minor, patch = match.groups()
  return int(major), int(minor), int(patch)


def _fixed_trash_is_addressable(api_version: str) -> bool:
  """Return whether fixed trash uses the addressable-area commands."""
  return _opentrons_version_components(api_version) >= _opentrons_version_components(
    _OT_DECK_IS_ADDRESSABLE_AREA_VERSION
  )


class _IOLogger:
  """Transparent proxy over the ``ot_api`` module that logs every call at
  ``LOG_LEVEL_IO``.

  The OT-2 talks HTTP through ``ot_api`` rather than a pylabrobot.io transport, so
  this wrapper gives it the same wire-level logging every other backend gets from
  its io object. Submodules (``lh``, ``health``, ...) are wrapped recursively;
  plain attributes (e.g. ``run_id``) pass through untouched.
  """

  def __init__(self, target: Any, prefix: str = ""):
    self._target = target
    self._prefix = prefix

  def __getattr__(self, name: str) -> Any:
    attr = getattr(self._target, name)
    qualified = f"{self._prefix}.{name}" if self._prefix else name
    if inspect.ismodule(attr):
      return _IOLogger(attr, qualified)
    if callable(attr):

      def _logged(*args, **kwargs):
        parts = [repr(a) for a in args] + [f"{k}={v!r}" for k, v in kwargs.items()]
        logger.log(LOG_LEVEL_IO, "%s(%s)", qualified, ", ".join(parts))
        return attr(*args, **kwargs)

      return _logged
    return attr


def _fixed_head_tip_geometry(tip: Tip) -> Tuple[object, ...]:
  """Compare physical tip properties independently of resource identity and placement."""
  return (
    tip.get_size_x(),
    tip.get_size_y(),
    tip.get_size_z(),
    tip.fitting_depth,
    tip.has_filter,
    tip.maximal_volume,
    tip.nominal_volume,
    tip.collar_height if tip.has_collar_height else None,
    tip.pick_up_location,
  )


class OpentronsOT2Backend(LiquidHandlerBackend):
  """Backends for the Opentrons OT2 liquid handling robots."""

  _FIXED_HEAD_CHANNELS = list(range(8))
  _FIXED_HEAD_VOLUME_RANGES = {
    "p20_multi_gen2": (1, 20),
    "p300_multi_gen2": (20, 300),
  }

  pipette_name2volume = {
    "p10_single": 10,
    "p10_multi": 10,
    "p20_single_gen2": 20,
    "p20_multi_gen2": 20,
    "p50_single": 50,
    "p50_multi": 50,
    "p300_single": 300,
    "p300_multi": 300,
    "p300_single_gen2": 300,
    "p300_multi_gen2": 300,
    "p1000_single": 1000,
    "p1000_single_gen2": 1000,
    "p300_single_gen3": 300,
    "p1000_single_gen3": 1000,
  }

  def __init__(
    self,
    host: str,
    port: int = 31950,
    fixed_head_mount: Optional[Literal["left", "right"]] = None,
  ):
    """Create an OT-2 backend.

    Args:
      host: Hostname or IP address of the OT-2.
      port: Robot-server port.
      fixed_head_mount: Mount containing a supported OT-2 GEN2 eight-channel pipette. When set,
        the backend exposes the eight fixed nozzles as one full-head operation. All eight channels
        must be selected together. P20 and P300 GEN2 models are supported, with per-nozzle volumes
        of 1–20 and 20–300 µL, respectively. The other mount is not exposed in this mode. Resources
        must be unrotated A-to-H columns of a tip rack or plate at 9 mm pitch.
    """
    super().__init__()

    if not USE_OT:
      raise RuntimeError(
        "Opentrons is not installed. Please run pip install pylabrobot[opentrons]."
        f" Import error: {_OT_IMPORT_ERROR}."
      )

    self.host = host
    self.port = port
    if fixed_head_mount not in {None, "left", "right"}:
      raise ValueError("fixed_head_mount must be left, right, or None")
    self.fixed_head_mount = fixed_head_mount

    # All hardware I/O goes through this handle so a subclass (e.g. the chatterbox)
    # can dry-run the backend by swapping it for a recording stand-in. The real handle
    # wraps ot_api to log every HTTP call at LOG_LEVEL_IO, like other backends' io.
    self._ot: Any = _IOLogger(ot_api)

    self._ot.set_host(host)
    self._ot.set_port(port)

    self.ot_api_version: Optional[str] = None
    self.left_pipette: Optional[Dict[str, str]] = None
    self.right_pipette: Optional[Dict[str, str]] = None

    self.traversal_height = 120  # test
    self._tip_racks: Dict[str, int] = {}  # tip_rack.name -> slot index
    self._plr_name_to_load_name: Dict[str, str] = {}

  def serialize(self) -> dict:
    data = {
      **super().serialize(),
      "host": self.host,
      "port": self.port,
    }
    if self.fixed_head_mount is not None:
      data["fixed_head_mount"] = self.fixed_head_mount
    return data

  def _fixed_head_pipette(self) -> Dict[str, str]:
    """Return the selected fixed-head pipette or reject the mounted configuration."""

    if self.fixed_head_mount is None:
      raise RuntimeError("fixed-head operation is not enabled")
    pipette = self.left_pipette if self.fixed_head_mount == "left" else self.right_pipette
    if pipette is None or pipette["name"] not in self._FIXED_HEAD_VOLUME_RANGES:
      raise NoChannelError(
        f"The {self.fixed_head_mount} mount must contain a supported OT-2 GEN2 "
        "eight-channel pipette."
      )
    return pipette

  async def setup(self, skip_home: bool = False):
    # create run
    run_id = self._ot.runs.create()
    self._ot.set_run(run_id)

    # get pipettes, then assign them
    self.left_pipette, self.right_pipette = self._ot.lh.add_mounted_pipettes()

    self.left_pipette_has_tip = self.right_pipette_has_tip = False
    if self.fixed_head_mount is not None:
      self._fixed_head_pipette()

    # get api version
    health = self._ot.health.get()
    self.ot_api_version = health["api_version"]

    if not skip_home:
      await self.home()

  @property
  def num_channels(self) -> int:
    if self.fixed_head_mount is not None:
      return len(self._FIXED_HEAD_CHANNELS)
    return len([p for p in [self.left_pipette, self.right_pipette] if p is not None])

  def can_pick_up_tip(self, channel_idx: int, tip: Tip) -> bool:
    def supports_tip(channel_vol: float, tip_vol: float) -> bool:
      if channel_vol == 20:
        return tip_vol in {10, 20}
      if channel_vol == 300:
        return tip_vol in {200, 300}
      if channel_vol == 1000:
        return tip_vol in {1000}
      raise ValueError(f"Unknown channel volume: {channel_vol}")

    if self.fixed_head_mount is not None:
      if channel_idx not in self._FIXED_HEAD_CHANNELS:
        return False
      pipette = self._fixed_head_pipette()
      return supports_tip(self.pipette_name2volume[pipette["name"]], tip.maximal_volume)
    if channel_idx == 0:
      if self.left_pipette is None:
        return False
      left_volume = OpentronsOT2Backend.pipette_name2volume[self.left_pipette["name"]]
      return supports_tip(left_volume, tip.maximal_volume)
    if channel_idx == 1:
      if self.right_pipette is None:
        return False
      right_volume = OpentronsOT2Backend.pipette_name2volume[self.right_pipette["name"]]
      return supports_tip(right_volume, tip.maximal_volume)
    return False

  def _validate_fixed_head_ops(self, ops: Sequence[PipettingOp], use_channels: List[int]) -> None:
    """Validate one ordered, 9 mm-pitch column for the fixed eight-nozzle head."""

    if use_channels != self._FIXED_HEAD_CHANNELS or len(ops) != len(self._FIXED_HEAD_CHANNELS):
      raise ValueError("fixed-head operations require channels 0 through 7 in order")

    geometry = _fixed_head_tip_geometry(ops[0].tip)
    if any(_fixed_head_tip_geometry(op.tip) != geometry for op in ops[1:]):
      raise ValueError("fixed-head operations require one shared tip geometry")
    if not all(self.can_pick_up_tip(channel, op.tip) for channel, op in zip(use_channels, ops)):
      raise NoChannelError("The fixed-head pipette cannot use the selected tips.")

    resources = [op.resource for op in ops]
    if len({id(resource) for resource in resources}) != len(resources):
      if all(
        isinstance(op, Drop)
        and op.resource is resources[0]
        and isinstance(op.resource, Trash)
        and op.resource.name == "trash"
        and op.resource.is_in_subtree_of(self.deck)
        for op in ops
      ):
        anchor = ops[0].offset
        if not all(op.offset == anchor + Coordinate(y=-9 * index) for index, op in enumerate(ops)):
          raise ValueError("fixed-head trash offsets must form one 9 mm-pitch column")
        return
      raise ValueError("fixed-head operations require eight distinct positions")

    if any(op.offset != ops[0].offset for op in ops[1:]):
      raise ValueError("fixed-head operations require one shared offset")

    parent = resources[0].parent
    if parent is None or any(resource.parent is not parent for resource in resources):
      raise ValueError("fixed-head positions must share one itemized resource")
    if not isinstance(parent, (TipRack, Plate)):
      raise ValueError("fixed-head positions must belong to a tip rack or plate")

    itemized_parent = cast(ItemizedResource[Resource], parent)
    rows = [itemized_parent.get_child_row(resource) for resource in resources]
    columns = [itemized_parent.get_child_column(resource) for resource in resources]
    if rows != self._FIXED_HEAD_CHANNELS or len(set(columns)) != 1:
      raise ValueError("fixed-head positions must be one ordered A-to-H column")
    z_anchor = (
      "cavity_bottom"
      if isinstance(ops[0], (SingleChannelAspiration, SingleChannelDispense))
      else "b"
    )
    positions = [resource.get_location_wrt(self.deck, "c", "c", z_anchor) for resource in resources]
    anchor = positions[0]
    for index, (resource, position) in enumerate(zip(resources, positions)):
      rotation = resource.get_absolute_rotation()
      if any(angle % 360 != 0 for angle in (rotation.x, rotation.y, rotation.z)):
        raise ValueError("fixed-head positions must be unrotated")
      if not all(
        math.isclose(actual, expected, rel_tol=0, abs_tol=1e-6)
        for actual, expected in (
          (position.x, anchor.x),
          (position.y, anchor.y - 9 * index),
          (position.z, anchor.z),
        )
      ):
        raise ValueError("fixed-head positions must use aligned 9 mm row pitch at one height")

  @staticmethod
  def _validate_equal_fixed_head_values(
    ops: Sequence[Union[SingleChannelAspiration, SingleChannelDispense]],
  ) -> None:
    """Reject per-nozzle settings that one fixed-head command cannot represent."""

    first = ops[0]
    if any(op.volume != first.volume for op in ops[1:]):
      raise ValueError("fixed-head operations require one shared volume")
    if any(op.flow_rate != first.flow_rate for op in ops[1:]):
      raise ValueError("fixed-head operations require one shared flow_rate")
    if any(op.liquid_height != first.liquid_height for op in ops[1:]):
      raise ValueError("fixed-head operations require one shared liquid_height")
    if any(op.blow_out_air_volume != first.blow_out_air_volume for op in ops[1:]):
      raise ValueError("fixed-head operations require one shared blow_out_air_volume")
    if any(op.mix != first.mix for op in ops[1:]):
      raise ValueError("fixed-head operations require one shared mix")

  async def stop(self):
    """Cancel any active OT run, then clear labware definitions."""
    self._plr_name_to_load_name = {}
    self._tip_racks = {}
    self.left_pipette = None
    self.right_pipette = None

    # cancel the HTTP-API run if it exists (helpful to make device available again in official Opentrons app)
    run_id = getattr(self._ot, "run_id", None)
    if run_id:
      try:
        self._ot.requestor.post(f"/runs/{run_id}/cancel")
      except Exception:
        try:
          self._ot.requestor.post(f"/runs/{run_id}/actions/cancel")
        except Exception:
          try:
            self._ot.requestor.delete(f"/runs/{run_id}")
          except Exception:
            pass

  def get_ot_name(self, plr_resource_name: str) -> str:
    """Opentrons only allows names in ^[a-z0-9._]+$, but in PLR we are flexible.
    So we map PLR names to OT names here.
    """
    if plr_resource_name not in self._plr_name_to_load_name:
      ot_load_name = uuid.uuid4().hex
      self._plr_name_to_load_name[plr_resource_name] = ot_load_name
    return self._plr_name_to_load_name[plr_resource_name]

  def select_tip_pipette(self, tip: Tip, with_tip: bool) -> Optional[str]:
    """Select a pipette based on maximum tip volume for tip pick up or drop.

    The volume of the head must match the maximum tip volume. If both pipettes have the same
    maximum volume, the left pipette is selected.

    Args:
      with_tip: If True, get a channel that has a tip.

    Returns:
      The id of the pipette, or None if no pipette is available.
    """

    if self.can_pick_up_tip(0, tip) and with_tip == self.left_pipette_has_tip:
      assert self.left_pipette is not None
      return cast(str, self.left_pipette["pipetteId"])

    if self.can_pick_up_tip(1, tip) and with_tip == self.right_pipette_has_tip:
      assert self.right_pipette is not None
      return cast(str, self.right_pipette["pipetteId"])

    return None

  async def _assign_tip_rack(self, tip_rack: TipRack, tip: Tip):
    ot_slot_size_y = 86
    lw = {
      "schemaVersion": 2,
      "version": 1,
      "namespace": "pylabrobot",
      "metadata": {
        "displayName": self.get_ot_name(tip_rack.name),
        "displayCategory": "tipRack",
        "displayVolumeUnits": "µL",
      },
      "brand": {
        "brand": "unknown",
      },
      "parameters": {
        "format": "96Standard",
        "isTiprack": True,
        # should we get the tip length from calibration on the robot? /calibration/tip_length
        "tipLength": tip.get_size_z(),
        "tipOverlap": tip.fitting_depth,
        "loadName": self.get_ot_name(tip_rack.name),
        "isMagneticModuleCompatible": False,  # do we really care? If yes, store.
      },
      "ordering": utils.reshape_2d(
        [self.get_ot_name(tip_spot.name) for tip_spot in tip_rack.get_all_items()],
        (tip_rack.num_items_x, tip_rack.num_items_y),
      ),
      "cornerOffsetFromSlot": {
        "x": 0,
        "y": ot_slot_size_y
        - tip_rack.get_absolute_size_y(),  # hinges push it to the back (PLR is LFB, OT is LBB)
        "z": 0,
      },
      "dimensions": {
        "xDimension": tip_rack.get_absolute_size_x(),
        "yDimension": tip_rack.get_absolute_size_y(),
        "zDimension": tip_rack.get_absolute_size_z(),
      },
      "wells": {
        self.get_ot_name(child.name): {
          "depth": child.get_absolute_size_z(),
          "x": cast(Coordinate, child.location).x + child.get_absolute_size_x() / 2,
          "y": cast(Coordinate, child.location).y + child.get_absolute_size_y() / 2,
          "z": cast(Coordinate, child.location).z,
          "shape": "circular",
          "diameter": child.get_absolute_size_x(),
          "totalLiquidVolume": tip.maximal_volume,
        }
        for child in tip_rack.children
      },
      "groups": [
        {
          "wells": [self.get_ot_name(tip_spot.name) for tip_spot in tip_rack.get_all_items()],
          "metadata": {
            "displayName": None,
            "displayCategory": "tipRack",
            "wellBottomShape": "flat",  # required even for tip racks
          },
        }
      ],
    }

    data = self._ot.labware.define(lw)
    namespace, definition, version = data["data"]["definitionUri"].split("/")

    # assign labware to robot
    labware_uuid = self.get_ot_name(tip_rack.name)

    deck = tip_rack.parent
    while deck is not None and not isinstance(deck, OTDeck):
      deck = deck.parent  # labware sits in a slot holder, whose parent is the deck
    assert isinstance(deck, OTDeck)
    slot = deck.get_slot(tip_rack)
    assert slot is not None, "tip rack must be on deck"

    self._ot.labware.add(
      load_name=definition,
      namespace=namespace,
      ot_location=slot,
      version=version,
      labware_id=labware_uuid,
      display_name=self.get_ot_name(tip_rack.name),
    )

    self._tip_racks[tip_rack.name] = slot

  def _get_pickup_pipette(self, ops: List[Pickup], use_channels: Optional[List[int]] = None) -> str:
    """Get the pipette for a tip pick-up, or raise."""
    use_channels = list(range(len(ops))) if use_channels is None else use_channels
    if self.fixed_head_mount is not None:
      self._validate_fixed_head_ops(ops, use_channels)
      pipette = self._fixed_head_pipette()
      has_tip = (
        self.left_pipette_has_tip if self.fixed_head_mount == "left" else self.right_pipette_has_tip
      )
      if has_tip:
        raise NoChannelError("The fixed-head pipette already has tips.")
      return cast(str, pipette["pipetteId"])
    assert len(ops) == 1, "only one channel supported for now"
    op = ops[0]
    assert op.resource.parent is not None, "must not be a floating resource"
    pipette_id = self.select_tip_pipette(op.tip, with_tip=False)
    if not pipette_id:
      raise NoChannelError("No pipette channel of right type with no tip available.")
    return pipette_id

  def _get_drop_pipette(self, ops: List[Drop], use_channels: Optional[List[int]] = None) -> str:
    """Get the pipette for a tip drop, or raise."""
    use_channels = list(range(len(ops))) if use_channels is None else use_channels
    if self.fixed_head_mount is not None:
      self._validate_fixed_head_ops(ops, use_channels)
      pipette = self._fixed_head_pipette()
      has_tip = (
        self.left_pipette_has_tip if self.fixed_head_mount == "left" else self.right_pipette_has_tip
      )
      if not has_tip:
        raise NoChannelError("The fixed-head pipette has no tips.")
      return cast(str, pipette["pipetteId"])
    assert len(ops) == 1, "only one channel supported for now"
    op = ops[0]
    assert op.resource.parent is not None, "must not be a floating resource"
    pipette_id = self.select_tip_pipette(op.tip, with_tip=True)
    if not pipette_id:
      raise NoChannelError("No pipette channel of right type with tip available.")
    return pipette_id

  def _get_liquid_pipette(
    self,
    ops: Union[List[SingleChannelAspiration], List[SingleChannelDispense]],
    use_channels: Optional[List[int]] = None,
  ) -> str:
    """Get the pipette for an aspirate/dispense, or raise."""
    use_channels = list(range(len(ops))) if use_channels is None else use_channels
    if self.fixed_head_mount is not None:
      self._validate_fixed_head_ops(ops, use_channels)
      self._validate_equal_fixed_head_values(ops)
      pipette = self._fixed_head_pipette()
      minimum, maximum = self._FIXED_HEAD_VOLUME_RANGES[pipette["name"]]
      if not minimum <= ops[0].volume <= maximum:
        raise NoChannelError(
          f"{pipette['name']} supports {minimum} through {maximum} µL per nozzle."
        )
      if ops[0].volume > ops[0].tip.maximal_volume:
        raise ValueError("fixed-head volume exceeds the selected tip capacity")
      if ops[0].mix is not None:
        mix = ops[0].mix
        if not minimum <= mix.volume <= min(maximum, ops[0].tip.maximal_volume):
          raise ValueError(f"fixed-head mix volume must be within {minimum} through {maximum} µL")
        if mix.surface_following_distance not in (None, 0) or mix.auto_surface_following:
          raise ValueError("fixed-head mixing does not support surface following")
      if ops[0].blow_out_air_volume not in (None, 0):
        raise ValueError("fixed-head operations do not support blow_out_air_volume")
      has_tip = (
        self.left_pipette_has_tip if self.fixed_head_mount == "left" else self.right_pipette_has_tip
      )
      if not has_tip:
        raise NoChannelError("The fixed-head pipette has no tips.")
      return cast(str, pipette["pipetteId"])
    assert len(ops) == 1, "only one channel supported for now"
    pipette_id = self.select_liquid_pipette(ops[0].volume)
    if pipette_id is None:
      raise NoChannelError("No pipette channel of right type with tip available.")
    return pipette_id

  def _set_tip_state(self, pipette_id: str, has_tip: bool):
    """Update tip-mounted state for the pipette that was used.

    This method now validates the provided ``pipette_id`` against both the left
    and right pipette configurations. It updates the state only if the ID
    matches a known, configured pipette; otherwise it raises an error to avoid
    silently putting the backend into an inconsistent state.
    """
    if self.left_pipette is not None and pipette_id == self.left_pipette["pipetteId"]:
      self.left_pipette_has_tip = has_tip
      return

    if self.right_pipette is not None and pipette_id == self.right_pipette["pipetteId"]:
      self.right_pipette_has_tip = has_tip
      return

    raise ValueError(f"Unknown or unconfigured pipette_id {pipette_id!r} in _set_tip_state.")

  async def pick_up_tips(self, ops: List[Pickup], use_channels: List[int]):
    """Pick up tips from the specified resource."""

    pipette_id = self._get_pickup_pipette(ops, use_channels)
    op = ops[0]

    offset_x, offset_y, offset_z = (
      op.offset.x,
      op.offset.y,
      op.offset.z,
    )

    # define tip rack JIT if it's not already assigned
    tip_rack = op.resource.parent
    assert isinstance(tip_rack, TipRack), "TipSpot's parent must be a TipRack."
    if tip_rack.name not in self._tip_racks:
      await self._assign_tip_rack(tip_rack, op.tip)

    offset_z += op.tip.get_size_z()

    self._ot.lh.pick_up_tip(
      labware_id=self.get_ot_name(tip_rack.name),
      well_name=self.get_ot_name(op.resource.name),
      pipette_id=pipette_id,
      offset_x=offset_x,
      offset_y=offset_y,
      offset_z=offset_z,
    )

    self._set_tip_state(pipette_id, True)

  async def drop_tips(self, ops: List[Drop], use_channels: List[int]):
    """Drop tips from the specified resource."""

    pipette_id = self._get_drop_pipette(ops, use_channels)
    op = ops[0]

    use_fixed_trash = op.resource.name == "trash" and _fixed_trash_is_addressable(
      cast(str, self.ot_api_version)
    )
    if use_fixed_trash:
      labware_id = "fixedTrash"
    else:
      tip_rack = op.resource.parent
      assert isinstance(tip_rack, TipRack), "TipSpot's parent must be a TipRack."
      if tip_rack.name not in self._tip_racks:
        await self._assign_tip_rack(tip_rack, op.tip)
      labware_id = self.get_ot_name(tip_rack.name)

    offset_x, offset_y, offset_z = (
      op.offset.x,
      op.offset.y,
      op.offset.z,
    )

    # ad-hoc offset adjustment that makes it smoother.
    offset_z += 10

    if use_fixed_trash:
      self._ot.lh.move_to_addressable_area_for_drop_tip(
        pipette_id=pipette_id,
        offset_x=offset_x,
        offset_y=offset_y,
        offset_z=offset_z,
      )
      self._ot.lh.drop_tip_in_place(pipette_id=pipette_id)
    else:
      self._ot.lh.drop_tip(
        labware_id,
        well_name=self.get_ot_name(op.resource.name),
        pipette_id=pipette_id,
        offset_x=offset_x,
        offset_y=offset_y,
        offset_z=offset_z,
      )

    self._set_tip_state(pipette_id, False)

  def select_liquid_pipette(self, volume: float) -> Optional[str]:
    """Select a pipette based on volume for an aspiration or dispense.

    The volume of the tip mounted on the head must be greater than the volume to aspirate or
    dispense. If both pipettes have the same maximum volume, the left pipette is selected.

    Only heads with a tip are considered.

    Args:
      volume: The volume to aspirate or dispense.

    Returns:
      The id of the pipette, or None if no pipette is available.
    """

    if self.left_pipette is not None:
      left_volume = OpentronsOT2Backend.pipette_name2volume[self.left_pipette["name"]]
      if left_volume >= volume and self.left_pipette_has_tip:
        return cast(str, self.left_pipette["pipetteId"])

    if self.right_pipette is not None:
      right_volume = OpentronsOT2Backend.pipette_name2volume[self.right_pipette["name"]]
      if right_volume >= volume and self.right_pipette_has_tip:
        return cast(str, self.right_pipette["pipetteId"])

    return None

  def get_pipette_name(self, pipette_id: str) -> str:
    """Get the name of a pipette from its id."""

    if self.left_pipette is not None and pipette_id == self.left_pipette["pipetteId"]:
      return cast(str, self.left_pipette["name"])
    if self.right_pipette is not None and pipette_id == self.right_pipette["pipetteId"]:
      return cast(str, self.right_pipette["name"])
    raise ValueError(f"Unknown pipette id: {pipette_id}")

  def _get_default_aspiration_flow_rate(self, pipette_name: str) -> float:
    """Get the default aspiration flow rate for the specified pipette in uL/s.

    Data from https://archive.ph/ZUN9f
    """

    return {
      "p300_multi_gen2": 94,
      "p10_single": 5,
      "p10_multi": 5,
      "p50_single": 25,
      "p50_multi": 25,
      "p300_single": 150,
      "p300_multi": 150,
      "p1000_single": 500,
      "p20_single_gen2": 3.78,
      "p300_single_gen2": 46.43,
      "p1000_single_gen2": 137.35,
      "p20_multi_gen2": 7.6,
    }[pipette_name]

  def _deck_to_robot_frame(self, location: Coordinate) -> Coordinate:
    """Convert a deck-frame coordinate to the OT-2 robot frame.

    pylabrobot positions OT deck slots from the deck plate corner, whereas the OT-2 motion API
    expects coordinates in the robot frame whose origin is slot 1's corner. The two frames differ by
    slot 1's position in the deck frame, so subtract it.
    """
    return location - cast(OTDeck, self.deck).slot_locations[0]

  async def aspirate(self, ops: List[SingleChannelAspiration], use_channels: List[int]):
    """Aspirate liquid from the specified resource using pip."""

    pipette_id = self._get_liquid_pipette(ops, use_channels)
    op = ops[0]
    volume = op.volume

    pipette_name = self.get_pipette_name(pipette_id)
    flow_rate = op.flow_rate or self._get_default_aspiration_flow_rate(pipette_name)

    location = self._deck_to_robot_frame(
      op.resource.get_location_wrt(self.deck, "c", "c", "cavity_bottom")
      + op.offset
      + Coordinate(z=op.liquid_height or 0)
    )

    await self.move_pipette_head(
      location=location,
      minimum_z_height=self.traversal_height,
      pipette_id=pipette_id,
    )

    if op.mix is not None:
      for _ in range(op.mix.repetitions):
        self._ot.lh.aspirate_in_place(
          volume=op.mix.volume,
          flow_rate=op.mix.flow_rate,
          pipette_id=pipette_id,
        )
        self._ot.lh.dispense_in_place(
          volume=op.mix.volume,
          flow_rate=op.mix.flow_rate,
          pipette_id=pipette_id,
        )

    self._ot.lh.aspirate_in_place(
      volume=volume,
      flow_rate=flow_rate,
      pipette_id=pipette_id,
    )

    traversal_location = self._deck_to_robot_frame(
      op.resource.get_location_wrt(self.deck, "c", "c", "cavity_bottom") + op.offset
    )
    traversal_location.z = self.traversal_height
    await self.move_pipette_head(
      location=traversal_location,
      minimum_z_height=self.traversal_height,
      pipette_id=pipette_id,
    )

  def _get_default_dispense_flow_rate(self, pipette_name: str) -> float:
    """Get the default dispense flow rate for the specified pipette.

    Data from https://archive.ph/ZUN9f

    Returns:
      The default flow rate in ul/s.
    """

    return {
      "p300_multi_gen2": 94,
      "p10_single": 10,
      "p10_multi": 10,
      "p50_single": 50,
      "p50_multi": 50,
      "p300_single": 300,
      "p300_multi": 300,
      "p1000_single": 1000,
      "p20_single_gen2": 7.56,
      "p300_single_gen2": 92.86,
      "p1000_single_gen2": 274.7,
      "p20_multi_gen2": 7.6,
    }[pipette_name]

  async def dispense(self, ops: List[SingleChannelDispense], use_channels: List[int]):
    """Dispense liquid from the specified resource using pip."""

    pipette_id = self._get_liquid_pipette(ops, use_channels)
    op = ops[0]
    volume = op.volume

    pipette_name = self.get_pipette_name(pipette_id)
    flow_rate = op.flow_rate or self._get_default_dispense_flow_rate(pipette_name)

    location = self._deck_to_robot_frame(
      op.resource.get_location_wrt(self.deck, "c", "c", "cavity_bottom")
      + op.offset
      + Coordinate(z=op.liquid_height or 0)
    )
    await self.move_pipette_head(
      location=location,
      minimum_z_height=self.traversal_height,
      pipette_id=pipette_id,
    )

    self._ot.lh.dispense_in_place(
      volume=volume,
      flow_rate=flow_rate,
      pipette_id=pipette_id,
    )

    if op.mix is not None:
      for _ in range(op.mix.repetitions):
        self._ot.lh.aspirate_in_place(
          volume=op.mix.volume,
          flow_rate=op.mix.flow_rate,
          pipette_id=pipette_id,
        )
        self._ot.lh.dispense_in_place(
          volume=op.mix.volume,
          flow_rate=op.mix.flow_rate,
          pipette_id=pipette_id,
        )

    traversal_location = self._deck_to_robot_frame(
      op.resource.get_location_wrt(self.deck, "c", "c", "cavity_bottom") + op.offset
    )
    traversal_location.z = self.traversal_height
    await self.move_pipette_head(
      location=traversal_location,
      minimum_z_height=self.traversal_height,
      pipette_id=pipette_id,
    )

  async def home(self):
    self._ot.health.home()

  async def pick_up_tips96(self, pickup: PickupTipRack):
    raise NotImplementedError("The Opentrons backend does not support the 96 head.")

  async def drop_tips96(self, drop: DropTipRack):
    raise NotImplementedError("The Opentrons backend does not support the 96 head.")

  async def aspirate96(
    self, aspiration: Union[MultiHeadAspirationPlate, MultiHeadAspirationContainer]
  ):
    raise NotImplementedError("The Opentrons backend does not support the 96 head.")

  async def dispense96(self, dispense: Union[MultiHeadDispensePlate, MultiHeadDispenseContainer]):
    raise NotImplementedError("The Opentrons backend does not support the 96 head.")

  async def pick_up_resource(self, pickup: ResourcePickup):
    raise NotImplementedError("The Opentrons backend does not support the robotic arm.")

  async def move_picked_up_resource(self, move: ResourceMove):
    raise NotImplementedError("The Opentrons backend does not support the robotic arm.")

  async def drop_resource(self, drop: ResourceDrop):
    raise NotImplementedError("The Opentrons backend does not support the robotic arm.")

  async def list_connected_modules(self) -> List[dict]:
    """List all connected temperature modules."""
    return cast(List[dict], self._ot.modules.list_connected_modules())

  def _pipette_id_for_channel(self, channel: int) -> str:
    if self.fixed_head_mount is not None:
      if channel != 0:
        raise NoChannelError("Position the fixed head through anchor channel 0.")
      return cast(str, self._fixed_head_pipette()["pipetteId"])
    pipettes = []
    if self.left_pipette is not None:
      pipettes.append(self.left_pipette["pipetteId"])
    if self.right_pipette is not None:
      pipettes.append(self.right_pipette["pipetteId"])
    if channel < 0 or channel >= len(pipettes):
      raise NoChannelError(f"Channel {channel} not available on this OT-2 setup.")
    return pipettes[channel]

  def _current_channel_position(self, channel: int) -> Tuple[str, Coordinate]:
    """Return the pipette id and current coordinate for a given channel."""

    pipette_id = self._pipette_id_for_channel(channel)
    try:
      res = self._ot.lh.save_position(pipette_id=pipette_id)
      pos = res["data"]["result"]["position"]
      current = Coordinate(pos["x"], pos["y"], pos["z"])
    except Exception as exc:
      raise RuntimeError("Failed to query current pipette position") from exc

    return pipette_id, current

  async def prepare_for_manual_channel_operation(self, channel: int):
    """Validate channel exists (no-op otherwise for OT-2)."""

    _ = self._pipette_id_for_channel(channel)

  async def move_channel_x(self, channel: int, x: float):
    """Move a channel to an absolute x coordinate using savePosition to seed pose."""

    pipette_id, current = self._current_channel_position(channel)
    target = Coordinate(x=x, y=current.y, z=current.z)
    await self.move_pipette_head(
      location=target, minimum_z_height=self.traversal_height, pipette_id=pipette_id
    )

  async def move_channel_y(self, channel: int, y: float):
    """Move a channel to an absolute y coordinate using savePosition to seed pose."""

    pipette_id, current = self._current_channel_position(channel)
    target = Coordinate(x=current.x, y=y, z=current.z)
    await self.move_pipette_head(
      location=target, minimum_z_height=self.traversal_height, pipette_id=pipette_id
    )

  async def move_channel_z(self, channel: int, z: float):
    """Move a channel to an absolute z coordinate using savePosition to seed pose."""

    pipette_id, current = self._current_channel_position(channel)
    target = Coordinate(x=current.x, y=current.y, z=z)
    await self.move_pipette_head(
      location=target, minimum_z_height=self.traversal_height, pipette_id=pipette_id
    )

  async def move_pipette_head(
    self,
    location: Coordinate,
    speed: Optional[float] = None,
    minimum_z_height: Optional[float] = None,
    pipette_id: Optional[str] = None,
    force_direct: bool = False,
  ):
    """Move the pipette head to the specified location. When a tip is mounted, the location refers
    to the bottom of the tip. If no tip is mounted, the location refers to the bottom of the
    pipette head.

    Args:
      location: The location to move to.
      speed: The speed to move at, in mm/s.
      minimum_z_height: The minimum z height to move to. Appears to be broken in the Opentrons API.
      pipette_id: The id of the pipette to move. If `"left"` or `"right"`, the left or right
        pipette is used.
      force_direct: If True, move the pipette head directly in all dimensions.
    """

    if self.left_pipette is not None and pipette_id == "left":
      pipette_id = self.left_pipette["pipetteId"]
    elif self.right_pipette is not None and pipette_id == "right":
      pipette_id = self.right_pipette["pipetteId"]

    if pipette_id is None:
      raise ValueError("No pipette id given or left/right pipette not available.")

    self._ot.lh.move_arm(
      pipette_id=pipette_id,
      location_x=location.x,
      location_y=location.y,
      location_z=location.z,
      minimum_z_height=minimum_z_height,
      speed=speed,
      force_direct=force_direct,
    )
