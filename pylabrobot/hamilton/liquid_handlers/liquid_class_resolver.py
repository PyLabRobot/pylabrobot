"""Resolve Hamilton liquid classes and corrected volumes for PIP backends.

Lives alongside :mod:`liquid_class`. Automatic lookup defaults to
:class:`~pylabrobot.hamilton.liquid_handlers.star.liquid_classes.get_star_liquid_class`
(STAR calibration tables); pass ``lookup=`` for instrument-specific tables.
"""

from __future__ import annotations

from typing import Any, Callable, List, Optional, Sequence, Union

from pylabrobot.hamilton.liquid_handlers.liquid_class import HamiltonLiquidClass
from pylabrobot.resources.hamilton import HamiltonTip
from pylabrobot.resources.liquid import Liquid

_Lookup = Callable[..., Optional[HamiltonLiquidClass]]


def resolve_hamilton_liquid_classes(
  explicit: Optional[List[Optional[HamiltonLiquidClass]]],
  ops: list,
  *,
  jet: Union[bool, List[bool]] = False,
  blow_out: Union[bool, List[bool]] = False,
  is_aspirate: bool = True,
  lookup: Optional[_Lookup] = None,
) -> List[Optional[HamiltonLiquidClass]]:
  """Resolve per-op Hamilton liquid classes.

  If ``explicit`` is None, resolve from each op's tip via ``lookup`` (default
  :func:`get_star_liquid_class`). Non-``HamiltonTip`` tips yield ``None``.

  If ``explicit`` is a list, it is returned as a shallow copy; ``None`` entries
  are preserved (legacy STAR behavior).

  Args:
    explicit: Caller-provided liquid classes, or None for automatic lookup.
    ops: Aspiration or dispense operations (must have a ``tip`` attribute).
    jet: Per-op or scalar flags passed to automatic liquid class lookup.
    blow_out: Per-op or scalar flags passed to automatic liquid class lookup.
    is_aspirate: Reserved for API compatibility with STAR; unused.
    lookup: Optional callable with the same signature as ``get_star_liquid_class``.
  """
  del is_aspirate
  n = len(ops)
  if isinstance(jet, bool):
    jet = [jet] * n
  if isinstance(blow_out, bool):
    blow_out = [blow_out] * n

  if explicit is not None:
    return list(explicit)

  if lookup is None:
    # Lazy import avoids circular import: star package __init__ may pull in pip_backend,
    # which imports this module.
    from pylabrobot.hamilton.liquid_handlers.star.liquid_classes import get_star_liquid_class

    fn = get_star_liquid_class
  else:
    fn = lookup
  result: List[Optional[HamiltonLiquidClass]] = []
  for i, op in enumerate(ops):
    tip = op.tip
    if not isinstance(tip, HamiltonTip):
      result.append(None)
      continue
    result.append(
      fn(
        tip_volume=tip.maximal_volume,
        is_core=False,
        is_tip=True,
        has_filter=tip.has_filter,
        liquid=Liquid.WATER,
        jet=jet[i],
        blow_out=blow_out[i],
      )
    )

  return result


def corrected_volumes_for_ops(
  ops: Sequence[Any],
  hlcs: Sequence[Optional[HamiltonLiquidClass]],
  disable_volume_correction: Optional[Sequence[bool]] = None,
) -> List[float]:
  """Apply liquid-class volume correction per op when enabled."""
  n = len(ops)
  if len(hlcs) != n:
    raise ValueError(f"hlcs length must match ops ({n}), got {len(hlcs)}")
  dvc = (
    list(disable_volume_correction) if disable_volume_correction is not None else [False] * n
  )
  if len(dvc) != n:
    raise ValueError(
      f"disable_volume_correction length must match ops ({n}), got {len(dvc)}"
    )
  return [
    float(hlc.compute_corrected_volume(op.volume))
    if hlc is not None and not disabled
    else float(op.volume)
    for op, hlc, disabled in zip(ops, hlcs, dvc)
  ]
