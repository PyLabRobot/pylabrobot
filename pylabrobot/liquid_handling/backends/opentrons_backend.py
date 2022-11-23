from typing import Dict, Optional, List, cast

from pylabrobot.liquid_handling.backends import LiquidHandlerBackend
from pylabrobot.liquid_handling.errors import NoChannelError
from pylabrobot.liquid_handling.resources import (
  Coordinate,
  ItemizedResource,
  Plate,
  Resource,
  TipRack,
  Well
)
from pylabrobot.liquid_handling.resources.opentrons import OTDeck
from pylabrobot.liquid_handling.standard import (
  Pickup,
  Discard,
  Aspiration,
  Dispense,
  Move
)
from pylabrobot import utils

try:
  import ot_api
  USE_OT = True
except ImportError:
  USE_OT = False


class OpentronsBackend(LiquidHandlerBackend):
  """ Backends for the Opentrons liquid handling robots """

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
      raise RuntimeError("Opentrons is not installed. Please run pip install pylabrobot[opentrons]")

    ot_api.set_host(host)
    ot_api.set_port(port)

    self.defined_labware: Dict[str, str] = {}

  def setup(self):
    super().setup()

    # create run
    run_id = ot_api.runs.create()
    ot_api.set_run(run_id)

    # get pipettes, then assign them
    self.left_pipette, self.right_pipette = ot_api.lh.add_mounted_pipettes()

    self.left_pipette_has_tip = self.right_pipette_has_tip = False

  @property
  def num_channels(self) -> int:
    return len([p for p in [self.left_pipette, self.right_pipette] if p is not None])

  def stop(self):
    self.defined_labware = {}
    super().stop()

  def assigned_resource_callback(self, resource: Resource):
    super().assigned_resource_callback(resource)

    if not isinstance(resource, (TipRack, Plate)):
      raise RuntimeError(f"Resource {resource} is not supported by the Opentrons backend.")

    wells = resource.children
    well_names = [well.name for well in wells]
    ordering = utils.reshape_2d(well_names, (resource.num_items_x, resource.num_items_y))

    def _get_volume(well: Well) -> float:
      """ Temporary hack to get the volume of the well (in ul), TODO: store in resource. """
      return well.get_size_x() * well.get_size_y() * well.get_size_z()

    display_category = {
      TipRack: "tipRack",
      Plate: "wellPlate",
    }[type(resource)]

    well_definitions = {
      well.name: {
        "depth": well.get_size_z(),
        "x": cast(Coordinate, well.location).x,
        "y": cast(Coordinate, well.location).y,
        "z": cast(Coordinate, well.location).z,

        "shape": "circular",
        "diameter": well.get_size_x(),# inscribed circle has diameter equal to the width of the well
      } for well in wells
    }

    if isinstance(resource, Plate):
      for i, v in enumerate(well_definitions.values()):
        v["totalLiquidVolume"] = _get_volume(utils.force_unwrap(resource.get_well(i)))

    format_ = "irregular" # Property to determine compatibility with multichannel pipette
    if isinstance(resource, ItemizedResource):
      if resource.num_items_x * resource.num_items_y == 96:
        format_ = "96Standard"
      elif resource.num_items_x * resource.num_items_y == 384:
        format_ = "384Standard"

    lw = {
      "schemaVersion": 2,
      "version": 1,
      "namespace": "pylabrobot",
      "metadata":{
        "displayName": resource.name,
        "displayCategory": display_category,
        "displayVolumeUnits":"µL",
      },
      "brand":{
        "brand": "unknown",
      },
      "parameters":{
        "format": format_,
        "isTiprack": isinstance(resource, TipRack),
        # should we get the tip length from calibration on the robot? /calibration/tip_length
        "tipLength":
          resource.tip_type.total_tip_length if isinstance(resource, TipRack) else None,
        # TODO: we need to fetch this. - specifies the length of the area of the tip that overlaps
        # the nozzle of the pipette
        "tipOverlap": 0,
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

    slot = cast(OTDeck, resource.parent).get_slot(resource)

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

  def unassigned_resource_callback(self, name: str):
    super().unassigned_resource_callback(name)

    # The OT API does not support deleting labware, so we just forget about it locally.
    del self.defined_labware[name]

  def select_tip_pipette(self, tip_max_volume: float, with_tip: bool) -> Optional[str]:
    """ Select a pipette based on maximum tip volume for tip pick up or discard.

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

  def pick_up_tips(self, ops: List[Pickup], use_channels: List[int]):
    """ Pick up tips from the specified resource. """

    assert len(ops) == 1, "only one channel supported for now"
    assert use_channels == [0], "manual channel selection not supported on OT for now"
    op = ops[0] # for channel in channels
    # this feels wrong, why should backends check?
    assert op.resource.parent is not None, "must not be a floating resource"

    labware_id = self.defined_labware[op.resource.parent.name]
    pipette_id = self.select_tip_pipette(op.resource.tip_type.maximal_volume, with_tip=False)
    if not pipette_id:
      raise NoChannelError("No pipette channel of right type with no tip available.")

    ot_api.lh.pick_up_tip(labware_id, well_name=op.resource.name, pipette_id=pipette_id,
      offset_x=op.offset.x, offset_y=op.offset.y, offset_z=op.offset.z)

    if pipette_id == self.left_pipette["pipetteId"]:
      self.left_pipette_has_tip = True
    else:
      self.right_pipette_has_tip = True

  def discard_tips(self, ops: List[Discard], use_channels: List[int]):
    """ Discard tips from the specified resource. """

    assert len(ops) == 1 # only one channel supported for now
    assert use_channels == [0], "manual channel selection not supported on OT for now"
    op = ops[0] # for channel in channels
    # this feels wrong, why should backends check?
    assert op.resource.parent is not None, "must not be a floating resource"

    labware_id = self.defined_labware[op.resource.parent.name]
    pipette_id = self.select_tip_pipette(op.resource.tip_type.maximal_volume, with_tip=True)
    if not pipette_id:
      raise NoChannelError("No pipette channel of right type with tip available.")

    ot_api.lh.drop_tip(labware_id, well_name=op.resource.name, pipette_id=pipette_id,
      offset_x=op.offset.x, offset_y=op.offset.y, offset_z=op.offset.z)

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

  def aspirate(self, ops: List[Aspiration], use_channels: List[int]):
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
    if op.flow_rate is None:
      op.flow_rate = self._get_default_aspiration_flow_rate(pipette_name)

    labware_id = self.defined_labware[op.resource.parent.name]

    ot_api.lh.aspirate(labware_id, well_name=op.resource.name, pipette_id=pipette_id,
      volume=volume, flow_rate=op.flow_rate, offset_x=op.offset.x,
       offset_y=op.offset.y, offset_z=op.offset.z)

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

  def dispense(self, ops: List[Dispense], use_channels: List[int]):
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
    if op.flow_rate is None:
      op.flow_rate = self._get_default_dispense_flow_rate(pipette_name)

    labware_id = self.defined_labware[op.resource.parent.name]

    ot_api.lh.dispense(labware_id, well_name=op.resource.name, pipette_id=pipette_id,
      volume=volume, flow_rate=op.flow_rate, offset_x=op.offset.x,
       offset_y=op.offset.y, offset_z=op.offset.z)

  def pick_up_tips96(self, tip_rack: TipRack):
    raise NotImplementedError("The Opentrons backend does not support the CoRe 96.")

  def discard_tips96(self, tip_rack: TipRack):
    raise NotImplementedError("The Opentrons backend does not support the CoRe 96.")

  def aspirate96(self, aspiration: Aspiration):
    raise NotImplementedError("The Opentrons backend does not support the CoRe 96.")

  def dispense96(self, dispense: Dispense):
    raise NotImplementedError("The Opentrons backend does not support the CoRe 96.")

  def move_resource(self, move: Move):
    """ Move the specified lid within the robot. """
    raise NotImplementedError("Moving resources in Opentrons is not implemented yet.")
