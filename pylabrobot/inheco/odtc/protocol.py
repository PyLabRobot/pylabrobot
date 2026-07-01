"""ODTC protocol conversion, duration estimation, and DataEvent progress parsing.

Provides:
- ODTCProtocol.from_protocol() classmethod (replaces protocol_to_odtc_protocol + ODTCConfig)
- Duration estimation and timeline building
- DataEvent payload parsing and ODTCProgress construction
"""

from __future__ import annotations

import html
import logging
import math
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Tuple

if TYPE_CHECKING:
  from .model import FluidQuantity

from pylabrobot.capabilities.thermocycling.standard import (
  Overshoot,
  Protocol,
  Ramp,
  Stage,
  Step,
)

from .model import (
  ODTCPID,
  PREMETHOD_ESTIMATED_DURATION_SECONDS,
  ODTCProgress,
  ODTCProtocol,
  ODTCVariant,
  generate_odtc_timestamp,
  get_constraints,
)
from .xml import _flatten_stages_for_xml

logger = logging.getLogger(__name__)


# =============================================================================
# Overshoot Calculation constants
# =============================================================================

_OVERSHOOT_HEAT_COEFFS: Dict[int, Tuple[float, float]] = {
  0: (5.1144, -6.6037),
  1: (19.469, -20.875),
  2: (22.829, -13.278),
}
_OVERSHOOT_COOL_COEFFS: Dict[int, Tuple[float, float]] = {
  0: (4.0941, -6.1247),
  1: (4.9773, -8.0866),
  2: (9.0513, -17.015),
}


def _calc_overshoot(
  plateau_temp: float,
  pre_temp: float,
  slope: float,
  hold_time: float,
  fluid_quantity: int,
) -> Optional[Overshoot]:
  """Calculate overshoot parameters for a temperature transition.

  Returns an Overshoot if overshoot is warranted, or None for no overshoot.
  """
  os2_default = 2.2 if slope >= 1.0 else 0.1

  if fluid_quantity not in (0, 1, 2) or hold_time == 0.0:
    return None

  heating = plateau_temp > pre_temp
  cooling = plateau_temp < pre_temp
  if not heating and not cooling:
    return None

  delta = (plateau_temp - pre_temp) if heating else (pre_temp - plateau_temp)
  if heating and (delta <= 5.0 or plateau_temp <= 35.0):
    return None
  if cooling and (delta <= 10.0 or plateau_temp <= 35.0):
    return None
  if slope <= 0.5:
    return None

  delta = min(delta, 60.0)
  coeffs = _OVERSHOOT_HEAT_COEFFS if heating else _OVERSHOOT_COOL_COEFFS
  a, b = coeffs[fluid_quantity]
  energy = a * math.log(delta) + b
  if energy <= 0.0:
    return None

  os_temp = math.sqrt(2.0 * energy / (1.0 / slope + 1.0 / 2.2))

  if heating and plateau_temp + os_temp > 102.0:
    cap = 102.0 - plateau_temp
    tri_time = cap / slope + cap / 2.2
    remaining = energy - 0.5 * tri_time * cap
    return Overshoot(
      target_temp=round(cap, 1),
      hold_seconds=round(remaining / cap, 1),
      return_rate=2.2,
    )

  return Overshoot(target_temp=round(os_temp, 1), hold_seconds=0.0, return_rate=2.2)


def _calculate_slope(
  from_temp: float,
  to_temp: float,
  rate: float,
  variant: ODTCVariant,
  default_heating_slope: Optional[float],
  default_cooling_slope: Optional[float],
) -> float:
  """Validate and clamp ramp rate against hardware limits."""
  constraints = get_constraints(variant)
  is_heating = to_temp > from_temp
  max_slope = constraints.max_heating_slope if is_heating else constraints.max_cooling_slope
  direction = "heating" if is_heating else "cooling"

  if not math.isinf(rate):
    if rate > max_slope:
      logger.warning(
        "Requested %s rate %.2f °C/s exceeds hardware maximum %.2f °C/s. "
        "Clamping to maximum. Transition: %.1f°C → %.1f°C",
        direction, rate, max_slope, from_temp, to_temp,
      )
      return max_slope
    return rate

  # inf = full device speed → use defaults
  if is_heating:
    slope = default_heating_slope if default_heating_slope is not None else constraints.max_heating_slope
  else:
    slope = default_cooling_slope if default_cooling_slope is not None else constraints.max_cooling_slope
  return min(slope, max_slope)


def _transform_step(
  step: Step,
  prev_temp: float,
  variant: ODTCVariant,
  fluid_quantity: int,
  default_heating_slope: Optional[float],
  default_cooling_slope: Optional[float],
  default_lid_temp: float,
  apply_overshoot: bool = True,
) -> Step:
  """Transform a user Step into an ODTC-compiled Step with computed overshoot if needed."""
  slope = _calculate_slope(
    prev_temp, step.temperature, step.ramp.rate,
    variant, default_heating_slope, default_cooling_slope,
  )
  # Honour explicit overshoot; compute automatically only when requested
  overshoot = step.ramp.overshoot
  if overshoot is None and apply_overshoot:
    overshoot = _calc_overshoot(step.temperature, prev_temp, slope, step.hold_seconds, fluid_quantity)

  ramp = Ramp(rate=slope, overshoot=overshoot)
  lid_temp = step.lid_temperature if step.lid_temperature is not None else default_lid_temp
  return Step(
    temperature=step.temperature,
    hold_seconds=step.hold_seconds,
    ramp=ramp,
    lid_temperature=lid_temp,
    backend_params=step.backend_params,
  )


def _transform_stages(
  stages: List[Stage],
  prev_temp_box: List[float],  # mutable single-element list for shared state
  variant: ODTCVariant,
  fluid_quantity: int,
  default_heating_slope: Optional[float],
  default_cooling_slope: Optional[float],
  default_lid_temp: float,
  apply_overshoot: bool = True,
) -> List[Stage]:
  """Recursively transform all steps in a stage tree, computing slopes and overshoot.

  Steps and inner_stages are processed in the same interleaved execution order
  used by ``_flatten_one_stage``: steps[0], inner[0], steps[1], inner[1], …,
  then any remaining steps. This ensures each overshoot is computed against the
  actual preceding temperature the device will see.
  """
  result = []
  for stage in stages:
    steps = stage.steps
    inner_stages = stage.inner_stages or []

    new_steps: List[Step] = []
    new_inner: List[Stage] = []

    # Interleave: steps[gi] → inner[gi] → steps[gi+1] → inner[gi+1] → …
    for gi, inner in enumerate(inner_stages):
      if gi < len(steps):
        new_step = _transform_step(
          steps[gi], prev_temp_box[0], variant, fluid_quantity,
          default_heating_slope, default_cooling_slope, default_lid_temp,
          apply_overshoot,
        )
        prev_temp_box[0] = steps[gi].temperature
        new_steps.append(new_step)
      transformed_inner = _transform_stages(
        [inner], prev_temp_box, variant, fluid_quantity,
        default_heating_slope, default_cooling_slope, default_lid_temp,
        apply_overshoot,
      )
      new_inner.extend(transformed_inner)

    # Remaining steps after all inner stages (or all steps when no inner_stages)
    for step in steps[len(inner_stages):]:
      new_step = _transform_step(
        step, prev_temp_box[0], variant, fluid_quantity,
        default_heating_slope, default_cooling_slope, default_lid_temp,
        apply_overshoot,
      )
      prev_temp_box[0] = step.temperature
      new_steps.append(new_step)

    result.append(Stage(steps=new_steps, repeats=stage.repeats, inner_stages=new_inner))
  return result


def _from_protocol(
  protocol: Protocol,
  variant: ODTCVariant,
  fluid_quantity: "FluidQuantity",
  plate_type: int,
  post_heating: bool,
  pid_set: List[ODTCPID],
  apply_overshoot: bool,
  name: Optional[str] = None,
  lid_temperature: Optional[float] = None,
  start_lid_temperature: Optional[float] = None,
  default_heating_slope: Optional[float] = None,
  default_cooling_slope: Optional[float] = None,
  creator: Optional[str] = None,
  description: Optional[str] = None,
  datetime: Optional[str] = None,
) -> ODTCProtocol:
  """Private implementation of ODTCProtocol.from_protocol().

  All compilation config params are required — callers must resolve defaults
  from ODTCBackendParams before calling. Use ODTCProtocol.from_protocol() as
  the public API.
  """
  constraints = get_constraints(variant)
  effective_lid = lid_temperature if lid_temperature is not None else constraints.max_lid_temp

  prev_temp_box: List[float] = [25.0]  # start from ambient
  transformed_stages = _transform_stages(
    protocol.stages, prev_temp_box, variant, fluid_quantity,
    default_heating_slope, default_cooling_slope, effective_lid,
    apply_overshoot,
  )

  # start_block_temperature = first step's target
  if protocol.stages and protocol.stages[0].steps:
    start_block_temp = protocol.stages[0].steps[0].temperature
  else:
    start_block_temp = 25.0

  effective_start_lid = start_lid_temperature if start_lid_temperature is not None else effective_lid
  resolved_datetime = datetime or generate_odtc_timestamp()
  resolved_name = name or protocol.name or "plr_currentProtocol"
  is_scratch = name is None

  return ODTCProtocol(
    stages=transformed_stages,
    name=resolved_name,
    lid_temperature=effective_lid,
    is_scratch=is_scratch,
    variant=variant,
    plate_type=plate_type,
    fluid_quantity=fluid_quantity,
    post_heating=post_heating,
    start_block_temperature=start_block_temp,
    start_lid_temperature=effective_start_lid,
    pid_set=list(pid_set),
    kind="method",
    creator=creator,
    description=description,
    datetime=resolved_datetime,
  )




# =============================================================================
# Flat step view for duration / timeline / progress computation
# =============================================================================


@dataclass(frozen=True)
class _FlatStep:
  """A step with its serialization metadata (number, goto, loop)."""

  step: Step
  number: int
  goto_number: int
  loop_number: int  # LoopNumber = additional iterations (total - 1)


def _get_flat_steps(odtc_protocol: ODTCProtocol) -> List[_FlatStep]:
  """Flatten ODTCProtocol.stages to _FlatStep list with goto/loop derived from stage structure."""
  raw = _flatten_stages_for_xml(odtc_protocol.stages)
  return [_FlatStep(step=s, number=n, goto_number=g, loop_number=l) for s, n, g, l in raw]


def _analyze_flat_loops(flat_steps: List[_FlatStep]) -> List[Tuple[int, int, int]]:
  """Return (start_num, end_num, total_repeats) for each loop."""
  return sorted(
    [
      (fs.goto_number, fs.number, fs.loop_number + 1)
      for fs in flat_steps if fs.goto_number > 0
    ],
    key=lambda x: x[1],
  )


def _expand_flat_sequence(
  flat_steps: List[_FlatStep],
  loops: List[Tuple[int, int, int]],
) -> List[int]:
  """Return step numbers in execution order (loops expanded)."""
  if not flat_steps:
    return []
  by_num = {fs.number: fs for fs in flat_steps}
  max_step = max(fs.number for fs in flat_steps)
  loop_by_end = {end: (start, count) for start, end, count in loops}
  expanded: List[int] = []
  i = 1
  while i <= max_step:
    if i not in by_num:
      i += 1
      continue
    expanded.append(i)
    if i in loop_by_end:
      start, count = loop_by_end[i]
      for _ in range(count - 1):
        for j in range(start, i + 1):
          if j in by_num:
            expanded.append(j)
    i += 1
  return expanded


def _expanded_step_count(odtc_protocol: ODTCProtocol) -> int:
  flat = _get_flat_steps(odtc_protocol)
  loops = _analyze_flat_loops(flat)
  return len(_expand_flat_sequence(flat, loops))


def _cycle_count(odtc_protocol: ODTCProtocol) -> int:
  flat = _get_flat_steps(odtc_protocol)
  if not flat:
    return 0
  loops = _analyze_flat_loops(flat)
  if not loops:
    return 1
  top_level = [
    (s, e, c) for (s, e, c) in loops
    if not any((s2, e2, _) != (s, e, c) and s2 <= s and e <= e2 for (s2, e2, _) in loops)
  ]
  if not top_level:
    return 0
  main = max(top_level, key=lambda x: x[1] - x[0])
  return main[2]


def estimate_method_duration_seconds(odtc_protocol: ODTCProtocol) -> float:
  """Estimate total method duration in seconds (ramp + overshoot + plateau per step × loops)."""
  if odtc_protocol.kind == "premethod":
    return PREMETHOD_ESTIMATED_DURATION_SECONDS
  flat = _get_flat_steps(odtc_protocol)
  if not flat:
    return 0.0
  loops = _analyze_flat_loops(flat)
  step_nums = _expand_flat_sequence(flat, loops)
  by_num = {fs.number: fs for fs in flat}

  total = 0.0
  prev_temp = odtc_protocol.start_block_temperature
  min_slope = 0.1

  for step_num in step_nums:
    fs = by_num[step_num]
    step = fs.step
    slope = max(step.ramp.rate if not math.isinf(step.ramp.rate) else 4.4, min_slope)
    ramp_time = abs(step.temperature - prev_temp) / slope
    if step.ramp.overshoot:
      os = step.ramp.overshoot
      os1 = max(slope, min_slope)
      os2 = max(os.return_rate, min_slope)
      os_total = os.target_temp / os1 + os.hold_seconds + os.target_temp / os2
    else:
      os_total = 0.0
    total += ramp_time + os_total + step.hold_seconds
    prev_temp = step.temperature

  return total


# =============================================================================
# Protocol timeline and progress
# =============================================================================


def _build_protocol_timeline(
  odtc_protocol: ODTCProtocol,
) -> List[Tuple[float, float, int, int, float, float]]:
  """Build timeline segments: (t_start, t_end, step_idx, cycle_idx, setpoint, plateau_end)."""
  if odtc_protocol.kind == "premethod":
    duration = PREMETHOD_ESTIMATED_DURATION_SECONDS
    setpoint = odtc_protocol.target_block_temperature
    return [(0.0, duration, 0, 0, setpoint, duration)]

  flat = _get_flat_steps(odtc_protocol)
  if not flat:
    return []

  loops = _analyze_flat_loops(flat)
  step_nums = _expand_flat_sequence(flat, loops)
  by_num = {fs.number: fs for fs in flat}
  total_expanded = len(step_nums)
  total_cycles = _cycle_count(odtc_protocol)
  steps_per_cycle = total_expanded // total_cycles if total_cycles > 0 else max(1, total_expanded)

  segments: List[Tuple[float, float, int, int, float, float]] = []
  t = 0.0
  prev_temp = odtc_protocol.start_block_temperature
  min_slope = 0.1

  for flat_index, step_num in enumerate(step_nums):
    step = by_num[step_num].step
    slope = max(step.ramp.rate if not math.isinf(step.ramp.rate) else 4.4, min_slope)
    ramp_time = abs(step.temperature - prev_temp) / slope
    if step.ramp.overshoot:
      os = step.ramp.overshoot
      os1 = max(slope, min_slope)
      os2 = max(os.return_rate, min_slope)
      os_total = os.target_temp / os1 + os.hold_seconds + os.target_temp / os2
    else:
      os_total = 0.0
    plateau_end_t = t + ramp_time + os_total + step.hold_seconds
    cycle_index = flat_index // steps_per_cycle
    step_index = flat_index % steps_per_cycle
    segments.append((t, plateau_end_t, step_index, cycle_index, step.temperature, plateau_end_t))
    t = plateau_end_t
    prev_temp = step.temperature

  return segments


_SNAP_TEMP_TOLERANCE = 0.5


def _snap_step_from_target_temp(
  step_nums: List[int],
  by_num: Dict[int, _FlatStep],
  new_target_c: float,
  current_flat_index: int,
  total_cycles: int,
) -> Optional[Dict[str, Any]]:
  n = len(step_nums)
  if n == 0:
    return None
  steps_per_cycle = n // total_cycles if total_cycles > 0 else n
  for offset in range(1, n + 1):
    idx = (current_flat_index + offset) % n
    fs = by_num.get(step_nums[idx])
    if fs is None:
      continue
    if abs(fs.step.temperature - new_target_c) <= _SNAP_TEMP_TOLERANCE:
      return {
        "step_index": idx % steps_per_cycle,
        "cycle_index": idx // steps_per_cycle,
        "setpoint_c": fs.step.temperature,
      }
  return None


def _protocol_position_from_elapsed(
  odtc_protocol: ODTCProtocol, elapsed_s: float
) -> Dict[str, Any]:
  if elapsed_s < 0:
    elapsed_s = 0.0
  segments = _build_protocol_timeline(odtc_protocol)
  if not segments:
    flat = _get_flat_steps(odtc_protocol)
    loops = _analyze_flat_loops(flat)
    total_steps = len(_expand_flat_sequence(flat, loops)) if flat else 0
    total_cycles = _cycle_count(odtc_protocol) if flat else 1
    return {
      "step_index": 0, "cycle_index": 0,
      "setpoint_c": odtc_protocol.start_block_temperature,
      "remaining_hold_s": 0.0,
      "total_steps": total_steps, "total_cycles": total_cycles,
    }

  flat = _get_flat_steps(odtc_protocol) if odtc_protocol.kind == "method" else []
  if flat:
    loops = _analyze_flat_loops(flat)
    step_nums = _expand_flat_sequence(flat, loops)
    total_expanded = len(step_nums)
    total_cycles = _cycle_count(odtc_protocol)
    steps_per_cycle = total_expanded // total_cycles if total_cycles > 0 else total_expanded
  else:
    steps_per_cycle = 1
    total_cycles = 1

  for t_start, t_end, step_index, cycle_index, setpoint_c, plateau_end_t in segments:
    if elapsed_s <= t_end:
      return {
        "step_index": step_index, "cycle_index": cycle_index,
        "setpoint_c": setpoint_c,
        "remaining_hold_s": max(0.0, plateau_end_t - elapsed_s),
        "total_steps": steps_per_cycle, "total_cycles": total_cycles,
      }

  _, _, step_index, cycle_index, setpoint_c, _ = segments[-1]
  return {
    "step_index": step_index, "cycle_index": cycle_index,
    "setpoint_c": setpoint_c, "remaining_hold_s": 0.0,
    "total_steps": steps_per_cycle, "total_cycles": total_cycles,
  }


# =============================================================================
# DataEvent payload parsing
# =============================================================================


def _parse_data_event_series_value(series_elem: Any) -> Optional[float]:
  values = series_elem.findall(".//integerValue")
  if not values:
    return None
  text = values[-1].text
  if text is None:
    return None
  try:
    return float(text)
  except ValueError:
    return None


def _parse_data_event_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
  """Parse a DataEvent payload; returns elapsed_s + optional temperatures.

  The ODTC device emits exactly four data series per DataEvent:
  Elapsed time, Target temperature, Current temperature, LID temperature.
  """
  data_value = payload.get("dataValue")
  if not data_value or not isinstance(data_value, str):
    raise ValueError(f"DataEvent missing dataValue: {payload}")
  outer = ET.fromstring(data_value)
  any_data = outer.find(".//{*}AnyData")
  if any_data is None:
    any_data = outer.find(".//AnyData")
  if any_data is None or any_data.text is None:
    raise ValueError(f"DataEvent missing AnyData: {data_value[:200]}")
  inner_xml = any_data.text.strip()
  if "&lt;" in inner_xml or "&gt;" in inner_xml:
    inner_xml = html.unescape(inner_xml)
  inner = ET.fromstring(inner_xml)
  elapsed_s = 0.0
  target_temp_c: Optional[float] = None
  current_temp_c: Optional[float] = None
  lid_temp_c: Optional[float] = None
  for elem in inner.iter():
    if not elem.tag.endswith("dataSeries"):
      continue
    name_id = elem.get("nameId")
    unit = elem.get("unit") or ""
    raw = _parse_data_event_series_value(elem)
    if raw is None:
      continue
    if name_id == "Elapsed time" and unit == "ms":
      elapsed_s = raw / 1000.0
    elif name_id == "Target temperature" and unit == "1/100°C":
      target_temp_c = raw / 100.0
    elif name_id == "Current temperature" and unit == "1/100°C":
      current_temp_c = raw / 100.0
    elif name_id == "LID temperature" and unit == "1/100°C":
      lid_temp_c = raw / 100.0
  return {
    "elapsed_s": elapsed_s,
    "target_temp_c": target_temp_c,
    "current_temp_c": current_temp_c,
    "lid_temp_c": lid_temp_c,
  }


def build_progress_from_data_event(
  payload: Dict[str, Any],
  odtc_protocol: Optional[ODTCProtocol] = None,
  last_target_temp_c: Optional[float] = None,
) -> ODTCProgress:
  """Build ODTCProgress from a raw DataEvent payload and optional protocol."""
  parsed = _parse_data_event_payload(payload)
  elapsed_s = parsed["elapsed_s"]
  target_temp_c = parsed.get("target_temp_c")
  current_temp_c = parsed.get("current_temp_c")
  lid_temp_c = parsed.get("lid_temp_c")

  if odtc_protocol is None:
    return ODTCProgress(
      elapsed_s=elapsed_s, target_temp_c=target_temp_c,
      current_temp_c=current_temp_c, lid_temp_c=lid_temp_c,
      estimated_duration_s=None, remaining_duration_s=0.0,
    )

  position = _protocol_position_from_elapsed(odtc_protocol, elapsed_s)

  if (
    odtc_protocol.kind == "method"
    and odtc_protocol.stages
    and target_temp_c is not None
    and last_target_temp_c is not None
    and abs(target_temp_c - last_target_temp_c) > _SNAP_TEMP_TOLERANCE
  ):
    flat = _get_flat_steps(odtc_protocol)
    loops = _analyze_flat_loops(flat)
    step_nums = _expand_flat_sequence(flat, loops)
    by_num = {fs.number: fs for fs in flat}
    total_cycles = _cycle_count(odtc_protocol)
    steps_per_cycle = len(step_nums) // total_cycles if total_cycles > 0 else len(step_nums)
    current_flat = position["step_index"] + position["cycle_index"] * steps_per_cycle
    snapped = _snap_step_from_target_temp(step_nums, by_num, target_temp_c, current_flat, total_cycles)
    if snapped is not None:
      position = {**position, **snapped}

  target = target_temp_c
  if odtc_protocol.kind == "premethod":
    target = odtc_protocol.target_block_temperature
  elif position.get("setpoint_c") is not None and target is None:
    target = position["setpoint_c"]

  est_s: Optional[float] = (
    PREMETHOD_ESTIMATED_DURATION_SECONDS if odtc_protocol.kind == "premethod"
    else estimate_method_duration_seconds(odtc_protocol)
  )
  rem_s = max(0.0, (est_s or 0.0) - elapsed_s)

  return ODTCProgress(
    elapsed_s=elapsed_s, target_temp_c=target,
    current_temp_c=current_temp_c, lid_temp_c=lid_temp_c,
    current_step_index=position["step_index"],
    total_step_count=position.get("total_steps") or 0,
    current_cycle_index=position["cycle_index"],
    total_cycle_count=position.get("total_cycles") or 0,
    remaining_hold_s=position.get("remaining_hold_s") or 0.0,
    estimated_duration_s=est_s,
    remaining_duration_s=rem_s,
    is_premethod=(odtc_protocol.kind == "premethod"),
  )
