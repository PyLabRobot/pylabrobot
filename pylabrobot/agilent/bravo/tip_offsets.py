"""Per-(head, tip box) Tips On / Tips Off geometry overrides.

The Tips On press depth/tolerance and the Tips Off eject depth (Z) and
ejector throw (W) depend on the physical combination of head type and tip
box: a long LT200 tip needs to eject much further above the box than a short
ST10 tip, and a deeper-engaging tip trips the Tips On force-press accept
window if it is sized for a shorter tip.

These overrides are resolved at Tips On / Tips Off time by matching the
active head type and the tip box labware (by name, case/whitespace-
insensitive, or by ``tipbox_id``). Any field left unset on a matching entry
-- or the absence of any matching entry -- falls back to the caller-supplied
defaults (typically a profile's own Tips On/Off settings) and the global
:data:`~pylabrobot.agilent.bravo.types.TIPBOX_JOG_TOLERANCE` for the press
window.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional, Union

from .types import TIPBOX_JOG_TOLERANCE, HeadType


@dataclass(frozen=True)
class TipOffsetEntry:
  """One (head, tip box) override row.

  Every numeric field is optional; ``None`` means "fall back to the caller's
  default for this field". Matching requires ``head_type`` plus at least one
  of ``tipbox`` (labware name) or ``tipbox_id``.

  Attributes:
    head_type: The head type this row applies to.
    tipbox: The tip box labware name to match, case/whitespace-insensitive.
    tipbox_id: The tip box labware type id to match.
    tips_off_z_offset: Millimetres the head ejects above the seated press
      depth during Tips Off.
    tips_off_w_position: The W (plunger) target during Tips Off, before
      returning W to 0.
    tips_on_jog_tolerance: Millimetres of accept window for the Tips On
      force press.
    tips_on_z_offset: Millimetres added to the Tips On press target
      (positive presses deeper).
  """

  head_type: HeadType
  tipbox: str = ""
  tipbox_id: str = ""
  tips_off_z_offset: Optional[float] = None
  tips_off_w_position: Optional[float] = None
  tips_on_jog_tolerance: Optional[float] = None
  tips_on_z_offset: Optional[float] = None


@dataclass(frozen=True)
class ResolvedTipOffsets:
  """Fully resolved offsets, with every field filled (override or default).

  Attributes:
    tips_off_z_offset: Millimetres the head ejects above the seated press
      depth during Tips Off.
    tips_off_w_position: The W (plunger) target during Tips Off.
    tips_on_jog_tolerance: Millimetres of accept window for the Tips On
      force press.
    tips_on_z_offset: Millimetres added to the Tips On press target.
    matched: Whether an override row was found.
    source: A human-readable description of where the values came from.
  """

  tips_off_z_offset: float
  tips_off_w_position: float
  tips_on_jog_tolerance: float
  tips_on_z_offset: float
  matched: bool
  source: str


def _normalize_key(value: Any) -> str:
  """Collapse whitespace and case for a name/id comparison key.

  Args:
    value: The value to normalize; falsy values normalize to ``""``.

  Returns:
    The value's whitespace-collapsed, lowercased string form.
  """
  return " ".join(str(value or "").split()).strip().lower()


def _normalize_head(value: Any) -> str:
  """Normalize a head type value for comparison.

  Args:
    value: A head type string, in any case.

  Returns:
    The lowercase, stripped string form.
  """
  return str(value if value is not None else "").strip().lower()


class TipOffsetTable:
  """In-memory collection of :class:`TipOffsetEntry` rows with lookup."""

  def __init__(self, entries: list[TipOffsetEntry]) -> None:
    """Initialize the table.

    Args:
      entries: The override rows the table serves, in match priority order.
    """
    self._entries = list(entries)

  @property
  def entries(self) -> list[TipOffsetEntry]:
    """Return the table's rows, in match priority order."""
    return list(self._entries)

  def find(
    self,
    head_type: Union[HeadType, str],
    *,
    tipbox_name: str = "",
    tipbox_id: str = "",
  ) -> Optional[TipOffsetEntry]:
    """Return the first entry matching *head_type* and the tip box.

    A row matches when its head type matches AND either its tip box id or
    its tip box name matches the supplied values. Id match is preferred but
    a name match is equally accepted (entries are scanned in file order).

    Args:
      head_type: The active head type.
      tipbox_name: The tip box labware's name.
      tipbox_id: The tip box labware's type id.

    Returns:
      The first matching entry, or ``None``.
    """
    head = _normalize_head(head_type)
    if not head:
      return None
    name_key = _normalize_key(tipbox_name)
    id_key = _normalize_key(tipbox_id)
    for entry in self._entries:
      if _normalize_head(entry.head_type) != head:
        continue
      entry_id = _normalize_key(entry.tipbox_id)
      entry_name = _normalize_key(entry.tipbox)
      if entry_id and id_key and entry_id == id_key:
        return entry
      if entry_name and name_key and entry_name == name_key:
        return entry
    return None

  def resolve(
    self,
    head_type: Union[HeadType, str],
    *,
    tipbox_name: str = "",
    tipbox_id: str = "",
    default_z_offset: float,
    default_w_position: float,
    default_jog_tolerance: float = TIPBOX_JOG_TOLERANCE,
    default_z_on_offset: float = 0.0,
  ) -> ResolvedTipOffsets:
    """Resolve offsets for a (head, tip box), filling gaps with defaults.

    Args:
      head_type: The active head type.
      tipbox_name: The tip box labware's name.
      tipbox_id: The tip box labware's type id.
      default_z_offset: Fallback for ``tips_off_z_offset``.
      default_w_position: Fallback for ``tips_off_w_position``.
      default_jog_tolerance: Fallback for ``tips_on_jog_tolerance``.
      default_z_on_offset: Fallback for ``tips_on_z_offset``.

    Returns:
      The resolved offsets, with ``matched`` set to whether an override row
      was found and ``source`` describing where the values came from.
    """
    entry = self.find(head_type, tipbox_name=tipbox_name, tipbox_id=tipbox_id)
    if entry is None:
      return ResolvedTipOffsets(
        tips_off_z_offset=float(default_z_offset),
        tips_off_w_position=float(default_w_position),
        tips_on_jog_tolerance=float(default_jog_tolerance),
        tips_on_z_offset=float(default_z_on_offset),
        matched=False,
        source="caller defaults",
      )
    label = entry.tipbox or entry.tipbox_id or "?"
    return ResolvedTipOffsets(
      tips_off_z_offset=float(
        entry.tips_off_z_offset if entry.tips_off_z_offset is not None else default_z_offset
      ),
      tips_off_w_position=float(
        entry.tips_off_w_position if entry.tips_off_w_position is not None else default_w_position
      ),
      tips_on_jog_tolerance=float(
        entry.tips_on_jog_tolerance
        if entry.tips_on_jog_tolerance is not None
        else default_jog_tolerance
      ),
      tips_on_z_offset=float(
        entry.tips_on_z_offset if entry.tips_on_z_offset is not None else default_z_on_offset
      ),
      matched=True,
      source=f"tip_offsets[{_normalize_head(entry.head_type)} / {label}]",
    )


# Transcribed from config/tip_offsets.yaml. Every numeric field is a
# tuned physical value measured against real hardware.
_TIP_OFFSET_ENTRIES: tuple[TipOffsetEntry, ...] = (
  # 96-channel LT (200 uL) head with the long V11 LT200 tips. These tips
  # engage ~7-8 mm shallower than the ST10-tuned press target, which trips a
  # false "within tolerance" warning at the default jog tolerance, so the
  # accept window is widened. They also stand much taller out of the box, so
  # Tips Off must eject ~25 mm above the seated depth.
  #
  # tips_off_w_position: the W ejector hits a hard mechanical stop at -11 on
  # this head -- driving to -11 (or beyond) stalls the W servo and aborts
  # the move even though the tips strip off. -9 clears the stop and ejects
  # cleanly; do not round this toward -10/-11.
  TipOffsetEntry(
    head_type="96_d_200",
    tipbox="96 V11 LT200 Tip Box 06880.002",
    tipbox_id="lw-b0704e550d2a",
    tips_off_z_offset=25.0,
    tips_off_w_position=-9.0,
    tips_on_jog_tolerance=12.0,
    tips_on_z_offset=0.0,
  ),
  # 384-channel ST (70 uL) head with the short V11 ST10 tips. The short tips
  # never trip the Tips On warning, so the press window stays at the 5 mm
  # default. A small eject offset keeps the short tips close to the box.
  TipOffsetEntry(
    head_type="384_d_70",
    tipbox="384 V11 ST10 Tip Box 10734.102",
    tipbox_id="lw-4914769d0af7",
    tips_off_z_offset=14.0,
    tips_off_w_position=-7.0,
    tips_on_jog_tolerance=5.0,
    tips_on_z_offset=0.0,
  ),
)

DEFAULT_TIP_OFFSET_TABLE = TipOffsetTable(list(_TIP_OFFSET_ENTRIES))
"""The built-in (head, tip box) offset table."""


def get_tip_offset_table() -> TipOffsetTable:
  """Return the built-in (head, tip box) offset table."""
  return DEFAULT_TIP_OFFSET_TABLE
