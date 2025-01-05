from typing import List, Optional
from pylabrobot.liquid_handling.liquid_handler import LiquidHandler
from pylabrobot.resources.coordinate import Coordinate
from pylabrobot.resources.filter import Filter
from pylabrobot.resources.plate import Plate


class RetroFilter(Filter):
  filter_dispense_offset = Coordinate(
    0, 0, 9
  )  # height above parent plate to dispense through filter

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
    self,
    indices: List[int],
    volumes: List[float],
    lh: LiquidHandler,
    anticlog_loops: int = 4,
    **dispense_kwargs,
  ):
    if not isinstance(self.parent, Plate):
      raise RuntimeError("Filter must be on a Plate.")

    if len(volumes) != len(indices):
      raise ValueError("Mismatch in volumes vs. indices.")

    offsets = dispense_kwargs.pop("offsets", Coordinate(0, 0, 0))
    if isinstance(offsets, Coordinate):
      offsets = [offsets] * len(indices)
    offsets = [offset + self.filter_dispense_offset for offset in offsets]

    wells = [self.parent[i][0] for i in indices]
    travel_z = self.parent.get_absolute_location("c", "c", "t").z + 20
    pip_z_at_dsp = [
      well.get_absolute_location().z + offsets[i].z + well.material_z_thickness
      for i, well in enumerate(wells)
    ]
    print("pip_z_at_dsp", pip_z_at_dsp)
    overrides = {
      "offsets": offsets,
      "transport_air_volume": 0,
      "settling_time": 5,
      "swap_speed": 100,
      "pull_out_distance_transport_air": 0,
      "minimum_traverse_height_at_beginning_of_a_command": travel_z,
      "min_z_endpos": min(pip_z_at_dsp),
    }
    merged_kwargs = {**dispense_kwargs, **overrides}

    await lh.dispense(resources=wells, vols=volumes, **merged_kwargs)
    for i in range(anticlog_loops):
      await lh.backend.position_channels_in_y_direction_relative(
        ys={
          7: +2,
          6: +2,
        },
        yv=100,
      )
      await lh.backend.position_channels_in_y_direction_relative(
        ys={
          7: -4,
          6: -4,
        },
        yv=100,
      )
      await lh.backend.position_channels_in_y_direction_relative(
        ys={
          7: +2,
          6: +2,
        },
        yv=100,
      )
      # this movement is choppy, ideally we use dispense on the fly X0 continuous drive movement
      for i in range(5):
        await lh.backend.move_iswap_x_relative(step_size=0.4, allow_splitting=False)
      for i in range(5):
        await lh.backend.move_iswap_x_relative(step_size=-0.8, allow_splitting=False)
      for i in range(5):
        await lh.backend.move_iswap_x_relative(step_size=0.4, allow_splitting=False)
