"""The grounded OT-3 (Flex) operating envelope — one source of truth for reach.

Every reachability cap the Flex native checks use is a *derived property* of the
primitive OT-3 values below, each cited to its source in the installed ``opentrons``
/ ``opentrons_shared_data`` package. Deriving the caps (rather than hardcoding e.g.
324.38) makes the source-to-cap relationship a rule in code that cannot silently
drift when a primitive changes -- exactly the drift the ``padding_rear`` value saw
between opentrons 8.3.0 (-177.42) and 8.8.1 (-169.42).
"""

from dataclasses import dataclass
from typing import TYPE_CHECKING, List, Tuple

from pylabrobot.resources import Coordinate

if TYPE_CHECKING:
  from pylabrobot.resources.opentrons.flex_deck import FlexDeck

# 8-channel single-config active-nozzle Y offsets (9 mm pitch, A1 back .. H1 front),
# relative to the mount reference. pipette/.../eight_channel/p1000/3_5.json.
FLEX_8CH_NOZZLE_A1_Y = -16.0
FLEX_8CH_NOZZLE_H1_Y = -79.0
FLEX_8CH_NOZZLE_PITCH_Y = -9.0  # 9 mm pitch between adjacent nozzles (channel 0=A .. 7=H)


def nozzle_offset_y(channel: int) -> float:
  """Y offset (from the mount reference) of an 8-channel nozzle by channel index.

  Channel 0 = A1 (rearmost, −16), channel 7 = H1 (frontmost, −79); linear at the 9 mm
  pitch. The frontmost active nozzle (largest ``|offset|``) drives the head casing
  furthest rearward, so this is what binds the rear reach cap for a single/partial move.
  """
  return FLEX_8CH_NOZZLE_A1_Y + FLEX_8CH_NOZZLE_PITCH_Y * channel


@dataclass(frozen=True)
class FlexEnvelope:
  """The OT-3 operating envelope as primitive values + derived caps.

  Frozen so an instance is a value object; use ``dataclasses.replace`` to explore a
  perturbed envelope (the grounding test does this).
  """

  # --- primitives (grounded; do not pre-compute anything here) ---
  deck_extent_x: float = 477.2  # robot/definitions/1/ot3.json extents[0]
  deck_extent_y: float = 493.8  # ot3.json extents[1] (rear/home limit)
  # PIPETTE mounts only: the extension mount's reference sits 161.8 mm lower
  # (gripper offset z 93.85 vs 255.675), so never bound a gripper move by this.
  z_max: float = 300.0  # ot3controller.py axis_bounds Z_L/Z_R
  carriage_offset_x: float = 477.20  # defaults_ot3.py:72 DEFAULT_CARRIAGE_OFFSET
  carriage_offset_y: float = 493.8
  carriage_offset_z: float = 253.475
  padding_rear: float = -169.42  # ot3.json paddingOffsets.rear @ 8.8.1 (8.3.0 had -177.42)
  padding_front: float = 51.8  # paddingOffsets.front
  padding_left: float = 31.88  # paddingOffsets.leftSide
  padding_right: float = -80.32  # paddingOffsets.rightSide
  casing_depth_y: float = 95.0  # 8-channel head casing depth (mount ref .. front corner)

  # --- derived caps (never literals) ---
  @property
  def rear_cap_y(self) -> float:
    """8-channel rear reach cap: deck extent plus the (negative) rear padding = 324.38."""
    return self.deck_extent_y + self.padding_rear

  @property
  def front_cap_y(self) -> float:
    """8-channel front reach cap = front padding = 51.8."""
    return self.padding_front

  @property
  def home_xy(self) -> Tuple[float, float]:
    """Home carriage reference in the deck frame (rear-right corner) = (477.2, 493.8)."""
    return (self.carriage_offset_x, self.carriage_offset_y)

  def reach_y(self, active_nozzle_offset_y: float) -> Tuple[float, float]:
    """Per-config nozzle-Y reach for an 8-channel single/partial move: ``(min_y, max_y)``.

    The 95 mm casing binds the reach: the casing front corner (mount_ref − casing_depth)
    must stay <= rear_cap_y, and the casing back corner (mount_ref) must stay >= front_cap_y,
    where mount_ref = nozzle_y − active_nozzle_offset_y. Solving for nozzle_y gives A1 (−16)
    -> max 403.38, H1 (−79) -> max 340.38.
    """
    max_y = self.rear_cap_y + self.casing_depth_y + active_nozzle_offset_y
    min_y = self.front_cap_y + active_nozzle_offset_y
    return (min_y, max_y)

  def check_bounds(self, coordinate: Coordinate) -> None:
    """Refuse a target outside the coarse addressable deck envelope (per-axis)."""
    if not 0.0 <= coordinate.x <= self.deck_extent_x:
      raise ValueError(f"target x {coordinate.x:.1f} outside Flex reach [0, {self.deck_extent_x}]")
    if not 0.0 <= coordinate.y <= self.deck_extent_y:
      raise ValueError(f"target y {coordinate.y:.1f} outside Flex reach [0, {self.deck_extent_y}]")
    if not 0.0 <= coordinate.z <= self.z_max:
      raise ValueError(f"target z {coordinate.z:.1f} outside Flex Z [0, {self.z_max}]")

  def check_eight_channel(
    self,
    nozzle_target: Coordinate,
    partial: bool,
    active_nozzle_offset_y: float = FLEX_8CH_NOZZLE_A1_Y,
  ) -> None:
    """Refuse an 8-channel single/partial move whose 95 mm casing exceeds the reach caps.

    Full column (``partial=False``) is not extent-checked (mirrors Opentrons'
    ``_is_within_pipette_extents`` 8-ch branch, which only bounds single/partial configs).
    """
    if not partial:
      return
    mount_ref_y = nozzle_target.y - active_nozzle_offset_y
    casing_front_y = mount_ref_y - self.casing_depth_y
    casing_back_y = mount_ref_y
    if casing_front_y > self.rear_cap_y:
      _, reach = self.reach_y(active_nozzle_offset_y)
      raise ValueError(
        f"8-channel single/partial move: nozzle Y {nozzle_target.y:.1f} drives the casing to "
        f"Y {casing_front_y:.1f} > rear cap {self.rear_cap_y:.2f} — would hit the back panel "
        f"(rear reach for this nozzle is Y <= {reach:.2f})"
      )
    if casing_back_y < self.front_cap_y:
      raise ValueError(
        f"8-channel single/partial move: nozzle Y {nozzle_target.y:.1f} puts the casing at "
        f"Y {casing_back_y:.1f} < front cap {self.front_cap_y:.2f}"
      )

  def check_point(
    self,
    coordinate: Coordinate,
    channels: int,
    n_active: int,
    active_nozzle_offset_y: float = FLEX_8CH_NOZZLE_A1_Y,
  ) -> None:
    """The single check surface: coarse bounds, plus the 8-ch casing caps when partial."""
    self.check_bounds(coordinate)
    if channels == 8 and n_active < 8:
      self.check_eight_channel(
        coordinate, partial=True, active_nozzle_offset_y=active_nozzle_offset_y
      )

  def unreachable_slots(
    self,
    deck: "FlexDeck",
    channels: int,
    n_active: int,
    active_nozzle_offset_y: float = FLEX_8CH_NOZZLE_A1_Y,
  ) -> List[str]:
    """The standard slots (A1–D3) whose center this pipette config cannot reach.

    Combines the deck-grid geometry (slot centers) with the per-config reach caps: a
    slot is unreachable when :meth:`check_point` refuses its center. Staging slots
    (A4–D4) are excluded — they are gripper-only storage, not pipette targets. This is
    the executable form of "a labware assigned to a rear slot is not necessarily
    reachable by this pipette config."
    """
    from pylabrobot.resources.opentrons.flex_deck import (
      SLOT_DEPTH,
      SLOT_LOCATIONS,
      SLOT_WIDTH,
    )

    out: List[str] = []
    for slot in sorted(SLOT_LOCATIONS):
      loc = deck.get_slot_location(slot)
      center = Coordinate(loc["x"] + SLOT_WIDTH / 2, loc["y"] + SLOT_DEPTH / 2, loc["z"])
      try:
        self.check_point(center, channels, n_active, active_nozzle_offset_y)
      except ValueError:
        out.append(slot)
    return out


FLEX_ENVELOPE = FlexEnvelope()
