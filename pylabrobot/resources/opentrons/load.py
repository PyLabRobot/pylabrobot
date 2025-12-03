import json
import math
import os
import urllib.request
from typing import Dict, List, cast

from pylabrobot.resources import Coordinate, Tip, TipRack, TipSpot
from pylabrobot.resources.resource_holder import ResourceHolder
from pylabrobot.resources.tube_rack import TubeRack


def _download_file(url: str, local_path: str) -> bytes:
  with urllib.request.urlopen(url) as response, open(local_path, "wb") as out_file:
    data = response.read()
    out_file.write(data)
    return data  # type: ignore


def _download_ot_resource_file(ot_name: str, force_download: bool):
  """Download an Opentrons tip rack definition file from GitHub.

  Args:
    ot_name: The name of the tip rack, like "opentrons_96_tiprack_300ul".

  Returns:
    The labware definition as a dictionary.
  """
  url = f"https://raw.githubusercontent.com/Opentrons/opentrons/5b51a98ce736b2bb5aff780bf3fdf91941a038fa/shared-data/labware/definitions/2/{ot_name}/1.json"
  path = f"/tmp/{ot_name}.json"
  if force_download or not os.path.exists(path):
    data = _download_file(url=url, local_path=path)
  else:
    with open(path, "rb") as f:
      data = f.read()
  return json.loads(data)


def load_ot_tip_rack(
  ot_name: str, plr_resource_name: str, with_tips: bool = True, force_download: bool = False
) -> TipRack:
  """Convert an Opentrons tip rack definition file to a PyLabRobot TipRack resource."""

  data = _download_ot_resource_file(ot_name=ot_name, force_download=force_download)

  display_category = data["metadata"]["displayCategory"]
  if not display_category == "tipRack":
    raise ValueError("Not a tip rack definition file.")

  items = data["ordering"]
  wells: List[TipSpot] = []

  for column in items:
    for item in column:
      well_data = data["wells"][item]

      assert well_data["shape"] == "circular", "We assume all tip racks are circular."
      diameter = well_data["diameter"]
      well_size_x = well_size_y = round(diameter / math.sqrt(2), 3)

      # closure
      def make_tip(name: str) -> Tip:
        return Tip(
          name=name,
          total_tip_length=data["parameters"]["tipLength"],
          has_filter="Filter" in data["metadata"]["displayName"],
          maximal_volume=well_data["totalLiquidVolume"],
          fitting_depth=data["parameters"]["tipOverlap"],
        )

      tip_spot = TipSpot(
        name=item,
        size_x=well_size_x,
        size_y=well_size_y,
        make_tip=make_tip,
      )
      tip_spot.location = Coordinate(
        x=well_data["x"] - well_size_x / 2,
        y=well_data["y"] - well_size_y / 2,
        z=well_data["z"],
      )
      wells.append(tip_spot)

  ordering = data["ordering"]
  flattened_ordering = [item for sublist in ordering for item in sublist]
  ordered_items = dict(zip(flattened_ordering, wells))

  tr = TipRack(
    name=plr_resource_name,
    size_x=data["dimensions"]["xDimension"],
    size_y=data["dimensions"]["yDimension"],
    size_z=data["dimensions"]["zDimension"],
    ordered_items=cast(Dict[str, TipSpot], ordered_items),
    model=data["metadata"]["displayName"],
  )
  if with_tips:
    tr.fill()
  else:
    tr.empty()
  return tr


def load_ot_tube_rack(
  ot_name: str, plr_resource_name: str, force_download: bool = False
) -> TubeRack:
  """Convert an Opentrons tube rack definition file to a PyLabRobot TubeRack resource."""

  data = _download_ot_resource_file(ot_name=ot_name, force_download=force_download)

  display_category = data["metadata"]["displayCategory"]
  if display_category not in {"tubeRack", "aluminumBlock"}:
    raise ValueError("Not a tube rack definition file.")

  items = data["ordering"]
  wells: List[ResourceHolder] = []

  for column in items:
    for item in column:
      well_data = data["wells"][item]

      assert well_data["shape"] == "circular", "We assume all tip racks are circular."
      diameter = well_data["diameter"]
      well_size_x = well_size_y = round(diameter / math.sqrt(2), 3)
      well_size_z = well_data["depth"]

      resource_holder = ResourceHolder(
        name=item,
        size_x=well_size_x,
        size_y=well_size_y,
        size_z=well_size_z,
      )
      resource_holder.location = Coordinate(
        x=well_data["x"] - well_size_x / 2,
        y=well_data["y"] - well_size_y / 2,
        z=well_data["z"],
      )
      wells.append(resource_holder)

  ordering = data["ordering"]
  flattened_ordering = [item for sublist in ordering for item in sublist]
  ordered_items = dict(zip(flattened_ordering, wells))

  return TubeRack(
    name=plr_resource_name,
    size_x=data["dimensions"]["xDimension"],
    size_y=data["dimensions"]["yDimension"],
    size_z=data["dimensions"]["zDimension"],
    ordered_items=cast(Dict[str, ResourceHolder], ordered_items),
    model=data["metadata"]["displayName"],
  )
