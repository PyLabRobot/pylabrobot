"""Opentrons' own default flow rates for the Flex pipettes, per model and tip.

Every Opentrons liquid-handling command carries a required ``flowRate``, and no
robot-server endpoint serves the defaults: ``GET /instruments`` reports channels
and volume range only, and the protocol engine rejects a command that omits the
field rather than filling one in. The numbers therefore have to live on the
client, the same way the Hamilton backends carry their liquid classes.

They are transcribed from Opentrons' own pipette definitions
(``shared-data/pipette/definitions/2/liquid``) at version 9.1.2.
``pipette_defaults_tests`` re-reads those definitions and fails on any drift,
whenever ``opentrons-shared-data`` happens to be installed.

Rates span more than two orders of magnitude across the range, 6 uL/s to 716, so
one constant is wrong for almost every pipette. They also change between
VERSIONS of the same model: a p1000 on a 50 uL tip is 6 uL/s at v3.3 and 478 at
v3.4. Models are therefore keyed by full version string, and one this table does
not name raises instead of falling back, because no neighbour is near enough to
guess with.
"""

from typing import Dict, NamedTuple, Tuple


class FlowRates(NamedTuple):
  """Default aspirate, dispense and blow-out rates in uL/s."""

  aspirate: float
  dispense: float
  blow_out: float


_P1000_ORIGINAL = {
  "t50": FlowRates(6, 6, 80),
  "t200": FlowRates(80, 80, 80),
  "t1000": FlowRates(160, 160, 80),
}

_P1000_FAST = {
  "t50": FlowRates(478, 478, 478),
  "t200": FlowRates(716, 716, 716),
  "t1000": FlowRates(716, 716, 716),
}

_P200_96_ORIGINAL = {
  "t20": FlowRates(6.5, 6.5, 10),
  "t50": FlowRates(6, 6, 10),
  "t200": FlowRates(10, 10, 10),
}

_P200_96_REVISED = {
  "t20": FlowRates(6.5, 6.5, 10),
  "t50": FlowRates(22, 22, 22),
  "t200": FlowRates(15, 15, 10),
}

_P50_SLOW_ON_50 = {
  "t20": FlowRates(35, 57, 57),
  "t50": FlowRates(8, 8, 4),
}

_P50_FAST_ON_50 = {
  "t20": FlowRates(35, 57, 57),
  "t50": FlowRates(35, 57, 57),
}

_P50_SLOW_ON_20 = {
  "t20": FlowRates(22, 22, 57),
  "t50": FlowRates(35, 57, 57),
}

_DEFAULTS: Dict[str, Dict[str, FlowRates]] = {
  "p1000_96_v3.0": _P1000_ORIGINAL,
  "p1000_96_v3.3": _P1000_ORIGINAL,
  "p1000_96_v3.4": _P1000_ORIGINAL,
  "p1000_96_v3.5": _P1000_ORIGINAL,
  "p1000_96_v3.6": _P1000_ORIGINAL,
  "p1000_96_v3.7": _P1000_ORIGINAL,
  "p1000_multi_v3.0": _P1000_ORIGINAL,
  "p1000_multi_v3.3": _P1000_ORIGINAL,
  "p1000_multi_v3.4": _P1000_FAST,
  "p1000_multi_v3.5": _P1000_FAST,
  "p1000_multi_v3.6": _P1000_FAST,
  "p1000_single_v3.0": _P1000_ORIGINAL,
  "p1000_single_v3.3": _P1000_ORIGINAL,
  "p1000_single_v3.4": _P1000_FAST,
  "p1000_single_v3.5": _P1000_FAST,
  "p1000_single_v3.6": _P1000_FAST,
  "p1000_single_v3.7": _P1000_FAST,
  "p200_96_v3.0": _P200_96_ORIGINAL,
  "p200_96_v3.1": _P200_96_REVISED,
  "p200_96_v3.2": _P200_96_REVISED,
  "p200_96_v3.3": _P200_96_REVISED,
  "p50_multi_v3.0": _P50_SLOW_ON_50,
  "p50_multi_v3.3": _P50_SLOW_ON_50,
  "p50_multi_v3.4": _P50_FAST_ON_50,
  "p50_multi_v3.5": _P50_FAST_ON_50,
  "p50_single_v3.0": _P50_SLOW_ON_50,
  "p50_single_v3.3": _P50_SLOW_ON_50,
  "p50_single_v3.4": _P50_FAST_ON_50,
  "p50_single_v3.5": _P50_FAST_ON_50,
  "p50_single_v3.6": _P50_SLOW_ON_20,
  "p50_single_v3.7": _P50_SLOW_ON_20,
}


def _rates_by_tip(pipette_model: str) -> Dict[str, FlowRates]:
  if not pipette_model:
    raise ValueError("No pipette model given, so its default flow rates are unknown.")

  rates = _DEFAULTS.get(pipette_model)
  if rates is None:
    raise ValueError(
      f"No default flow rates are recorded for pipette '{pipette_model}'. Pass an explicit "
      "flow_rate, or add the model to pylabrobot.opentrons.pipette_defaults. Rates change "
      "between versions of the same pipette, so the nearest version is not a safe stand-in."
    )
  return rates


def flow_rates(pipette_model: str, tip_volume: float) -> FlowRates:
  """Look up the defaults for a pipette model ("p50_multi_v3.5") and tip volume.

  Raises:
    ValueError: If the pipette does not support a tip of that volume. There is
      no near-enough tip to fall back to: the p1000 eight-channel alone spans
      478 uL/s on a 50 uL tip and 716 on a 200, so guessing would silently
      pipette at the wrong speed.
  """
  rates = _rates_by_tip(pipette_model)
  tip_name = f"t{int(tip_volume)}"
  if tip_name not in rates:
    supported = ", ".join(sorted(rates))
    raise ValueError(
      f"'{pipette_model}' publishes no default flow rate for a {tip_volume} uL tip "
      f"(it supports {supported}). Pass an explicit flow_rate for this tip."
    )
  return rates[tip_name]


def supported_tip_volumes(pipette_model: str) -> Tuple[float, ...]:
  """The tip volumes this pipette publishes defaults for, ascending."""
  return tuple(sorted(float(name[1:]) for name in _rates_by_tip(pipette_model)))
