from typing import Optional, Union

from pylabrobot.liquid_handling.liquid_handler import LiquidHandler
from pylabrobot.resources.carrier import ResourceHolder
from pylabrobot.resources.coordinate import Coordinate
from pylabrobot.resources.plate import Plate
from pylabrobot.resources.resource import Resource


class Filter(Resource):
  """Filter for plates for use in filtering cells before flow cytometry."""

  filter_dispense_offset = Coordinate(
    0, 0, 7
  )  # height to pipette through filter (required pressure on filter)

  def __init__(
    self,
    name: str,
    size_x: float,
    size_y: float,
    size_z: float,
    nesting_z_height: float,
    category: str = "filter",
    model: Optional[str] = None,
  ):
    super().__init__(
      name=name,
      size_x=size_x,
      size_y=size_y,
      size_z=size_z,
      category=category,
      model=model,
      nesting_z_height=nesting_z_height,
    )

  async def move_filter(
    self, lh: LiquidHandler, to_dest: Union[Plate, ResourceHolder], arm: str = "core", **kwargs
  ):
    """move filter from CarrierSite to a Plate using core grippers (faster) or iSWAP (slower)"""
    await lh.move_lid(
      lid=self,
      to=to_dest,
      use_arm=arm,
      pickup_distance_from_top=15,
      core_grip_strength=20,
      return_core_gripper=True,
      **kwargs,
    )

  async def dispense_through_filter(
    self, indices: list[int], volume: float, lh: LiquidHandler, **disp_kwargs
  ):
    assert isinstance(self.parent, Plate), "Filter must be placed on a plate to be pipetted."

    offsets = disp_kwargs.get("offsets", self.filter_dispense_offset)
    if not isinstance(offsets, Coordinate):
      raise ValueError("Offsets must be a Coordinate.")

    defaults = {
      "offsets": [offsets + self.filter_dispense_offset] * len(indices)
      if isinstance(offsets, Coordinate)
      else [offsets] * len(indices),
      "transport_air_volume": 5,
      "swap_speed": 100,
      "minimum_traverse_height_at_beginning_of_a_command": self.parent.get_absolute_location(
        "c", "c", "t"
      ).z
      + 20,
      "min_z_endpos": self.parent.get_absolute_location("c", "c", "t").z + 20,
    }

    disp_params = {**defaults, **{k: v for k, v in disp_kwargs.items() if k in defaults}}

    await lh.dispense([self.parent[i][0] for i in indices], [volume] * len(indices), **disp_params)


def RetroFilterv4(name: str) -> Filter:
  return Filter(name=name, size_x=129, size_y=88, size_z=19.7, nesting_z_height=2)
