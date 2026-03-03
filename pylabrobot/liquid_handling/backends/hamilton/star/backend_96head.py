"""96-head mixin for the STAR backend.

Contains STARBackend96HeadMixin with all 96-head functionality.
"""

import datetime
import logging
import warnings
from typing import (
  Dict,
  List,
  Literal,
  Optional,
  Union,
)

from pylabrobot.liquid_handling.liquid_classes.hamilton import (
  HamiltonLiquidClass,
  get_star_liquid_class,
)
from pylabrobot.liquid_handling.standard import (
  DropTipRack,
  MultiHeadAspirationContainer,
  MultiHeadAspirationPlate,
  MultiHeadDispenseContainer,
  MultiHeadDispensePlate,
  PickupTipRack,
)
from pylabrobot.resources import (
  Coordinate,
  Plate,
  Resource,
  TipRack,
)
from pylabrobot.resources.hamilton import (
  HamiltonTip,
  TipSize,
)
from pylabrobot.resources.liquid import Liquid
from pylabrobot.resources.trash import Trash

from pylabrobot.liquid_handling.backends.hamilton.star.errors import (
  STARFirmwareError,
  convert_star_firmware_error_to_plr_error,
)
from pylabrobot.liquid_handling.backends.hamilton.star.shared import (
  Head96Information,
  STARBaseMixin,
  _dispensing_mode_for_op,
  _requires_head96,
  need_iswap_parked,
)

logger = logging.getLogger("pylabrobot")


class STARBackend96HeadMixin(STARBaseMixin):
  """Mixin class providing all 96-head functionality for the STAR backend."""

  # -------------- Properties & Constants --------------

  @property
  def head96_installed(self) -> Optional[bool]:
    return self.core96_head_installed

  # Conversion factors for 96-Head (mm per increment)
  _head96_z_drive_mm_per_increment = 0.005
  _head96_y_drive_mm_per_increment = 0.015625
  _head96_dispensing_drive_mm_per_increment = 0.001025641026
  _head96_dispensing_drive_uL_per_increment = 0.019340933
  _head96_squeezer_drive_mm_per_increment = 0.0002086672009

  HEAD96_DISPENSING_DRIVE_VOL_LIMIT_BOTTOM = 0
  HEAD96_DISPENSING_DRIVE_VOL_LIMIT_TOP = 1244.59

  # -------------- Position helpers --------------

  def _position_96_head_in_resource(self, resource: Resource) -> Coordinate:
    """The firmware command expects location of tip A1 of the head. We center the head in the given
    resource."""
    head_size_x = 9 * 11  # 12 channels, 9mm spacing in between
    head_size_y = 9 * 7  #   8 channels, 9mm spacing in between
    channel_size = 9
    loc = resource.get_location_wrt(self.deck)
    loc.x += (resource.get_size_x() - head_size_x) / 2 + channel_size / 2
    loc.y += (resource.get_size_y() - head_size_y) / 2 + channel_size / 2
    return loc

  def _check_96_position_legal(self, c: Coordinate, skip_z=False) -> None:
    """Validate that a coordinate is within the allowed range for the 96 head.

    Args:
      c: The coordinate of the A1 position of the head.
      skip_z: If True, the z coordinate is not checked. This is useful for commands that handle
        the z coordinate separately, such as the big four.

    Raises:
      ValueError: If one or more components are out of range. The error message contains all offending components.
    """

    # TODO: these are values for a STARBackend. Find them for a STARlet.

    errors = []
    if not (-271.0 <= c.x <= 974.0):
      errors.append(f"x={c.x}")
    if not (108.0 <= c.y <= 560.0):
      errors.append(f"y={c.y}")
    if not (180.5 <= c.z <= 342.5) and not skip_z:
      errors.append(f"z={c.z}")

    if len(errors) > 0:
      raise ValueError(
        "Illegal 96 head position: "
        + ", ".join(errors)
        + " (allowed ranges: x [-271, 974], y [108, 560], z [180.5, 342.5])"
      )

  # -------------- Setup helper --------------

  async def _setup_96_head(self, skip: bool):
    """Set up the 96-head during initialization.

    Args:
      skip: If True, skip 96-head setup entirely.
    """
    if self.core96_head_installed and not skip:
      # Initialize 96-head
      core96_head_initialized = await self.request_core_96_head_initialization_status()
      if not core96_head_initialized:
        await self.initialize_core_96_head(
          trash96=self.deck.get_trash_area96(),
          z_position_at_the_command_end=self._channel_traversal_height,
        )

      # Cache firmware version and configuration for version-specific behavior
      fw_version = await self.head96_request_firmware_version()
      configuration_96head = await self._head96_request_configuration()
      head96_type = await self.head96_request_type()

      self._head96_information = Head96Information(
        fw_version=fw_version,
        supports_clot_monitoring_clld=bool(int(configuration_96head[0])),
        stop_disc_type="core_i" if configuration_96head[1] == "0" else "core_ii",
        instrument_type="legacy" if configuration_96head[2] == "0" else "FM-STAR",
        head_type=head96_type,
      )

  # -------------- High-level operations --------------

  @_requires_head96
  async def pick_up_tips96(
    self,
    pickup: PickupTipRack,
    tip_pickup_method: Literal["from_rack", "from_waste", "full_blowout"] = "from_rack",
    minimum_height_command_end: Optional[float] = None,
    minimum_traverse_height_at_beginning_of_a_command: Optional[float] = None,
    experimental_alignment_tipspot_identifier: str = "A1",
  ):
    """Pick up tips using the 96 head.

    `tip_pickup_method` can be one of the following:
        - "from_rack": standard tip pickup from a tip rack. this moves the plunger all the way down before mounting tips.
        - "from_waste":
            1. it actually moves the plunger all the way up
            2. mounts tips
            3. moves up like 10mm
            4. moves plunger all the way down
            5. moves to traversal height (tips out of rack)
        - "full_blowout":
            1. it actually moves the plunger all the way up
            2. mounts tips
            3. moves to traversal height (tips out of rack)

    Args:
      pickup: The standard `PickupTipRack` operation.
      tip_pickup_method: The method to use for picking up tips. One of "from_rack", "from_waste", "full_blowout".
      minimum_height_command_end: The minimum height to move to at the end of the command.
      minimum_traverse_height_at_beginning_of_a_command: The minimum height to move to at the beginning of the command.
      experimental_alignment_tipspot_identifier: The tipspot to use for alignment with head's A1 channel. Defaults to "tipspot A1".  allowed range is A1 to H12.
    """

    if isinstance(tip_pickup_method, int):
      warnings.warn(
        "tip_pickup_method as int is deprecated and will be removed in the future. Use string literals instead.",
        DeprecationWarning,
      )
      tip_pickup_method = {0: "from_rack", 1: "from_waste", 2: "full_blowout"}[tip_pickup_method]

    if tip_pickup_method not in {"from_rack", "from_waste", "full_blowout"}:
      raise ValueError(f"Invalid tip_pickup_method: '{tip_pickup_method}'.")

    prototypical_tip = next((tip for tip in pickup.tips if tip is not None), None)
    if prototypical_tip is None:
      raise ValueError("No tips found in the tip rack.")
    if not isinstance(prototypical_tip, HamiltonTip):
      raise TypeError("Tip type must be HamiltonTip.")

    ttti = await self.get_or_assign_tip_type_index(prototypical_tip)

    tip_length = prototypical_tip.total_tip_length
    fitting_depth = prototypical_tip.fitting_depth
    tip_engage_height_from_tipspot = tip_length - fitting_depth

    # Adjust tip engage height based on tip size
    if prototypical_tip.tip_size == TipSize.LOW_VOLUME:
      tip_engage_height_from_tipspot += 2
    elif prototypical_tip.tip_size != TipSize.STANDARD_VOLUME:
      tip_engage_height_from_tipspot -= 2

    # Compute pickup Z
    alignment_tipspot = pickup.resource.get_item(experimental_alignment_tipspot_identifier)
    tip_spot_z = alignment_tipspot.get_location_wrt(self.deck).z + pickup.offset.z
    z_pickup_position = tip_spot_z + tip_engage_height_from_tipspot

    # Compute full position (used for x/y)
    pickup_position = (
      alignment_tipspot.get_location_wrt(self.deck) + alignment_tipspot.center() + pickup.offset
    )
    pickup_position.z = round(z_pickup_position, 2)

    self._check_96_position_legal(pickup_position, skip_z=True)

    if tip_pickup_method == "from_rack":
      # the STAR will not automatically move the dispensing drive down if it is still up
      # so we need to move it down here
      # see https://github.com/PyLabRobot/pylabrobot/pull/835
      lowest_dispensing_drive_height_no_tips = 218.19
      await self.head96_dispensing_drive_move_to_position(lowest_dispensing_drive_height_no_tips)

    try:
      await self.pick_up_tips_core96(
        x_position=abs(round(pickup_position.x * 10)),
        x_direction=0 if pickup_position.x >= 0 else 1,
        y_position=round(pickup_position.y * 10),
        tip_type_idx=ttti,
        tip_pickup_method={
          "from_rack": 0,
          "from_waste": 1,
          "full_blowout": 2,
        }[tip_pickup_method],
        z_deposit_position=round(pickup_position.z * 10),
        minimum_traverse_height_at_beginning_of_a_command=round(
          (minimum_traverse_height_at_beginning_of_a_command or self._channel_traversal_height) * 10
        ),
        minimum_height_command_end=round(
          (minimum_height_command_end or self._channel_traversal_height) * 10
        ),
      )
    except STARFirmwareError as e:
      if plr_e := convert_star_firmware_error_to_plr_error(e):
        raise plr_e from e
      raise e

  @_requires_head96
  async def drop_tips96(
    self,
    drop: DropTipRack,
    minimum_height_command_end: Optional[float] = None,
    minimum_traverse_height_at_beginning_of_a_command: Optional[float] = None,
    experimental_alignment_tipspot_identifier: str = "A1",
  ):
    """Drop tips from the 96 head."""

    if isinstance(drop.resource, TipRack):
      tip_spot_a1 = drop.resource.get_item(experimental_alignment_tipspot_identifier)
      position = tip_spot_a1.get_location_wrt(self.deck) + tip_spot_a1.center() + drop.offset
      tip_rack = tip_spot_a1.parent
      assert tip_rack is not None
      position.z = tip_rack.get_location_wrt(self.deck).z + 1.45
      # This should be the case for all normal hamilton tip carriers + racks
      # In the future, we might want to make this more flexible
      assert abs(position.z - 216.4) < 1e-6, f"z position must be 216.4, got {position.z}"
    else:
      position = self._position_96_head_in_resource(drop.resource) + drop.offset

    self._check_96_position_legal(position, skip_z=True)

    x_direction = 0 if position.x >= 0 else 1

    return await self.discard_tips_core96(
      x_position=abs(round(position.x * 10)),
      x_direction=x_direction,
      y_position=round(position.y * 10),
      z_deposit_position=round(position.z * 10),
      minimum_traverse_height_at_beginning_of_a_command=round(
        (minimum_traverse_height_at_beginning_of_a_command or self._channel_traversal_height) * 10
      ),
      minimum_height_command_end=round(
        (minimum_height_command_end or self._channel_traversal_height) * 10
      ),
    )

  @_requires_head96
  async def aspirate96(
    self,
    aspiration: Union[MultiHeadAspirationPlate, MultiHeadAspirationContainer],
    jet: bool = False,
    blow_out: bool = False,
    use_lld: bool = False,
    pull_out_distance_transport_air: float = 10,
    hlc: Optional[HamiltonLiquidClass] = None,
    aspiration_type: int = 0,
    minimum_traverse_height_at_beginning_of_a_command: Optional[float] = None,
    min_z_endpos: Optional[float] = None,
    lld_search_height: float = 199.9,
    minimum_height: Optional[float] = None,
    second_section_height: float = 3.2,
    second_section_ratio: float = 618.0,
    immersion_depth: float = 0,
    surface_following_distance: float = 0,
    transport_air_volume: float = 5.0,
    pre_wetting_volume: float = 5.0,
    gamma_lld_sensitivity: int = 1,
    swap_speed: float = 2.0,
    settling_time: float = 1.0,
    mix_position_from_liquid_surface: float = 0,
    mix_surface_following_distance: float = 0,
    limit_curve_index: int = 0,
    disable_volume_correction: bool = False,
    # Deprecated parameters, to be removed in future versions
    # rm: >2026-01
    liquid_surface_sink_distance_at_the_end_of_aspiration: float = 0,
    minimal_end_height: Optional[float] = None,
    air_transport_retract_dist: Optional[float] = None,
    maximum_immersion_depth: Optional[float] = None,
    surface_following_distance_during_mix: float = 0,
    tube_2nd_section_height_measured_from_zm: float = 3.2,
    tube_2nd_section_ratio: float = 618.0,
    immersion_depth_direction: Optional[int] = None,
    mix_volume: float = 0,
    mix_cycles: int = 0,
    speed_of_mix: float = 0.0,
  ):
    """Aspirate using the Core96 head.

    Args:
      aspiration: The aspiration to perform.

      jet: Whether to search for a jet liquid class. Only used on dispense.
      blow_out: Whether to use "blow out" dispense mode. Only used on dispense. Note that this is
        labelled as "empty" in the VENUS liquid editor, but "blow out" in the firmware
        documentation.
      hlc: The Hamiltonian liquid class to use. If `None`, the liquid class will be determined
        automatically.

      use_lld: If True, use gamma liquid level detection. If False, use liquid height.
      pull_out_distance_transport_air: The distance to retract after aspirating, in millimeters.

      aspiration_type: The type of aspiration to perform. (0 = simple; 1 = sequence; 2 = cup emptied)
      minimum_traverse_height_at_beginning_of_a_command: The minimum height to move to before
        starting the command.
      min_z_endpos: The minimum height to move to after the command.
      lld_search_height: The height to search for the liquid level.
      minimum_height: Minimum height (maximum immersion depth)
      second_section_height: Height of the second section.
      second_section_ratio: Ratio of [the diameter of the bottom * 10000] / [the diameter of the top]
      immersion_depth: The immersion depth above or below the liquid level.
      surface_following_distance: The distance to follow the liquid surface when aspirating.
      transport_air_volume: The volume of air to aspirate after the liquid.
      pre_wetting_volume: The volume of liquid to use for pre-wetting.
      gamma_lld_sensitivity: The sensitivity of the gamma liquid level detection.
      swap_speed: Swap speed (on leaving liquid) [1mm/s]. Must be between 0.3 and 160. Default 2.
      settling_time: The time to wait after aspirating.
      mix_position_from_liquid_surface: The position of the mix from the liquid surface.
      mix_surface_following_distance: The distance to follow the liquid surface during mix.
      limit_curve_index: The index of the limit curve to use.
      disable_volume_correction: Whether to disable liquid class volume correction.
    """

    # # # TODO: delete > 2026-01 # # #
    if mix_volume != 0 or mix_cycles != 0 or speed_of_mix != 0:
      raise NotImplementedError(
        "Mixing through backend kwargs is deprecated. Use the `mix` parameter of LiquidHandler.aspirate96 instead. "
        "https://docs.pylabrobot.org/user_guide/00_liquid-handling/mixing.html"
      )

    if immersion_depth_direction is not None:
      warnings.warn(
        "The immersion_depth_direction parameter is deprecated and will be removed in the future. "
        "Use positive values for immersion_depth to move into the liquid, and negative values to move "
        "out of the liquid.",
        DeprecationWarning,
      )

    if liquid_surface_sink_distance_at_the_end_of_aspiration != 0:
      surface_following_distance = liquid_surface_sink_distance_at_the_end_of_aspiration
      warnings.warn(
        "The liquid_surface_sink_distance_at_the_end_of_aspiration parameter is deprecated and will be removed in the future. "
        "Use the Hamilton-standard surface_following_distance parameter instead.\n"
        "liquid_surface_sink_distance_at_the_end_of_aspiration currently superseding surface_following_distance.",
        DeprecationWarning,
      )

    if minimal_end_height is not None:
      min_z_endpos = minimal_end_height
      warnings.warn(
        "The minimal_end_height parameter is deprecated and will be removed in the future. "
        "Use the Hamilton-standard min_z_endpos parameter instead.\n"
        "min_z_endpos currently superseding minimal_end_height.",
        DeprecationWarning,
      )

    if air_transport_retract_dist is not None:
      pull_out_distance_transport_air = air_transport_retract_dist
      warnings.warn(
        "The air_transport_retract_dist parameter is deprecated and will be removed in the future. "
        "Use the Hamilton-standard pull_out_distance_transport_air parameter instead.\n"
        "pull_out_distance_transport_air currently superseding air_transport_retract_dist.",
        DeprecationWarning,
      )

    if maximum_immersion_depth is not None:
      minimum_height = maximum_immersion_depth
      warnings.warn(
        "The maximum_immersion_depth parameter is deprecated and will be removed in the future. "
        "Use the Hamilton-standard minimum_height parameter instead.\n"
        "minimum_height currently superseding maximum_immersion_depth.",
        DeprecationWarning,
      )

    if surface_following_distance_during_mix != 0:
      mix_surface_following_distance = surface_following_distance_during_mix
      warnings.warn(
        "The surface_following_distance_during_mix parameter is deprecated and will be removed in the future. "
        "Use the Hamilton-standard mix_surface_following_distance parameter instead.\n"
        "mix_surface_following_distance currently superseding surface_following_distance_during_mix.",
        DeprecationWarning,
      )

    if tube_2nd_section_height_measured_from_zm != 3.2:
      second_section_height = tube_2nd_section_height_measured_from_zm
      warnings.warn(
        "The tube_2nd_section_height_measured_from_zm parameter is deprecated and will be removed in the future. "
        "Use the Hamilton-standard second_section_height parameter instead.\n"
        "second_section_height_measured_from_zm currently superseding second_section_height.",
        DeprecationWarning,
      )

    if tube_2nd_section_ratio != 618.0:
      second_section_ratio = tube_2nd_section_ratio
      warnings.warn(
        "The tube_2nd_section_ratio parameter is deprecated and will be removed in the future. "
        "Use the Hamilton-standard second_section_ratio parameter instead.\n"
        "second_section_ratio currently superseding tube_2nd_section_ratio.",
        DeprecationWarning,
      )
    # # # delete # # #

    # get the first well and tip as representatives
    if isinstance(aspiration, MultiHeadAspirationPlate):
      plate = aspiration.wells[0].parent
      assert isinstance(plate, Plate), "MultiHeadAspirationPlate well parent must be a Plate"
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
        ref_well.get_location_wrt(self.deck)
        + ref_well.center()
        + Coordinate(z=ref_well.material_z_thickness)
        + aspiration.offset
      )
    else:
      x_width = (12 - 1) * 9  # 12 tips in a row, 9 mm between them
      y_width = (8 - 1) * 9  # 8 tips in a column, 9 mm between them
      x_position = (aspiration.container.get_absolute_size_x() - x_width) / 2
      y_position = (aspiration.container.get_absolute_size_y() - y_width) / 2 + y_width
      position = (
        aspiration.container.get_location_wrt(self.deck, z="cavity_bottom")
        + Coordinate(x=x_position, y=y_position)
        + aspiration.offset
      )
    self._check_96_position_legal(position, skip_z=True)

    tip = next(tip for tip in aspiration.tips if tip is not None)

    liquid_height = position.z + (aspiration.liquid_height or 0)

    hlc = hlc or get_star_liquid_class(
      tip_volume=tip.maximal_volume,
      is_core=True,
      is_tip=True,
      has_filter=tip.has_filter,
      # get last liquid in pipette, first to be dispensed
      liquid=Liquid.WATER,  # default to WATER
      jet=jet,
      blow_out=blow_out,  # see comment in method docstring
    )

    if disable_volume_correction or hlc is None:
      volume = aspiration.volume
    else:  # hlc is not None and not disable_volume_correction
      volume = hlc.compute_corrected_volume(aspiration.volume)

    # Get better default values from the HLC if available
    transport_air_volume = transport_air_volume or (
      hlc.aspiration_air_transport_volume if hlc is not None else 0
    )
    blow_out_air_volume = aspiration.blow_out_air_volume or (
      hlc.aspiration_blow_out_volume if hlc is not None else 0
    )
    flow_rate = aspiration.flow_rate or (hlc.aspiration_flow_rate if hlc is not None else 250)
    swap_speed = swap_speed or (hlc.aspiration_swap_speed if hlc is not None else 100)
    settling_time = settling_time or (hlc.aspiration_settling_time if hlc is not None else 0.5)

    x_direction = 0 if position.x >= 0 else 1
    return await self.aspirate_core_96(
      x_position=abs(round(position.x * 10)),
      x_direction=x_direction,
      y_positions=round(position.y * 10),
      aspiration_type=aspiration_type,
      minimum_traverse_height_at_beginning_of_a_command=round(
        (minimum_traverse_height_at_beginning_of_a_command or self._channel_traversal_height) * 10
      ),
      min_z_endpos=round((min_z_endpos or self._channel_traversal_height) * 10),
      lld_search_height=round(lld_search_height * 10),
      liquid_surface_no_lld=round(liquid_height * 10),
      pull_out_distance_transport_air=round(pull_out_distance_transport_air * 10),
      minimum_height=round((minimum_height or position.z) * 10),
      second_section_height=round(second_section_height * 10),
      second_section_ratio=round(second_section_ratio * 10),
      immersion_depth=round(immersion_depth * 10),
      immersion_depth_direction=immersion_depth_direction or (0 if (immersion_depth >= 0) else 1),
      surface_following_distance=round(surface_following_distance * 10),
      aspiration_volumes=round(volume * 10),
      aspiration_speed=round(flow_rate * 10),
      transport_air_volume=round(transport_air_volume * 10),
      blow_out_air_volume=round(blow_out_air_volume * 10),
      pre_wetting_volume=round(pre_wetting_volume * 10),
      lld_mode=int(use_lld),
      gamma_lld_sensitivity=gamma_lld_sensitivity,
      swap_speed=round(swap_speed * 10),
      settling_time=round(settling_time * 10),
      mix_volume=round(aspiration.mix.volume * 10) if aspiration.mix is not None else 0,
      mix_cycles=aspiration.mix.repetitions if aspiration.mix is not None else 0,
      mix_position_from_liquid_surface=round(mix_position_from_liquid_surface * 10),
      mix_surface_following_distance=round(mix_surface_following_distance * 10),
      speed_of_mix=round(aspiration.mix.flow_rate * 10) if aspiration.mix is not None else 1200,
      channel_pattern=[True] * 12 * 8,
      limit_curve_index=limit_curve_index,
      tadm_algorithm=False,
      recording_mode=0,
    )

  @_requires_head96
  async def dispense96(
    self,
    dispense: Union[MultiHeadDispensePlate, MultiHeadDispenseContainer],
    jet: bool = False,
    empty: bool = False,
    blow_out: bool = False,
    hlc: Optional[HamiltonLiquidClass] = None,
    pull_out_distance_transport_air=10,
    use_lld: bool = False,
    minimum_traverse_height_at_beginning_of_a_command: Optional[float] = None,
    min_z_endpos: Optional[float] = None,
    lld_search_height: float = 199.9,
    minimum_height: Optional[float] = None,
    second_section_height: float = 3.2,
    second_section_ratio: float = 618.0,
    immersion_depth: float = 0,
    surface_following_distance: float = 0,
    transport_air_volume: float = 5.0,
    gamma_lld_sensitivity: int = 1,
    swap_speed: float = 2.0,
    settling_time: float = 0,
    mix_position_from_liquid_surface: float = 0,
    mix_surface_following_distance: float = 0,
    limit_curve_index: int = 0,
    cut_off_speed: float = 5.0,
    stop_back_volume: float = 0,
    disable_volume_correction: bool = False,
    # Deprecated parameters, to be removed in future versions
    # rm: >2026-01
    liquid_surface_sink_distance_at_the_end_of_dispense: float = 0,  # surface_following_distance!
    maximum_immersion_depth: Optional[float] = None,
    minimal_end_height: Optional[float] = None,
    mixing_position_from_liquid_surface: float = 0,
    surface_following_distance_during_mixing: float = 0,
    air_transport_retract_dist=10,
    tube_2nd_section_ratio: float = 618.0,
    tube_2nd_section_height_measured_from_zm: float = 3.2,
    immersion_depth_direction: Optional[int] = None,
    mixing_volume: float = 0,
    mixing_cycles: int = 0,
    speed_of_mixing: float = 0.0,
    dispense_mode: Optional[int] = None,
  ):
    """Dispense using the Core96 head.

    Args:
      dispense: The Dispense command to execute.
      jet: Whether to use jet dispense mode.
      empty: Whether to use empty dispense mode.
      blow_out: Whether to blow out after dispensing.
      pull_out_distance_transport_air: The distance to retract after dispensing, in mm.
      use_lld: Whether to use gamma LLD.

      minimum_traverse_height_at_beginning_of_a_command: Minimum traverse height at beginning of a
        command, in mm.
      min_z_endpos: Minimal end height, in mm.
      lld_search_height: LLD search height, in mm.
      minimum_height: Maximum immersion depth, in mm. Equals Minimum height during command.
      second_section_height: Height of the second section, in mm.
      second_section_ratio: Ratio of [the diameter of the bottom * 10000] / [the diameter of the top].
      immersion_depth: Immersion depth, in mm.
      surface_following_distance: Surface following distance, in mm. Default 0.
      transport_air_volume: Transport air volume, to dispense before aspiration.
      gamma_lld_sensitivity: Gamma LLD sensitivity.
      swap_speed: Swap speed (on leaving liquid) [mm/s]. Must be between 0.3 and 160. Default 10.
      settling_time: Settling time, in seconds.
      mix_position_from_liquid_surface: Mixing position from liquid surface, in mm.
      mix_surface_following_distance: Surface following distance during mixing, in mm.
      limit_curve_index: Limit curve index.
      cut_off_speed: Unknown.
      stop_back_volume: Unknown.
      disable_volume_correction: Whether to disable liquid class volume correction.
    """

    # # # TODO: delete > 2026-01 # # #
    if mixing_volume != 0 or mixing_cycles != 0 or speed_of_mixing != 0:
      raise NotImplementedError(
        "Mixing through backend kwargs is deprecated. Use the `mix` parameter of LiquidHandler.dispense instead. "
        "https://docs.pylabrobot.org/user_guide/00_liquid-handling/mixing.html"
      )

    if immersion_depth_direction is not None:
      warnings.warn(
        "The immersion_depth_direction parameter is deprecated and will be removed in the future. "
        "Use positive values for immersion_depth to move into the liquid, and negative values to move "
        "out of the liquid.",
        DeprecationWarning,
      )

    if liquid_surface_sink_distance_at_the_end_of_dispense != 0:
      surface_following_distance = liquid_surface_sink_distance_at_the_end_of_dispense
      warnings.warn(
        "The liquid_surface_sink_distance_at_the_end_of_dispense parameter is deprecated and will be removed in the future. "
        "Use the Hamilton-standard surface_following_distance parameter instead.\n"
        "liquid_surface_sink_distance_at_the_end_of_dispense currently superseding surface_following_distance.",
        DeprecationWarning,
      )

    if maximum_immersion_depth is not None:
      minimum_height = maximum_immersion_depth
      warnings.warn(
        "The maximum_immersion_depth parameter is deprecated and will be removed in the future. "
        "Use the Hamilton-standard minimum_height parameter instead.\n"
        "minimum_height currently superseding maximum_immersion_depth.",
        DeprecationWarning,
      )

    if minimal_end_height is not None:
      min_z_endpos = minimal_end_height
      warnings.warn(
        "The minimal_end_height parameter is deprecated and will be removed in the future. "
        "Use the Hamilton-standard min_z_endpos parameter instead.\n"
        "min_z_endpos currently superseding minimal_end_height.",
        DeprecationWarning,
      )

    if mixing_position_from_liquid_surface != 0:
      mix_position_from_liquid_surface = mixing_position_from_liquid_surface
      warnings.warn(
        "The mixing_position_from_liquid_surface parameter is deprecated and will be removed in the future "
        "Use the Hamilton-standard mix_position_from_liquid_surface parameter instead.\n"
        "mix_position_from_liquid_surface currently superseding mixing_position_from_liquid_surface.",
        DeprecationWarning,
      )

    if surface_following_distance_during_mixing != 0:
      mix_surface_following_distance = surface_following_distance_during_mixing
      warnings.warn(
        "The surface_following_distance_during_mixing parameter is deprecated and will be removed in the future. "
        "Use the Hamilton-standard mix_surface_following_distance parameter instead.\n"
        "mix_surface_following_distance currently superseding surface_following_distance_during_mixing.",
        DeprecationWarning,
      )

    if air_transport_retract_dist != 10:
      pull_out_distance_transport_air = air_transport_retract_dist
      warnings.warn(
        "The air_transport_retract_dist parameter is deprecated and will be removed in the future. "
        "Use the Hamilton-standard pull_out_distance_transport_air parameter instead.\n"
        "pull_out_distance_transport_air currently superseding air_transport_retract_dist.",
        DeprecationWarning,
      )

    if tube_2nd_section_ratio != 618.0:
      second_section_ratio = tube_2nd_section_ratio
      warnings.warn(
        "The tube_2nd_section_ratio parameter is deprecated and will be removed in the future. "
        "Use the Hamilton-standard second_section_ratio parameter instead.\n"
        "second_section_ratio currently superseding tube_2nd_section_ratio.",
        DeprecationWarning,
      )

    if tube_2nd_section_height_measured_from_zm != 3.2:
      second_section_height = tube_2nd_section_height_measured_from_zm
      warnings.warn(
        "The tube_2nd_section_height_measured_from_zm parameter is deprecated and will be removed in the future. "
        "Use the Hamilton-standard second_section_height parameter instead.\n"
        "second_section_height currently superseding tube_2nd_section_height_measured_from_zm.",
        DeprecationWarning,
      )

    if dispense_mode is not None:
      warnings.warn(
        "The dispense_mode parameter is deprecated and will be removed in the future. "
        "Use the combination of the `jet`, `empty` and `blow_out` parameters instead. "
        "dispense_mode currently superseding those parameters.",
        DeprecationWarning,
      )
    else:
      dispense_mode = _dispensing_mode_for_op(empty=empty, jet=jet, blow_out=blow_out)
    # # # delete # # #

    # get the first well and tip as representatives
    if isinstance(dispense, MultiHeadDispensePlate):
      plate = dispense.wells[0].parent
      assert isinstance(plate, Plate), "MultiHeadDispensePlate well parent must be a Plate"
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
        ref_well.get_location_wrt(self.deck)
        + ref_well.center()
        + Coordinate(z=ref_well.material_z_thickness)
        + dispense.offset
      )
    else:
      # dispense in the center of the container
      # but we have to get the position of the center of tip A1
      x_width = (12 - 1) * 9  # 12 tips in a row, 9 mm between them
      y_width = (8 - 1) * 9  # 8 tips in a column, 9 mm between them
      x_position = (dispense.container.get_absolute_size_x() - x_width) / 2
      y_position = (dispense.container.get_absolute_size_y() - y_width) / 2 + y_width
      position = (
        dispense.container.get_location_wrt(self.deck, z="cavity_bottom")
        + Coordinate(x=x_position, y=y_position)
        + dispense.offset
      )
    self._check_96_position_legal(position, skip_z=True)
    tip = next(tip for tip in dispense.tips if tip is not None)

    liquid_height = position.z + (dispense.liquid_height or 0)

    hlc = hlc or get_star_liquid_class(
      tip_volume=tip.maximal_volume,
      is_core=True,
      is_tip=True,
      has_filter=tip.has_filter,
      # get last liquid in pipette, first to be dispensed
      liquid=Liquid.WATER,  # default to WATER
      jet=jet,
      blow_out=blow_out,  # see comment in method docstring
    )

    if disable_volume_correction or hlc is None:
      volume = dispense.volume
    else:  # hlc is not None and not disable_volume_correction
      volume = hlc.compute_corrected_volume(dispense.volume)

    transport_air_volume = transport_air_volume or (
      hlc.dispense_air_transport_volume if hlc is not None else 0
    )
    blow_out_air_volume = dispense.blow_out_air_volume or (
      hlc.dispense_blow_out_volume if hlc is not None else 0
    )
    flow_rate = dispense.flow_rate or (hlc.dispense_flow_rate if hlc is not None else 120)
    swap_speed = swap_speed or (hlc.dispense_swap_speed if hlc is not None else 100)
    settling_time = settling_time or (hlc.dispense_settling_time if hlc is not None else 5)

    return await self.dispense_core_96(
      dispensing_mode=dispense_mode,
      x_position=abs(round(position.x * 10)),
      x_direction=0 if position.x >= 0 else 1,
      y_position=round(position.y * 10),
      minimum_traverse_height_at_beginning_of_a_command=round(
        (minimum_traverse_height_at_beginning_of_a_command or self._channel_traversal_height) * 10
      ),
      min_z_endpos=round((min_z_endpos or self._channel_traversal_height) * 10),
      lld_search_height=round(lld_search_height * 10),
      liquid_surface_no_lld=round(liquid_height * 10),
      pull_out_distance_transport_air=round(pull_out_distance_transport_air * 10),
      minimum_height=round((minimum_height or position.z) * 10),
      second_section_height=round(second_section_height * 10),
      second_section_ratio=round(second_section_ratio * 10),
      immersion_depth=round(immersion_depth * 10),
      immersion_depth_direction=immersion_depth_direction or (0 if (immersion_depth >= 0) else 1),
      surface_following_distance=round(surface_following_distance * 10),
      dispense_volume=round(volume * 10),
      dispense_speed=round(flow_rate * 10),
      transport_air_volume=round(transport_air_volume * 10),
      blow_out_air_volume=round(blow_out_air_volume * 10),
      lld_mode=int(use_lld),
      gamma_lld_sensitivity=gamma_lld_sensitivity,
      swap_speed=round(swap_speed * 10),
      settling_time=round(settling_time * 10),
      mixing_volume=round(dispense.mix.volume * 10) if dispense.mix is not None else 0,
      mixing_cycles=dispense.mix.repetitions if dispense.mix is not None else 0,
      mix_position_from_liquid_surface=round(mix_position_from_liquid_surface * 10),
      mix_surface_following_distance=round(mix_surface_following_distance * 10),
      speed_of_mixing=round(dispense.mix.flow_rate * 10) if dispense.mix is not None else 1200,
      channel_pattern=[True] * 12 * 8,
      limit_curve_index=limit_curve_index,
      tadm_algorithm=False,
      recording_mode=0,
      cut_off_speed=round(cut_off_speed * 10),
      stop_back_volume=round(stop_back_volume * 10),
    )

  # -------------- Calibration --------------

  async def set_x_offset_x_axis_core_96_head(self, x_offset: int):
    """Set X-offset X-axis <-> CoRe 96 head

    Args:
      x_offset: X-offset [0.1mm]
    """

    return await self.send_command(module="C0", command="AF", x_offset=x_offset)

  # -------------- 3.10 96-Head commands --------------

  async def head96_request_firmware_version(self) -> datetime.date:
    """Request 96 Head firmware version (MEM-READ command)."""
    resp: str = await self.send_command(module="H0", command="RF")
    return self._parse_firmware_version_datetime(resp)

  async def _head96_request_configuration(self) -> List[str]:
    """Request the 96-head configuration (raw) using the QU command.

    The instrument returns a sequence of positional tokens. This method returns
    those tokens without decoding them, but the following indices are currently
    understood:

        - index 0: clot_monitoring_with_clld
        - index 1: stop_disc_type (codes: 0=core_i, 1=core_ii)
        - index 2: instrument_type (codes: 0=legacy, 1=FM-STAR)
        - indices 3..9: reservable positions (positions 4..10)

    Returns:
      Raw positional tokens extracted from the QU response (the portion after the last ``"au"`` marker).
    """
    resp: str = await self.send_command(module="H0", command="QU")
    return resp.split("au")[-1].split()

  async def head96_request_type(self) -> Head96Information.HeadType:
    """Send QG and return the 96-head type as a human-readable string."""
    type_map: Dict[int, Head96Information.HeadType] = {
      0: "Low volume head",
      1: "High volume head",
      2: "96 head II",
      3: "96 head TADM",
    }
    resp = await self.send_command(module="H0", command="QG", fmt="qg#")
    return type_map.get(resp["qg"], "unknown")

  # -------------- 3.10.1 Initialization --------------

  async def initialize_core_96_head(
    self, trash96: Trash, z_position_at_the_command_end: float = 245.0
  ):
    """Initialize CoRe 96 Head

    Args:
      trash96: Trash object where tips should be disposed. The 96 head will be positioned in the
        center of the trash.
      z_position_at_the_command_end: Z position at the end of the command [mm].
    """

    # The firmware command expects location of tip A1 of the head.
    loc = self._position_96_head_in_resource(trash96)
    self._check_96_position_legal(loc, skip_z=True)

    return await self.send_command(
      module="C0",
      command="EI",
      read_timeout=60,
      xs=f"{abs(round(loc.x * 10)):05}",
      xd=0 if loc.x >= 0 else 1,
      yh=f"{abs(round(loc.y * 10)):04}",
      za=f"{round(loc.z * 10):04}",
      ze=f"{round(z_position_at_the_command_end * 10):04}",
    )

  async def request_core_96_head_initialization_status(self) -> bool:
    # not available in the C0 docs, so get from module H0 itself instead
    response = await self.send_command(module="H0", command="QW", fmt="qw#")
    return bool(response.get("qw", 0) == 1)  # type?

  async def head96_dispensing_drive_and_squeezer_driver_initialize(
    self,
    squeezer_speed: float = 15.0,  # mm/sec
    squeezer_acceleration: float = 62.0,  # mm/sec**2,
    squeezer_current_limit: int = 15,
    dispensing_drive_current_limit: int = 7,
  ):
    """Initialize 96-head's dispensing drive AND squeezer drive

    This command...
      - drops any tips that might be on the channel (in place, without moving to trash!)
      - moves the dispense drive to volume position 215.92 uL
        (after tip pickup it will be at 218.19 uL)

    Args:
      squeezer_speed: Speed of the movement (mm/sec). Default is 15.0 mm/sec.
      squeezer_acceleration: Acceleration of the movement (mm/sec**2). Default is 62.0 mm/sec**2.
      squeezer_current_limit: Current limit for the squeezer drive (1-15). Default is 15.
      dispensing_drive_current_limit: Current limit for the dispensing drive (1-15). Default is 7.
    """

    if not (0.01 <= squeezer_speed <= 16.69):
      raise ValueError(
        f"96-head squeezer drive speed must be between 0.01 and 16.69 mm/sec, is {squeezer_speed}"
      )
    if not (1.04 <= squeezer_acceleration <= 62.6):
      raise ValueError(
        "96-head squeezer drive acceleration must be between 1.04 and "
        f"62.6 mm/sec**2, is {squeezer_acceleration}"
      )
    if not (1 <= squeezer_current_limit <= 15):
      raise ValueError(
        "96-head squeezer drive current limit must be between 1 and 15, "
        f"is {squeezer_current_limit}"
      )
    if not (1 <= dispensing_drive_current_limit <= 15):
      raise ValueError(
        "96-head dispensing drive current limit must be between 1 and 15, "
        f"is {dispensing_drive_current_limit}"
      )

    squeezer_speed_increment = self._head96_squeezer_drive_mm_to_increment(squeezer_speed)
    squeezer_acceleration_increment = self._head96_squeezer_drive_mm_to_increment(
      squeezer_acceleration
    )

    resp = await self.send_command(
      module="H0",
      command="PI",
      sv=f"{squeezer_speed_increment:05}",
      sr=f"{squeezer_acceleration_increment:06}",
      sw=f"{squeezer_current_limit:02}",
      dw=f"{dispensing_drive_current_limit:02}",
    )

    return resp

  # -------------- 3.10.2 96-Head Movements --------------

  # Z-axis conversions

  def _head96_z_drive_mm_to_increment(self, value_mm: float) -> int:
    """Convert mm to Z-axis hardware increments for 96-head."""
    return round(value_mm / self._head96_z_drive_mm_per_increment)

  def _head96_z_drive_increment_to_mm(self, value_increments: int) -> float:
    """Convert Z-axis hardware increments to mm for 96-head."""
    return round(value_increments * self._head96_z_drive_mm_per_increment, 2)

  # Y-axis conversions

  def _head96_y_drive_mm_to_increment(self, value_mm: float) -> int:
    """Convert mm to Y-axis hardware increments for 96-head."""
    return round(value_mm / self._head96_y_drive_mm_per_increment)

  def _head96_y_drive_increment_to_mm(self, value_increments: int) -> float:
    """Convert Y-axis hardware increments to mm for 96-head."""
    return round(value_increments * self._head96_y_drive_mm_per_increment, 2)

  # Dispensing drive conversions (mm and uL)

  def _head96_dispensing_drive_mm_to_increment(self, value_mm: float) -> int:
    """Convert mm to dispensing drive hardware increments for 96-head."""
    return round(value_mm / self._head96_dispensing_drive_mm_per_increment)

  def _head96_dispensing_drive_increment_to_mm(self, value_increments: int) -> float:
    """Convert dispensing drive hardware increments to mm for 96-head."""
    return round(value_increments * self._head96_dispensing_drive_mm_per_increment, 2)

  def _head96_dispensing_drive_uL_to_increment(self, value_uL: float) -> int:
    """Convert uL to dispensing drive hardware increments for 96-head."""
    return round(value_uL / self._head96_dispensing_drive_uL_per_increment)

  def _head96_dispensing_drive_increment_to_uL(self, value_increments: int) -> float:
    """Convert dispensing drive hardware increments to uL for 96-head."""
    return round(value_increments * self._head96_dispensing_drive_uL_per_increment, 2)

  def _head96_dispensing_drive_mm_to_uL(self, value_mm: float) -> float:
    """Convert dispensing drive mm to uL for 96-head."""
    # Convert mm -> increment -> uL
    increment = self._head96_dispensing_drive_mm_to_increment(value_mm)
    return self._head96_dispensing_drive_increment_to_uL(increment)

  def _head96_dispensing_drive_uL_to_mm(self, value_uL: float) -> float:
    """Convert dispensing drive uL to mm for 96-head."""
    # Convert uL -> increment -> mm
    increment = self._head96_dispensing_drive_uL_to_increment(value_uL)
    return self._head96_dispensing_drive_increment_to_mm(increment)

  # Squeezer drive conversions

  def _head96_squeezer_drive_mm_to_increment(self, value_mm: float) -> int:
    """Convert mm to squeezer drive hardware increments for 96-head."""
    return round(value_mm / self._head96_squeezer_drive_mm_per_increment)

  def _head96_squeezer_drive_increment_to_mm(self, value_increments: int) -> float:
    """Convert squeezer drive hardware increments to mm for 96-head."""
    return round(value_increments * self._head96_squeezer_drive_mm_per_increment, 2)

  # Movement commands

  async def move_core_96_to_safe_position(self):
    """Move CoRe 96 Head to Z safe position."""
    warnings.warn(
      "move_core_96_to_safe_position is deprecated. Use head96_move_to_z_safety instead. "
      "This method will be removed in 2026-04",  # TODO: remove 2026-04
      DeprecationWarning,
      stacklevel=2,
    )
    return await self.head96_move_to_z_safety()

  @_requires_head96
  async def head96_move_to_z_safety(self):
    """Move 96-Head to Z safety coordinate, i.e. z=342.5 mm."""
    return await self.send_command(module="C0", command="EV")

  @_requires_head96
  async def head96_park(
    self,
  ):
    """Park the 96-head.

    Uses firmware default speeds and accelerations.
    """

    return await self.send_command(module="H0", command="MO")

  @_requires_head96
  async def head96_move_x(self, x: float):
    """Move the 96-head to a specified X-axis coordinate.

    Note: Unlike head96_move_y and head96_move_z, the X-axis movement does not have
    dedicated speed/acceleration parameters - it uses the EM command which moves
    all axes together.

    Args:
      x: Target X coordinate in mm. Valid range: [-271.0, 974.0]

    Returns:
      Response from the hardware command.

    Raises:
      RuntimeError: If 96-head is not installed.
      AssertionError: If parameter out of range.
    """
    assert -271 <= x <= 974, "x must be between -271.0 and 974.0 mm"

    current_pos = await self.head96_request_position()
    return await self.head96_move_to_coordinate(
      Coordinate(x, current_pos.y, current_pos.z),
      minimum_height_at_beginning_of_a_command=current_pos.z - 10,
    )

  @_requires_head96
  async def head96_move_y(
    self,
    y: float,
    speed: float = 300.0,
    acceleration: float = 300.0,
    current_protection_limiter: int = 15,
  ):
    """Move the 96-head to a specified Y-axis coordinate.

    Args:
      y: Target Y coordinate in mm. Valid range: [93.75, 562.5]
      speed: Movement speed in mm/sec. Valid range: [0.78125, 390.625 or 625.0]. Default: 300.0
      acceleration: Movement acceleration in mm/sec**2. Valid range: [78.125, 781.25]. Default: 300.0
      current_protection_limiter: Motor current limit (0-15, hardware units). Default: 15

    Returns:
      Response from the hardware command.

    Raises:
      RuntimeError: If 96-head is not installed.
      AssertionError: If firmware info missing or parameters out of range.

    Note:
      Maximum speed varies by firmware version:
      - Pre-2021: 390.625 mm/sec (25,000 increments)
      - 2021+: 625.0 mm/sec (40,000 increments)
      The exact firmware version introducing this change is undocumented.
    """
    assert self._head96_information is not None, (
      "requires 96-head firmware version information for safe operation"
    )

    fw_version = self._head96_information.fw_version

    # Determine speed limit based on firmware version
    # Pre-2021 firmware appears to have lower speed capability or safety limits
    # TODO: Verify exact firmware version and investigate the reason for this change
    y_speed_upper_limit = 390.625 if fw_version.year <= 2021 else 625.0  # mm/sec

    # Validate parameters before hardware communication
    assert 93.75 <= y <= 562.5, "y must be between 93.75 and 562.5 mm"
    assert 0.78125 <= speed <= y_speed_upper_limit, (
      f"speed must be between 0.78125 and {y_speed_upper_limit} mm/sec for firmware version {fw_version}. "
      f"Your firmware version: {self._head96_information.fw_version}. "
      "If this limit seems incorrect, please test cautiously with an empty deck and report "
      "accurate limits + firmware to PyLabRobot: https://github.com/PyLabRobot/pylabrobot/issues"
    )
    assert 78.125 <= acceleration <= 781.25, (
      "acceleration must be between 78.125 and 781.25 mm/sec**2"
    )
    assert isinstance(current_protection_limiter, int) and (
      0 <= current_protection_limiter <= 15
    ), "current_protection_limiter must be an integer between 0 and 15"

    # Convert mm-based parameters to hardware increments using conversion methods
    y_increment = self._head96_y_drive_mm_to_increment(y)
    speed_increment = self._head96_y_drive_mm_to_increment(speed)
    acceleration_increment = self._head96_y_drive_mm_to_increment(acceleration)

    resp = await self.send_command(
      module="H0",
      command="YA",
      ya=f"{y_increment:05}",
      yv=f"{speed_increment:05}",
      yr=f"{acceleration_increment:05}",
      yw=f"{current_protection_limiter:02}",
    )

    return resp

  @_requires_head96
  async def head96_move_z(
    self,
    z: float,
    speed: float = 80.0,
    acceleration: float = 300.0,
    current_protection_limiter: int = 15,
  ):
    """Move the 96-head to a specified Z-axis coordinate.

    Args:
      z: Target Z coordinate in mm. Valid range: [180.5, 342.5]
      speed: Movement speed in mm/sec. Valid range: [0.25, 100.0]. Default: 80.0
      acceleration: Movement acceleration in mm/sec^2. Valid range: [25.0, 500.0]. Default: 300.0
      current_protection_limiter: Motor current limit (0-15, hardware units). Default: 15

    Returns:
      Response from the hardware command.

    Raises:
      RuntimeError: If 96-head is not installed.
      AssertionError: If firmware info missing or parameters out of range.

    Note:
      Firmware versions from 2021+ use 1:1 acceleration scaling, while pre-2021 versions
      use 100x scaling. Both maintain a 100,000 increment upper limit.
    """
    assert self._head96_information is not None, (
      "requires 96-head firmware version information for safe operation"
    )

    fw_version = self._head96_information.fw_version

    # Validate parameters before hardware communication
    assert 180.5 <= z <= 342.5, "z must be between 180.5 and 342.5 mm"
    assert 0.25 <= speed <= 100.0, "speed must be between 0.25 and 100.0 mm/sec"
    assert 25.0 <= acceleration <= 500.0, "acceleration must be between 25.0 and 500.0 mm/sec**2"
    assert isinstance(current_protection_limiter, int) and (
      0 <= current_protection_limiter <= 15
    ), "current_protection_limiter must be an integer between 0 and 15"

    # Determine acceleration scaling based on firmware version
    # Pre-2010 firmware: acceleration parameter is multiplied by 1000
    # 2010+ firmware: acceleration parameter is 1:1 with increment/sec**2
    # TODO: identify exact firmware version that introduced this change
    acceleration_multiplier = 1 if fw_version.year >= 2010 else 0.001

    # Convert mm-based parameters to hardware increments
    z_increment = self._head96_z_drive_mm_to_increment(z)
    speed_increment = self._head96_z_drive_mm_to_increment(speed)
    acceleration_increment = round(
      self._head96_z_drive_mm_to_increment(acceleration) * acceleration_multiplier
    )

    resp = await self.send_command(
      module="H0",
      command="ZA",
      za=f"{z_increment:05}",
      zv=f"{speed_increment:05}",
      zr=f"{acceleration_increment:06}",
      zw=f"{current_protection_limiter:02}",
    )

    return resp

  # -------------- 3.10.2 Tip handling using CoRe 96 Head --------------

  @need_iswap_parked
  @_requires_head96
  async def pick_up_tips_core96(
    self,
    x_position: int,
    x_direction: int,
    y_position: int,
    tip_type_idx: int,
    tip_pickup_method: int = 2,
    z_deposit_position: int = 3425,
    minimum_traverse_height_at_beginning_of_a_command: int = 3425,
    minimum_height_command_end: int = 3425,
  ):
    """Pick up tips with CoRe 96 head

    Args:
      x_position: x position [0.1mm]. Must be between 0 and 30000. Default 0.
      x_direction: X-direction. 0 = positive 1 = negative. Must be between 0 and 1. Default 0.
      y_position: y position [0.1mm]. Must be between 1080 and 5600. Default 5600.
      tip_size: Tip type.
      tip_pickup_method: Tip pick up method. 0 = pick up from rack. 1 = pick up from C0Re 96 tip
        wash station. 2 = pick up with " full volume blow out"
      z_deposit_position: Z- deposit position [0.1mm] (collar bearing position) Must bet between
        0 and 3425. Default 3425.
      minimum_traverse_height_at_beginning_of_a_command: Minimum traverse height at beginning
        of a command [0.1mm]. Must be between 0 and 3425.
      minimum_height_command_end: Minimal height at command end [0.1 mm] Must be between 0 and 3425.
    """

    assert 0 <= x_position <= 30000, "x_position must be between 0 and 30000"
    assert 0 <= x_direction <= 1, "x_direction must be between 0 and 1"
    assert 1080 <= y_position <= 5600, "y_position must be between 1080 and 5600"
    assert 0 <= z_deposit_position <= 3425, "z_deposit_position must be between 0 and 3425"
    assert 0 <= minimum_traverse_height_at_beginning_of_a_command <= 3425, (
      "minimum_traverse_height_at_beginning_of_a_command must be between 0 and 3425"
    )
    assert 0 <= minimum_height_command_end <= 3425, (
      "minimum_height_command_end must be between 0 and 3425"
    )

    return await self.send_command(
      module="C0",
      command="EP",
      xs=f"{x_position:05}",
      xd=x_direction,
      yh=f"{y_position:04}",
      tt=f"{tip_type_idx:02}",
      wu=tip_pickup_method,
      za=f"{z_deposit_position:04}",
      zh=f"{minimum_traverse_height_at_beginning_of_a_command:04}",
      ze=f"{minimum_height_command_end:04}",
    )

  @need_iswap_parked
  @_requires_head96
  async def discard_tips_core96(
    self,
    x_position: int,
    x_direction: int,
    y_position: int,
    z_deposit_position: int = 3425,
    minimum_traverse_height_at_beginning_of_a_command: int = 3425,
    minimum_height_command_end: int = 3425,
  ):
    """Drop tips with CoRe 96 head

    Args:
      x_position: x position [0.1mm]. Must be between 0 and 30000. Default 0.
      x_direction: X-direction. 0 = positive 1 = negative. Must be between 0 and 1. Default 0.
      y_position: y position [0.1mm]. Must be between 1080 and 5600. Default 5600.
      tip_type: Tip type.
      tip_pickup_method: Tip pick up method. 0 = pick up from rack. 1 = pick up from C0Re 96
        tip wash station. 2 = pick up with " full volume blow out"
      z_deposit_position: Z- deposit position [0.1mm] (collar bearing position) Must bet between
        0 and 3425. Default 3425.
      minimum_traverse_height_at_beginning_of_a_command: Minimum traverse height at beginning
        of a command [0.1mm]. Must be between 0 and 3425.
      minimum_height_command_end: Minimal height at command end [0.1 mm] Must be between 0 and 3425
    """

    assert 0 <= x_position <= 30000, "x_position must be between 0 and 30000"
    assert 0 <= x_direction <= 1, "x_direction must be between 0 and 1"
    assert 1080 <= y_position <= 5600, "y_position must be between 1080 and 5600"
    assert 0 <= z_deposit_position <= 3425, "z_deposit_position must be between 0 and 3425"
    assert 0 <= minimum_traverse_height_at_beginning_of_a_command <= 3425, (
      "minimum_traverse_height_at_beginning_of_a_command must be between 0 and 3425"
    )
    assert 0 <= minimum_height_command_end <= 3425, (
      "minimum_height_command_end must be between 0 and 3425"
    )

    return await self.send_command(
      module="C0",
      command="ER",
      xs=f"{x_position:05}",
      xd=x_direction,
      yh=f"{y_position:04}",
      za=f"{z_deposit_position:04}",
      zh=f"{minimum_traverse_height_at_beginning_of_a_command:04}",
      ze=f"{minimum_height_command_end:04}",
    )

  # -------------- 3.10.3 Liquid handling using CoRe 96 Head --------------

  # # # Granular commands # # #

  async def head96_dispensing_drive_move_to_home_volume(
    self,
  ):
    """Move the 96-head dispensing drive into its home position (vol=0.0 uL).

    .. warning::
      This firmware command is known to be broken: the 96-head dispensing drive cannot reach
      vol=0.0 uL, which typically raises
      ``STARFirmwareError: {'CoRe 96 Head': UnknownHamiltonError('Position out of permitted
      area')}``.
    """

    logger.warning(
      "head96_dispensing_drive_move_to_home_volume is a known broken firmware command: "
      "the 96-head dispensing drive cannot reach vol=0.0 uL and will likely raise "
      "STARFirmwareError: {'CoRe 96 Head': UnknownHamiltonError('Position out of permitted "
      "area')}. Attempting to send the command anyway."
    )

    return await self.send_command(
      module="H0",
      command="DL",
    )

  # # # "Atomic" liquid handling commands # # #

  @need_iswap_parked
  @_requires_head96
  async def aspirate_core_96(
    self,
    aspiration_type: int = 0,
    x_position: int = 0,
    x_direction: int = 0,
    y_positions: int = 0,
    minimum_traverse_height_at_beginning_of_a_command: int = 3425,
    min_z_endpos: int = 3425,
    lld_search_height: int = 3425,
    liquid_surface_no_lld: int = 3425,
    pull_out_distance_transport_air: int = 3425,
    minimum_height: int = 3425,
    second_section_height: int = 0,
    second_section_ratio: int = 3425,
    immersion_depth: int = 0,
    immersion_depth_direction: int = 0,
    surface_following_distance: float = 0,
    aspiration_volumes: int = 0,
    aspiration_speed: int = 1000,
    transport_air_volume: int = 0,
    blow_out_air_volume: int = 200,
    pre_wetting_volume: int = 0,
    lld_mode: int = 1,
    gamma_lld_sensitivity: int = 1,
    swap_speed: int = 100,
    settling_time: int = 5,
    mix_volume: int = 0,
    mix_cycles: int = 0,
    mix_position_from_liquid_surface: int = 250,
    mix_surface_following_distance: int = 0,
    speed_of_mix: int = 1000,
    channel_pattern: List[bool] = [True] * 96,
    limit_curve_index: int = 0,
    tadm_algorithm: bool = False,
    recording_mode: int = 0,
    # Deprecated parameters, to be removed in future versions
    # rm: >2026-01:
    liquid_surface_sink_distance_at_the_end_of_aspiration: float = 0,
    minimal_end_height: int = 3425,
    liquid_surface_at_function_without_lld: int = 3425,
    pull_out_distance_to_take_transport_air_in_function_without_lld: int = 50,
    maximum_immersion_depth: int = 3425,
    surface_following_distance_during_mix: int = 0,
    tube_2nd_section_ratio: int = 3425,
    tube_2nd_section_height_measured_from_zm: int = 0,
  ):
    """aspirate CoRe 96

    Aspiration of liquid using CoRe 96

    Args:
      aspiration_type: Type of aspiration (0 = simple; 1 = sequence; 2 = cup emptied). Must be
          between 0 and 2. Default 0.
      x_position: X-Position [0.1mm] of well A1. Must be between 0 and 30000. Default 0.
      x_direction: X-direction. 0 = positive 1 = negative. Must be between 0 and 1. Default 0.
      y_positions: Y-Position [0.1mm] of well A1. Must be between 1080 and 5600. Default 0.
      minimum_traverse_height_at_beginning_of_a_command: Minimum traverse height at beginning of
          a command 0.1mm] (refers to all channels independent of tip pattern parameter 'tm').
          Must be between 0 and 3425. Default 3425.
      min_z_endpos: Minimal height at command end [0.1mm]. Must be between 0 and 3425. Default 3425.
      lld_search_height: LLD search height [0.1mm]. Must be between 0 and 3425. Default 3425.
      liquid_surface_no_lld: Liquid surface at function without LLD [0.1mm]. Must be between 0 and 3425. Default 3425.
      pull_out_distance_transport_air: pull out distance to take transport air in function without LLD [0.1mm]. Must be between 0 and 3425. Default 50.
      minimum_height: Minimum height (maximum immersion depth) [0.1mm]. Must be between 0 and 3425. Default 3425.
      second_section_height: second ratio height. Must be between 0 and 3425. Default 0.
      second_section_ratio: Tube 2nd section ratio (See Fig 2.). Must be between 0 and 10000. Default 3425.
      immersion_depth: Immersion depth [0.1mm]. Must be between 0 and 3600. Default 0.
      immersion_depth_direction: Direction of immersion depth (0 = go deeper, 1 = go up out of
          liquid). Must be between 0 and 1. Default 0.
      surface_following_distance_at_the_end_of_aspiration: Surface following distance during
          aspiration [0.1mm]. Must be between 0 and 990. Default 0. (renamed for clarity from
          'liquid_surface_sink_distance_at_the_end_of_aspiration' in firmware docs)
      aspiration_volumes: Aspiration volume [0.1ul]. Must be between 0 and 11500. Default 0.
      aspiration_speed: Aspiration speed [0.1ul/s]. Must be between 3 and 5000. Default 1000.
      transport_air_volume: Transport air volume [0.1ul]. Must be between 0 and 500. Default 0.
      blow_out_air_volume: Blow-out air volume [0.1ul]. Must be between 0 and 11500. Default 200.
      pre_wetting_volume: Pre-wetting volume. Must be between 0 and 11500. Default 0.
      lld_mode: LLD mode (0 = off, 1 = gamma, 2 = dP, 3 = dual, 4 = Z touch off). Must be between
          0 and 4. Default 1.
      gamma_lld_sensitivity: gamma LLD sensitivity (1= high, 4=low). Must be between 1 and 4.
          Default 1.
      swap_speed: Swap speed (on leaving liquid) [0.1mm/s]. Must be between 3 and 1000. Default 100.
      settling_time: Settling time [0.1s]. Must be between 0 and 99. Default 5.
      mix_volume: mix volume [0.1ul]. Must be between 0 and 11500. Default 0.
      mix_cycles: Number of mix cycles. Must be between 0 and 99. Default 0.
      mix_position_from_liquid_surface: mix position in Z- direction from
          liquid surface (LLD or absolute terms) [0.1mm]. Must be between 0 and 990. Default 250.
      mix_surface_following_distance: surface following distance during
          mix [0.1mm]. Must be between 0 and 990. Default 0.
      speed_of_mix: Speed of mix [0.1ul/s]. Must be between 3 and 5000.
          Default 1000.
      todo: TODO: 24 hex chars. Must be between 4 and 5000.
      limit_curve_index: limit curve index. Must be between 0 and 999. Default 0.
      tadm_algorithm: TADM algorithm. Default False.
      recording_mode: Recording mode 0 : no 1 : TADM errors only 2 : all TADM measurement.
          Must be between 0 and 2. Default 0.
    """

    # # # TODO: delete > 2026-01 # # #
    # deprecated liquid_surface_sink_distance_at_the_end_of_aspiration:
    if liquid_surface_sink_distance_at_the_end_of_aspiration != 0.0:
      surface_following_distance = liquid_surface_sink_distance_at_the_end_of_aspiration
      warnings.warn(
        "The liquid_surface_sink_distance_at_the_end_of_aspiration parameter is deprecated and will be removed in the future. "
        "Use the Hamilton-standard surface_following_distance parameter instead.\n"
        "liquid_surface_sink_distance_at_the_end_of_aspiration currently superseding "
        "surface_following_distance.",
        DeprecationWarning,
      )

    if minimal_end_height != 3425:
      min_z_endpos = minimal_end_height
      warnings.warn(
        "The minimal_end_height parameter is deprecated and will be removed in the future. "
        "Use the Hamilton-standard min_z_endpos parameter instead.\n"
        "minimal_end_height currently superseding min_z_endpos.",
        DeprecationWarning,
      )

    if liquid_surface_at_function_without_lld != 3425:
      liquid_surface_no_lld = liquid_surface_at_function_without_lld
      warnings.warn(
        "The liquid_surface_at_function_without_lld parameter is deprecated and will be removed in the future. "
        "Use the Hamilton-standard liquid_surface_no_lld parameter instead.\n"
        "liquid_surface_at_function_without_lld currently superseding liquid_surface_no_lld.",
        DeprecationWarning,
      )

    if pull_out_distance_to_take_transport_air_in_function_without_lld != 50:
      pull_out_distance_transport_air = (
        pull_out_distance_to_take_transport_air_in_function_without_lld
      )
      warnings.warn(
        "The pull_out_distance_to_take_transport_air_in_function_without_lld parameter is deprecated and will be removed in the future. "
        "Use the Hamilton-standard pull_out_distance_transport_air parameter instead.\n"
        "pull_out_distance_to_take_transport_air_in_function_without_lld currently superseding pull_out_distance_transport_air.",
        DeprecationWarning,
      )

    if maximum_immersion_depth != 3425:
      minimum_height = maximum_immersion_depth
      warnings.warn(
        "The maximum_immersion_depth parameter is deprecated and will be removed in the future. "
        "Use the Hamilton-standard minimum_height parameter instead.\n"
        "minimum_height currently superseding maximum_immersion_depth.",
        DeprecationWarning,
      )

    if surface_following_distance_during_mix != 0:
      mix_surface_following_distance = surface_following_distance_during_mix
      warnings.warn(
        "The surface_following_distance_during_mix parameter is deprecated and will be removed in the future. "
        "Use the Hamilton-standard mix_surface_following_distance parameter instead.\n"
        "surface_following_distance_during_mix currently superseding mix_surface_following_distance.",
        DeprecationWarning,
      )

    if tube_2nd_section_ratio != 3425:
      second_section_ratio = tube_2nd_section_ratio
      warnings.warn(
        "The tube_2nd_section_ratio parameter is deprecated and will be removed in the future. "
        "Use the Hamilton-standard second_section_ratio parameter instead.\n"
        "tube_2nd_section_ratio currently superseding second_section_ratio.",
        DeprecationWarning,
      )

    if tube_2nd_section_height_measured_from_zm != 0:
      second_section_height = tube_2nd_section_height_measured_from_zm
      warnings.warn(
        "The tube_2nd_section_height_measured_from_zm parameter is deprecated and will be removed in the future. "
        "Use the Hamilton-standard tube_2nd_section_height_measured_from_zm parameter instead.\n"
        "tube_2nd_section_height_measured_from_zm currently superseding tube_2nd_section_height_measured_from_zm.",
        DeprecationWarning,
      )
    # # # delete # # #

    assert 0 <= aspiration_type <= 2, "aspiration_type must be between 0 and 2"
    assert 0 <= x_position <= 30000, "x_position must be between 0 and 30000"
    assert 0 <= x_direction <= 1, "x_direction must be between 0 and 1"
    assert 1080 <= y_positions <= 5600, "y_positions must be between 1080 and 5600"
    assert 0 <= minimum_traverse_height_at_beginning_of_a_command <= 3425, (
      "minimum_traverse_height_at_beginning_of_a_command must be between 0 and 3425"
    )
    assert 0 <= min_z_endpos <= 3425, "min_z_endpos must be between 0 and 3425"
    assert 0 <= lld_search_height <= 3425, "lld_search_height must be between 0 and 3425"
    assert 0 <= liquid_surface_no_lld <= 3425, "liquid_surface_no_lld must be between 0 and 3425"
    assert 0 <= pull_out_distance_transport_air <= 3425, (
      "pull_out_distance_transport_air must be between 0 and 3425"
    )
    assert 0 <= minimum_height <= 3425, "minimum_height must be between 0 and 3425"
    assert 0 <= second_section_height <= 3425, "second_section_height must be between 0 and 3425"
    assert 0 <= second_section_ratio <= 10000, "second_section_ratio must be between 0 and 10000"
    assert 0 <= immersion_depth <= 3600, "immersion_depth must be between 0 and 3600"
    assert 0 <= immersion_depth_direction <= 1, "immersion_depth_direction must be between 0 and 1"
    assert 0 <= surface_following_distance <= 990, (
      "surface_following_distance must be between 0 and 990"
    )
    assert 0 <= aspiration_volumes <= 11500, "aspiration_volumes must be between 0 and 11500"
    assert 3 <= aspiration_speed <= 5000, "aspiration_speed must be between 3 and 5000"
    assert 0 <= transport_air_volume <= 500, "transport_air_volume must be between 0 and 500"
    assert 0 <= blow_out_air_volume <= 11500, "blow_out_air_volume must be between 0 and 11500"
    assert 0 <= pre_wetting_volume <= 11500, "pre_wetting_volume must be between 0 and 11500"
    assert 0 <= lld_mode <= 4, "lld_mode must be between 0 and 4"
    assert 1 <= gamma_lld_sensitivity <= 4, "gamma_lld_sensitivity must be between 1 and 4"
    assert 3 <= swap_speed <= 1000, "swap_speed must be between 3 and 1000"
    assert 0 <= settling_time <= 99, "settling_time must be between 0 and 99"
    assert 0 <= mix_volume <= 11500, "mix_volume must be between 0 and 11500"
    assert 0 <= mix_cycles <= 99, "mix_cycles must be between 0 and 99"
    assert 0 <= mix_position_from_liquid_surface <= 990, (
      "mix_position_from_liquid_surface must be between 0 and 990"
    )
    assert 0 <= mix_surface_following_distance <= 990, (
      "mix_surface_following_distance must be between 0 and 990"
    )
    assert 3 <= speed_of_mix <= 5000, "speed_of_mix must be between 3 and 5000"
    assert 0 <= limit_curve_index <= 999, "limit_curve_index must be between 0 and 999"

    assert 0 <= recording_mode <= 2, "recording_mode must be between 0 and 2"

    # Convert bool list to hex string
    assert len(channel_pattern) == 96, "channel_pattern must be a list of 96 boolean values"
    channel_pattern_bin_str = reversed(["1" if x else "0" for x in channel_pattern])
    channel_pattern_hex = hex(int("".join(channel_pattern_bin_str), 2)).upper()[2:]

    return await self.send_command(
      module="C0",
      command="EA",
      aa=aspiration_type,
      xs=f"{x_position:05}",
      xd=x_direction,
      yh=f"{y_positions:04}",
      zh=f"{minimum_traverse_height_at_beginning_of_a_command:04}",
      ze=f"{min_z_endpos:04}",
      lz=f"{lld_search_height:04}",
      zt=f"{liquid_surface_no_lld:04}",
      pp=f"{pull_out_distance_transport_air:04}",
      zm=f"{minimum_height:04}",
      zv=f"{second_section_height:04}",
      zq=f"{second_section_ratio:05}",
      iw=f"{immersion_depth:03}",
      ix=immersion_depth_direction,
      fh=f"{surface_following_distance:03}",
      af=f"{aspiration_volumes:05}",
      ag=f"{aspiration_speed:04}",
      vt=f"{transport_air_volume:03}",
      bv=f"{blow_out_air_volume:05}",
      wv=f"{pre_wetting_volume:05}",
      cm=lld_mode,
      cs=gamma_lld_sensitivity,
      bs=f"{swap_speed:04}",
      wh=f"{settling_time:02}",
      hv=f"{mix_volume:05}",
      hc=f"{mix_cycles:02}",
      hp=f"{mix_position_from_liquid_surface:03}",
      mj=f"{mix_surface_following_distance:03}",
      hs=f"{speed_of_mix:04}",
      cw=channel_pattern_hex,
      cr=f"{limit_curve_index:03}",
      cj=tadm_algorithm,
      cx=recording_mode,
    )

  @need_iswap_parked
  @_requires_head96
  async def dispense_core_96(
    self,
    dispensing_mode: int = 0,
    x_position: int = 0,
    x_direction: int = 0,
    y_position: int = 0,
    second_section_height: int = 0,
    second_section_ratio: int = 3425,
    lld_search_height: int = 3425,
    liquid_surface_no_lld: int = 3425,
    pull_out_distance_transport_air: int = 50,
    minimum_height: int = 3425,
    immersion_depth: int = 0,
    immersion_depth_direction: int = 0,
    surface_following_distance: float = 0,
    minimum_traverse_height_at_beginning_of_a_command: int = 3425,
    min_z_endpos: int = 3425,
    dispense_volume: int = 0,
    dispense_speed: int = 5000,
    cut_off_speed: int = 250,
    stop_back_volume: int = 0,
    transport_air_volume: int = 0,
    blow_out_air_volume: int = 200,
    lld_mode: int = 1,
    gamma_lld_sensitivity: int = 1,
    side_touch_off_distance: int = 0,
    swap_speed: int = 100,
    settling_time: int = 5,
    mixing_volume: int = 0,
    mixing_cycles: int = 0,
    mix_position_from_liquid_surface: int = 250,
    mix_surface_following_distance: int = 0,
    speed_of_mixing: int = 1000,
    channel_pattern: List[bool] = [True] * 12 * 8,
    limit_curve_index: int = 0,
    tadm_algorithm: bool = False,
    recording_mode: int = 0,
    # Deprecated parameters, to be removed in future versions
    # rm: >2026-01:
    liquid_surface_sink_distance_at_the_end_of_dispense: float = 0,  # surface_following_distance!
    tube_2nd_section_ratio: int = 3425,
    liquid_surface_at_function_without_lld: int = 3425,
    maximum_immersion_depth: int = 3425,
    minimal_end_height: int = 3425,
    mixing_position_from_liquid_surface: int = 250,
    surface_following_distance_during_mixing: int = 0,
    pull_out_distance_to_take_transport_air_in_function_without_lld: int = 50,
    tube_2nd_section_height_measured_from_zm: int = 0,
  ):
    """Dispensing of liquid using CoRe 96

    Args:
      dispensing_mode: Type of dispensing mode 0 = Partial volume in jet mode 1 = Blow out
          in jet mode 2 = Partial volume at surface 3 = Blow out at surface 4 = Empty tip at fix
          position. Must be between 0 and 4. Default 0.
      x_position: X-Position [0.1mm] of well A1. Must be between 0 and 30000. Default 0.
      x_direction: X-direction. 0 = positive 1 = negative. Must be between 0 and 1. Default 0.
      y_position: Y-Position [0.1mm] of well A1. Must be between 1080 and 5600. Default 0.
      minimum_height: Minimum height (maximum immersion depth) [0.1mm]. Must be between 0 and 3425. Default 3425.
      second_section_height: Second ratio height. [0.1mm]. Must be between 0 and 3425. Default 0.
      second_section_ratio: Tube 2nd section ratio (See Fig 2.). Must be between 0 and 10000. Default 3425.
      lld_search_height: LLD search height [0.1mm]. Must be between 0 and 3425. Default 3425.
      liquid_surface_no_lld: Liquid surface at function without LLD [0.1mm]. Must be between 0 and 3425. Default 3425.
      pull_out_distance_transport_air: pull out distance to take transport air in function without LLD [0.1mm]. Must be between 0 and 3425. Default 50.
      immersion_depth: Immersion depth [0.1mm]. Must be between 0 and 3600. Default 0.
      immersion_depth_direction: Direction of immersion depth (0 = go deeper, 1 = go up out of
          liquid). Must be between 0 and 1. Default 0.
      surface_following_distance: Liquid surface following distance during dispense [0.1mm].
          Must be between 0 and 990. Default 0. (renamed for clarity from
          'liquid_surface_sink_distance_at_the_end_of_dispense' in firmware docs)
      minimum_traverse_height_at_beginning_of_a_command: Minimal traverse height at begin of
          command [0.1mm]. Must be between 0 and 3425. Default 3425.
      min_z_endpos: Minimal height at command end [0.1mm]. Must be between 0 and 3425. Default 3425.
      dispense_volume: Dispense volume [0.1ul]. Must be between 0 and 11500. Default 0.
      dispense_speed: Dispense speed [0.1ul/s]. Must be between 3 and 5000. Default 5000.
      cut_off_speed: Cut-off speed [0.1ul/s]. Must be between 3 and 5000. Default 250.
      stop_back_volume: Stop back volume [0.1ul/s]. Must be between 0 and 999. Default 0.
      transport_air_volume: Transport air volume [0.1ul]. Must be between 0 and 500. Default 0.
      blow_out_air_volume: Blow-out air volume [0.1ul]. Must be between 0 and 11500. Default 200.
      lld_mode: LLD mode (0 = off, 1 = gamma, 2 = dP, 3 = dual, 4 = Z touch off). Must be
          between 0 and 4. Default 1.
      gamma_lld_sensitivity: gamma LLD sensitivity (1= high, 4=low). Must be between 1 and 4.
          Default 1.
      side_touch_off_distance: side touch off distance [0.1 mm] 0 = OFF ( > 0 = ON & turns LLD off)
        Must be between 0 and 45. Default 1.
      swap_speed: Swap speed (on leaving liquid) [0.1mm/s]. Must be between 3 and 1000. Default 100.
      settling_time: Settling time [0.1s]. Must be between 0 and 99. Default 5.
      mixing_volume: mix volume [0.1ul]. Must be between 0 and 11500. Default 0.
      mixing_cycles: Number of mixing cycles. Must be between 0 and 99. Default 0.
      mix_position_from_liquid_surface: mix position in Z- direction from liquid surface (LLD or absolute terms) [0.1mm]. Must be between 0 and 990. Default 250.
      mix_surface_following_distance: surface following distance during mixing [0.1mm].  Must be between 0 and 990. Default 0.
      speed_of_mixing: Speed of mixing [0.1ul/s]. Must be between 3 and 5000. Default 1000.
      channel_pattern: list of 96 boolean values
      limit_curve_index: limit curve index. Must be between 0 and 999. Default 0.
      tadm_algorithm: TADM algorithm. Default False.
      recording_mode: Recording mode 0 : no 1 : TADM errors only 2 : all TADM measurement. Must
          be between 0 and 2. Default 0.
    """

    # # # TODO: delete > 2026-01 # # #
    # deprecated liquid_surface_sink_distance_at_the_end_of_aspiration:
    if liquid_surface_sink_distance_at_the_end_of_dispense != 0.0:
      surface_following_distance = liquid_surface_sink_distance_at_the_end_of_dispense
      warnings.warn(
        "The liquid_surface_sink_distance_at_the_end_of_dispense parameter is deprecated and will be removed in the future. "
        "Use the Hamilton-standard surface_following_distance parameter instead.\n"
        "liquid_surface_sink_distance_at_the_end_of_dispense currently superseding surface_following_distance.",
        DeprecationWarning,
      )

    if tube_2nd_section_ratio != 3425:
      second_section_ratio = tube_2nd_section_ratio
      warnings.warn(
        "The tube_2nd_section_ratio parameter is deprecated and will be removed in the future. "
        "Use the Hamilton-standard second_section_ratio parameter instead.\n"
        "second_section_ratio currently superseding tube_2nd_section_ratio.",
        DeprecationWarning,
      )

    if maximum_immersion_depth != 3425:
      minimum_height = maximum_immersion_depth
      warnings.warn(
        "The maximum_immersion_depth parameter is deprecated and will be removed in the future. "
        "Use the Hamilton-standard minimum_height parameter instead.\n"
        "minimum_height currently superseding maximum_immersion_depth.",
        DeprecationWarning,
      )

    if liquid_surface_at_function_without_lld != 3425:
      liquid_surface_no_lld = liquid_surface_at_function_without_lld
      warnings.warn(
        "The liquid_surface_at_function_without_lld parameter is deprecated and will be removed in the future. "
        "Use the Hamilton-standard liquid_surface_no_lld parameter instead.\n"
        "liquid_surface_at_function_without_lld currently superseding liquid_surface_no_lld.",
        DeprecationWarning,
      )

    if minimal_end_height != 3425:
      min_z_endpos = minimal_end_height
      warnings.warn(
        "The minimal_end_height parameter is deprecated and will be removed in the future. "
        "Use the Hamilton-standard min_z_endpos parameter instead.\n"
        "minimal_end_height currently superseding min_z_endpos.",
        DeprecationWarning,
      )

    if mixing_position_from_liquid_surface != 250:
      mix_position_from_liquid_surface = mixing_position_from_liquid_surface
      warnings.warn(
        "The mixing_position_from_liquid_surface parameter is deprecated and will be removed in the future. "
        "Use the Hamilton-standard mix_position_from_liquid_surface parameter instead.\n"
        "mixing_position_from_liquid_surface currently superseding mix_position_from_liquid_surface.",
        DeprecationWarning,
      )

    if surface_following_distance_during_mixing != 0:
      mix_surface_following_distance = surface_following_distance_during_mixing
      warnings.warn(
        "The surface_following_distance_during_mixing parameter is deprecated and will be removed in the future. "
        "Use the Hamilton-standard mix_surface_following_distance parameter instead.\n"
        "mix_surface_following_distance currently superseding surface_following_distance_during_mixing.",
        DeprecationWarning,
      )

    if pull_out_distance_to_take_transport_air_in_function_without_lld != 50:
      pull_out_distance_transport_air = (
        pull_out_distance_to_take_transport_air_in_function_without_lld
      )
      warnings.warn(
        "The pull_out_distance_to_take_transport_air_in_function_without_lld parameter is deprecated and will be removed in the future. "
        "Use the Hamilton-standard pull_out_distance_transport_air parameter instead.\n"
        "pull_out_distance_to_take_transport_air_in_function_without_lld currently superseding pull_out_distance_transport_air.",
        DeprecationWarning,
      )

    if tube_2nd_section_height_measured_from_zm != 0:
      second_section_height = tube_2nd_section_height_measured_from_zm
      warnings.warn(
        "The tube_2nd_section_height_measured_from_zm parameter is deprecated and will be removed in the future. "
        "Use the Hamilton-standard second_section_height parameter instead.\n"
        "tube_2nd_section_height_measured_from_zm currently superseding second_section_height.",
        DeprecationWarning,
      )
    # # # delete # # #

    assert 0 <= dispensing_mode <= 4, "dispensing_mode must be between 0 and 4"
    assert 0 <= x_position <= 30000, "x_position must be between 0 and 30000"
    assert 0 <= x_direction <= 1, "x_direction must be between 0 and 1"
    assert 1080 <= y_position <= 5600, "y_position must be between 1080 and 5600"
    assert 0 <= minimum_height <= 3425, "minimum_height must be between 0 and 3425"
    assert 0 <= second_section_height <= 3425, "second_section_height must be between 0 and 3425"
    assert 0 <= second_section_ratio <= 10000, "second_section_ratio must be between 0 and 10000"
    assert 0 <= lld_search_height <= 3425, "lld_search_height must be between 0 and 3425"
    assert 0 <= liquid_surface_no_lld <= 3425, "liquid_surface_no_lld must be between 0 and 3425"
    assert 0 <= pull_out_distance_transport_air <= 3425, (
      "pull_out_distance_transport_air must be between 0 and 3425"
    )
    assert 0 <= immersion_depth <= 3600, "immersion_depth must be between 0 and 3600"
    assert 0 <= immersion_depth_direction <= 1, "immersion_depth_direction must be between 0 and 1"
    assert 0 <= surface_following_distance <= 990, (
      "surface_following_distance must be between 0 and 990"
    )
    assert 0 <= minimum_traverse_height_at_beginning_of_a_command <= 3425, (
      "minimum_traverse_height_at_beginning_of_a_command must be between 0 and 3425"
    )
    assert 0 <= min_z_endpos <= 3425, "min_z_endpos must be between 0 and 3425"
    assert 0 <= dispense_volume <= 11500, "dispense_volume must be between 0 and 11500"
    assert 3 <= dispense_speed <= 5000, "dispense_speed must be between 3 and 5000"
    assert 3 <= cut_off_speed <= 5000, "cut_off_speed must be between 3 and 5000"
    assert 0 <= stop_back_volume <= 999, "stop_back_volume must be between 0 and 999"
    assert 0 <= transport_air_volume <= 500, "transport_air_volume must be between 0 and 500"
    assert 0 <= blow_out_air_volume <= 11500, "blow_out_air_volume must be between 0 and 11500"
    assert 0 <= lld_mode <= 4, "lld_mode must be between 0 and 4"
    assert 1 <= gamma_lld_sensitivity <= 4, "gamma_lld_sensitivity must be between 1 and 4"
    assert 0 <= side_touch_off_distance <= 45, "side_touch_off_distance must be between 0 and 45"
    assert 3 <= swap_speed <= 1000, "swap_speed must be between 3 and 1000"
    assert 0 <= settling_time <= 99, "settling_time must be between 0 and 99"
    assert 0 <= mixing_volume <= 11500, "mixing_volume must be between 0 and 11500"
    assert 0 <= mixing_cycles <= 99, "mixing_cycles must be between 0 and 99"
    assert 0 <= mix_position_from_liquid_surface <= 990, (
      "mix_position_from_liquid_surface must be between 0 and 990"
    )
    assert 0 <= mix_surface_following_distance <= 990, (
      "mix_surface_following_distance must be between 0 and 990"
    )
    assert 3 <= speed_of_mixing <= 5000, "speed_of_mixing must be between 3 and 5000"
    assert 0 <= limit_curve_index <= 999, "limit_curve_index must be between 0 and 999"
    assert 0 <= recording_mode <= 2, "recording_mode must be between 0 and 2"

    # Convert bool list to hex string
    assert len(channel_pattern) == 96, "channel_pattern must be a list of 96 boolean values"
    channel_pattern_bin_str = reversed(["1" if x else "0" for x in channel_pattern])
    channel_pattern_hex = hex(int("".join(channel_pattern_bin_str), 2)).upper()[2:]

    return await self.send_command(
      module="C0",
      command="ED",
      da=dispensing_mode,
      xs=f"{x_position:05}",
      xd=x_direction,
      yh=f"{y_position:04}",
      zm=f"{minimum_height:04}",
      zv=f"{second_section_height:04}",
      zq=f"{second_section_ratio:05}",
      lz=f"{lld_search_height:04}",
      zt=f"{liquid_surface_no_lld:04}",
      pp=f"{pull_out_distance_transport_air:04}",
      iw=f"{immersion_depth:03}",
      ix=immersion_depth_direction,
      fh=f"{surface_following_distance:03}",
      zh=f"{minimum_traverse_height_at_beginning_of_a_command:04}",
      ze=f"{min_z_endpos:04}",
      df=f"{dispense_volume:05}",
      dg=f"{dispense_speed:04}",
      es=f"{cut_off_speed:04}",
      ev=f"{stop_back_volume:03}",
      vt=f"{transport_air_volume:03}",
      bv=f"{blow_out_air_volume:05}",
      cm=lld_mode,
      cs=gamma_lld_sensitivity,
      ej=f"{side_touch_off_distance:02}",
      bs=f"{swap_speed:04}",
      wh=f"{settling_time:02}",
      hv=f"{mixing_volume:05}",
      hc=f"{mixing_cycles:02}",
      hp=f"{mix_position_from_liquid_surface:03}",
      mj=f"{mix_surface_following_distance:03}",
      hs=f"{speed_of_mixing:04}",
      cw=channel_pattern_hex,
      cr=f"{limit_curve_index:03}",
      cj=tadm_algorithm,
      cx=recording_mode,
    )

  # -------------- 3.10.4 Adjustment & movement commands --------------

  @_requires_head96
  async def move_core_96_head_to_defined_position(
    self,
    x: float,
    y: float,
    z: float = 342.5,
    minimum_height_at_beginning_of_a_command: float = 342.5,
  ):
    """Move CoRe 96 Head to defined position

    Args:
      x: X-Position [1mm] of well A1. Must be between -300.0 and 300.0. Default 0.
      y: Y-Position [1mm]. Must be between 108.0 and 560.0. Default 0.
      z: Z-Position [1mm]. Must be between 0 and 560.0. Default 0.
      minimum_height_at_beginning_of_a_command: Minimum height at beginning of a command [1mm]
        (refers to all channels independent of tip pattern parameter 'tm'). Must be between 0 and
        342.5. Default 342.5.
    """

    warnings.warn(  # TODO: remove 2025-02
      "`move_core_96_head_to_defined_position` is deprecated and will be "
      "removed in 2025-02. Use `head96_move_to_coordinate` instead.",
      DeprecationWarning,
      stacklevel=2,
    )

    # TODO: these are values for a STARBackend. Find them for a STARlet.
    self._check_96_position_legal(Coordinate(x, y, z))
    assert 0 <= minimum_height_at_beginning_of_a_command <= 342.5, (
      "minimum_height_at_beginning_of_a_command must be between 0 and 342.5"
    )

    return await self.send_command(
      module="C0",
      command="EM",
      xs=f"{abs(round(x * 10)):05}",
      xd=0 if x >= 0 else 1,
      yh=f"{round(y * 10):04}",
      za=f"{round(z * 10):04}",
      zh=f"{round(minimum_height_at_beginning_of_a_command * 10):04}",
    )

  @_requires_head96
  async def head96_move_to_coordinate(
    self,
    coordinate: Coordinate,
    minimum_height_at_beginning_of_a_command: float = 342.5,
  ):
    """Move STAR(let) 96-Head to defined Coordinate

    Args:
      coordinate: Coordinate of A1 in mm
        - if tip present refers to tip bottom,
        - if not present refers to channel bottom
      minimum_height_at_beginning_of_a_command: Minimum height at beginning of a command [1mm]
        (refers to all channels independent of tip pattern parameter 'tm'). Must be between ? and
        342.5. Default 342.5.
    """

    self._check_96_position_legal(coordinate)

    assert 0 <= minimum_height_at_beginning_of_a_command <= 342.5, (
      "minimum_height_at_beginning_of_a_command must be between 0 and 342.5"
    )

    return await self.send_command(
      module="C0",
      command="EM",
      xs=f"{abs(round(coordinate.x * 10)):05}",
      xd="0" if coordinate.x >= 0 else "1",
      yh=f"{round(coordinate.y * 10):04}",
      za=f"{round(coordinate.z * 10):04}",
      zh=f"{round(minimum_height_at_beginning_of_a_command * 10):04}",
    )

  @_requires_head96
  async def head96_dispensing_drive_move_to_position(
    self,
    position,
    speed: float = 261.1,
    stop_speed: float = 0,
    acceleration: float = 17406.84,
    current_protection_limiter: int = 15,
  ):
    """Move dispensing drive to absolute position in uL

    Args:
      position: Position in uL. Between 0, 1244.59.
      speed: Speed in uL/s. Between 0.1, 1063.75.
      stop_speed: Stop speed in uL/s. Between 0, 1063.75.
      acceleration: Acceleration in uL/s^2. Between 96.7, 17406.84.
      current_protection_limiter: Current protection limiter (0-15), default 15
    """

    if not (
      self.HEAD96_DISPENSING_DRIVE_VOL_LIMIT_BOTTOM
      <= position
      <= self.HEAD96_DISPENSING_DRIVE_VOL_LIMIT_TOP
    ):
      raise ValueError("position must be between 0 and 1244.59")
    if not (0.1 <= speed <= 1063.75):
      raise ValueError("speed must be between 0.1 and 1063.75")
    if not (0 <= stop_speed <= 1063.75):
      raise ValueError("stop_speed must be between 0 and 1063.75")
    if not (96.7 <= acceleration <= 17406.84):
      raise ValueError("acceleration must be between 96.7 and 17406.84")
    if not (0 <= current_protection_limiter <= 15):
      raise ValueError("current_protection_limiter must be between 0 and 15")

    position_increments = self._head96_dispensing_drive_uL_to_increment(position)
    speed_increments = self._head96_dispensing_drive_uL_to_increment(speed)
    stop_speed_increments = self._head96_dispensing_drive_uL_to_increment(stop_speed)
    acceleration_increments = self._head96_dispensing_drive_uL_to_increment(acceleration)

    await self.send_command(
      module="H0",
      command="DQ",
      dq=f"{position_increments:05}",
      dv=f"{speed_increments:05}",
      du=f"{stop_speed_increments:05}",
      dr=f"{acceleration_increments:06}",
      dw=f"{current_protection_limiter:02}",
    )

  async def move_core_96_head_x(self, x_position: float):
    """Move CoRe 96 Head X to absolute position

    .. deprecated::
      Use :meth:`head96_move_x` instead. Will be removed in 2026-06.
    """
    warnings.warn(
      "`move_core_96_head_x` is deprecated. Use `head96_move_x` instead.",
      DeprecationWarning,
      stacklevel=2,
    )
    return await self.head96_move_x(x_position)

  async def move_core_96_head_y(self, y_position: float):
    """Move CoRe 96 Head Y to absolute position

    .. deprecated::
      Use :meth:`head96_move_y` instead. Will be removed in 2026-06.
    """
    warnings.warn(
      "`move_core_96_head_y` is deprecated. Use `head96_move_y` instead.",
      DeprecationWarning,
      stacklevel=2,
    )
    return await self.head96_move_y(y_position)

  async def move_core_96_head_z(self, z_position: float):
    """Move CoRe 96 Head Z to absolute position

    .. deprecated::
      Use :meth:`head96_move_z` instead. Will be removed in 2026-06.
    """
    warnings.warn(
      "`move_core_96_head_z` is deprecated. Use `head96_move_z` instead.",
      DeprecationWarning,
      stacklevel=2,
    )
    return await self.head96_move_z(z_position)

  async def move_96head_to_coordinate(
    self,
    coordinate: Coordinate,
    minimum_height_at_beginning_of_a_command: float = 342.5,
  ):
    """Move STAR(let) 96-Head to defined Coordinate

    .. deprecated::
      Use :meth:`head96_move_to_coordinate` instead. Will be removed in 2026-06.
    """
    warnings.warn(
      "`move_96head_to_coordinate` is deprecated. Use `head96_move_to_coordinate` instead.",
      DeprecationWarning,
      stacklevel=2,
    )
    return await self.head96_move_to_coordinate(
      coordinate=coordinate,
      minimum_height_at_beginning_of_a_command=minimum_height_at_beginning_of_a_command,
    )

  # -------------- 3.10.5 Wash procedure commands using CoRe 96 Head --------------

  # TODO:(command:EG) Washing tips using CoRe 96 Head
  # TODO:(command:EU) Empty washed tips (end of wash procedure only)

  # -------------- 3.10.6 Query CoRe 96 Head --------------

  async def request_tip_presence_in_core_96_head(self):
    """Deprecated - use `head96_request_tip_presence` instead.

    Returns:
      dictionary with key qh:
        qh: 0 = no tips, 1 = tips are picked up
    """
    warnings.warn(  # TODO: remove 2026-06
      "`request_tip_presence_in_core_96_head` is deprecated and will be "
      "removed in 2026-06 use `head96_request_tip_presence` instead.",
      DeprecationWarning,
      stacklevel=2,
    )

    return await self.send_command(module="C0", command="QH", fmt="qh#")

  async def head96_request_tip_presence(self) -> int:
    """Request Tip presence on the 96-Head

    Note: this command requests this information from the STAR(let)'s
      internal memory.
      It does not directly sense whether tips are present.

    Returns:
      0 = no tips
      1 = firmware believes tips are on the 96-head
    """
    resp = await self.send_command(module="C0", command="QH", fmt="qh#")

    return int(resp["qh"])

  async def request_position_of_core_96_head(self):
    """Deprecated - use `head96_request_position` instead."""

    warnings.warn(  # TODO: remove 2026-02
      "`request_position_of_core_96_head` is deprecated and will be "
      "removed in 2026-02 use `head96_request_position` instead.",
      DeprecationWarning,
      stacklevel=2,
    )

    return await self.head96_request_position()

  async def head96_request_position(self) -> Coordinate:
    """Request position of CoRe 96 Head (A1 considered to tip length)

    Returns:
      Coordinate: x, y, z in mm
    """

    resp = await self.send_command(module="C0", command="QI", fmt="xs#####xd#yh####za####")

    x_coordinate = resp["xs"] / 10
    y_coordinate = resp["yh"] / 10
    z_coordinate = resp["za"] / 10

    x_coordinate = x_coordinate if resp["xd"] == 0 else -x_coordinate

    return Coordinate(x=x_coordinate, y=y_coordinate, z=z_coordinate)

  async def request_core_96_head_channel_tadm_status(self):
    """Request CoRe 96 Head channel TADM Status

    Returns:
      qx: TADM channel status 0 = off 1 = on
    """

    return await self.send_command(module="C0", command="VC", fmt="qx#")

  async def request_core_96_head_channel_tadm_error_status(self):
    """Request CoRe 96 Head channel TADM error status

    Returns:
      vb: error pattern 0 = no error
    """

    return await self.send_command(module="C0", command="VB", fmt="vb" + "&" * 24)

  async def head96_dispensing_drive_request_position_mm(self) -> float:
    """Request 96 Head dispensing drive position in mm"""
    resp = await self.send_command(module="H0", command="RD", fmt="rd######")
    return self._head96_dispensing_drive_increment_to_mm(resp["rd"])

  async def head96_dispensing_drive_request_position_uL(self) -> float:
    """Request 96 Head dispensing drive position in uL"""
    position_mm = await self.head96_dispensing_drive_request_position_mm()
    return self._head96_dispensing_drive_mm_to_uL(position_mm)
