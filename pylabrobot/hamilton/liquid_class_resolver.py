"""Resolve Hamilton liquid classes and corrected volumes for Prep PIP ops.

Automatic lookup defaults to
:func:`~pylabrobot.hamilton.star.liquid_classes.get_star_liquid_class`
(STAR calibration tables); pass ``lookup=`` for instrument-specific tables.
"""

from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional, Sequence, Union

from pylabrobot.hamilton.liquid_classes import HamiltonLiquidClass
from pylabrobot.resources.hamilton import HamiltonTip
from pylabrobot.resources.liquid import Liquid

_Lookup = Callable[..., Optional[HamiltonLiquidClass]]

# What an aspirate argument takes from a class when it is not given.
ASPIRATE_CLASS_ATTRIBUTES: Dict[str, Callable[[HamiltonLiquidClass], float]] = {
  "flow_rates": lambda hlc: hlc.aspiration_flow_rate,
  "clot_detection_heights": lambda hlc: hlc.aspiration_clot_retract_height,
  "blow_out_air_volumes": lambda hlc: hlc.aspiration_blow_out_volume,
  "pre_wetting_volumes": lambda hlc: hlc.aspiration_over_aspirate_volume,
  "settling_times": lambda hlc: hlc.aspiration_settling_time,
  "swap_speeds": lambda hlc: hlc.aspiration_swap_speed,
  "transport_air_volumes": lambda hlc: hlc.aspiration_air_transport_volume,
}

# What a dispense argument takes from a class when it is not given.
DISPENSE_CLASS_ATTRIBUTES: Dict[str, Callable[[HamiltonLiquidClass], float]] = {
  "flow_rates": lambda hlc: hlc.dispense_flow_rate,
  "transport_air_volumes": lambda hlc: hlc.dispense_air_transport_volume,
  "stop_back_volumes": lambda hlc: hlc.dispense_stop_back_volume,
  "blow_out_air_volumes": lambda hlc: hlc.dispense_blow_out_volume,
  "settling_times": lambda hlc: hlc.dispense_settling_time,
  "swap_speeds": lambda hlc: hlc.dispense_swap_speed,
}


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
    from pylabrobot.hamilton.star.liquid_classes import get_star_liquid_class

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
  dvc = list(disable_volume_correction) if disable_volume_correction is not None else [False] * n
  if len(dvc) != n:
    raise ValueError(f"disable_volume_correction length must match ops ({n}), got {len(dvc)}")
  return [
    float(hlc.compute_corrected_volume(op.volume))
    if hlc is not None and not disabled
    else float(op.volume)
    for op, hlc, disabled in zip(ops, hlcs, dvc)
  ]


def per_container(name: str, given: Optional[Sequence[Any]], n: int) -> Optional[List[Any]]:
  """`given` as a list of one entry per container; None stays None.

  Raises:
    ValueError: Not one entry per container.
  """
  if given is None:
    return None
  if len(given) != n:
    raise ValueError(f"{name} must have one entry per container, {n}, has {len(given)}")
  return list(given)


def check_volume_arguments(
  volumes: Optional[Sequence[float]],
  piston_volumes: Optional[Sequence[float]],
  hamilton_liquid_classes: Optional[Sequence[Optional[HamiltonLiquidClass]]],
  how_moved: str,
) -> None:
  """Raise unless exactly one of `volumes` and `piston_volumes` is given, without a class beside
  `piston_volumes`.

  Args:
    volumes: liquid per container, corrected by a class; or None.
    piston_volumes: piston travel per container, as given; or None.
    hamilton_liquid_classes: the classes given, if any.
    how_moved: "drawn" or "pushed out", for the refusals.
  """
  if (volumes is None) == (piston_volumes is None):
    raise ValueError(
      f"give volumes, which a liquid class corrects, or piston_volumes, {how_moved} as given; "
      "not both and not neither"
    )
  if piston_volumes is not None and hamilton_liquid_classes is not None:
    raise ValueError(f"piston_volumes are {how_moved} as given; a liquid class would correct them")


def from_class(
  name: str,
  given: Optional[Sequence[Any]],
  n: int,
  classes: Optional[Sequence[HamiltonLiquidClass]],
  attributes: Dict[str, Callable[[HamiltonLiquidClass], float]],
) -> Optional[List[Any]]:
  """What is given, else what the liquid classes say, else None: the driver's own default. A None
  entry of `given` takes its class's value.

  Args:
    name: the argument, a key of `attributes`.
    given: one entry per container, or None.
    n: how many containers.
    classes: one per container, or None with piston volumes.
    attributes: `ASPIRATE_CLASS_ATTRIBUTES` or `DISPENSE_CLASS_ATTRIBUTES`.
  """
  values = per_container(name, given, n)
  if classes is None:
    return values
  if values is None:
    return [attributes[name](hlc) for hlc in classes]
  return [attributes[name](hlc) if v is None else v for v, hlc in zip(values, classes)]
