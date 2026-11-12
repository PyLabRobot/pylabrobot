"""A Multichannel pod's 96-channel head: its mandrels and plunger (D). Reached as
`driver.left_x_arm.head96` (on whichever pod is a Multichannel).

The head's axes (Z, D) are the pod's, moved through `Pod.move_axes`. The host commands here are the
Master Controller's Multichannel ones: `LT` (`driver/recipes/tip_load.py`) and `HD`. Picking tips up,
dropping them into a rack and discarding them are Biomek's compositions of those:

- pick up: the gripper turned clear (GR to its park angle), mandrel A1 over its tip spot, Z down to
  the seat (the rack's top: on a TL position the rack sits 0.254 cm lower, on the ALP's
  adaptor plate), `LT` with the arguments Biomek derives (`TipLoad.from_configuration`: the
  force scales with the tips loaded), Z up;
- drop into a rack: mandrel A1 over its spot, Z to the seat + `TipUnloadZOffset`, then the eject;
- discard: mandrel A1 at the trash position's `TipDisposalOffsetX`/`Y` from its back left corner,
  the tips' ends `TipDisposalOffsetZ` above it (`resource_model.alps.TipDisposal`), then the eject;
- the eject: the plunger to the bottom of its travel, which drives the ejector, while Z rises by
  the plunger's stroke (so the tips stay put as the mandrels leave them), then the plunger to 0, to
  the bottom again and to 0, at `TipUnloadShuckSpeed`.

Partial loads align as STAR's head does: mandrel `channel` over spot `tip_spot`; only mandrels over
a spot of the rack take a tip (Biomek's 12- and 24-tip loads put mandrel A1 over row H or G).
The head has no tip sensor: which mandrels carry tips is its record of the tips it picked up,
which the device saves and restores on the shafts (`BiomekI7Device`'s `state_json`).

A carried tip's end is the head's Z less the tip's effective length, `TipHeight` less the head's
`TipSeatingDepth` (on the 96-head the seating depth is the head's, not
the tip class's, so the `Tip`'s `fitting_depth`, the Span-8's, is not used here).
"""

import asyncio
import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Dict, List, Mapping, Optional, Sequence, Tuple

from pylabrobot.beckman_coulter.biomek_i7 import targeting
from pylabrobot.beckman_coulter.biomek_i7 import units
from pylabrobot.beckman_coulter.biomek_i7.driver import liquid
from pylabrobot.beckman_coulter.biomek_i7.driver.recipes.homing import PLUNGER_RECIPES, Homer
from pylabrobot.beckman_coulter.biomek_i7.driver.recipes.tip_load import TipLoad, load_tips
from pylabrobot.beckman_coulter.biomek_i7.resource_model.alps import BiomekPosition
from pylabrobot.beckman_coulter.biomek_i7.resource_model.heads import show_tip
from pylabrobot.beckman_coulter.biomek_i7.techniques import (
  Conditioning,
  HeightFrom,
  MixHeights,
  TipTouch,
)
from pylabrobot.beckman_coulter.biomek_i7.units import (
  offset_to_biomek,
  percent_to_field,
  rate_to_field,
)
from pylabrobot.beckman_coulter.biomek_i7.units import optional_cm as _cm
from pylabrobot.lib.liquid_handling.mix import Mix
from pylabrobot.resources.container import Container
from pylabrobot.resources.coordinate import Coordinate
from pylabrobot.resources.n_channel_pipettes import NChannelPipette
from pylabrobot.resources.plate import Plate
from pylabrobot.resources.resource import Resource
from pylabrobot.resources.beckman_coulter.tips import beckman_coulter_biomek_tip_1070uL
from pylabrobot.resources.tip import Tip
from pylabrobot.resources.tip_rack import TipRack, TipSpot
from pylabrobot.resources.volume_tracker import VolumeTracker, does_volume_tracking

if TYPE_CHECKING:
  from pylabrobot.beckman_coulter.biomek_i7.deck import BiomekI7Deck
  from pylabrobot.beckman_coulter.biomek_i7.driver.configuration import (
    DeviceConfiguration,
    PodConfiguration,
  )
  from pylabrobot.beckman_coulter.biomek_i7.driver.features.pod import Pod

logger = logging.getLogger(__name__)

ROWS, COLUMNS = 8, 12
# uL: how far a plunger read back from counts may sit below what it was sent to (a count is
# 0.0043 uL on this head).
PLUNGER_TOLERANCE = 0.01
DEFAULT_TRASH = "TR1"
# The longest tip the 96-head takes (1070 uL and 1025 uL classes: 10.9855 cm), assumed for a
# precautionary discard when no tips are recorded (`discard_any_tips`).
ASSUMED_TIP = beckman_coulter_biomek_tip_1070uL


def mandrel_name(row: int, column: int) -> str:
  return f"{'ABCDEFGH'[row]}{column + 1}"


def mandrel_index(name: str) -> Tuple[int, int]:
  """(row, column) of a mandrel or well name such as `A1`, `H12`."""
  row, column = "ABCDEFGH".index(name[0].upper()), int(name[1:]) - 1
  if not (0 <= row < ROWS and 0 <= column < COLUMNS):
    raise ValueError(f"no mandrel {name!r} on a 96-channel head")
  return row, column


@dataclass(frozen=True)
class HeadPlan:
  """Where the head goes for a tip operation: mandrel A1's X/Y and the operation's Z, in Biomek's
  frame (cm), and which mandrel meets which tip spot."""

  x: float
  y: float
  z: float
  pairs: Tuple[Tuple[str, TipSpot], ...] = ()


@dataclass(frozen=True)
class LiquidPlan:
  """An aspirate or dispense: mandrel A1's X/Y over the well(s) in Biomek's frame (cm), the
  channels, the template's moves (`driver/liquid.py`) and which mandrel works which container."""

  x: float
  y: float
  channels: Tuple[liquid.Channel, ...]
  steps: Tuple[liquid.Step, ...]
  pairs: Tuple[Tuple[str, Container], ...]


class Head96:
  """The 96-channel head of a Multichannel pod."""

  # Where neither the caller nor a technique gives a value (STAR P6): the tips' ends 1 mm above
  # the bottom, Biomek's Water liquid type's 90 uL/s, the Z speed of its MC techniques.
  default_liquid_height: float = 1.0  # mm
  default_flow_rate: float = 90.0  # uL/s
  default_z_speed: float = 19.0  # mm/s: its MC techniques' 20 % of the Multichannel Z on this unit

  def __init__(self, pod: "Pod"):
    self._pod = pod
    # The tips on the mandrels, by mandrel name, as this session picked them up.
    self._tips: Dict[str, Tip] = {}
    # The head in the resource tree, as the STAR's `head96.resource`: its shafts carry the tips.
    # Set by the device that models it (`BiomekI7Device`); None when nothing models it.
    self.resource: Optional[NChannelPipette] = None

  def __str__(self) -> str:
    return f"{self._pod}'s 96-channel head"

  @property
  def arm(self) -> "Pod":
    """The pod this head is on (STAR's `feature.arm`)."""
    return self._pod

  async def home_plunger(self, force: bool = False) -> None:
    """Home the plunger, the Master Controller's `HD`; `force` as for `Pod.home`."""
    pod = self._pod
    recipe = PLUNGER_RECIPES[pod.module_type]
    logger.info("%s HD%s", pod.component, "" if force else " (if not homed)")
    try:
      await Homer(pod, force=force).run(recipe)
    finally:
      pod._set_home_positions(Homer.axes(recipe))

  async def load_tips(self, load: TipLoad) -> int:
    """Press the head onto tips, the Master Controller's `LT`: see `driver/recipes/tip_load.py`, and
    `TipLoad.from_configuration` for the arguments Biomek would send. Returns the loaded Z, in
    counts.

    Raises:
      ValueError: for arguments the Master Controller refuses; nothing is sent.
    """
    return await load_tips(self._pod, load)

  # -- tips ---------------------------------------------------------------------------------------

  @property
  def tips(self) -> List[Optional[str]]:
    """Each mandrel's tip (its model, else its name), A1..H12 row by row; None for none. The head
    cannot sense tips: these are the ones it picked up, or that its shafts carried when the device
    restored the last session's state (`_adopt_tips`)."""
    out: List[Optional[str]] = []
    for row in range(ROWS):
      for column in range(COLUMNS):
        tip = self._tips.get(mandrel_name(row, column))
        out.append(None if tip is None else tip.model or tip.name)
    return out

  def _set_tips(self, mandrels: Mapping[str, Optional[Tip]]) -> None:
    """Record the tip each of `mandrels` carries (None: none), show it on the mandrel's shaft
    (`_show_tips`), and tell the driver (`on_tips_changed`: the device saves its heads' tips)."""
    for name, tip in mandrels.items():
      if tip is None:
        self._tips.pop(name, None)
      else:
        self._tips[name] = tip
    self._show_tips(list(mandrels))
    if self._pod.driver is not None:
      self._pod.driver.on_tips_changed()

  def _adopt_tips(self) -> None:
    """Take as carried the tips the head's shafts hold: the device puts them there from the last
    session's state."""
    if self.resource is None:
      return
    for row in range(ROWS):
      for column in range(COLUMNS):
        name = mandrel_name(row, column)
        tip = self.resource.get_item(name).tip
        if isinstance(tip, Tip):
          self._tips[name] = tip

  def _show_tips(self, mandrels: Optional[Sequence[str]] = None) -> None:
    """Put on each of `mandrels`' shafts (all, when None) in the head's model the tip it carries,
    and take off the shaft of one that carries none, as the STAR mounts and releases its tips."""
    head = self.resource
    if head is None:
      return
    names = mandrels
    if names is None:
      names = [mandrel_name(r, c) for r in range(ROWS) for c in range(COLUMNS)]
    for name in names:
      show_tip(head.get_item(name), self._carried_tip(name))

  def _carried_tip(self, mandrel: str) -> Optional[Tip]:
    """The tip on `mandrel`, or None."""
    return self._tips.get(mandrel)

  def _context(self) -> Tuple["BiomekI7Deck", "DeviceConfiguration", "PodConfiguration"]:
    driver, declared = self._pod.driver, self._pod.declared
    if driver is None or driver.deck is None or declared is None:
      raise RuntimeError(f"{self} needs a deck and a declared configuration (use `BiomekI7`)")
    return driver.deck, driver.declared, declared

  def _pairs(self, rack: TipRack, channel: str, tip_spot: str) -> Tuple[Tuple[str, TipSpot], ...]:
    """Each mandrel over one of `rack`'s spots, with mandrel `channel` over spot `tip_spot`."""
    cr, cc = mandrel_index(channel)
    sr, sc = mandrel_index(tip_spot)
    pairs = []
    for r in range(ROWS):
      for c in range(COLUMNS):
        rr, rc = r - cr + sr, c - cc + sc
        if 0 <= rr < rack.num_items_y and 0 <= rc < rack.num_items_x:
          pairs.append((mandrel_name(r, c), rack.get_item(mandrel_name(rr, rc))))
    return tuple(pairs)

  def _a1_over(
    self, rack: TipRack, channel: str, tip_spot: str, offset: Coordinate
  ) -> Tuple[float, float]:
    """Mandrel A1's X/Y (Biomek cm) with mandrel `channel` over spot `tip_spot`."""
    deck, configuration, declared = self._context()
    spot = targeting.target(
      deck, configuration, self._pod.pod_number, rack.get_item(tip_spot), offset=offset
    )
    cr, cc = mandrel_index(channel)
    geometry = declared.geometry
    return spot.x - cc * geometry.mandrel_x_spacing, spot.y - cr * geometry.mandrel_y_spacing

  def _seat(self, rack: TipRack) -> float:
    """The seat: the rack's top (the position's Z + its offset for the rack's class + the class's
    `Height`), as the logged tip loads put it."""
    deck, configuration, _ = self._context()
    return targeting.target(deck, configuration, self._pod.pod_number, rack, z="t").z

  def effective_tip_length(self, tip: Tip) -> float:
    """How far below the mandrels `tip`'s end is, mm: its length less the head's
    `TipSeatingDepth` (`_effective_tip_length`)."""
    return units.to_mm(self._effective_tip_length(tip))

  def _effective_tip_length(self, tip: Tip) -> float:
    """How far below the mandrels `tip`'s end is, cm: its length less the head's `TipSeatingDepth`
    (as the logged tip moves confirm)."""
    if self._pod.declared is None:
      raise RuntimeError(f"{self} needs a declared configuration")
    return float(tip.get_size_z() / 10 - self._pod.declared.settings["TipSeatingDepth"])

  def plan_pick_up(
    self,
    tip_rack: TipRack,
    offset: Coordinate = Coordinate.zero(),
    channel: str = "A1",
    tip_spot: str = "A1",
  ) -> Tuple[HeadPlan, TipLoad]:
    """Where a pickup goes, and the `LT` it sends. Only spots holding a tip (when tracked) count.

    Raises:
      ValueError: if no mandrel would meet a tip, or the rack is on a deck position that does not
        take a 96-head tip load (Biomek's "Multichannel Can Load Tips": only the tip-loading
        ALP's positions; elsewhere the press is not meant to land).
    """
    deck, _, declared = self._context()
    position = targeting.deck_position_of(deck, tip_rack)
    if isinstance(position, BiomekPosition) and not position.head96_tip_loading:
      raise ValueError(
        f"{self}: {position.name} is not a tip-loading position; the 96-head loads tips only on a "
        "tip-loading ALP (TipLoad1x1)"
      )
    pairs = tuple(
      (m, s) for m, s in self._pairs(tip_rack, channel, tip_spot) if not s.tracks_tips or s.tip
    )
    if not pairs:
      raise ValueError(f"{self}: no tips under the head on {tip_rack.name}")
    x, y = self._a1_over(tip_rack, channel, tip_spot, offset)
    seat = self._seat(tip_rack)
    plan = HeadPlan(x, y, seat, pairs)
    return plan, TipLoad.from_configuration(declared, seat, len(pairs))

  def plan_drop(
    self,
    tip_rack: TipRack,
    offset: Coordinate = Coordinate.zero(),
    channel: str = "A1",
    tip_spot: str = "A1",
  ) -> HeadPlan:
    """Where a drop into `tip_rack` goes: the seat + `TipUnloadZOffset`."""
    _, _, declared = self._context()
    x, y = self._a1_over(tip_rack, channel, tip_spot, offset)
    z = self._seat(tip_rack) + declared.settings["TipUnloadZOffset"]
    return HeadPlan(x, y, z, self._pairs(tip_rack, channel, tip_spot))

  def plan_discard(self, tip_length: float, trash: Optional[Resource] = None) -> HeadPlan:
    """Where a discard of tips `tip_length` cm long (effective: `_effective_tip_length`) goes:
    mandrel A1 at the trash position's `TipDisposal` X/Y from its back left corner, the tips' ends
    its Z above it, as the logged discards put it.

    Raises:
      ValueError: for a position that is not a trash (no `tip_disposal`).
    """
    deck, configuration, _ = self._context()
    position = trash if trash is not None else deck.get_position(DEFAULT_TRASH)
    disposal = getattr(position, "tip_disposal", None)
    if disposal is None:
      raise ValueError(f"{position.name} is not a trash position (no tip disposal offsets)")
    corner = targeting.target(
      deck, configuration, self._pod.pod_number, position, x="l", y="b", z="b"
    )
    z = corner.z + tip_length + disposal.z
    return HeadPlan(corner.x + disposal.x, corner.y + disposal.y, z)

  def _tip_length(self, mandrels: List[str]) -> float:
    """The longest carried tip's effective length, cm (`_carried_tip`)."""
    lengths = []
    for i, key in enumerate(self.tips):
      name = mandrel_name(i // COLUMNS, i % COLUMNS)
      if key is None or name not in mandrels:
        continue
      tip = self._carried_tip(name)
      if tip is not None:
        lengths.append(self._effective_tip_length(tip))
    if not lengths:
      raise RuntimeError(f"{self}: the carried tips' length is not known")
    return max(lengths)

  def _carried_tip_lengths(self) -> Dict[str, float]:
    """`{"Z": cm}`: how far below the mandrels the longest carried tip ends; empty without tips.

    Raises:
      RuntimeError: for carried tips whose length is not known.
    """
    carried = [
      mandrel_name(i // COLUMNS, i % COLUMNS) for i, t in enumerate(self.tips) if t is not None
    ]
    return {"Z": self._tip_length(carried)} if carried else {}

  async def pick_up_tips(
    self,
    tip_rack: TipRack,
    offset: Optional[Coordinate] = None,
    *,
    minimum_traverse_height_start: Optional[float] = None,
    minimum_traverse_height_end: Optional[float] = None,
    channel: str = "A1",
    tip_spot: str = "A1",
  ) -> None:
    """Pick up tips from `tip_rack` with mandrel `channel` over spot `tip_spot` (STAR's `Head96`
    signature). Every mandrel over a spot holding a tip takes it. Heights are mm above the deck of
    the head's lowest point (the tips' ends when it carries tips, else the mandrels), as STAR's;
    None travels and leaves the head as high as it goes.

    Raises:
      RuntimeError: if the head already carries tips, or there is no deck.
      ValueError: if no mandrel would meet a tip.
    """
    if any(t is not None for t in self.tips):
      raise RuntimeError(f"{self} already carries tips; drop or discard them first")
    plan, load = self.plan_pick_up(tip_rack, offset or Coordinate.zero(), channel, tip_spot)
    _, _, declared = self._context()
    pod = self._pod
    logger.info("%s pick up %d tips from %s", pod.component, len(plan.pairs), tip_rack.name)
    await self._clear_gripper()
    await pod._move_over(
      {"X": declared.to_counts("X", plan.x), "Y": declared.to_counts("Y", plan.y)},
      minimum_traverse_height=_cm(minimum_traverse_height_start),
    )
    await pod.move_axes({"Z": declared.to_counts("Z", plan.z)}, 1000)
    await self.load_tips(load)
    picked: Dict[str, Optional[Tip]] = {}
    for mandrel, spot in plan.pairs:
      tip = spot.tip_for_pickup()
      if spot.tracks_tips:
        spot.unassign_tip()
      self._tips[mandrel] = tip
      picked[mandrel] = tip
    self._set_tips(picked)
    await pod._lift_channels(_cm(minimum_traverse_height_end))

  async def drop_tips(
    self,
    resource: TipRack,
    offset: Optional[Coordinate] = None,
    *,
    minimum_traverse_height_start: Optional[float] = None,
    minimum_traverse_height_end: Optional[float] = None,
    channel: str = "A1",
    tip_spot: str = "A1",
  ) -> None:
    """Put the head's tips into `resource`, mandrel `channel` over spot `tip_spot`, and eject them.
    Heights as `pick_up_tips`.

    Raises:
      RuntimeError: if the head carries no tips, or there is no deck.
      ValueError: if a mandrel with a tip would meet an occupied spot, or none would meet a spot.
    """
    carried = self._carried()
    plan = self.plan_drop(resource, offset or Coordinate.zero(), channel, tip_spot)
    targets = [(m, s) for m, s in plan.pairs if m in carried]
    if not targets:
      raise ValueError(f"{self}: no mandrel with a tip is over {resource.name}")
    occupied = [s.name for _, s in targets if s.tracks_tips and s.tip is not None]
    if occupied:
      raise ValueError(f"{self}: tip spots {occupied} already hold tips")
    await self._eject_at(plan, minimum_traverse_height_start)
    for mandrel, spot in targets:
      tip = self._tips.pop(mandrel, None)
      if spot.tracks_tips:
        spot.assign_tip(tip if tip is not None else spot.make_tip())
    self._set_tips({m: None for m in carried})
    await self._pod._lift_channels(_cm(minimum_traverse_height_end))

  async def discard_tips(
    self,
    *,
    trash: Optional[Resource] = None,
    minimum_traverse_height_start: Optional[float] = None,
    minimum_traverse_height_end: Optional[float] = None,
  ) -> None:
    """Eject the head's tips into the trash (`TR1` by default). Heights as `pick_up_tips`.

    Raises:
      RuntimeError: if the head carries no tips, or there is no deck.
    """
    carried = self._carried()
    plan = self.plan_discard(self._tip_length(carried), trash)
    await self._eject_at(plan, minimum_traverse_height_start)
    for mandrel in carried:
      self._tips.pop(mandrel, None)
    self._set_tips({m: None for m in carried})
    await self._pod._lift_channels(_cm(minimum_traverse_height_end))

  async def discard_any_tips(self, trash: Optional[Resource] = None) -> None:
    """Eject into the trash (`TR1` by default) whatever tips the head may carry, recorded or not:
    the head has no tip sensor, so a lost record would otherwise leave tips on it unseen.
    With tips recorded, as `discard_tips`; with none, the head travels and ejects as if it carried
    the longest tip it takes (1070 uL, `ASSUMED_TIP`), so its travel clears the deck for any tip
    and an ejected tip of any length falls into the bin. With no tips on, the eject runs over the
    empty bin. `BiomekI7.setup` does this by default.

    Raises:
      RailCollisionError, ValueError: if the way to the trash is not clear for the longest tip
        (the move is refused before anything is sent).
    """
    if any(t is not None for t in self.tips):
      await self.discard_tips(trash=trash)
      return
    length = self._effective_tip_length(ASSUMED_TIP("assumed tip"))
    logger.warning(
      "%s: discarding any tips on the 96-head (it cannot sense them), as if %.2f cm long",
      self._pod.component,
      length,
    )
    plan = self.plan_discard(length, trash)
    await self._eject_at(plan, None, tip_length=length)
    await self._pod._lift_channels(None, tip_lengths={"Z": length})

  def _carried(self) -> List[str]:
    carried = [
      mandrel_name(i // COLUMNS, i % COLUMNS) for i, t in enumerate(self.tips) if t is not None
    ]
    if not carried:
      raise RuntimeError(f"{self} carries no tips")
    return carried

  async def _eject_at(
    self, plan: HeadPlan, start: Optional[float], tip_length: Optional[float] = None
  ) -> None:
    """Travel over `plan` and eject there. `tip_length`: the tip the travel clears the deck for,
    cm; the recorded tips' when None."""
    _, _, declared = self._context()
    pod = self._pod
    logger.info("%s eject tips at X %.3f Y %.3f Z %.3f cm", pod.component, plan.x, plan.y, plan.z)
    await self._clear_gripper()
    await pod._move_over(
      {"X": declared.to_counts("X", plan.x), "Y": declared.to_counts("Y", plan.y)},
      minimum_traverse_height=_cm(start),
      tip_lengths=None if tip_length is None else {"Z": tip_length},
    )
    await pod.move_axes({"Z": declared.to_counts("Z", plan.z)}, 1000)
    await self.eject()

  async def eject(self) -> None:
    """Push the tips off the mandrels where the head is: the plunger to the bottom of its travel
    (which drives the ejector) while Z rises by the plunger's stroke, so the tips stay where they
    are as the mandrels leave them; then the plunger to 0, the bottom and 0 again; all at
    `TipUnloadShuckSpeed`."""
    _, _, declared = self._context()
    pod = self._pod
    bottom = declared.limits["D"][0]  # uL, below 0: the ejector stroke
    stroke = -bottom / declared.settings["DAxisULPerCM"]  # cm the plunger travels
    speed = percent_to_field(declared.settings["TipUnloadShuckSpeed"])
    z = declared.to_value("Z", pod.cpositions["Z"][1])
    d_bottom, d_zero = declared.to_counts("D", bottom), declared.to_counts("D", 0.0)
    await pod.move_axes({"Z": declared.to_counts("Z", z + stroke), "D": d_bottom}, speed)
    for target in (d_zero, d_bottom, d_zero):
      await pod.move_axes({"D": target}, speed)

  async def _clear_gripper(self) -> None:
    """Turn the gripper to its park angle (GR), as Biomek does before the head works the deck.

    Raises:
      RuntimeError: while the gripper holds labware (turning would swing it).
    """
    if self._pod._held_labware is not None:
      raise RuntimeError(f"{self}: the gripper holds labware; place it before the head works")
    _, _, declared = self._context()
    gr = declared.to_counts("GR", declared.park["GR"])
    if self._pod.cpositions["GR"][1] != gr:
      await self._pod.move_axes({"GR": gr})

  # -- aspirate and dispense ----------------------------------------------------------------------

  def _tip_on(self, mandrel: str) -> Tip:
    tip = self._carried_tip(mandrel)
    if tip is None:
      row, column = mandrel_index(mandrel)
      key = self.tips[row * COLUMNS + column]
      raise RuntimeError(f"{self}: the tip on {mandrel} ({key or 'none'}) is not known")
    return tip

  def _wells(self, resource: Resource) -> Tuple[Tuple[str, Container], ...]:
    """Each carrying mandrel and the container under it: the wells of a 96-well plate (mandrel A1
    over well A1), or one container (a reservoir's, or a 1-well plate's) under every mandrel."""
    carried = self._carried()
    if isinstance(resource, Plate) and resource.num_items == ROWS * COLUMNS:
      return tuple((m, resource.get_well(m)) for m in carried)
    container: Optional[Resource] = resource
    if isinstance(resource, Plate) and resource.num_items == 1:
      container = resource.get_item(0)
    if not isinstance(container, Container):
      raise ValueError(
        f"{self}: aspirate or dispense from a 96-well plate or one container, not {resource.name}"
      )
    return tuple((m, container) for m in carried)

  def plan_liquid(
    self,
    operation: str,
    resource: Resource,
    volume: float,
    settings: liquid.Settings,
    flow_rate: float,
  ) -> LiquidPlan:
    """Where an aspirate (`operation` "aspirate") or dispense goes and the template's moves: mandrel
    A1 over well A1 of a 96-well plate, or the head centred over one container; the liquid's levels
    from the containers' trackers when volume tracking is on (else not known).

    Raises:
      ValueError: for a height that cannot be worked out (from the liquid, or following, in a
        container without a height <-> volume function or with untracked contents).
    """
    deck, configuration, declared = self._context()
    number = self._pod.pod_number
    pairs = self._wells(resource)
    tracking = does_volume_tracking()
    share = len(pairs) if len({id(c) for _, c in pairs}) == 1 else 1
    channels = []
    for mandrel, container in pairs:
      shape = liquid.ContainerShape(container)
      tip = self._tip_on(mandrel)
      channels.append(
        liquid.Channel(
          name=mandrel,
          well=shape,
          bottom=targeting.target(deck, configuration, number, container, z="b").z
          + shape.floor / 10,
          tip_length=self._effective_tip_length(tip),
          volume=volume,
          held=container.tracker.get_used_volume() if tracking else None,
          tip_radius=tip.get_size_x() / 20,  # its diameter, mm -> its radius, cm
          share=share,
        )
      )
    geometry = declared.geometry
    if share > 1:  # one container under the head: the head centred over it
      centre = targeting.target(deck, configuration, number, pairs[0][1])
      x = centre.x - (COLUMNS - 1) / 2 * geometry.mandrel_x_spacing
      y = centre.y - (ROWS - 1) / 2 * geometry.mandrel_y_spacing
    else:
      at = targeting.target(deck, configuration, number, pairs[0][1])
      row, column = mandrel_index(pairs[0][0])
      x = at.x - column * geometry.mandrel_x_spacing
      y = at.y - row * geometry.mandrel_y_spacing
    plan = (liquid.plan_aspirate if operation == "aspirate" else liquid.plan_dispense)(
      channels, settings, flow_rate
    )
    return LiquidPlan(x, y, tuple(channels), tuple(plan), tuple(pairs))

  def moves(
    self, plan: LiquidPlan, offset: Coordinate, d_start: float
  ) -> List[Tuple[Dict[str, float], int, float]]:
    """The plan as the head's moves after its travel to the well: (axis targets in Biomek's units:
    X/Y/Z cm, D uL; speed field; delay s after). The approach carries `offset` (mm, PyLabRobot's
    frame) as the reservoir templates' first move does; the plunger starts at `d_start` uL. One Z
    serves every mandrel: the lowest any of them needs.

    Raises:
      ValueError: for a dispense of more than the tips hold.
    """
    x, y = offset_to_biomek(plan.x, plan.y, offset)
    d = d_start
    out: List[Tuple[Dict[str, float], int, float]] = []
    first = True
    for step in plan.steps:
      if isinstance(step, liquid.ZMove):
        targets: Dict[str, float] = {"Z": min(step.z.values())}
        if first and (offset.x or offset.y):
          targets.update(X=x, Y=y)
        first = False
        out.append((targets, step.speed, 0.0))
      elif isinstance(step, liquid.PlungerMove):
        amount = next(iter(step.volume.values()))
        before, d = d, 0.0 if step.empty else d + amount
        if d < -PLUNGER_TOLERANCE:
          raise ValueError(f"{self}: a dispense of {-amount:.3f} uL is more than the tips hold")
        d = max(d, 0.0)
        if abs(d - before) <= PLUNGER_TOLERANCE:
          continue  # nothing to move (a 0 uL operation): Biomek sends no move
        targets = {"D": round(d, 6)}
        if step.z:
          targets["Z"] = min(step.z.values())
        out.append((targets, self._d_speed(step.flow_rate), step.delay))
      elif step.dx or step.dy:
        out.append(({"X": x + step.dx, "Y": y + step.dy}, step.speed, 0.0))
        out.append(({"X": x, "Y": y}, step.speed, step.delay))
    return out

  def _d_speed(self, flow_rate: float) -> int:
    """The `AM` speed field for the plunger at `flow_rate` uL/s: per mille of D's maximum."""
    return rate_to_field(self._pod, "D", flow_rate, clamp=True)

  def _settings(self, **given: Any) -> liquid.Settings:
    """The template's settings: the arguments given, else this feature's `default_*`."""
    z_speed = given.pop("z_speed", None)
    rate = self.default_z_speed if z_speed is None else z_speed
    base = liquid.Settings(
      liquid_height=self.default_liquid_height,
      z_speed_field=units.rate_to_field(self._pod, "Z", rate),
    )
    return liquid.settings(base, **given)

  async def _run_liquid(
    self,
    plan: LiquidPlan,
    offset: Coordinate,
    minimum_traverse_height_start: Optional[float],
    minimum_traverse_height_end: Optional[float],
  ) -> None:
    _, _, declared = self._context()
    pod = self._pod
    d_start = declared.to_value("D", pod.cpositions["D"][1])
    # Below 0 the plunger is in the ejector's stroke (after homing or a tip load's push-off):
    # Biomek brings it to 0 at full speed before pipetting.
    below = d_start < -PLUNGER_TOLERANCE
    moves = self.moves(plan, offset, 0.0 if below else d_start)  # checked before anything moves
    if below:
      await pod.move_axes({"D": declared.to_counts("D", 0.0)})
    await self._clear_gripper()
    await pod._move_over(
      {"X": declared.to_counts("X", plan.x), "Y": declared.to_counts("Y", plan.y)},
      minimum_traverse_height=_cm(minimum_traverse_height_start),
    )
    for targets, speed, delay in moves:
      await pod.move_axes({a: declared.to_counts(a, v) for a, v in targets.items()}, speed)
      if delay > 0:
        await asyncio.sleep(delay)
    await pod._lift_channels(_cm(minimum_traverse_height_end))

  def _book(self, plan: LiquidPlan, volume: float, aspirate: bool) -> List[Any]:
    """Move `volume` per mandrel between the containers' and the tips' trackers (pending, to commit
    or roll back)."""
    if not does_volume_tracking():
      return []
    booked = []
    for mandrel, container in plan.pairs:
      tip = self._tips.get(mandrel)
      giver: Optional[VolumeTracker] = container.tracker
      taker: Optional[VolumeTracker] = tip.tracker if tip is not None else None
      if not aspirate:
        giver, taker = taker, giver
      if giver is not None:
        booked.append(giver)
        giver.remove_liquid(min(volume, giver.get_used_volume()))
      if taker is not None:
        booked.append(taker)
        taker.add_liquid(volume)
    return booked

  async def _liquid(
    self,
    operation: str,
    resource: Resource,
    volume: float,
    offset: Optional[Coordinate],
    flow_rate: float,
    settings: liquid.Settings,
    start: Optional[float],
    end: Optional[float],
  ) -> None:
    offset = offset or Coordinate.zero()
    plan = self.plan_liquid(operation, resource, volume, settings, flow_rate)
    aspirate = operation == "aspirate"
    moved = volume + (settings.conditioning.excess if aspirate else 0.0)
    if settings.mix is not None:
      liquid.check_tips_empty_for_mix(
        self, [self._tips.get(m) for m, _ in plan.pairs],
        [0.0 if aspirate else volume] * len(plan.pairs), settings,
      )  # fmt: skip
    logger.info(
      "%s %s %.3f uL %s %s", self._pod.component, operation, volume,
      "from" if aspirate else "into", resource.name,
    )  # fmt: skip
    booked = self._book(plan, moved, aspirate)
    try:
      await self._run_liquid(plan, offset, start, end)
    except BaseException:
      for tracker in booked:
        tracker.rollback()
      raise
    for tracker in booked:
      tracker.commit()

  async def aspirate(
    self,
    resource: Resource,
    volume: float,
    offset: Optional[Coordinate] = None,
    liquid_height: Optional[float] = None,
    lld_mode: Optional[Any] = None,
    flow_rate: Optional[float] = None,
    *,
    height_from: Optional[HeightFrom] = None,
    follow_liquid: Optional[bool] = None,
    minimum_height: Optional[float] = None,
    z_speed: Optional[float] = None,
    approach_height: Optional[float] = None,
    settling_time: Optional[float] = None,
    blow_out_air_volume: Optional[float] = None,
    pre_mix: Optional[Mix] = None,
    mix_heights: Optional[MixHeights] = None,
    transport_air_volume: Optional[float] = None,
    conditioning: Optional[Conditioning] = None,
    tip_touch: Optional[TipTouch] = None,
    minimum_traverse_height_start: Optional[float] = None,
    minimum_traverse_height_end: Optional[float] = None,
  ) -> None:
    """Draw `volume` uL into every carried tip, from a 96-well plate (mandrel A1 over well A1) or
    one container (the head centred over it), as Biomek's "MC Pipetting" template does (see
    `driver/liquid.py`).

    Args:
      resource: a plate of 96 wells, or a container (a reservoir's well, a 1-well plate).
      volume: uL per tip.
      offset: added to the target, mm (a reservoir template's X offset is (-2, 0, 0)).
      liquid_height: mm from `height_from` of the tips' ends while drawing
        (`default_liquid_height`).
      lld_mode: the head has no liquid-level sensor: None or OFF only.
      flow_rate: uL/s (`default_flow_rate`).
      height_from: "bottom" (default), "top" or "liquid" (needs volume tracking).
      follow_liquid: the tips follow the liquid down as it is drawn.
      minimum_height: mm above the bottom no height goes below (Biomek's Minimum Height).
      z_speed: mm/s of Z for the moves in the well (`default_z_speed`); above Z's maximum is
        refused.
      approach_height: mm above the top the head comes down to first (1).
      settling_time: s after drawing (Biomek's aspirate delay).
      blow_out_air_volume: uL of air drawn above the well first, for a blowout.
      pre_mix: mixed before drawing (Biomek's Mix): `repetitions` times, draw and push back
        `volume` uL at `flow_rate` uL/s, at `z_speed`; `auto_surface_following` follows the liquid
        while drawing and pushing (Biomek's Follow Liquid). The tips must hold no liquid.
      mix_heights: where the mix draws and pushes; the operation's own height when None.
      transport_air_volume: uL of air drawn after leaving the well (the trailing air gap).
      conditioning: extra drawn and pushed back before leaving (Biomek's conditioning).
      tip_touch: a touch-off before leaving.
      minimum_traverse_height_start / _end: as `pick_up_tips`.

    Raises:
      NotImplementedError: for a liquid-level sensing mode.
      RuntimeError: if the head carries no tips.
      ValueError: for a resource or height that cannot be worked out, or more than the tips hold.
    """
    _no_lld(self, lld_mode)
    settings = self._settings(
      liquid_height=liquid_height, height_from=height_from, follow_liquid=follow_liquid,
      minimum_height=minimum_height, z_speed=z_speed, approach_height=approach_height,
      settling_time=settling_time, blow_out_air_volume=blow_out_air_volume,
      transport_air_volume=transport_air_volume, conditioning=conditioning, tip_touch=tip_touch,
    )  # fmt: skip
    settings = liquid.with_mix(settings, pre_mix, mix_heights)
    await self._liquid(
      "aspirate", resource, volume, offset, flow_rate or self.default_flow_rate, settings,
      minimum_traverse_height_start, minimum_traverse_height_end,
    )  # fmt: skip

  async def dispense(
    self,
    resource: Resource,
    volume: float,
    offset: Optional[Coordinate] = None,
    liquid_height: Optional[float] = None,
    lld_mode: Optional[Any] = None,
    flow_rate: Optional[float] = None,
    *,
    height_from: Optional[HeightFrom] = None,
    follow_liquid: Optional[bool] = None,
    minimum_height: Optional[float] = None,
    z_speed: Optional[float] = None,
    approach_height: Optional[float] = None,
    settling_time: Optional[float] = None,
    post_mix: Optional[Mix] = None,
    mix_heights: Optional[MixHeights] = None,
    blow_out_air_volume: Optional[float] = None,
    blow_out_delay: Optional[float] = None,
    tip_touch: Optional[TipTouch] = None,
    empty: bool = False,
    minimum_traverse_height_start: Optional[float] = None,
    minimum_traverse_height_end: Optional[float] = None,
  ) -> None:
    """Push `volume` uL out of every carried tip, into a 96-well plate or one container, as
    Biomek's "MC Pipetting" template does (see `driver/liquid.py`); arguments as `aspirate`.

    Args:
      follow_liquid: the tips follow the liquid up as it fills.
      post_mix, mix_heights: mixed after dispensing, as `aspirate`'s `pre_mix`; the tips must be
        left holding no liquid (Biomek: "The mix operation cannot begin until tips are empty").
      blow_out_air_volume: uL of air pushed out after the liquid (Biomek's blowout), then
        `blow_out_delay` s.
      empty: push out everything the tips hold, at the starting height (Biomek's Empty Tips).

    Raises:
      NotImplementedError: for a liquid-level sensing mode.
      RuntimeError: if the head carries no tips.
      ValueError: for a resource or height that cannot be worked out, or more than the tips hold.
    """
    _no_lld(self, lld_mode)
    settings = self._settings(
      liquid_height=liquid_height, height_from=height_from, follow_liquid=follow_liquid,
      minimum_height=minimum_height, z_speed=z_speed, approach_height=approach_height,
      settling_time=settling_time, blow_out_air_volume=blow_out_air_volume,
      blow_out_delay=blow_out_delay, tip_touch=tip_touch, empty=empty,
    )  # fmt: skip
    settings = liquid.with_mix(settings, post_mix, mix_heights)
    await self._liquid(
      "dispense", resource, volume, offset, flow_rate or self.default_flow_rate, settings,
      minimum_traverse_height_start, minimum_traverse_height_end,
    )  # fmt: skip


def _no_lld(head: Head96, lld_mode: Any) -> None:
  if lld_mode is not None and getattr(lld_mode, "name", lld_mode) != "OFF":
    raise NotImplementedError(f"{head}: the 96-channel head has no liquid-level sensing")
