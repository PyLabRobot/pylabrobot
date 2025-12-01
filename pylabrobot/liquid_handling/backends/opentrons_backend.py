import uuid
from typing import Dict, List, Optional, Tuple, Union, cast

from pylabrobot import utils
from pylabrobot.liquid_handling.backends.backend import (
  LiquidHandlerBackend,
)
from pylabrobot.liquid_handling.errors import NoChannelError
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
from pylabrobot.resources import (
  Coordinate,
  Tip,
)
from pylabrobot.resources.opentrons import OTDeck
from pylabrobot.resources.tip_rack import TipRack

try:
  import ot_api

  # for run cancellation
  import ot_api.requestor as _req
  from requests import HTTPError

  USE_OT = True
except ImportError as e:
  USE_OT = False
  _OT_IMPORT_ERROR = e


# https://github.com/Opentrons/opentrons/issues/14590
# https://labautomation.io/t/connect-pylabrobot-to-ot2/2862/18
_OT_DECK_IS_ADDRESSABLE_AREA_VERSION = "7.1.0"


class OpentronsOT2Backend(LiquidHandlerBackend):
  """Backends for the Opentrons OT2 liquid handling robots."""

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

  def __init__(self, host: str, port: int = 31950):
    super().__init__()

    if not USE_OT:
      raise RuntimeError(
        "Opentrons is not installed. Please run pip install pylabrobot[opentrons]."
        f" Import error: {_OT_IMPORT_ERROR}."
      )

    self.host = host
    self.port = port

    ot_api.set_host(host)
    ot_api.set_port(port)

    self.ot_api_version: Optional[str] = None
    self.left_pipette: Optional[Dict[str, str]] = None
    self.right_pipette: Optional[Dict[str, str]] = None

    self.traversal_height = 120  # test
    self._tip_racks: Dict[str, int] = {}  # tip_rack.name -> slot index
    self._plr_name_to_load_name: Dict[str, str] = {}

  def serialize(self) -> dict:
    return {
      **super().serialize(),
      "host": self.host,
      "port": self.port,
    }

  async def setup(self, skip_home: bool = False):
    # create run
    run_id = ot_api.runs.create()
    ot_api.set_run(run_id)

    # get pipettes, then assign them
    self.left_pipette, self.right_pipette = ot_api.lh.add_mounted_pipettes()

    self.left_pipette_has_tip = self.right_pipette_has_tip = False

    # get api version
    health = ot_api.health.get()
    self.ot_api_version = health["api_version"]

    if not skip_home:
      await self.home()

  @property
  def num_channels(self) -> int:
    return len([p for p in [self.left_pipette, self.right_pipette] if p is not None])

  async def stop(self):
    """Cancel any active OT run, then clear labware definitions."""
    self._plr_name_to_load_name = {}
    self._tip_racks = {}
    self.left_pipette = None
    self.right_pipette = None

    # cancel the HTTP-API run if it exists (helpful to make device available again in official Opentrons app)
    run_id = getattr(ot_api, "run_id", None)
    if run_id:
      try:
        _req.post(f"/runs/{run_id}/cancel")
      except HTTPError as err:
        if err.response.status_code == 404:
          _req.post(f"/runs/{run_id}/actions/cancel")
        else:
          raise
      except Exception:
        # fallback: delete the run entirely
        try:
          _req.delete(f"/runs/{run_id}")
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
        "tipLength": tip.total_tip_length,
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

    data = ot_api.labware.define(lw)
    namespace, definition, version = data["data"]["definitionUri"].split("/")

    # assign labware to robot
    labware_uuid = self.get_ot_name(tip_rack.name)

    deck = tip_rack.parent
    assert isinstance(deck, OTDeck)
    slot = deck.get_slot(tip_rack)
    assert slot is not None, "tip rack must be on deck"

    ot_api.labware.add(
      load_name=definition,
      namespace=namespace,
      ot_location=slot,
      version=version,
      labware_id=labware_uuid,
      display_name=self.get_ot_name(tip_rack.name),
    )

    self._tip_racks[tip_rack.name] = slot

  async def pick_up_tips(self, ops: List[Pickup], use_channels: List[int]):
    """Pick up tips from the specified resource."""

    assert len(ops) == 1, "only one channel supported for now"
    op = ops[0]  # for channel in channels
    # this feels wrong, why should backends check?
    assert op.resource.parent is not None, "must not be a floating resource"

    # labware_id = self.defined_labware[op.resource.parent.name]  # get name of tip rack
    pipette_id = self.select_tip_pipette(op.tip, with_tip=False)
    if not pipette_id:
      raise NoChannelError("No pipette channel of right type with no tip available.")

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

    offset_z += op.tip.total_tip_length

    ot_api.lh.pick_up_tip(
      labware_id=self.get_ot_name(tip_rack.name),
      well_name=self.get_ot_name(op.resource.name),
      pipette_id=pipette_id,
      offset_x=offset_x,
      offset_y=offset_y,
      offset_z=offset_z,
    )

    if self.left_pipette is not None and pipette_id == self.left_pipette["pipetteId"]:
      self.left_pipette_has_tip = True
    else:
      self.right_pipette_has_tip = True

  async def drop_tips(self, ops: List[Drop], use_channels: List[int]):
    """Drop tips from the specified resource."""

    # right now we get the tip rack, and then identifier within that tip rack?
    # how do we do that with trash, assuming we don't want to have a child for the trash?

    assert len(ops) == 1  # only one channel supported for now
    op = ops[0]  # for channel in channels
    # this feels wrong, why should backends check?
    assert op.resource.parent is not None, "must not be a floating resource"

    use_fixed_trash = (
      cast(str, self.ot_api_version) >= _OT_DECK_IS_ADDRESSABLE_AREA_VERSION
      and op.resource.name == "trash"
    )
    if use_fixed_trash:
      labware_id = "fixedTrash"
    else:
      tip_rack = op.resource.parent
      assert isinstance(tip_rack, TipRack), "TipSpot's parent must be a TipRack."
      if tip_rack.name not in self._tip_racks:
        await self._assign_tip_rack(tip_rack, op.tip)
      labware_id = self.get_ot_name(tip_rack.name)
    pipette_id = self.select_tip_pipette(op.tip, with_tip=True)
    if not pipette_id:
      raise NoChannelError("No pipette channel of right type with tip available.")

    offset_x, offset_y, offset_z = (
      op.offset.x,
      op.offset.y,
      op.offset.z,
    )

    # ad-hoc offset adjustment that makes it smoother.
    offset_z += 10

    if use_fixed_trash:
      ot_api.lh.move_to_addressable_area_for_drop_tip(
        pipette_id=pipette_id,
        offset_x=offset_x,
        offset_y=offset_y,
        offset_z=offset_z,
      )
      ot_api.lh.drop_tip_in_place(pipette_id=pipette_id)
    else:
      ot_api.lh.drop_tip(
        labware_id,
        well_name=self.get_ot_name(op.resource.name),
        pipette_id=pipette_id,
        offset_x=offset_x,
        offset_y=offset_y,
        offset_z=offset_z,
      )

    if self.left_pipette is not None and pipette_id == self.left_pipette["pipetteId"]:
      self.left_pipette_has_tip = False
    else:
      self.right_pipette_has_tip = False

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

  async def aspirate(self, ops: List[SingleChannelAspiration], use_channels: List[int]):
    """Aspirate liquid from the specified resource using pip."""

    assert len(ops) == 1, "only one channel supported for now"
    op = ops[0]

    volume = op.volume

    pipette_id = self.select_liquid_pipette(volume)
    if pipette_id is None:
      raise NoChannelError("No pipette channel of right type with tip available.")

    pipette_name = self.get_pipette_name(pipette_id)
    flow_rate = op.flow_rate or self._get_default_aspiration_flow_rate(pipette_name)

    location = (
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
        ot_api.lh.aspirate_in_place(
          volume=op.mix.volume,
          flow_rate=op.mix.flow_rate,
          pipette_id=pipette_id,
        )
        ot_api.lh.dispense_in_place(
          volume=op.mix.volume,
          flow_rate=op.mix.flow_rate,
          pipette_id=pipette_id,
        )

    ot_api.lh.aspirate_in_place(
      volume=volume,
      flow_rate=flow_rate,
      pipette_id=pipette_id,
    )

    traversal_location = (
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

    assert len(ops) == 1, "only one channel supported for now"
    op = ops[0]

    volume = op.volume

    pipette_id = self.select_liquid_pipette(volume)
    if pipette_id is None:
      raise NoChannelError("No pipette channel of right type with tip available.")

    pipette_name = self.get_pipette_name(pipette_id)
    flow_rate = op.flow_rate or self._get_default_dispense_flow_rate(pipette_name)

    location = (
      op.resource.get_location_wrt(self.deck, "c", "c", "cavity_bottom")
      + op.offset
      + Coordinate(z=op.liquid_height or 0)
    )
    await self.move_pipette_head(
      location=location,
      minimum_z_height=self.traversal_height,
      pipette_id=pipette_id,
    )

    ot_api.lh.dispense_in_place(
      volume=volume,
      flow_rate=flow_rate,
      pipette_id=pipette_id,
    )

    if op.mix is not None:
      for _ in range(op.mix.repetitions):
        ot_api.lh.aspirate_in_place(
          volume=op.mix.volume,
          flow_rate=op.mix.flow_rate,
          pipette_id=pipette_id,
        )
        ot_api.lh.dispense_in_place(
          volume=op.mix.volume,
          flow_rate=op.mix.flow_rate,
          pipette_id=pipette_id,
        )

    traversal_location = (
      op.resource.get_location_wrt(self.deck, "c", "c", "cavity_bottom") + op.offset
    )
    traversal_location.z = self.traversal_height
    await self.move_pipette_head(
      location=traversal_location,
      minimum_z_height=self.traversal_height,
      pipette_id=pipette_id,
    )

  async def home(self):
    ot_api.health.home()

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
    return cast(List[dict], ot_api.modules.list_connected_modules())

  def _pipette_id_for_channel(self, channel: int) -> str:
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
      res = ot_api.lh.save_position(pipette_id=pipette_id)
      pos = res["data"]["result"]["position"]
      current = Coordinate(pos["x"], pos["y"], pos["z"])
    except Exception as exc:  # noqa: BLE001
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

    ot_api.lh.move_arm(
      pipette_id=pipette_id,
      location_x=location.x,
      location_y=location.y,
      location_z=location.z,
      minimum_z_height=minimum_z_height,
      speed=speed,
      force_direct=force_direct,
    )

  def can_pick_up_tip(self, channel_idx: int, tip: Tip) -> bool:
    def supports_tip(channel_vol: float, tip_vol: float) -> bool:
      if channel_vol == 20:
        return tip_vol in {10, 20}
      if channel_vol == 300:
        return tip_vol in {200, 300}
      if channel_vol == 1000:
        return tip_vol in {1000}
      raise ValueError(f"Unknown channel volume: {channel_vol}")

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
