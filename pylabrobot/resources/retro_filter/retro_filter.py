from typing import Optional
from pylabrobot.liquid_handling.liquid_handler import LiquidHandler
from pylabrobot.resources.coordinate import Coordinate
from pylabrobot.resources.filter import Filter
from pylabrobot.resources.plate import Plate


class RetroFilter(Filter):
  filter_dispense_offset = Coordinate(
    0, 0, 7
  )  # height to pipette through filter (required pressure on filter)

  def __init__(
    self,
    name: str,
    size_x: float = 129,
    size_y: float = 88,
    size_z: float = 19.7,
    category: str = "filter",
    model: Optional[str] = None,
    nesting_z_height: float = 2,
  ):
    self.nesting_z_height = nesting_z_height
    super().__init__(
      name=name, size_x=size_x, size_y=size_y, size_z=size_z, category=category, model=model
    )

  async def move_filter(
    self,
    lh: LiquidHandler,
    to_dest: Plate,  # lh drop_resource only supports filters on Plates (for now)
    arm: str = "core",
    channel_1=7,
    channel_2=8,
    **kwargs,
  ):
    """move filter from CarrierSite to a Plate using core grippers (faster) or iSWAP (slower)"""

    pickup_kwargs = kwargs.copy()
    if arm == "core":
      pickup_kwargs.update(
        {"core_grip_strength": 15, "channel_1": channel_1, "channel_2": channel_2}
      )
    await lh.pick_up_resource(
      resource=self, use_arm=arm, pickup_distance_from_top=15, **pickup_kwargs
    )

    await lh.drop_resource(destination=to_dest, use_arm=arm, **kwargs)

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
