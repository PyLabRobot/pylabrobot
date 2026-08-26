"""The disposable/pintool tip catalogue.

Every tip a Bravo head can pick up: its capacity, physical length (where
measured), and which head types it fits. The catalogue is a fixed table of
measured values, not a runtime-editable store.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Optional, Union

from .types import ALL_HEAD_TYPES, HeadType


@dataclass(frozen=True)
class TipDefinition:
  """One entry in the tip catalogue.

  Attributes:
    tip_id: The catalogue's stable identifier for this tip, e.g. ``"st_10ul"``.
    capacity_ul: The tip's nominal liquid capacity, in microlitres.
    label: A short human-readable name, e.g. ``"10 uL"``.
    length_mm: The tip's measured physical length, in millimetres, or
      ``None`` if it has not been measured.
    source: Where the value came from, e.g. ``"measured"`` or
      ``"vendor-source-option"``.
    compatible_heads: The head types this tip fits. An empty tuple means no
      compatibility filter is recorded for this tip.
  """

  tip_id: str
  capacity_ul: float
  label: str
  length_mm: Optional[float]
  source: str
  compatible_heads: tuple[HeadType, ...] = ()


# Transcribed from config/tips.yaml. Every row is a measured or
# vendor-documented physical value; a `length_mm` of `None` means the tip's
# length has not been measured, not that it is zero.
_TIP_DEFINITIONS: tuple[TipDefinition, ...] = (
  TipDefinition(
    "st_10ul",
    10.0,
    "10 uL",
    19.9,
    "measured",
    ("16_d_st", "384_d_70", "384_d_70_s2", "96_d_70", "96_d_70_s2"),
  ),
  TipDefinition(
    "st_15ul",
    15.0,
    "15 uL",
    None,
    "vendor-source-option",
    ("16_d_st", "384_d_70", "384_d_70_s2", "96_d_70", "96_d_70_s2"),
  ),
  TipDefinition(
    "lt_200ul",
    200.0,
    "200 uL",
    None,
    "vendor-source-option",
    ("8_d_lt", "96_d_200", "96_d_200_s2"),
  ),
  TipDefinition(
    "lt_250ul",
    250.0,
    "250 uL",
    55.2,
    "vendor-source-default",
    ("8_d_lt", "96_d_200", "96_d_200_s2"),
  ),
  TipDefinition(
    "st_30ul",
    30.0,
    "30 uL",
    26.1,
    "vendor-source-comment",
    ("16_d_st", "384_d_70", "384_d_70_s2", "96_d_70", "96_d_70_s2"),
  ),
  TipDefinition(
    "st_50ul",
    50.0,
    "50 uL",
    None,
    "vendor-source-option",
    ("16_d_st", "384_d_70", "384_d_70_s2", "96_d_70", "96_d_70_s2"),
  ),
  TipDefinition(
    "st_51ul",
    51.0,
    "51 uL",
    None,
    "vendor-source-option",
    ("16_d_st", "384_d_70", "384_d_70_s2", "96_d_70", "96_d_70_s2"),
  ),
  TipDefinition(
    "st_70ul",
    70.0,
    "70 uL",
    None,
    "vendor-source-option",
    ("16_d_st", "384_d_70", "384_d_70_s2", "96_d_70", "96_d_70_s2"),
  ),
  TipDefinition(
    "pin_fp1cb",
    0.0,
    "FP1CB",
    None,
    "vendor-source-option",
    ("1536_pintool", "384_pintool", "96_pintool"),
  ),
  TipDefinition(
    "pin_fp1n",
    0.0,
    "FP1N",
    None,
    "vendor-source-option",
    ("1536_pintool", "384_pintool", "96_pintool"),
  ),
  TipDefinition(
    "pin_fp1t",
    0.0,
    "FP1T",
    None,
    "vendor-source-option",
    ("1536_pintool", "384_pintool", "96_pintool"),
  ),
)


def _is_close_capacity(value: object, capacity_ul: float) -> bool:
  """Return whether *value* is numerically close to *capacity_ul*.

  Args:
    value: A candidate capacity, of any type; non-numeric values return
      False rather than raising.
    capacity_ul: The capacity to compare against, in microlitres.

  Returns:
    True if *value* converts to a float within 1e-6 of *capacity_ul*.
  """
  try:
    return abs(float(value) - float(capacity_ul)) < 1e-6  # type: ignore[arg-type]
  except (TypeError, ValueError):
    return False


def _normalize_head_type(head_type: Union[HeadType, str]) -> Optional[HeadType]:
  """Normalize a head type value to a canonical :data:`HeadType`.

  Args:
    head_type: A head type string, in any case.

  Returns:
    The lowercase, canonical head type if it is a recognized value,
    otherwise ``None``.
  """
  text = str(head_type).strip().lower()
  return text if text in ALL_HEAD_TYPES else None  # type: ignore[return-value]


def get_tip_definitions_for_head(head_type: Union[HeadType, str]) -> list[TipDefinition]:
  """Return every tip compatible with a head type, sorted by capacity.

  Args:
    head_type: The head type to look up tips for.

  Returns:
    Tips whose ``compatible_heads`` includes *head_type* (or is empty, which
    means no compatibility filter is recorded), sorted by capacity, then
    label, then tip id. Returns an empty list for an unrecognized head type.
  """
  normalized = _normalize_head_type(head_type)
  if normalized is None:
    return []
  matches = [
    tip
    for tip in _TIP_DEFINITIONS
    if not tip.compatible_heads or normalized in tip.compatible_heads
  ]
  matches.sort(key=lambda tip: (float(tip.capacity_ul or 0.0), tip.label.lower(), tip.tip_id))
  return matches


def get_tip_definition(
  head_type: Union[HeadType, str], tip_id_or_capacity: Union[str, float, int, None]
) -> Optional[TipDefinition]:
  """Resolve a tip definition for a head, by id or by capacity.

  Args:
    head_type: The head type the tip must be compatible with.
    tip_id_or_capacity: Either a ``tip_id`` string, or a capacity in
      microlitres (matched within a small tolerance).

  Returns:
    The first matching :class:`TipDefinition`, or ``None`` if nothing
    matches.
  """
  if tip_id_or_capacity is None:
    return None
  for tip in get_tip_definitions_for_head(head_type):
    if str(tip.tip_id) == str(tip_id_or_capacity):
      return tip
    if _is_close_capacity(tip_id_or_capacity, tip.capacity_ul):
      return tip
  return None


def get_tip_definition_by_id(tip_id: Optional[str]) -> Optional[TipDefinition]:
  """Return the tip definition with the given id, regardless of head.

  Args:
    tip_id: The tip id to look up.

  Returns:
    The matching :class:`TipDefinition`, or ``None`` if not found or
    *tip_id* is falsy.
  """
  if not tip_id:
    return None
  for tip in _TIP_DEFINITIONS:
    if tip.tip_id == str(tip_id):
      return tip
  return None


def get_tip_length_mm(
  head_type: Union[HeadType, str], tip_id_or_capacity: Union[str, float, int, None]
) -> Optional[float]:
  """Return a tip's measured length, in millimetres.

  Args:
    head_type: The head type the tip must be compatible with.
    tip_id_or_capacity: Either a ``tip_id`` string, or a capacity in
      microlitres.

  Returns:
    The tip's ``length_mm``, which is ``None`` both when the tip cannot be
    resolved and when it resolves to a tip whose length has not been
    measured.
  """
  tip = get_tip_definition(head_type, tip_id_or_capacity)
  return None if tip is None else tip.length_mm


def get_tip_capacity_ul(
  head_type: Union[HeadType, str], tip_id_or_capacity: Union[str, float, int, None]
) -> float:
  """Return a tip's capacity, in microlitres.

  Args:
    head_type: The head type the tip must be compatible with.
    tip_id_or_capacity: Either a ``tip_id`` string, or a capacity value.

  Returns:
    The resolved tip's ``capacity_ul``. If no tip resolves, falls back to
    interpreting *tip_id_or_capacity* itself as a numeric capacity, or 0.0
    if that also fails.
  """
  tip = get_tip_definition(head_type, tip_id_or_capacity)
  if tip is not None:
    return float(tip.capacity_ul)
  try:
    return float(tip_id_or_capacity or 0.0)  # type: ignore[arg-type]
  except (TypeError, ValueError):
    return 0.0


def get_tip_id_for_capacity(
  head_type: Union[HeadType, str], capacity_ul: Optional[float]
) -> Optional[str]:
  """Return the tip id matching a capacity for a head type.

  Args:
    head_type: The head type the tip must be compatible with.
    capacity_ul: The capacity to match, in microlitres.

  Returns:
    The matching tip's ``tip_id``, or ``None`` if nothing matches.
  """
  tip = get_tip_definition(head_type, capacity_ul)
  return None if tip is None else tip.tip_id


def get_default_tip_id_for_head(head_type: Union[HeadType, str]) -> Optional[str]:
  """Return the tip id a head type should default to.

  Args:
    head_type: The head type to pick a default tip for.

  Returns:
    The 200 uL tip's id for long-tip (8_d_lt/96_d_200/96_d_200_s2) heads, the
    30 uL tip's id for every other compatible head, or the first compatible
    tip's id if the preferred capacity has no match. ``None`` if the head
    type is unrecognized or has no compatible tips.
  """
  normalized = _normalize_head_type(head_type)
  if normalized is None:
    return None
  options = get_tip_definitions_for_head(normalized)
  if not options:
    return None
  preferred_capacity = 200.0 if normalized in {"8_d_lt", "96_d_200", "96_d_200_s2"} else 30.0
  match = get_tip_definition(normalized, preferred_capacity)
  return match.tip_id if match is not None else options[0].tip_id


def serialize_tip_options_for_head(head_type: Union[HeadType, str]) -> list[dict[str, object]]:
  """Return every tip compatible with a head type as plain dicts.

  Args:
    head_type: The head type to look up tips for.

  Returns:
    Each compatible :class:`TipDefinition`, in the same order as
    :func:`get_tip_definitions_for_head`, converted with ``dataclasses.asdict``.
  """
  return [asdict(tip) for tip in get_tip_definitions_for_head(head_type)]
