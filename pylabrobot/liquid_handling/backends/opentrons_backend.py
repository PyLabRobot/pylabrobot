import sys
from typing import Dict, Optional, List, cast

from pylabrobot.liquid_handling.backends.backend import LiquidHandlerBackend
from pylabrobot.liquid_handling.errors import NoChannelError
from pylabrobot.liquid_handling.standard import (
  Pickup,
  PickupTipRack,
  Drop,
  DropTipRack,
  Aspiration,
  AspirationPlate,
  Dispense,
  DispensePlate,
  Move
)
from pylabrobot.resources import (
  Coordinate,
  ItemizedResource,
  Plate,
  Resource,
  TipRack,
  TipSpot
)
from pylabrobot.resources.opentrons import OTDeck
from pylabrobot.temperature_controlling import OpentronsTemperatureModuleV2
from pylabrobot import utils

PYTHON_VERSION = sys.version_info[:2]

if PYTHON_VERSION <= (3, 10):
  try:
    import ot_api
    USE_OT = True
  except ImportError:
    USE_OT = False
else:
  USE_OT = False


class OpentronsBackend(LiquidHandlerBackend):
  """ Backends for the Opentrons liquid handling robots. Only supported on Python 3.10 and below.
  """

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
    "p1000_single_gen3": 1000
  }

  def __init__(self, host: str, port: int = 31950):
    super().__init__()

    if not USE_OT:
      raise RuntimeError("Opentrons is not installed. Please run pip install pylabrobot[opentrons]."
                         " Only supported on Python 3.10 and below.")

    self.host = host
    self.port = port

    ot_api.set_host(host)
    ot_api.set_port(port)

    self.defined_labware: Dict[str, str] = {}

  def serialize(self) -> dict:
    return {
      **super().serialize(),
      "host": self.host,
      "port": self.port
    }

  async def setup(self):
    await super().setup()

    # create run
    run_id = ot_api.runs.create()
    ot_api.set_run(run_id)

    # get pipettes, then assign them
    self.left_pipette, self.right_pipette = ot_api.lh.add_mounted_pipettes()

    self.left_pipette_has_tip = self.right_pipette_has_tip = False

  @property
  def num_channels(self) -> int:
    return len([p for p in [self.left_pipette, self.right_pipette] if p is not None])

  async def stop(self):
    self.defined_labware = {}
    await super().stop()

  def _get_resource_slot(self, resource: Resource) -> int:
    """ Get the ultimate slot of a given resource. Some resources are assigned to another resource,
    such as a temperature controller, and we need to find the slot of the parent resource. Nesting
    may be deeper than one level, so we need to traverse the tree from the bottom up. """

    slot = None
    while resource.parent is not None:
      if isinstance(resource.parent, OTDeck):
        slot = cast(OTDeck, resource.parent).get_slot(resource)
        break
      resource = resource.parent
    if slot is None:
      raise ValueError("Resource not on the deck.")
    return slot

  async def assigned_resource_callback(self, resource: Resource):
    """ Called when a resource is assigned to a backend.

    Note that for Opentrons, all children to all resources on the deck are named "wells". They also
    have well-like attributes such as `displayVolumeUnits` and `totalLiquidVolume`. These seem to
    be ignored when they are not used for aspirating/dispensing.
    """

    await super().assigned_resource_callback(resource)

    if resource.name == "deck":
      return

    slot = self._get_resource_slot(resource)

    # check if resource is actually a Module
    if isinstance(resource, OpentronsTemperatureModuleV2):
      ot_api.modules.load_module(
        slot=slot,
        model="temperatureModuleV2",
        module_id=resource.backend.opentrons_id
      )
      # call self to assign the tube rack
      await self.assigned_resource_callback(resource.tube_rack)
      return

    well_names = [well.name for well in resource.children]
    if isinstance(resource, ItemizedResource):
      ordering = utils.reshape_2d(well_names, (resource.num_items_x, resource.num_items_y))
    else:
      ordering = [well_names]

    def _get_volume(well: Resource) -> float:
      """ Temporary hack to get the volume of the well (in ul), TODO: store in resource. """
      if isinstance(well, TipSpot):
        return well.make_tip().maximal_volume
      return well.get_size_x() * well.get_size_y() * well.get_size_z()

    # try to stick to opentrons' naming convention
    if isinstance(resource, Plate):
      display_category = "wellPlate"
    elif isinstance(resource, TipRack):
      display_category = "tipRack"
    else:
      display_category = "other"

    well_definitions = {
      child.name: {
        "depth": child.get_size_z(),
        "x": cast(Coordinate, child.location).x,
        "y": cast(Coordinate, child.location).y,
        "z": cast(Coordinate, child.location).z,
        "shape": "circular",

        # inscribed circle has diameter equal to the width of the well
        "diameter": child.get_size_x(),

        # Opentrons requires `totalLiquidVolume`, even for tip racks!
        "totalLiquidVolume": _get_volume(child),
      } for child in resource.children
    }

    format_ = "irregular" # Property to determine compatibility with multichannel pipette
    if isinstance(resource, ItemizedResource):
      if resource.num_items_x * resource.num_items_y == 96:
        format_ = "96Standard"
      elif resource.num_items_x * resource.num_items_y == 384:
        format_ = "384Standard"

    # Again, use default values and only set the real ones if applicable...
    tip_overlap: float = 0
    total_tip_length: float = 0
    if isinstance(resource, TipRack):
      tip_overlap = resource.get_tip("A1").fitting_depth
      total_tip_length = resource.get_tip("A1").total_tip_length

    lw = {
      "schemaVersion": 2,
      "version": 1,
      "namespace": "pylabrobot",
      "metadata":{
        "displayName": resource.name,
        "displayCategory": display_category,
        "displayVolumeUnits": "µL",
      },
      "brand":{
        "brand": "unknown",
      },
      "parameters":{
        "format": format_,
        "isTiprack": isinstance(resource, TipRack),
        # should we get the tip length from calibration on the robot? /calibration/tip_length
        "tipLength": total_tip_length,
        "tipOverlap": tip_overlap,
        "loadName": resource.name,
        "isMagneticModuleCompatible": False, # do we really care? If yes, store.
      },
      "ordering": ordering,
      "cornerOffsetFromSlot":{
        "x": 0,
        "y": 0,
        "z": 0
      },
      "dimensions":{
        "xDimension": resource.get_size_x(),
        "yDimension": resource.get_size_y(),
        "zDimension": resource.get_size_z(),
      },
      "wells": well_definitions,
      "groups": [
        {
          "wells": well_names,
          "metadata": {
            "displayName": "all wells",
            "displayCategory": display_category,
            "wellBottomShape": "flat" # TODO: get this from the resource
          },
        }
      ]
    }

    data = ot_api.labware.define(lw)
    namespace, definition, version = data["data"]["definitionUri"].split("/")

    # assign labware to robot
    labware_uuid = resource.name

    ot_api.labware.add(
      load_name=definition,
      namespace=namespace,
      slot=slot,
      version=version,
      labware_id=labware_uuid,
      display_name=resource.name)

    self.defined_labware[resource.name] = labware_uuid

  async def unassigned_resource_callback(self, name: str):
    await super().unassigned_resource_callback(name)

    # The OT API does not support deleting labware, so we just forget about it locally.
    del self.defined_labware[name]

  def select_tip_pipette(self, tip_max_volume: float, with_tip: bool) -> Optional[str]:
    """ Select a pipette based on maximum tip volume for tip pick up or drop.

    The volume of the head must match the maximum tip volume. If both pipettes have the same
    maximum volume, the left pipette is selected.

    Args:
      tip_max_volume: The maximum volume of the tip.
      prefer_tip: If True, get a channel that has a tip.

    Returns:
      The id of the pipette, or None if no pipette is available.
    """

    left_volume = right_volume = None
    if self.left_pipette is not None:
      left_volume = OpentronsBackend.pipette_name2volume[self.left_pipette["name"]]
    if self.right_pipette is not None:
      right_volume = OpentronsBackend.pipette_name2volume[self.right_pipette["name"]]

    if left_volume is not None and left_volume == tip_max_volume and \
      with_tip == self.left_pipette_has_tip:
      return cast(str, self.left_pipette["pipetteId"])

    if right_volume is not None and right_volume == tip_max_volume and \
      with_tip == self.right_pipette_has_tip:
      return cast(str, self.right_pipette["pipetteId"])

    return None

  async def pick_up_tips(self, ops: List[Pickup], use_channels: List[int]):
    """ Pick up tips from the specified resource. """

    assert len(ops) == 1, "only one channel supported for now"
    assert use_channels == [0], "manual channel selection not supported on OT for now"
    op = ops[0] # for channel in channels
    # this feels wrong, why should backends check?
    assert op.resource.parent is not None, "must not be a floating resource"

    labware_id = self.defined_labware[op.resource.parent.name] # get name of tip rack
    tip_max_volume = op.tip.maximal_volume
    pipette_id = self.select_tip_pipette(tip_max_volume, with_tip=False)
    if not pipette_id:
      raise NoChannelError("No pipette channel of right type with no tip available.")

    if op.offset is not None:
      offset_x, offset_y, offset_z = op.offset.x, op.offset.y, op.offset.z
    else:
      offset_x = offset_y = offset_z = 0

    # ad-hoc offset adjustment that makes it smoother.
    offset_z += 50

    ot_api.lh.pick_up_tip(labware_id, well_name=op.resource.name, pipette_id=pipette_id,
      offset_x=offset_x, offset_y=offset_y, offset_z=offset_z)

    if pipette_id == self.left_pipette["pipetteId"]:
      self.left_pipette_has_tip = True
    else:
      self.right_pipette_has_tip = True

  async def drop_tips(self, ops: List[Drop], use_channels: List[int]):
    """ Drop tips from the specified resource. """

    # right now we get the tip rack, and then identifier within that tip rack?
    # how do we do that with trash, assuming we don't want to have a child for the trash?

    assert len(ops) == 1 # only one channel supported for now
    assert use_channels == [0], "manual channel selection not supported on OT for now"
    op = ops[0] # for channel in channels
    # this feels wrong, why should backends check?
    assert op.resource.parent is not None, "must not be a floating resource"

    labware_id = self.defined_labware[op.resource.parent.name] # get name of tip rack
    tip_max_volume = op.tip.maximal_volume
    pipette_id = self.select_tip_pipette(tip_max_volume, with_tip=True)
    if not pipette_id:
      raise NoChannelError("No pipette channel of right type with tip available.")

    if op.offset is not None:
      offset_x, offset_y, offset_z = op.offset.x, op.offset.y, op.offset.z
    else:
      offset_x = offset_y = offset_z = 0

    # ad-hoc offset adjustment that makes it smoother.
    offset_z += 10

    ot_api.lh.drop_tip(labware_id, well_name=op.resource.name, pipette_id=pipette_id,
      offset_x=offset_x, offset_y=offset_y, offset_z=offset_z)

    if pipette_id == self.left_pipette["pipetteId"]:
      self.left_pipette_has_tip = False
    else:
      self.right_pipette_has_tip = False

  def select_liquid_pipette(self, volume: float) -> Optional[str]:
    """ Select a pipette based on volume for an aspiration or dispense.

    The volume of the tip mounted on the head must be greater than the volume to aspirate or
    dispense. If both pipettes have the same maximum volume, the left pipette is selected.

    Only heads with a tip are considered.

    Args:
      volume: The volume to aspirate or dispense.

    Returns:
      The id of the pipette, or None if no pipette is available.
    """

    left_volume = right_volume = None
    if self.left_pipette is not None:
      left_volume = OpentronsBackend.pipette_name2volume[self.left_pipette["name"]]
    if self.right_pipette is not None:
      right_volume = OpentronsBackend.pipette_name2volume[self.right_pipette["name"]]

    if left_volume is not None and left_volume >= volume and self.left_pipette_has_tip:
      return cast(str, self.left_pipette["pipetteId"])

    if right_volume is not None and right_volume >= volume and self.right_pipette_has_tip:
      return cast(str, self.right_pipette["pipetteId"])

    return None

  def get_pipette_name(self, pipette_id: str) -> str:
    """ Get the name of a pipette from its id. """

    if pipette_id == self.left_pipette["pipetteId"]:
      return cast(str, self.left_pipette["name"])
    if pipette_id == self.right_pipette["pipetteId"]:
      return cast(str, self.right_pipette["name"])
    raise ValueError(f"Unknown pipette id: {pipette_id}")

  def _get_default_aspiration_flow_rate(self, pipette_name: str) -> float:
    """ Get the default aspiration flow rate for the specified pipette.

    Data from https://archive.ph/ZUN9f

    Returns:
      The default flow rate in ul/s.
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
      "p20_multi_gen2": 7.6
    }[pipette_name]

  async def aspirate(self, ops: List[Aspiration], use_channels: List[int]):
    """ Aspirate liquid from the specified resource using pip. """

    assert len(ops) == 1, "only one channel supported for now"
    assert use_channels == [0], "manual channel selection not supported on OT for now"
    op = ops[0]
    # this feels wrong, why should backends check?
    assert op.resource.parent is not None, "must not be a floating resource"

    volume = op.volume

    pipette_id   = self.select_liquid_pipette(volume)
    if pipette_id is None:
      raise NoChannelError("No pipette channel of right type with tip available.")

    pipette_name = self.get_pipette_name(pipette_id)
    flow_rate = op.flow_rate or self._get_default_aspiration_flow_rate(pipette_name)

    labware_id = self.defined_labware[op.resource.parent.name]

    if op.offset is not None:
      offset_x, offset_y, offset_z = op.offset.x, op.offset.y, op.offset.z
    else:
      offset_x = offset_y = offset_z = 0

    ot_api.lh.aspirate(labware_id, well_name=op.resource.name, pipette_id=pipette_id,
      volume=volume, flow_rate=flow_rate, offset_x=offset_x, offset_y=offset_y, offset_z=offset_z)

  def _get_default_dispense_flow_rate(self, pipette_name: str) -> float:
    """ Get the default dispense flow rate for the specified pipette.

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
      "p20_multi_gen2": 7.6
    }[pipette_name]

  async def dispense(self, ops: List[Dispense], use_channels: List[int]):
    """ Dispense liquid from the specified resource using pip. """

    assert len(ops) == 1, "only one channel supported for now"
    assert use_channels == [0], "manual channel selection not supported on OT for now"
    op = ops[0]
    # this feels wrong, why should backends check?
    assert op.resource.parent is not None, "must not be a floating resource"

    volume = op.volume

    pipette_id = self.select_liquid_pipette(volume)
    if pipette_id is None:
      raise NoChannelError("No pipette channel of right type with tip available.")

    pipette_name = self.get_pipette_name(pipette_id)
    flow_rate = op.flow_rate or self._get_default_dispense_flow_rate(pipette_name)

    labware_id = self.defined_labware[op.resource.parent.name]

    if op.offset is not None:
      offset_x, offset_y, offset_z = op.offset.x, op.offset.y, op.offset.z
    else:
      offset_x = offset_y = offset_z = 0

    ot_api.lh.dispense(labware_id, well_name=op.resource.name, pipette_id=pipette_id,
      volume=volume, flow_rate=flow_rate, offset_x=offset_x, offset_y=offset_y, offset_z=offset_z)

  async def home(self):
    """ Home the robot """
    ot_api.health.home()

  async def pick_up_tips96(self, pickup: PickupTipRack):
    raise NotImplementedError("The Opentrons backend does not support the CoRe 96.")

  async def drop_tips96(self, drop: DropTipRack):
    raise NotImplementedError("The Opentrons backend does not support the CoRe 96.")

  async def aspirate96(self, aspiration: AspirationPlate):
    raise NotImplementedError("The Opentrons backend does not support the CoRe 96.")

  async def dispense96(self, dispense: DispensePlate):
    raise NotImplementedError("The Opentrons backend does not support the CoRe 96.")

  async def move_resource(self, move: Move):
    """ Move the specified lid within the robot. """
    raise NotImplementedError("Moving resources in Opentrons is not implemented yet.")

  async def list_connected_modules(self) -> List[dict]:
    """ List all connected temperature modules. """
    return cast(List[dict], ot_api.modules.list_connected_modules())

  async def move_pipette_head(
    self,
    location: Coordinate,
    speed: Optional[float] = None,
    minimum_z_height: Optional[float] = None,
    pipette_id: Optional[str] = None,
    force_direct: bool = False
  ):
    """ Move the pipette head to the specified location. Whe a tip is mounted, the location refers
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

    if pipette_id == "left":
      pipette_id = self.left_pipette["pipetteId"]
    elif pipette_id == "right":
      pipette_id = self.right_pipette["pipetteId"]

    ot_api.lh.move_arm(
      pipette_id=pipette_id,
      location_x=location.x,
      location_y=location.y,
      location_z=location.z,
      minimum_z_height=minimum_z_height,
      speed=speed,
      force_direct=force_direct
    )
