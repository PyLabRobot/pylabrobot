"""Diagnostics: where the device's parts really stand, probed with its own channels."""

import asyncio
import logging
import math
import statistics
from typing import TYPE_CHECKING, Dict, List, Literal, Mapping, Optional, Sequence, Tuple

from pylabrobot.resources.coordinate import Coordinate
from pylabrobot.resources.deck import Deck
from pylabrobot.resources.hamilton.core_grippers import HamiltonCoreGrippers
from pylabrobot.resources.head_tool import HeadTool

if TYPE_CHECKING:
  from pylabrobot.hamilton.star.driver.features.pipettes import Pipettes
  from pylabrobot.hamilton.star.driver.master import STARDriver

logger = logging.getLogger(__name__)


class Diagnostics:
  """Probes that find where parts of the device stand, against where the model has them.

  Reached as `driver.diagnostics`, on a device whose channels can carry the CoRe gripper tools.
  It sends nothing of its own: it composes the pipettes' moves and probes.
  """

  def __init__(self, driver: "STARDriver") -> None:
    """
    Args:
      driver: the driver whose channels and deck it probes with.
    """
    self._driver = driver

  # -- what it probes with -------------------------------------------------------------------------

  @property
  def _pipettes(self) -> "Pipettes":
    """The channels that probe.

    Raises:
      RuntimeError: If there are none.
    """
    pipettes = self._driver.pipettes
    if pipettes is None:
      raise RuntimeError("no pipettes to probe with; have you called `star.setup()`?")
    return pipettes

  @property
  def _deck(self) -> Deck:
    """The deck positions are measured from.

    Raises:
      RuntimeError: If the driver was given no deck.
    """
    if self._driver.deck is None:
      raise RuntimeError("diagnostics probe parts of the deck; this driver was given none")
    return self._driver.deck

  # ----------------------------------------
  # CoRe gripper tools
  # ----------------------------------------

  def _get_core_gripper_tools(self) -> Tuple[HeadTool, HeadTool]:
    """The CoRe gripper tools in the deck's holder.

    Returns:
      The back tool and the front tool.

    Raises:
      TypeError: If the deck carries no holder, or the holder not two tools.
    """
    deck = self._deck
    for resource in deck.get_all_children():
      if isinstance(resource, HamiltonCoreGrippers):
        tools = [child for child in resource.children if isinstance(child, HeadTool)]
        if len(tools) != 2:
          raise TypeError(f"the CoRe gripper holder carries {len(tools)} tools, not 2")
        front, back = sorted(tools, key=lambda tool: tool.get_location_wrt(other=deck, y="c").y)
        return back, front
    raise TypeError("the deck carries no CoRe gripper holder")

  async def _require_walls(
    self, found: Mapping[int, Sequence[Optional[float]]], wall: str, reach: float
  ) -> Dict[int, float]:
    """Each channel's wall, the median of its readings, once every search found it.

    Args:
      found: each channel's readings of the wall, in mm; None where a search found nothing.
      wall: which wall, for the error.
      reach: how far past the modelled centre each search went, in mm, for the error.

    Returns:
      Each channel's median reading, in mm.

    Raises:
      RuntimeError: If any search found nothing, every such channel named; Z safety first.
    """
    missed = {
      channel: sum(reading is None for reading in readings)
      for channel, readings in found.items()
      if any(reading is None for reading in readings)
    }
    if missed:
      await self._pipettes.move_to_safe_z()
      raise RuntimeError(
        "; ".join(
          f"channel {channel} found no {wall} wall within {reach:.2f} mm of the modelled centre "
          f"in {count} of {len(found[channel])} searches"
          for channel, count in missed.items()
        )
      )
    return {
      channel: statistics.median(reading for reading in readings if reading is not None)
      for channel, readings in found.items()
    }

  @staticmethod
  def _compute_circle_center_x(
    chord_x: float, chord_back_y: float, chord_front_y: float, left_x: float
  ) -> float:
    """X of a circle's centre, from a chord parallel to Y and a point on the chord's bisector.

    Args:
      chord_x: X of the chord, in mm.
      chord_back_y: Y of the chord's back end, in mm.
      chord_front_y: Y of the chord's front end, in mm.
      left_x: X of the circle's point level with the chord's midpoint, left of the chord, in mm.

    Returns:
      The centre's X, in mm.
    """
    half_chord = (chord_back_y - chord_front_y) / 2
    return (left_x**2 - chord_x**2 - half_chord**2) / (2 * (left_x - chord_x))

  async def probe_core_gripper_tool_centers(
    self,
    *,
    front_channel: int,
    top_probe_front_channel: Optional[int] = None,
    modelled_z_top: Optional[float] = None,
    depth: float = 2.0,
    max_top_deviation: float = 1.0,
    max_center_deviation: float = 2.0,
    n_replicates: int = 3,
    top_probe_x_offset: float = 2.5,
    top_probe_post_detection_distance: float = 4.0,
  ) -> Dict[int, Coordinate]:
    """Probe where the two CoRe gripper tools stand, with one neighbouring pair of channels.

    First a bare pair probes both tools' tops with its stop discs, unless `modelled_z_top` is
    given, in which case nothing is probed in Z. Then each channel of the probing pair probes its
    own tool from inside the opening, `depth` below its top: its back and front walls (both
    channels together, from the tools' modelled centres), then its left wall from the chord's
    midpoint (one channel at a time, the other at Z safety). Each wall is the median of
    `n_replicates`; the centre is the circle through the three. Every reading is logged at DEBUG.

    Assumptions:
      - Each tool's opening is a circle at the probing depth.
      - Both Y walls are probed at the same X, the tool's modelled centre: one arm carries both
        channels. The chord through them need not pass through the centre.
      - The left wall is probed level with the chord's midpoint, to within the master's 0.1 mm
        Y step.
      - Each wall lies within half the tool's width plus `max_center_deviation` of the
        modelled centre; that bounds each search and does not enter the fit.

    Args:
      front_channel: the channel probing the front tool; `front_channel - 1` probes the back one,
        as the pair is named in `CoreGrippers.pick_up_tools`. Both already carry a conductive
        needle or tip: this method picks up nothing.
      top_probe_front_channel: the channel whose stop disc probes the front tool's top;
        `top_probe_front_channel - 1` probes the back one's. Both carry nothing. Needed unless
        `modelled_z_top` is given.
      modelled_z_top: both tools' top height, in mm, used instead of probing the tops. Above the
        Z drive's floor, below the safety height `default_minimum_traverse_height`, and within
        `max_top_deviation` of the model, as a probed top. The tops are probed when None.
      depth: how far below its tool's top each needle or tip probes, in mm.
      max_top_deviation: how far a top may lie from the model, in mm; the search for each top
        runs from this plus `search_start_clearance` above the model down to this below it.
      max_center_deviation: how far a tool's centre may lie from the model, in mm; each wall
        search runs half the tool's width plus this past the modelled centre, and no further.
      n_replicates: how often each wall is searched, from the centre again each time.
      top_probe_x_offset: how far right of each tool's centre the stop disc probes its top, onto
        the rim beside the opening, in mm.
      top_probe_post_detection_distance: how far each stop disc moves up off a top once it has
        detected it, in mm.

    Returns:
      Each probing channel's tool centre, in mm on the deck, at the height it probed.

    Raises:
      ValueError: If a pair has no channel behind it, the two pairs share a channel, both or
        neither of `top_probe_front_channel` and `modelled_z_top` are given, `modelled_z_top` is
        outside the drive's reach or the safety height, or more than `max_top_deviation` from the
        model, or a value is out of its range; every such problem in one error.
      RuntimeError: If a probing channel senses no needle or tip, a top-probing channel senses
        one, a top is not found or lies more than `max_top_deviation` from the model, or a wall
        search finds nothing.
    """
    pipettes = self._pipettes

    # 1. checks
    back_channel = front_channel - 1
    channels = [back_channel, front_channel]
    # The tops are probed by a bare pair, or taken as given; one of the two.
    top_channels = (
      []
      if top_probe_front_channel is None
      else [top_probe_front_channel - 1, top_probe_front_channel]
    )
    # Every problem is reported at once, not only the first.
    problems = [
      f"{name} must be 1 to {pipettes.num_channels - 1}, is {front}"
      for name, front in (
        ("front_channel", front_channel),
        ("top_probe_front_channel", top_probe_front_channel),
      )
      if front is not None and not 1 <= front < pipettes.num_channels
    ]
    if (top_probe_front_channel is None) == (modelled_z_top is None):
      problems.append(
        "give either top_probe_front_channel, to probe the tops, or modelled_z_top, not "
        + ("both" if modelled_z_top is not None else "neither")
      )
    if set(channels) & set(top_channels):
      problems.append(
        f"the probing pair {channels} and the top-probing pair {top_channels} overlap"
      )
    for name, value, valid, needed in (
      ("depth", depth, depth > 0, "more than 0"),
      ("max_top_deviation", max_top_deviation, max_top_deviation > 0, "more than 0"),
      ("max_center_deviation", max_center_deviation, max_center_deviation > 0, "more than 0"),
      ("n_replicates", n_replicates, n_replicates >= 1, "at least 1"),
      ("top_probe_x_offset", top_probe_x_offset, top_probe_x_offset >= 0, "0 or more"),
      (
        "top_probe_post_detection_distance",
        top_probe_post_detection_distance,
        top_probe_post_detection_distance >= 0,
        "0 or more",
      ),
    ):
      if not valid:
        problems.append(f"{name} must be {needed}, is {value}")
    # A given top lies where a tool top can, above the Z drive's floor and below the safety
    # height, and as close to the model as a probed top must.
    if modelled_z_top is not None:
      floor, safety = pipettes.configuration.z_range[0], pipettes.default_minimum_traverse_height
      if not floor < modelled_z_top < safety:
        problems.append(
          f"modelled_z_top must lie between the Z drive's floor {floor} mm and the safety "
          f"height {safety} mm, is {modelled_z_top}"
        )
      for gripper_tool in self._get_core_gripper_tools():
        model_top = gripper_tool.get_location_wrt(other=self._deck, z="t").z
        if abs(modelled_z_top - model_top) > max_top_deviation:
          problems.append(
            f"modelled_z_top {modelled_z_top} mm is {modelled_z_top - model_top:+.2f} mm off "
            f"{gripper_tool.name}'s modelled top {model_top} mm, more than max_top_deviation "
            f"{max_top_deviation} mm"
          )
    if problems:
      raise ValueError("; ".join(problems))
    # Nothing is picked up or returned here: the probing pair already carries its needles or tips,
    # and the top-probing pair carries nothing, so its stop discs touch the tops.
    sensed = await pipettes.sense_tip_presence()
    bare = [channel for channel in channels if not sensed[channel]]
    carrying = [channel for channel in top_channels if sensed[channel]]
    problems = []
    if bare:
      problems.append(f"channels {bare} sense no needle or tip; pick one up on each first")
    if carrying:
      problems.append(f"channels {carrying} sense a needle or tip; the top probe needs them bare")
    if problems:
      raise RuntimeError("; ".join(problems))

    # 2. where the model has the tools
    deck = self._deck
    back_tool, front_tool = self._get_core_gripper_tools()
    back_top = back_tool.get_location_wrt(other=deck, x="c", y="c", z="t")
    front_top = front_tool.get_location_wrt(other=deck, x="c", y="c", z="t")
    center_x = back_top.x
    center_ys = {back_channel: back_top.y, front_channel: front_top.y}
    # Each wall lies at most half the tool's width, plus how far its centre may be off, away.
    search_reach = back_tool.get_absolute_size_y() / 2 + max_center_deviation

    # 3. the tools' tops: as given, or by the bare pair's stop discs on each tool's rim
    checked_tops: List[float] = []
    if modelled_z_top is not None:
      checked_tops = [modelled_z_top, modelled_z_top]
    else:
      # Each searched from a clearance above where the model allows its top, down to its lowest
      # allowed: a top outside that is refused anyway, so nothing below it is searched.
      model_tops = (back_top.z, front_top.z)
      search_start_positions = [
        round(model_top + max_top_deviation + pipettes.search_start_clearance, 2)
        for model_top in model_tops
      ]
      search_end_positions = [round(model_top - max_top_deviation, 2) for model_top in model_tops]
      await pipettes.move_to_safe_z()
      await pipettes.move_to_xy_positions(
        x=center_x + top_probe_x_offset,
        ys={top_channels[0]: back_top.y, top_channels[1]: front_top.y},
        make_space=True,
      )
      probed_tops = await asyncio.gather(
        *(
          pipettes.probe_z_using_clld(
            channel=channel,
            search_start_position=start,
            search_end_position=end,
            allow_without_tip=True,
            post_detection_distance=top_probe_post_detection_distance,
          )
          for channel, start, end in zip(top_channels, search_start_positions, search_end_positions)
        )
      )
      logger.debug("CoRe gripper tool tops by channels %s: %s", top_channels, probed_tops)
      problems = []
      for tool, probed_top, model_top, start, end in zip(
        ("back", "front"), probed_tops, model_tops, search_start_positions, search_end_positions
      ):
        if probed_top is None:
          problems.append(f"no top found on the {tool} tool between {start} and {end} mm")
        elif abs(probed_top - model_top) > max_top_deviation:
          problems.append(
            f"the {tool} tool's top is {probed_top - model_top:+.2f} mm off the model"
          )
        else:
          checked_tops.append(probed_top)
      if problems:
        # Up before raising; on success the next move takes the channels up anyway.
        await pipettes.move_to_safe_z()
        raise RuntimeError("; ".join(problems))
    probe_zs = {back_channel: checked_tops[0] - depth, front_channel: checked_tops[1] - depth}

    # 4. the pair over its tools' centres and its neighbours clear of its searches, in one move
    # Each neighbour the smallest gap past the farthest search, rounded away to the 0.1 mm Y step.
    spacings = pipettes.minimum_y_spacings
    ys = dict(center_ys)
    if back_channel > 0:
      reach_back = center_ys[back_channel] + search_reach + spacings[back_channel - 1]
      ys[back_channel - 1] = math.ceil(round(reach_back * 10, 6)) / 10
    if front_channel < pipettes.num_channels - 1:
      reach_front = center_ys[front_channel] - search_reach - spacings[front_channel]
      ys[front_channel + 1] = math.floor(round(reach_front * 10, 6)) / 10
    await pipettes.move_to_safe_z()
    await pipettes.move_to_xy_positions(x=center_x, ys=ys, make_space=True)

    # 5. Y: back and front walls, both channels together, each direction from the centres
    await pipettes.move_tool_bottom_to_z_positions(zs=probe_zs)
    readings: Dict[str, List] = {}
    walls: Dict[str, Dict[int, float]] = {}
    directions: Tuple[Tuple[Literal["backward", "forward"], int], ...] = (
      ("backward", +1),
      ("forward", -1),
    )
    for direction, sign in directions:
      await pipettes.move_to_y_positions(ys=center_ys)
      readings[direction] = []
      for replicate in range(n_replicates):
        found = await asyncio.gather(
          *(
            pipettes.probe_y_using_clld(
              channel=channel,
              direction=direction,
              search_end_position=center_ys[channel] + sign * search_reach,
            )
            for channel in channels
          )
        )
        logger.debug(
          "CoRe gripper tool %s search %d of %d by channels %s: %s",
          direction,
          replicate + 1,
          n_replicates,
          channels,
          found,
        )
        readings[direction].append(found)
      walls[direction] = await self._require_walls(
        found=dict(zip(channels, zip(*readings[direction]))),
        wall="back" if direction == "backward" else "front",
        reach=search_reach,
      )
    mid_ys = {
      channel: (walls["backward"][channel] + walls["forward"][channel]) / 2 for channel in channels
    }

    # 6. X: left wall from each chord's midpoint, one channel at a time
    # The arm carries both channels, so the other one waits at Z safety.
    await pipettes.move_to_safe_z()
    await pipettes.move_to_xy_positions(x=center_x, ys=mid_ys, make_space=True)
    left_readings: Dict[int, List[Optional[float]]] = {}
    for channel in channels:
      await pipettes.move_tool_bottom_to_z_positions(zs={channel: probe_zs[channel]})
      left_readings[channel] = []
      for replicate in range(n_replicates):
        left = await pipettes.probe_x_using_clld(
          channel=channel, direction="left", search_end_position=center_x - search_reach
        )
        logger.debug(
          "CoRe gripper tool left search %d of %d by channel %s: %s",
          replicate + 1,
          n_replicates,
          channel,
          left,
        )
        left_readings[channel].append(left)
      await pipettes.move_to_safe_z()
    readings["left"] = list(left_readings.values())
    lefts = await self._require_walls(found=left_readings, wall="left", reach=search_reach)

    # 7. back over the tools' centres; the X step left both channels at Z safety
    await pipettes.move_to_xy_positions(x=center_x, ys=center_ys, make_space=True)

    # 8. each tool's centre: the circle through its three walls
    centers = {
      channel: Coordinate(
        x=round(
          self._compute_circle_center_x(
            chord_x=center_x,
            chord_back_y=walls["backward"][channel],
            chord_front_y=walls["forward"][channel],
            left_x=lefts[channel],
          ),
          2,
        ),
        y=round(mid_ys[channel], 2),
        z=round(probe_zs[channel], 2),
      )
      for channel in channels
    }
    logger.debug("CoRe gripper tool walls by channels %s: %s", channels, readings)
    logger.info("CoRe gripper tool centres by channels %s: %s", channels, centers)
    return centers
