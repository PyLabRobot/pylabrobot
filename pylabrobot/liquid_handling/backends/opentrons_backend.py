from typing import List, Union, Optional, cast

from pylabrobot.liquid_handling.backends import LiquidHandlerBackend
from pylabrobot.liquid_handling.resources import (
  Coordinate,
  Plate,
  Resource,
  Lid,
  Tip,
)
from pylabrobot.liquid_handling.resources.abstract.tiprack import TipRack
from pylabrobot.liquid_handling.resources.opentrons import OTDeck
from pylabrobot.liquid_handling.standard import (
  Aspiration,
  Dispense
)
from pylabrobot import utils

try:
  import ot_api
  USE_OT = True
except ImportError:
  USE_OT = False


class NoTipError(Exception): # TODO: this error should be shared.
  pass


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
      raise RuntimeError("Opentrons is not installed. Please run pip install opentrons")

    ot_api.set_host(host)
    ot_api.set_port(port)

    self.defined_labware = {}

  def setup(self):
    super().setup()

    # create run
    run_id = ot_api.runs.create()
    ot_api.set_run(run_id)

    # get pipettes, then assign them
    self.left_pipette, self.right_pipette = ot_api.lh.add_mounted_pipettes()

    self.left_pipette_has_tip = self.right_pipette_has_tip = False

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

    def _get_volume(well):
      """ Temporary hack to get the volume of the well (in ul), TODO: store in resource. """
      return well.get_size_x() * well.get_size_y() * well.get_size_z()

    display_category = {
      "tip_rack": "tipRack",
      "plate": "wellPlate",
    }[resource.category]

    well_definitions = {
      well.name: {
        "depth": well.get_size_z(),
        "x": well.location.x,
        "y": well.location.y,
        "z": well.location.z,
        "totalLiquidVolume": _get_volume(well),

        "shape": "circular",
        "diameter": well.get_size_x(),# inscribed circle has diameter equal to the width of the well
      } for well in wells
    }

    format_ = None # Property to determine compatibility with multichannel pipette
    if resource.num_items_x * resource.num_items_y == 96:
      format_ = "96Standard"
    elif resource.num_items_x * resource.num_items_y == 384:
      format_ = "384Standard"
    else:
      format_ = "irregular"

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
      return self.left_pipette["pipetteId"]

    if right_volume is not None and right_volume == tip_max_volume and \
      with_tip == self.right_pipette_has_tip:
      return self.right_pipette["pipetteId"]

    return None

  def pickup_tips(self, *channels: List[Optional[Tip]], **backend_kwargs):
    """ Pick up tips from the specified resource. """

    assert len(channels) == 1, "only one channel supported for now"
    channel = channels[0] # for channel in channels

    labware_id = self.defined_labware[channel.parent.name]
    pipette_id = self.select_tip_pipette(channel.tip_type.maximal_volume, with_tip=False)
    if not pipette_id:
      raise NoTipError("No pipette channel of right type with no tip available.")

    ot_api.lh.pick_up_tip(labware_id, well_name=channel.name, pipette_id=pipette_id)

    if pipette_id == self.left_pipette["pipetteId"]:
      self.left_pipette_has_tip = True
    else:
      self.right_pipette_has_tip = True

  def discard_tips(self, *channels: List[Optional[Tip]], **backend_kwargs):
    """ Discard tips from the specified resource. """

    assert len(channels) == 1 # only one channel supported for now
    channel = channels[0] # for channel in channels

    labware_id = self.defined_labware[channel.parent.name]
    pipette_id = self.select_tip_pipette(channel.tip_type.maximal_volume, with_tip=True)
    if not pipette_id:
      raise NoTipError("No pipette channel of right type with tip available.")

    ot_api.lh.drop_tip(labware_id, well_name=channel.name, pipette_id=pipette_id)

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
      return self.left_pipette["pipetteId"]

    if right_volume is not None and right_volume >= volume and self.right_pipette_has_tip:
      return self.right_pipette["pipetteId"]

    return None

  def aspirate(self, *channels: Optional[Aspiration], **backend_kwargs):
    """ Aspirate liquid from the specified resource using pip. """

    assert len(channels) == 1 # only one channel supported for now

    channel   = channels[0] # for channel in channels
    volume    = channel.volume
    flow_rate = channel.liquid_class.flow_rate[0]

    labware_id = self.defined_labware[channel.resource.parent.name]
    pipette_id = self.select_liquid_pipette(volume)
    if pipette_id is None:
      raise NoTipError("No pipette channel of right type with tip available.")

    ot_api.lh.aspirate(labware_id, well_name=channel.resource.name, pipette_id=pipette_id,
      volume=volume, flow_rate=flow_rate)

  def dispense(self, *channels: Optional[Dispense], **backend_kwargs):
    """ Dispense liquid from the specified resource using pip. """

    assert len(channels) == 1 # only one channel supported for now

    channel   = channels[0] # for channel in channels
    volume    = channel.volume
    flow_rate = channel.liquid_class.flow_rate[0]

    labware_id = self.defined_labware[channel.resource.parent.name]
    pipette_id = self.select_liquid_pipette(volume)
    if pipette_id is None:
      raise NoTipError("No pipette channel of right type with tip available.")

    ot_api.lh.dispense(labware_id, well_name=channel.resource.name, pipette_id=pipette_id,
      volume=volume, flow_rate=flow_rate)

  def pickup_tips96(self, resource: Resource, **backend_kwargs):
    raise NotImplementedError("The Opentrons backend does not support the CoRe 96.")

  def discard_tips96(self, resource: Resource, **backend_kwargs):
    raise NotImplementedError("The Opentrons backend does not support the CoRe 96.")

  def aspirate96(
    self,
    resource: Resource,
    pattern: List[List[bool]],
    volume: float,
    **backend_kwargs
  ):
    raise NotImplementedError("The Opentrons backend does not support the CoRe 96.")

  def dispense96(
    self,
    resource: Resource,
    pattern: List[List[bool]],
    volume: float,
    **backend_kwargs
  ):
    raise NotImplementedError("The Opentrons backend does not support the CoRe 96.")

  def move_plate(self, plate: Plate, to: Union[Resource, Coordinate], **backend_kwargs):
    """ Move the specified plate within the robot. """
    raise NotImplementedError("Moving plates in Opentrons is not implemented yet.")

  def move_lid(self, lid: Lid, to: Union[Resource, Coordinate], **backend_kwargs):
    """ Move the specified lid within the robot. """
    raise NotImplementedError("The Opentrons backend does not support the move lid feature.")
