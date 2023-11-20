import math
import json
from typing import Union, List, TYPE_CHECKING, cast

try:
  import opentrons_shared_data.labware
  USE_OT = True
except ImportError:
  USE_OT = False

from pylabrobot.resources.coordinate import Coordinate
from pylabrobot.resources.plate import Plate
from pylabrobot.resources.tip import Tip, TipCreator
from pylabrobot.resources.tip_rack import TipRack, TipSpot
from pylabrobot.resources.well import Well

if TYPE_CHECKING:
  from opentrons_shared_data.labware.dev_types import LabwareDefinition


class UnknownResourceType(Exception):
  pass


def ot_definition_to_resource(
  data: "LabwareDefinition",
  name: str) -> Union[Plate, TipRack]:
  """ Convert an Opentrons definition file to a PyLabRobot resource file. """

  if not USE_OT:
    raise ImportError("opentrons_shared_data is not installed. "
                      "run `pip install opentrons_shared_data`")

  display_category = data["metadata"]["displayCategory"]

  size_x = data["dimensions"]["xDimension"]
  size_y = data["dimensions"]["yDimension"]
  size_z = data["dimensions"]["zDimension"]

  if display_category in ["wellPlate", "tipRack"]:
    items = data["ordering"]
    wells: List[List[Union[TipSpot, Well]]] = [] # TODO: can we use TypeGuard?

    def volume_from_name(name: str) -> float:
      # like "Opentrons 96 Filter Tip Rack 200 µL"
      items = name.split(" ")
      volume, unit = items[-2], items[-1]
      if unit == "mL":
        volume *= 1000
      return float(volume)

    for i, column in enumerate(items):
      wells.append([])
      for item in column:
        well_data = data["wells"][item]

        if well_data["shape"] == "circular":
          diameter = well_data["diameter"]
          # pythagoras. rounding: good enough?
          well_size_x = well_size_y = round(diameter/math.sqrt(2), 3)
        elif "xDimension" in well_data and "yDimension" in well_data:
          well_size_x = well_data["xDimension"]
          well_size_y = well_data["yDimension"]
        else:
          raise ValueError("Unknown well shape.")

        well_size_z = well_data["depth"]

        location=Coordinate(x=well_data["x"], y=well_data["y"], z=well_data["z"])
        if display_category == "wellPlate":
          well = Well(
            name=item,
            size_x=well_size_x,
            size_y=well_size_y,
            size_z=well_size_z,
          )
          well.location = location
          wells[i].append(well)
        else:
          # closure
          def make_make_tip(well_data) -> TipCreator:
            def make_tip() -> Tip:
              total_tip_length = well_data["depth"]
              return Tip(
                total_tip_length=total_tip_length,
                has_filter="Filter" in data["metadata"]["displayName"],
                maximal_volume=volume_from_name(data["metadata"]["displayName"]),
                fitting_depth=data["parameters"]["tipOverlap"]
              )
            return make_tip

          tip_spot = TipSpot(
            name=item,
            size_x=well_size_x,
            size_y=well_size_y,
            make_tip=make_make_tip(well_data)
          )
          tip_spot.location = location
          wells[i].append(tip_spot)

    if display_category == "wellPlate":
      return Plate(
        name=name,
        size_x=size_x,
        size_y=size_y,
        size_z=size_z,
        items=cast(List[List[Well]], wells),
        model=data["metadata"]["displayName"]
      )
    elif display_category == "tipRack":
      return TipRack(
        name=name,
        size_x=size_x,
        size_y=size_y,
        size_z=size_z,
        items=cast(List[List[TipSpot]], wells),
        model=data["metadata"]["displayName"]
      )
  raise UnknownResourceType(f"Unknown resource type '{display_category}'.")


def load_opentrons_resource(fn: str, name: str) -> Union[Plate, TipRack]:
  """ Load an Opentrons resource from a file.

  Args:
    fn: path to the file.

  Returns:
    A :class:`~pylabrobot.resources.Resource`.

  Raises:
    ValueError: if the file is not a valid opentrons definition file.

    UnknownResourceType: if the file is a valid opentrons definition file, but the resource type is
      not supported.

  Examples:

    Load a tip rack:

    >>> from pylabrobot.resources.opentrons import load_opentrons_resource
    >>> load_opentron_resource("opentrons/definitions/2/96_standard.json", "96Standard")

  """

  with open(fn, "r", encoding="utf-8") as f:
    data = json.load(f)
  return ot_definition_to_resource(data, name)


def load_shared_opentrons_resource(
  definition: str,
  name: str,
  version: int = 1
) -> Union[Plate, TipRack]:
  """ Load an Opentrons resource from the shared Opentrons resource library.

  See https://github.com/Opentrons/opentrons/tree/edge/shared-data.

  Args:
    definition: name of the labware definition.
    version: version of the labware definition.
    name: desired name of the PyLabRobot
      :class:`~pylabrobot.resources.Resource`

  Returns:
    A :class:`~pylabrobot.resources.Resource`.

  Raises:
    ValueError: if the file is not a valid opentrons definition file.

    UnknownResourceType: if the file is a valid opentrons definition file, but the resource type is
      not supported.

  Examples:

    Load a tip rack:

    >>> from pylabrobot.resources.opentrons import load_shared_opentrons_resource
    >>> load_shared_opentrons_resource("opentrons_96_tiprack_labware", "96Standard")

  """

  data = opentrons_shared_data.labware.load_definition(definition, version)
  return ot_definition_to_resource(data, name)
