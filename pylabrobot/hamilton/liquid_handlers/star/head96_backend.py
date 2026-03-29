"""STAR Head96 backend: translates Head96 operations into STAR firmware commands."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, List, Literal, Optional, Union

from pylabrobot.capabilities.capability import BackendParams
from pylabrobot.capabilities.liquid_handling.head96_backend import Head96Backend
from pylabrobot.capabilities.liquid_handling.standard import (
  DropTipRack,
  MultiHeadAspirationContainer,
  MultiHeadAspirationPlate,
  MultiHeadDispenseContainer,
  MultiHeadDispensePlate,
  PickupTipRack,
)
from pylabrobot.resources import Coordinate
from pylabrobot.resources.hamilton import HamiltonTip, TipSize

if TYPE_CHECKING:
  from .driver import STARDriver


def _dispensing_mode_for_op(empty: bool, jet: bool, blow_out: bool) -> int:
  """Compute firmware dispensing mode from boolean flags.

  Firmware modes:
    0 = Partial volume in jet mode
    1 = Blow out in jet mode (labelled "empty" in VENUS)
    2 = Partial volume at surface
    3 = Blow out at surface (labelled "empty" in VENUS)
    4 = Empty tip at fix position
  """
  if empty:
    return 4
  if jet:
    return 1 if blow_out else 0
  return 3 if blow_out else 2


def _channel_pattern_to_hex(pattern: List[bool]) -> str:
  """Convert a list of 96 booleans to the hex string expected by firmware."""
  assert len(pattern) == 96, "channel_pattern must be a list of 96 boolean values"
  channel_pattern_bin_str = reversed(["1" if x else "0" for x in pattern])
  return hex(int("".join(channel_pattern_bin_str), 2)).upper()[2:]


class STARHead96Backend(Head96Backend):
  """Translates Head96 operations into STAR firmware commands via the driver."""

  # Default traversal height [mm] matching the legacy STARBackend default.
  _traversal_height: float = 245.0

  def __init__(self, driver: STARDriver):
    self._driver = driver

  # ---------------------------------------------------------------------------
  # Pick up tips
  # ---------------------------------------------------------------------------

  @dataclass
  class PickUpTips96Params(BackendParams):
    """STAR-specific parameters for 96-head tip pickup."""

    tip_pickup_method: Literal["from_rack", "from_waste", "full_blowout"] = "from_rack"
    minimum_height_command_end: Optional[float] = None
    minimum_traverse_height_at_beginning_of_a_command: Optional[float] = None
    alignment_tipspot_identifier: str = "A1"

  async def pick_up_tips96(
    self, pickup: PickupTipRack, backend_params: Optional[BackendParams] = None
  ):
    """Pick up tips using the 96 head.

    Firmware command: C0 EP
    """
    if not isinstance(backend_params, STARHead96Backend.PickUpTips96Params):
      backend_params = STARHead96Backend.PickUpTips96Params()

    tip_pickup_method = backend_params.tip_pickup_method
    if tip_pickup_method not in {"from_rack", "from_waste", "full_blowout"}:
      raise ValueError(f"Invalid tip_pickup_method: '{tip_pickup_method}'.")

    prototypical_tip = next((tip for tip in pickup.tips if tip is not None), None)
    if prototypical_tip is None:
      raise ValueError("No tips found in the tip rack.")
    if not isinstance(prototypical_tip, HamiltonTip):
      raise TypeError("Tip type must be HamiltonTip.")

    ttti = await self._driver.request_or_assign_tip_type_index(prototypical_tip)

    tip_length = prototypical_tip.total_tip_length
    fitting_depth = prototypical_tip.fitting_depth
    tip_engage_height_from_tipspot = tip_length - fitting_depth

    # Adjust tip engage height based on tip size
    if prototypical_tip.tip_size == TipSize.LOW_VOLUME:
      tip_engage_height_from_tipspot += 2
    elif prototypical_tip.tip_size != TipSize.STANDARD_VOLUME:
      tip_engage_height_from_tipspot -= 2

    # Compute pickup position using absolute coordinates (deck is at origin)
    alignment_tipspot = pickup.resource.get_item(backend_params.alignment_tipspot_identifier)
    tip_spot_z = alignment_tipspot.get_absolute_location().z + pickup.offset.z
    z_pickup_position = tip_spot_z + tip_engage_height_from_tipspot

    pickup_position = (
      alignment_tipspot.get_absolute_location() + alignment_tipspot.center() + pickup.offset
    )
    pickup_position.z = round(z_pickup_position, 2)

    traversal = self._traversal_height

    if tip_pickup_method == "from_rack":
      # Move the dispensing drive down before pickup.
      # The STAR will not automatically move the dispensing drive down if it is still up.
      # See https://github.com/PyLabRobot/pylabrobot/pull/835
      #
      # Pre-computed increment values (uL / 0.019340933):
      #   position=218.19uL -> 11281, speed=261.1uL/s -> 13500,
      #   stop_speed=0 -> 0, acceleration=17406.84uL/s^2 -> 900000
      await self._driver.send_command(
        module="H0",
        command="DQ",
        dq="11281",
        dv="13500",
        du="00000",
        dr="900000",
        dw="15",
      )

    await self._driver.send_command(
      module="C0",
      command="EP",
      xs=f"{abs(round(pickup_position.x * 10)):05}",
      xd=0 if pickup_position.x >= 0 else 1,
      yh=f"{round(pickup_position.y * 10):04}",
      tt=f"{ttti:02}",
      wu={"from_rack": 0, "from_waste": 1, "full_blowout": 2}[tip_pickup_method],
      za=f"{round(pickup_position.z * 10):04}",
      zh=f"{round((backend_params.minimum_traverse_height_at_beginning_of_a_command or traversal) * 10):04}",
      ze=f"{round((backend_params.minimum_height_command_end or traversal) * 10):04}",
    )

  # ---------------------------------------------------------------------------
  # Drop tips
  # ---------------------------------------------------------------------------

  @dataclass
  class DropTips96Params(BackendParams):
    """STAR-specific parameters for 96-head tip drop."""

    minimum_height_command_end: Optional[float] = None
    minimum_traverse_height_at_beginning_of_a_command: Optional[float] = None
    alignment_tipspot_identifier: str = "A1"

  async def drop_tips96(
    self, drop: DropTipRack, backend_params: Optional[BackendParams] = None
  ):
    """Drop tips from the 96 head.

    Firmware command: C0 ER
    """
    if not isinstance(backend_params, STARHead96Backend.DropTips96Params):
      backend_params = STARHead96Backend.DropTips96Params()

    from pylabrobot.resources import TipRack

    if isinstance(drop.resource, TipRack):
      tip_spot_a1 = drop.resource.get_item(backend_params.alignment_tipspot_identifier)
      position = tip_spot_a1.get_absolute_location() + tip_spot_a1.center() + drop.offset
      tip_rack = tip_spot_a1.parent
      assert tip_rack is not None
      position.z = tip_rack.get_absolute_location().z + 1.45
    else:
      # Drop into trash or other resource: center the head in the resource.
      position = self._position_96_head_in_resource(drop.resource) + drop.offset

    traversal = self._traversal_height

    await self._driver.send_command(
      module="C0",
      command="ER",
      xs=f"{abs(round(position.x * 10)):05}",
      xd=0 if position.x >= 0 else 1,
      yh=f"{round(position.y * 10):04}",
      za=f"{round(position.z * 10):04}",
      zh=f"{round((backend_params.minimum_traverse_height_at_beginning_of_a_command or traversal) * 10):04}",
      ze=f"{round((backend_params.minimum_height_command_end or traversal) * 10):04}",
    )

  # ---------------------------------------------------------------------------
  # Aspirate
  # ---------------------------------------------------------------------------

  @dataclass
  class Aspirate96Params(BackendParams):
    """STAR-specific parameters for 96-head aspiration."""

    use_lld: bool = False
    aspiration_type: int = 0
    minimum_traverse_height_at_beginning_of_a_command: Optional[float] = None
    min_z_endpos: Optional[float] = None
    lld_search_height: float = 199.9
    minimum_height: Optional[float] = None
    second_section_height: float = 3.2
    second_section_ratio: float = 618.0
    immersion_depth: float = 0
    surface_following_distance: float = 0
    transport_air_volume: float = 5.0
    pre_wetting_volume: float = 5.0
    gamma_lld_sensitivity: int = 1
    swap_speed: float = 2.0
    settling_time: float = 1.0
    mix_position_from_liquid_surface: float = 0
    mix_surface_following_distance: float = 0
    limit_curve_index: int = 0
    pull_out_distance_transport_air: float = 10
    tadm_algorithm: bool = False
    recording_mode: int = 0

  async def aspirate96(
    self,
    aspiration: Union[MultiHeadAspirationPlate, MultiHeadAspirationContainer],
    backend_params: Optional[BackendParams] = None,
  ):
    """Aspirate using the Core96 head.

    Firmware command: C0 EA
    """
    if not isinstance(backend_params, STARHead96Backend.Aspirate96Params):
      backend_params = STARHead96Backend.Aspirate96Params()

    # Compute position
    if isinstance(aspiration, MultiHeadAspirationPlate):
      plate = aspiration.wells[0].parent
      assert plate is not None, "MultiHeadAspirationPlate well parent must not be None"
      rot = plate.get_absolute_rotation()
      if rot.x % 360 != 0 or rot.y % 360 != 0:
        raise ValueError("Plate rotation around x or y is not supported for 96 head operations")
      if rot.z % 360 == 180:
        ref_well = aspiration.wells[-1]
      elif rot.z % 360 == 0:
        ref_well = aspiration.wells[0]
      else:
        raise ValueError("96 head only supports plate rotations of 0 or 180 degrees around z")

      position = (
        ref_well.get_absolute_location()
        + ref_well.center()
        + Coordinate(z=ref_well.material_z_thickness)
        + aspiration.offset
      )
    else:
      # Container (trough): center the head
      x_width = (12 - 1) * 9  # 12 tips in a row, 9 mm between them
      y_width = (8 - 1) * 9  # 8 tips in a column, 9 mm between them
      x_position = (aspiration.container.get_absolute_size_x() - x_width) / 2
      y_position = (aspiration.container.get_absolute_size_y() - y_width) / 2 + y_width
      position = (
        aspiration.container.get_absolute_location(z="cavity_bottom")
        + Coordinate(x=x_position, y=y_position)
        + aspiration.offset
      )

    tip = next(tip for tip in aspiration.tips if tip is not None)

    liquid_height = position.z + (aspiration.liquid_height or 0)

    volume = aspiration.volume
    flow_rate = aspiration.flow_rate or 250
    blow_out_air_volume = aspiration.blow_out_air_volume or 0

    traversal = self._traversal_height

    immersion_depth = backend_params.immersion_depth
    immersion_depth_direction = 0 if immersion_depth >= 0 else 1

    await self._driver.send_command(
      module="C0",
      command="EA",
      aa=backend_params.aspiration_type,
      xs=f"{abs(round(position.x * 10)):05}",
      xd=0 if position.x >= 0 else 1,
      yh=f"{round(position.y * 10):04}",
      zh=f"{round((backend_params.minimum_traverse_height_at_beginning_of_a_command or traversal) * 10):04}",
      ze=f"{round((backend_params.min_z_endpos or traversal) * 10):04}",
      lz=f"{round(backend_params.lld_search_height * 10):04}",
      zt=f"{round(liquid_height * 10):04}",
      pp=f"{round(backend_params.pull_out_distance_transport_air * 10):04}",
      zm=f"{round((backend_params.minimum_height or position.z) * 10):04}",
      zv=f"{round(backend_params.second_section_height * 10):04}",
      zq=f"{round(backend_params.second_section_ratio * 10):05}",
      iw=f"{round(abs(immersion_depth) * 10):03}",
      ix=immersion_depth_direction,
      fh=f"{round(backend_params.surface_following_distance * 10):03}",
      af=f"{round(volume * 10):05}",
      ag=f"{round(flow_rate * 10):04}",
      vt=f"{round(backend_params.transport_air_volume * 10):03}",
      bv=f"{round(blow_out_air_volume * 10):05}",
      wv=f"{round(backend_params.pre_wetting_volume * 10):05}",
      cm=int(backend_params.use_lld),
      cs=backend_params.gamma_lld_sensitivity,
      bs=f"{round(backend_params.swap_speed * 10):04}",
      wh=f"{round(backend_params.settling_time * 10):02}",
      hv=f"{round(aspiration.mix.volume * 10):05}" if aspiration.mix is not None else "00000",
      hc=f"{aspiration.mix.repetitions:02}" if aspiration.mix is not None else "00",
      hp=f"{round(backend_params.mix_position_from_liquid_surface * 10):03}",
      mj=f"{round(backend_params.mix_surface_following_distance * 10):03}",
      hs=f"{round(aspiration.mix.flow_rate * 10):04}" if aspiration.mix is not None else "1200",
      cw=_channel_pattern_to_hex([True] * 96),
      cr=f"{backend_params.limit_curve_index:03}",
      cj=backend_params.tadm_algorithm,
      cx=backend_params.recording_mode,
    )

  # ---------------------------------------------------------------------------
  # Dispense
  # ---------------------------------------------------------------------------

  @dataclass
  class Dispense96Params(BackendParams):
    """STAR-specific parameters for 96-head dispense."""

    jet: bool = False
    empty: bool = False
    blow_out: bool = False
    use_lld: bool = False
    minimum_traverse_height_at_beginning_of_a_command: Optional[float] = None
    min_z_endpos: Optional[float] = None
    lld_search_height: float = 199.9
    minimum_height: Optional[float] = None
    second_section_height: float = 3.2
    second_section_ratio: float = 618.0
    immersion_depth: float = 0
    surface_following_distance: float = 0
    transport_air_volume: float = 5.0
    gamma_lld_sensitivity: int = 1
    swap_speed: float = 2.0
    settling_time: float = 5.0
    mix_position_from_liquid_surface: float = 0
    mix_surface_following_distance: float = 0
    limit_curve_index: int = 0
    cut_off_speed: float = 5.0
    stop_back_volume: float = 0
    pull_out_distance_transport_air: float = 10
    side_touch_off_distance: int = 0
    tadm_algorithm: bool = False
    recording_mode: int = 0

  async def dispense96(
    self,
    dispense: Union[MultiHeadDispensePlate, MultiHeadDispenseContainer],
    backend_params: Optional[BackendParams] = None,
  ):
    """Dispense using the Core96 head.

    Firmware command: C0 ED
    """
    if not isinstance(backend_params, STARHead96Backend.Dispense96Params):
      backend_params = STARHead96Backend.Dispense96Params()

    # Compute position
    if isinstance(dispense, MultiHeadDispensePlate):
      plate = dispense.wells[0].parent
      assert plate is not None, "MultiHeadDispensePlate well parent must not be None"
      rot = plate.get_absolute_rotation()
      if rot.x % 360 != 0 or rot.y % 360 != 0:
        raise ValueError("Plate rotation around x or y is not supported for 96 head operations")
      if rot.z % 360 == 180:
        ref_well = dispense.wells[-1]
      elif rot.z % 360 == 0:
        ref_well = dispense.wells[0]
      else:
        raise ValueError("96 head only supports plate rotations of 0 or 180 degrees around z")

      position = (
        ref_well.get_absolute_location()
        + ref_well.center()
        + Coordinate(z=ref_well.material_z_thickness)
        + dispense.offset
      )
    else:
      # Container (trough): center the head
      x_width = (12 - 1) * 9
      y_width = (8 - 1) * 9
      x_position = (dispense.container.get_absolute_size_x() - x_width) / 2
      y_position = (dispense.container.get_absolute_size_y() - y_width) / 2 + y_width
      position = (
        dispense.container.get_absolute_location(z="cavity_bottom")
        + Coordinate(x=x_position, y=y_position)
        + dispense.offset
      )

    tip = next(tip for tip in dispense.tips if tip is not None)

    liquid_height = position.z + (dispense.liquid_height or 0)

    volume = dispense.volume
    flow_rate = dispense.flow_rate or 120
    blow_out_air_volume = dispense.blow_out_air_volume or 0

    dispense_mode = _dispensing_mode_for_op(
      empty=backend_params.empty,
      jet=backend_params.jet,
      blow_out=backend_params.blow_out,
    )

    traversal = self._traversal_height

    immersion_depth = backend_params.immersion_depth
    immersion_depth_direction = 0 if immersion_depth >= 0 else 1

    await self._driver.send_command(
      module="C0",
      command="ED",
      da=dispense_mode,
      xs=f"{abs(round(position.x * 10)):05}",
      xd=0 if position.x >= 0 else 1,
      yh=f"{round(position.y * 10):04}",
      zm=f"{round((backend_params.minimum_height or position.z) * 10):04}",
      zv=f"{round(backend_params.second_section_height * 10):04}",
      zq=f"{round(backend_params.second_section_ratio * 10):05}",
      lz=f"{round(backend_params.lld_search_height * 10):04}",
      zt=f"{round(liquid_height * 10):04}",
      pp=f"{round(backend_params.pull_out_distance_transport_air * 10):04}",
      iw=f"{round(abs(immersion_depth) * 10):03}",
      ix=immersion_depth_direction,
      fh=f"{round(backend_params.surface_following_distance * 10):03}",
      zh=f"{round((backend_params.minimum_traverse_height_at_beginning_of_a_command or traversal) * 10):04}",
      ze=f"{round((backend_params.min_z_endpos or traversal) * 10):04}",
      df=f"{round(volume * 10):05}",
      dg=f"{round(flow_rate * 10):04}",
      es=f"{round(backend_params.cut_off_speed * 10):04}",
      ev=f"{round(backend_params.stop_back_volume * 10):03}",
      vt=f"{round(backend_params.transport_air_volume * 10):03}",
      bv=f"{round(blow_out_air_volume * 10):05}",
      cm=int(backend_params.use_lld),
      cs=backend_params.gamma_lld_sensitivity,
      ej=f"{backend_params.side_touch_off_distance:02}",
      bs=f"{round(backend_params.swap_speed * 10):04}",
      wh=f"{round(backend_params.settling_time * 10):02}",
      hv=f"{round(dispense.mix.volume * 10):05}" if dispense.mix is not None else "00000",
      hc=f"{dispense.mix.repetitions:02}" if dispense.mix is not None else "00",
      hp=f"{round(backend_params.mix_position_from_liquid_surface * 10):03}",
      mj=f"{round(backend_params.mix_surface_following_distance * 10):03}",
      hs=f"{round(dispense.mix.flow_rate * 10):04}" if dispense.mix is not None else "1200",
      cw=_channel_pattern_to_hex([True] * 96),
      cr=f"{backend_params.limit_curve_index:03}",
      cj=backend_params.tadm_algorithm,
      cx=backend_params.recording_mode,
    )

  # ---------------------------------------------------------------------------
  # Helpers
  # ---------------------------------------------------------------------------

  @staticmethod
  def _position_96_head_in_resource(resource) -> Coordinate:
    """Compute the A1 position for centering the 96-head in a resource."""
    head_size_x = 9 * 11  # 12 channels, 9mm spacing
    head_size_y = 9 * 7  # 8 channels, 9mm spacing
    channel_size = 9
    loc = resource.get_absolute_location()
    loc.x += (resource.get_size_x() - head_size_x) / 2 + channel_size / 2
    loc.y += (resource.get_size_y() - head_size_y) / 2 + channel_size / 2
    return loc
