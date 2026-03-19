"""ODTC protocol conversion.

Conversion between PyLabRobot Protocol and ODTC representation (ODTCProtocol),
loop analysis, step expansion, duration estimation, timeline building, and
DataEvent payload parsing.
"""

from __future__ import annotations

import html
import logging
import xml.etree.ElementTree as ET
from dataclasses import replace
from typing import (
  Any,
  Dict,
  List,
  Optional,
  Tuple,
  cast,
)

from pylabrobot.thermocycling.standard import Protocol, Stage, Step

from .odtc_model import (
  PREMETHOD_ESTIMATED_DURATION_SECONDS,
  ODTCConfig,
  ODTCProgress,
  ODTCProtocol,
  ODTCStage,
  ODTCStep,
  ODTCStepSettings,
  generate_odtc_timestamp,
  get_constraints,
)

logger = logging.getLogger(__name__)


# =============================================================================
# Protocol Conversion Functions
# =============================================================================


def _calculate_slope(
  from_temp: float,
  to_temp: float,
  rate: Optional[float],
  config: ODTCConfig,
) -> float:
  """Calculate and validate slope (ramp rate) for temperature transition.

  Both Protocol.Step.rate and ODTC slope represent the same thing: ramp rate in °C/s.
  This function validates against hardware limits and clamps if necessary.

  Args:
    from_temp: Starting temperature in °C.
    to_temp: Target temperature in °C.
    rate: Optional rate from Protocol Step (°C/s). Same units as ODTC slope.
    config: ODTC config with default slopes and variant.

  Returns:
    Slope value in °C/s, clamped to hardware limits if necessary.
  """
  constraints = get_constraints(config.variant)
  is_heating = to_temp > from_temp
  max_slope = constraints.max_heating_slope if is_heating else constraints.max_cooling_slope
  direction = "heating" if is_heating else "cooling"

  if rate is not None:
    # User provided an explicit rate - validate and clamp if needed
    if rate > max_slope:
      logger.warning(
        "Requested %s rate %.2f °C/s exceeds hardware maximum %.2f °C/s. "
        "Clamping to maximum. Temperature transition: %.1f°C → %.1f°C",
        direction,
        rate,
        max_slope,
        from_temp,
        to_temp,
      )
      return max_slope
    return rate

  # No rate specified - use config defaults (which should already be within limits)
  default_slope = config.default_heating_slope if is_heating else config.default_cooling_slope

  # Validate config defaults too (in case user configured invalid defaults)
  if default_slope > max_slope:
    logger.warning(
      "Config default_%s_slope %.2f °C/s exceeds hardware maximum %.2f °C/s. Clamping to maximum.",
      direction,
      default_slope,
      max_slope,
    )
    return max_slope

  return default_slope


def protocol_to_odtc_protocol(
  protocol: "Protocol",
  config: ODTCConfig = ODTCConfig(),
) -> ODTCProtocol:
  """Convert a standard Protocol to ODTCProtocol (kind='method').

  Args:
    protocol: Standard Protocol with stages and steps.
    config: ODTC config for variant, fluid_quantity, slopes, etc.

  Returns:
    ODTCProtocol (kind='method') ready for upload or run. Steps are authoritative;
    stages=[] so the stage view is derived via odtc_protocol_to_protocol(odtc) when needed.
  """

  odtc_steps: List[ODTCStep] = []
  step_number = 1

  # Track previous temperature for slope calculation
  # Start from room temperature - first step needs to ramp from ambient
  prev_temp = 25.0

  for stage_idx, stage in enumerate(protocol.stages):
    stage_start_step = step_number

    for step_idx, step in enumerate(stage.steps):
      # Get the target temperature (use first zone for ODTC single-zone)
      target_temp = step.temperature[0] if step.temperature else 25.0

      # Calculate slope
      slope = _calculate_slope(prev_temp, target_temp, step.rate, config)

      # Get step settings overrides if any
      # Use global step index (across all stages)
      global_step_idx = step_number - 1
      step_setting = config.step_settings.get(global_step_idx, ODTCStepSettings())

      # Create ODTC step with defaults or overrides
      odtc_step = ODTCStep(
        number=step_number,
        slope=step_setting.slope if step_setting.slope is not None else slope,
        plateau_temperature=target_temp,
        plateau_time=step.hold_seconds,
        overshoot_slope1=(
          step_setting.overshoot_slope1 if step_setting.overshoot_slope1 is not None else 0.1
        ),
        overshoot_temperature=(
          step_setting.overshoot_temperature
          if step_setting.overshoot_temperature is not None
          else 0.0
        ),
        overshoot_time=(
          step_setting.overshoot_time if step_setting.overshoot_time is not None else 0.0
        ),
        overshoot_slope2=(
          step_setting.overshoot_slope2 if step_setting.overshoot_slope2 is not None else 0.1
        ),
        goto_number=0,  # Will be set below for loops
        loop_number=0,  # Will be set below for loops
        pid_number=step_setting.pid_number if step_setting.pid_number is not None else 1,
        lid_temp=(
          step_setting.lid_temp if step_setting.lid_temp is not None else config.lid_temperature
        ),
      )

      odtc_steps.append(odtc_step)
      prev_temp = target_temp
      step_number += 1

    # If stage has repeats > 1, add loop on the last step of the stage
    if stage.repeats > 1 and odtc_steps:
      last_step = odtc_steps[-1]
      last_step.goto_number = stage_start_step
      last_step.loop_number = stage.repeats  # LoopNumber = actual repeat count (per loaded_set.xml)

  # Determine start temperatures
  start_block_temp = protocol.stages[0].steps[0].temperature[0] if protocol.stages else 25.0
  start_lid_temp = (
    config.start_lid_temperature
    if config.start_lid_temperature is not None
    else config.lid_temperature
  )

  # Generate timestamp if not already set
  resolved_datetime = config.datetime if config.datetime else generate_odtc_timestamp()

  odtc_protocol = ODTCProtocol(
    kind="method",
    variant=config.variant,
    plate_type=config.plate_type,
    fluid_quantity=config.fluid_quantity,
    post_heating=config.post_heating,
    start_block_temperature=start_block_temp,
    start_lid_temperature=start_lid_temp,
    steps=odtc_steps,
    pid_set=list(config.pid_set),
    creator=config.creator,
    description=config.description,
    datetime=resolved_datetime,
    stages=[],
  )
  if config.name is not None:
    odtc_protocol.name = config.name
    odtc_protocol.is_scratch = False
  return odtc_protocol


def odtc_protocol_to_protocol(odtc_protocol: ODTCProtocol) -> "Protocol":
  """Convert ODTCProtocol to a Protocol view (stages built from steps)."""
  from pylabrobot.thermocycling.standard import Protocol

  if odtc_protocol.kind == "method" and odtc_protocol.steps:
    stages = _build_odtc_stages_from_steps(odtc_protocol.steps)
    return Protocol(stages=cast(List[Stage], stages))
  return Protocol(stages=[])


# =============================================================================
# Loop Analysis and Stage/Step Conversion
# =============================================================================


def _analyze_loop_structure(
  steps: List[ODTCStep],
) -> List[Tuple[int, int, int]]:
  """Analyze loop structure in ODTC steps.

  Args:
    steps: List of ODTCStep objects.

  Returns:
    List of (start_step, end_step, repeat_count) tuples, sorted by end position.
    Step numbers are 1-based as in the XML.
  """
  loops = []
  for step in steps:
    if step.goto_number > 0:
      # LoopNumber in XML is actual repeat count (per loaded_set.xml / firmware doc)
      loops.append((step.goto_number, step.number, step.loop_number))
  return sorted(loops, key=lambda x: x[1])  # Sort by end position


def _build_one_odtc_stage_for_range(
  steps_by_num: Dict[int, ODTCStep],
  loops: List[Tuple[int, int, int]],
  start: int,
  end: int,
  repeats: int,
) -> ODTCStage:
  """Build one ODTCStage for step range [start, end] with repeats; recurse for inner loops."""
  # Loops strictly inside (start, end): contained if start <= s and e <= end and (start,end) != (s,e)
  inner_loops = [
    (s, e, r) for (s, e, r) in loops if start <= s and e <= end and (start, end) != (s, e)
  ]
  inner_loops_sorted = sorted(inner_loops, key=lambda x: x[0])

  if not inner_loops_sorted:
    # Flat: all steps in range are one stage (use ODTCStep directly)
    stage_steps = [steps_by_num[n] for n in range(start, end + 1) if n in steps_by_num]
    return ODTCStage(steps=cast(List[Step], stage_steps), repeats=repeats, inner_stages=None)

  # Nested: partition range into step-only segments and inner loops; interleave steps and inner_stages
  step_nums_in_range = set(range(start, end + 1))
  for is_, ie, _ in inner_loops_sorted:
    for n in range(is_, ie + 1):
      step_nums_in_range.discard(n)
  sorted(step_nums_in_range)

  # Groups: steps before first inner, between inners, after last inner
  step_groups: List[List[int]] = []
  pos = start
  for is_, ie, ir in inner_loops_sorted:
    group = [n for n in range(pos, is_) if n in steps_by_num]
    if group:
      step_groups.append(group)
    pos = ie + 1
  if pos <= end:
    group = [n for n in range(pos, end + 1) if n in steps_by_num]
    if group:
      step_groups.append(group)

  steps_list: List[ODTCStep] = []
  inner_stages_list: List[ODTCStage] = []
  for gi, (is_, ie, ir) in enumerate(inner_loops_sorted):
    if gi < len(step_groups):
      steps_list.extend(steps_by_num[n] for n in step_groups[gi])
    inner_stages_list.append(_build_one_odtc_stage_for_range(steps_by_num, loops, is_, ie, ir))
  if len(step_groups) > len(inner_loops_sorted):
    steps_list.extend(steps_by_num[n] for n in step_groups[len(inner_loops_sorted)])
  return ODTCStage(
    steps=cast(List[Step], steps_list), repeats=repeats, inner_stages=inner_stages_list
  )


def _odtc_stage_to_steps_impl(
  stage: "ODTCStage",
  start_number: int,
) -> Tuple[List[ODTCStep], int]:
  """Convert one ODTCStage to ODTCSteps with step numbers; return (steps, next_number)."""
  inner_stages = stage.inner_stages or []
  out: List[ODTCStep] = []
  num = start_number
  first_step_num = start_number

  for i, step in enumerate(stage.steps):
    # stage.steps are ODTCStep (or Step when from plain Stage); copy and assign number
    if isinstance(step, ODTCStep):
      step_copy = replace(step, number=num)
    else:
      step_copy = ODTCStep.from_step(step, number=num)
    out.append(step_copy)
    num += 1
    if i < len(inner_stages):
      inner_steps, num = _odtc_stage_to_steps_impl(inner_stages[i], num)
      out.extend(inner_steps)

  if stage.repeats > 1 and out:
    out[-1].goto_number = first_step_num
    out[-1].loop_number = stage.repeats
  return (out, num)


def _odtc_stages_to_steps(stages: List["ODTCStage"]) -> List[ODTCStep]:
  """Convert ODTCStage tree to flat List[ODTCStep] with correct step numbers and goto/loop."""
  result: List[ODTCStep] = []
  num = 1
  for stage in stages:
    steps, num = _odtc_stage_to_steps_impl(stage, num)
    result.extend(steps)
  return result


def _build_odtc_stages_from_steps(steps: List[ODTCStep]) -> List[ODTCStage]:
  """Build ODTCStage tree from ODTC steps (handles flat and nested loops).

  Uses _analyze_loop_structure for (start, end, repeat_count). No loops -> one stage
  with all steps, repeats=1. We only emit for top-level loops (loops not contained in
  any other), so outer 1-5 x 30 with inner 2-4 x 5 produces one ODTCStage with inner_stages.
  """
  if not steps:
    return []
  steps_by_num = {s.number: s for s in steps}
  loops = _analyze_loop_structure(steps)
  max_step = max(s.number for s in steps)

  if not loops:
    flat = [steps_by_num[n] for n in range(1, max_step + 1) if n in steps_by_num]
    return [ODTCStage(steps=cast(List[Step], flat), repeats=1, inner_stages=None)]

  def contains(outer: Tuple[int, int, int], inner: Tuple[int, int, int]) -> bool:
    (s, e, _), (s2, e2, _) = outer, inner
    return s <= s2 and e2 <= e and (s, e) != (s2, e2)

  top_level = [L for L in loops if not any(contains(M, L) for M in loops if M != L)]
  top_level.sort(key=lambda x: (x[0], x[1]))
  step_nums_in_top_level = set()
  for s, e, _ in top_level:
    for n in range(s, e + 1):
      step_nums_in_top_level.add(n)

  stages: List[ODTCStage] = []
  i = 1
  while i <= max_step:
    if i not in steps_by_num:
      i += 1
      continue
    if i not in step_nums_in_top_level:
      # Flat run of steps not in any top-level loop (use ODTCStep directly)
      flat_steps: List[ODTCStep] = []
      while i <= max_step and i in steps_by_num and i not in step_nums_in_top_level:
        flat_steps.append(steps_by_num[i])
        i += 1
      if flat_steps:
        stages.append(ODTCStage(steps=cast(List[Step], flat_steps), repeats=1, inner_stages=None))
      continue
    # i is inside some top-level loop; find the loop that ends at the smallest end >= i
    for start, end, repeats in top_level:
      if start <= i <= end:
        stages.append(_build_one_odtc_stage_for_range(steps_by_num, loops, start, end, repeats))
        i = end + 1
        break
    else:
      i += 1

  return stages


def _expand_step_sequence(
  steps: List[ODTCStep],
  loops: List[Tuple[int, int, int]],
) -> List[int]:
  """Return step numbers (1-based) in execution order with loops expanded."""
  if not steps:
    return []
  steps_by_num = {s.number: s for s in steps}
  max_step = max(s.number for s in steps)
  loop_by_end = {end: (start, count) for start, end, count in loops}

  expanded: List[int] = []
  i = 1
  while i <= max_step:
    if i not in steps_by_num:
      i += 1
      continue
    expanded.append(i)
    if i in loop_by_end:
      start, count = loop_by_end[i]
      for _ in range(count - 1):
        for j in range(start, i + 1):
          if j in steps_by_num:
            expanded.append(j)
    i += 1
  return expanded


def odtc_expanded_step_count(odtc_protocol: ODTCProtocol) -> int:
  """Return total step count in execution order (loops expanded). Used for progress display when device does not send it."""
  if not odtc_protocol.steps:
    return 0
  loops = _analyze_loop_structure(odtc_protocol.steps)
  return len(_expand_step_sequence(odtc_protocol.steps, loops))


def odtc_cycle_count(odtc_protocol: ODTCProtocol) -> int:
  """Return cycle count from ODTC loop structure (main/top-level loop repeat count). Used for progress when device does not send it."""
  if not odtc_protocol.steps:
    return 0
  loops = _analyze_loop_structure(odtc_protocol.steps)
  if not loops:
    return 1
  # Top-level loop(s): not contained in any other; take the outermost (largest span) as main cycle count.
  top_level = [
    (start, end, count)
    for (start, end, count) in loops
    if not any((s, e, _) != (start, end, count) and s <= start and end <= e for (s, e, _) in loops)
  ]
  if not top_level:
    return 0
  # Single top-level loop (typical PCR) -> its repeat count; else outermost span's repeat count.
  main = max(top_level, key=lambda x: x[1] - x[0])
  return main[2]


def estimate_method_duration_seconds(odtc_protocol: ODTCProtocol) -> float:
  """Estimate total method duration from steps (ramp + plateau + overshoot, with loops).

  Per ODTC Firmware Command Set: duration is slope time + overshoot time + plateau
  time per step in consideration of the loops. For estimation/tooling only; the ODTC
  backend does not use this for handle lifetime/eta (event-driven).

  Args:
    odtc_protocol: ODTCProtocol (kind='method') with steps and start_block_temperature.

  Returns:
    Estimated duration in seconds.
  """
  if odtc_protocol.kind == "premethod":
    return PREMETHOD_ESTIMATED_DURATION_SECONDS
  if not odtc_protocol.steps:
    return 0.0
  loops = _analyze_loop_structure(odtc_protocol.steps)
  step_nums = _expand_step_sequence(odtc_protocol.steps, loops)
  steps_by_num = {s.number: s for s in odtc_protocol.steps}

  total = 0.0
  prev_temp = odtc_protocol.start_block_temperature
  min_slope = 0.1

  for step_num in step_nums:
    step = steps_by_num[step_num]
    slope = max(abs(step.slope), min_slope)
    ramp_time = abs(step.plateau_temperature - prev_temp) / slope
    total += ramp_time + step.plateau_time + step.overshoot_time
    prev_temp = step.plateau_temperature

  return total


# =============================================================================
# Protocol position from elapsed time (private; used only inside ODTCProgress.from_data_event)
# =============================================================================


def _build_protocol_timeline(
  odtc_protocol: ODTCProtocol,
) -> List[Tuple[float, float, int, int, float, float]]:
  """Build timeline segments for an ODTCProtocol (method or premethod).

  Returns a list of (t_start, t_end, step_index, cycle_index, setpoint_c, plateau_end_t).
  step_index is 0-based within cycle; cycle_index is 0-based.
  plateau_end_t is the time at which the plateau (hold) ends for remaining_hold_s.
  """
  if odtc_protocol.kind == "premethod":
    duration = PREMETHOD_ESTIMATED_DURATION_SECONDS
    setpoint = odtc_protocol.target_block_temperature
    return [(0.0, duration, 0, 0, setpoint, duration)]

  if not odtc_protocol.steps:
    return []

  loops = _analyze_loop_structure(odtc_protocol.steps)
  step_nums = _expand_step_sequence(odtc_protocol.steps, loops)
  steps_by_num = {s.number: s for s in odtc_protocol.steps}
  total_expanded = len(step_nums)
  total_cycles = odtc_cycle_count(odtc_protocol)
  steps_per_cycle = total_expanded // total_cycles if total_cycles > 0 else max(1, total_expanded)

  segments: List[Tuple[float, float, int, int, float, float]] = []
  t = 0.0
  prev_temp = odtc_protocol.start_block_temperature
  min_slope = 0.1

  for flat_index, step_num in enumerate(step_nums):
    step = steps_by_num[step_num]
    slope = max(abs(step.slope), min_slope)
    ramp_time = abs(step.plateau_temperature - prev_temp) / slope
    plateau_end_t = t + ramp_time + step.plateau_time
    segment_end = t + ramp_time + step.plateau_time + step.overshoot_time

    cycle_index = flat_index // steps_per_cycle
    step_index = flat_index % steps_per_cycle
    setpoint = step.plateau_temperature

    segments.append((t, segment_end, step_index, cycle_index, setpoint, plateau_end_t))
    t = segment_end
    prev_temp = step.plateau_temperature

  return segments


def _protocol_position_from_elapsed(
  odtc_protocol: ODTCProtocol, elapsed_s: float
) -> Dict[str, Any]:
  """Compute protocol position (step, cycle, setpoint, remaining hold) from elapsed time.

  Used only inside ODTCProgress.from_data_event. Returns dict with keys:
  step_index, cycle_index, setpoint_c, remaining_hold_s, total_steps, total_cycles.
  """
  if elapsed_s < 0:
    elapsed_s = 0.0

  segments = _build_protocol_timeline(odtc_protocol)
  if not segments:
    total_steps = odtc_expanded_step_count(odtc_protocol) if odtc_protocol.steps else 0
    total_cycles = odtc_cycle_count(odtc_protocol) if odtc_protocol.steps else 1
    return {
      "step_index": 0,
      "cycle_index": 0,
      "setpoint_c": odtc_protocol.start_block_temperature
      if hasattr(odtc_protocol, "start_block_temperature")
      else None,
      "remaining_hold_s": 0.0,
      "total_steps": total_steps,
      "total_cycles": total_cycles,
    }

  if odtc_protocol.kind == "method" and odtc_protocol.steps:
    total_expanded = len(
      _expand_step_sequence(odtc_protocol.steps, _analyze_loop_structure(odtc_protocol.steps))
    )
    total_cycles = odtc_cycle_count(odtc_protocol)
    steps_per_cycle = total_expanded // total_cycles if total_cycles > 0 else total_expanded
  else:
    steps_per_cycle = 1
    total_cycles = 1

  for t_start, t_end, step_index, cycle_index, setpoint_c, plateau_end_t in segments:
    if elapsed_s <= t_end:
      remaining = max(0.0, plateau_end_t - elapsed_s)
      return {
        "step_index": step_index,
        "cycle_index": cycle_index,
        "setpoint_c": setpoint_c,
        "remaining_hold_s": remaining,
        "total_steps": steps_per_cycle,
        "total_cycles": total_cycles,
      }

  (_, _, step_index, cycle_index, setpoint_c, _) = segments[-1]
  return {
    "step_index": step_index,
    "cycle_index": cycle_index,
    "setpoint_c": setpoint_c,
    "remaining_hold_s": 0.0,
    "total_steps": steps_per_cycle,
    "total_cycles": total_cycles,
  }


# =============================================================================
# DataEvent payload parsing (private; used only inside ODTCProgress.from_data_event)
# =============================================================================


def _parse_data_event_series_value(series_elem: Any) -> Optional[float]:
  """Extract last integerValue from a dataSeries element as float."""
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
  """Parse a single DataEvent payload into a dict (elapsed_s, temps, etc.)."""
  data_value = payload.get("dataValue")
  if not data_value or not isinstance(data_value, str):
    raise ValueError(f"DataEvent missing dataValue: {payload}")
  outer = ET.fromstring(data_value)
  any_data = outer.find(".//{*}AnyData") or outer.find(".//AnyData")
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
  current_step_index: Optional[int] = None
  total_step_count: Optional[int] = None
  current_cycle_index: Optional[int] = None
  total_cycle_count: Optional[int] = None
  remaining_hold_s: Optional[float] = None
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
    elif name_id == "Step":
      current_step_index = max(0, int(raw) - 1)
    elif name_id == "Total steps":
      total_step_count = max(0, int(raw))
    elif name_id == "Cycle":
      current_cycle_index = max(0, int(raw) - 1)
    elif name_id == "Total cycles":
      total_cycle_count = max(0, int(raw))
    elif name_id == "Hold remaining" or name_id == "Remaining hold":
      remaining_hold_s = raw / 1000.0 if unit == "ms" else float(raw)
  if current_step_index is None:
    for elem in inner.iter():
      if elem.tag.endswith("experimentStep"):
        seq = elem.get("sequence")
        if seq is not None:
          try:
            current_step_index = max(0, int(seq) - 1)
          except ValueError:
            pass
        break
  return {
    "elapsed_s": elapsed_s,
    "target_temp_c": target_temp_c,
    "current_temp_c": current_temp_c,
    "lid_temp_c": lid_temp_c,
    "current_step_index": current_step_index,
    "total_step_count": total_step_count,
    "current_cycle_index": current_cycle_index,
    "total_cycle_count": total_cycle_count,
    "remaining_hold_s": remaining_hold_s,
  }


# =============================================================================
# Build ODTCProgress from DataEvent
# =============================================================================


def build_progress_from_data_event(
  payload: Dict[str, Any],
  odtc_protocol: Optional[ODTCProtocol] = None,
) -> ODTCProgress:
  """Build ODTCProgress from raw DataEvent payload and optional protocol."""
  parsed = _parse_data_event_payload(payload)
  elapsed_s = parsed["elapsed_s"]
  target_temp_c = parsed.get("target_temp_c")
  current_temp_c = parsed.get("current_temp_c")
  lid_temp_c = parsed.get("lid_temp_c")
  step_idx = parsed.get("current_step_index") or 0
  step_count = parsed.get("total_step_count") or 0
  cycle_idx = parsed.get("current_cycle_index") or 0
  cycle_count = parsed.get("total_cycle_count") or 0
  hold_s = parsed.get("remaining_hold_s") or 0.0

  if odtc_protocol is None:
    return ODTCProgress(
      elapsed_s=elapsed_s,
      target_temp_c=target_temp_c,
      current_temp_c=current_temp_c,
      lid_temp_c=lid_temp_c,
      current_step_index=step_idx,
      total_step_count=step_count,
      current_cycle_index=cycle_idx,
      total_cycle_count=cycle_count,
      remaining_hold_s=hold_s,
      estimated_duration_s=None,
      remaining_duration_s=0.0,
    )

  position = _protocol_position_from_elapsed(odtc_protocol, elapsed_s)
  target = target_temp_c
  if odtc_protocol.kind == "premethod":
    target = odtc_protocol.target_block_temperature
  elif position.get("setpoint_c") is not None and target is None:
    target = position["setpoint_c"]

  if odtc_protocol.kind == "premethod":
    est_s: Optional[float] = PREMETHOD_ESTIMATED_DURATION_SECONDS
  else:
    est_s = estimate_method_duration_seconds(odtc_protocol)
  rem_s = max(0.0, est_s - elapsed_s)

  return ODTCProgress(
    elapsed_s=elapsed_s,
    target_temp_c=target,
    current_temp_c=current_temp_c,
    lid_temp_c=lid_temp_c,
    current_step_index=position["step_index"],
    total_step_count=position.get("total_steps") or 0,
    current_cycle_index=position["cycle_index"],
    total_cycle_count=position.get("total_cycles") or 0,
    remaining_hold_s=position.get("remaining_hold_s") or 0.0,
    estimated_duration_s=est_s,
    remaining_duration_s=rem_s,
  )
