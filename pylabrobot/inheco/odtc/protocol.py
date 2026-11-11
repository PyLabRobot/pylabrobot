"""ODTC temperature profiles and compilation to the instrument's method format."""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field, replace
from typing import Literal

logger = logging.getLogger(__name__)

ODTCVariant = Literal[96, 384]
DEFAULT_PLATE_TYPE = 0
DEFAULT_POST_HEATING = True
DEFAULT_APPLY_OVERSHOOT = False


@dataclass(frozen=True)
class ODTCOvershoot:
  """A temperature excursion relative to a plateau, in degrees C and seconds.

  `temperature` is the positive excursion magnitude, not an absolute setpoint. The
  controller chooses its direction from the temperature transition.
  """

  temperature: float
  duration: float = 0
  return_rate: float = 2.2
  approach_rate: float | None = None


@dataclass(frozen=True)
class ODTCStep:
  """Hold a block temperature for `duration` seconds, with optional ramp and lid settings."""

  temperature: float
  duration: float
  ramp_rate: float | None = None
  lid_temperature: float | None = None
  overshoot: ODTCOvershoot | None = None
  pid_number: int = 1


@dataclass(frozen=True)
class ODTCStage:
  """An ordered sequence of steps or nested stages, repeated a fixed number of times."""

  steps: list[ODTCStep | ODTCStage]
  repeats: int = 1


@dataclass(frozen=True)
class ODTCProtocol:
  """A temperature profile; an omitted name selects the driver's working method slot."""

  stages: list[ODTCStage]
  name: str | None = None


@dataclass(frozen=True)
class ODTCPID:
  """Device PID parameters; defaults match the firmware manual's example PID set."""

  number: int = 1
  p_heating: float = 60
  p_cooling: float = 80
  i_heating: float = 250
  i_cooling: float = 100
  d_heating: float = 10
  d_cooling: float = 10
  p_lid: float = 100
  i_lid: float = 70


@dataclass(frozen=True)
class ODTCMethodStep:
  """A native step including its backward jump and additional repeat count."""

  number: int
  temperature: float
  duration: float
  ramp_rate: float
  lid_temperature: float
  overshoot: ODTCOvershoot
  goto: int = 0
  loops: int = 0
  pid_number: int = 1


@dataclass(frozen=True)
class ODTCMethod:
  """A compiled or downloaded method with explicit device settings and native steps."""

  name: str
  variant: ODTCVariant
  plate_type: int
  fluid_quantity: int
  post_heating: bool
  start_temperature: float
  start_lid_temperature: float
  steps: list[ODTCMethodStep]
  pid_set: list[ODTCPID]
  creator: str | None = None
  description: str | None = None
  date_time: str | None = None


@dataclass(frozen=True)
class ODTCPreMethod:
  """A stored preparation method that establishes block and lid temperatures."""

  name: str
  temperature: float
  lid_temperature: float
  creator: str | None = None
  description: str | None = None
  date_time: str | None = None


@dataclass(frozen=True)
class ODTCMethodSet:
  """Methods and premethods returned by the instrument or uploaded together."""

  methods: list[ODTCMethod] = field(default_factory=list)
  premethods: list[ODTCPreMethod] = field(default_factory=list)


@dataclass(frozen=True)
class ODTCTemperatures:
  """Temperature sensor values in degrees C, plus the device's timestamp."""

  mount: float
  mount_monitor: float
  lid: float
  lid_monitor: float
  ambient: float
  pcb: float
  heatsink: float
  heatsink_tec: float
  timestamp: str | None = None


@dataclass(frozen=True)
class ODTCProgress:
  """The latest device-reported sample; elapsed time is in seconds, temperatures in C."""

  elapsed: float
  target_temperature: float
  current_temperature: float
  lid_temperature: float


def validate_variant(variant: int) -> ODTCVariant:
  """Accept the two public well-count variants, without wire-code aliases."""
  if variant == 96:
    return 96
  if variant == 384:
    return 384
  raise ValueError("variant must be 96 or 384")


def validate_temperature(temperature: float, *, lid: bool, variant: ODTCVariant) -> None:
  """Check the supported block or lid temperature range."""
  low, high = (30, 110 if variant == 96 else 115) if lid else (4, 99)
  if not math.isfinite(temperature) or not low <= temperature <= high:
    raise ValueError(f"{'lid' if lid else 'block'} temperature must be between {low} and {high}")


def volume_to_fluid_quantity(volume: float) -> int:
  """Convert maximum sample volume in microliters to a native compensation range."""
  if not math.isfinite(volume) or not 0 < volume <= 100:
    raise ValueError("volume must be greater than zero and at most 100 microliters")
  return 0 if volume < 30 else 1 if volume < 75 else 2


def calculate_overshoot(
  temperature: float, previous_temperature: float, rate: float, duration: float, fluid_quantity: int
) -> ODTCOvershoot:
  """Compute the optional empirical volume compensation; hardware validation is required."""
  heating = temperature > previous_temperature
  delta = abs(temperature - previous_temperature)
  if duration == 0 or rate <= 0.5 or temperature <= 35 or delta <= (5 if heating else 10):
    return ODTCOvershoot(0, approach_rate=rate)
  heat = ((5.1144, -6.6037), (19.469, -20.875), (22.829, -13.278))
  cool = ((4.0941, -6.1247), (4.9773, -8.0866), (9.0513, -17.015))
  if fluid_quantity not in (0, 1, 2):
    return ODTCOvershoot(0, approach_rate=rate)
  a, b = (heat if heating else cool)[fluid_quantity]
  energy = a * math.log(min(delta, 60)) + b
  if energy <= 0:
    return ODTCOvershoot(0, approach_rate=rate)
  excursion = math.sqrt(2 * energy / (1 / rate + 1 / 2.2))
  hold = 0.0
  if heating and temperature + excursion > 102:
    excursion = 102 - temperature
    triangle_time = excursion / rate + excursion / 2.2
    hold = (energy - 0.5 * triangle_time * excursion) / excursion
  return ODTCOvershoot(round(excursion, 1), round(hold, 1), approach_rate=rate)


def _first_step(stage: ODTCStage) -> ODTCStep:
  """Find the first executable step without inventing a start temperature."""
  if not stage.steps:
    raise ValueError("Stages must not be empty")
  first = stage.steps[0]
  return _first_step(first) if isinstance(first, ODTCStage) else first


def _validate_method_step(
  step: ODTCMethodStep, variant: ODTCVariant, pid_numbers: set[int]
) -> None:
  """Check a native step before uploading it."""
  validate_temperature(step.temperature, lid=False, variant=variant)
  validate_temperature(step.lid_temperature, lid=True, variant=variant)
  if not math.isfinite(step.duration) or step.duration < 0:
    raise ValueError("Step duration must be finite and nonnegative")
  maximum_rate = 4.4 if variant == 96 else 5.0
  if not math.isfinite(step.ramp_rate) or not 0.1 <= step.ramp_rate <= maximum_rate:
    raise ValueError(f"Ramp rate must be between 0.1 and {maximum_rate}")
  overshoot = step.overshoot
  for value in (overshoot.temperature, overshoot.duration):
    if not math.isfinite(value) or value < 0:
      raise ValueError("Overshoot magnitude and duration must be finite and nonnegative")
  for rate in (overshoot.return_rate, overshoot.approach_rate):
    if rate is not None and (not math.isfinite(rate) or not 0.1 <= rate <= maximum_rate):
      raise ValueError("Invalid overshoot ramp rate")
  if step.pid_number not in pid_numbers:
    raise ValueError(f"Step references undefined PID {step.pid_number}")


def _incoming_temperatures(steps: list[ODTCMethodStep], start: float) -> list[set[float]]:
  """Collect the temperatures that can precede each step, including loop-back edges."""
  incoming = [{start}] + [{step.temperature} for step in steps[:-1]]
  for step in steps:
    if step.goto:
      incoming[step.goto - 1].add(step.temperature)
  return incoming


def validate_method(method: ODTCMethod) -> None:
  """Validate a compiled or edited native method before device communication."""
  validate_variant(method.variant)
  if method.name == "plr_currentPreMethod":
    raise ValueError("plr_currentPreMethod is reserved for preparation")
  if not method.name.strip():
    raise ValueError("Method name must not be empty")
  if method.plate_type not in ((0,) if method.variant == 96 else (0, 2)):
    raise ValueError("Unsupported plate_type for this variant")
  if method.fluid_quantity not in (-1, 0, 1, 2):
    raise ValueError("Invalid fluid quantity code")
  validate_temperature(method.start_temperature, lid=False, variant=method.variant)
  validate_temperature(method.start_lid_temperature, lid=True, variant=method.variant)
  if not 1 <= len(method.steps) <= 100:
    raise ValueError("ODTC methods require between 1 and 100 native steps")
  pid_numbers = {pid.number for pid in method.pid_set}
  if len(pid_numbers) != len(method.pid_set) or not 1 <= len(pid_numbers) <= 9:
    raise ValueError("A method needs 1 to 9 uniquely numbered PID sets")
  for pid in method.pid_set:
    if not 1 <= pid.number <= 9:
      raise ValueError("PID number must be between 1 and 9")
    values = (
      pid.p_heating,
      pid.p_cooling,
      pid.i_heating,
      pid.i_cooling,
      pid.d_heating,
      pid.d_cooling,
      pid.p_lid,
      pid.i_lid,
    )
    if any(not math.isfinite(value) or value < 0 for value in values):
      raise ValueError("PID coefficients must be finite and nonnegative")
  for number, step in enumerate(method.steps, 1):
    if any(
      not isinstance(value, int) or isinstance(value, bool)
      for value in (step.number, step.goto, step.loops, step.pid_number)
    ):
      raise ValueError("Step numbers, loop counts, and PID numbers must be integers")
    if step.number != number:
      raise ValueError("Method steps must be numbered consecutively from 1")
    if step.goto < 0 or step.goto > number or step.loops < 0:
      raise ValueError("Invalid backward loop")
    if bool(step.goto) != bool(step.loops):
      raise ValueError("A loop needs both a target and an additional repeat count")
    _validate_method_step(step, method.variant, pid_numbers)
  for step, incoming in zip(
    method.steps, _incoming_temperatures(method.steps, method.start_temperature)
  ):
    if any(previous > step.temperature for previous in incoming) and step.ramp_rate > 2.2:
      raise ValueError("Cooling transitions, including loop entries, cannot exceed 2.2 degrees C/s")


def compile_protocol(
  protocol: ODTCProtocol,
  *,
  variant: ODTCVariant,
  volume: float,
  lid_temperature: float,
  plate_type: int = DEFAULT_PLATE_TYPE,
  post_heating: bool = DEFAULT_POST_HEATING,
  apply_overshoot: bool = DEFAULT_APPLY_OVERSHOOT,
  pid_set: list[ODTCPID] | None = None,
  name: str | None = None,
) -> ODTCMethod:
  """Compile a profile without I/O, retaining loops and explicit per-step settings.

  Automatic overshoot is opt-in because the empirical coefficients have not been
  validated for this implementation. Explicit excursions are always retained.
  """
  validate_variant(variant)
  quantity = volume_to_fluid_quantity(volume)
  if not protocol.stages:
    raise ValueError("Protocol must contain at least one stage")
  if protocol.name in ("plr_currentProtocol", "plr_currentPreMethod"):
    raise ValueError("Names beginning plr_current are reserved for working methods")
  first = _first_step(protocol.stages[0])
  start_lid = first.lid_temperature if first.lid_temperature is not None else lid_temperature
  if apply_overshoot:
    logger.warning(
      "Automatic ODTC overshoot requires validation on the selected variant and labware"
    )
  result: list[ODTCMethodStep] = []
  source_steps: list[ODTCStep] = []

  def append_stage(stage: ODTCStage) -> None:
    """Compile a stage, expanding coincident loop endpoints when necessary."""
    if not isinstance(stage.repeats, int) or isinstance(stage.repeats, bool) or stage.repeats < 1:
      raise ValueError("Stage repeats must be a positive integer")
    if not stage.steps:
      raise ValueError("Stages must not be empty")
    start = len(result)
    for item in stage.steps:
      if isinstance(item, ODTCStage):
        append_stage(item)
        continue
      validate_temperature(item.temperature, lid=False, variant=variant)
      rate = item.ramp_rate if item.ramp_rate is not None else (4.4 if variant == 96 else 5.0)
      overshoot = item.overshoot or ODTCOvershoot(0)
      result.append(
        ODTCMethodStep(
          number=len(result) + 1,
          temperature=item.temperature,
          duration=item.duration,
          ramp_rate=rate,
          lid_temperature=lid_temperature if item.lid_temperature is None else item.lid_temperature,
          overshoot=overshoot,
          pid_number=item.pid_number,
        )
      )
      source_steps.append(item)
      if len(result) > 100:
        raise ValueError("Compiled method exceeds the ODTC's 100-step limit")
    if stage.repeats > 1:
      if result[-1].goto:
        block = result[start:]
        source_block = source_steps[start:]
        if len(result) + len(block) * (stage.repeats - 1) > 100:
          raise ValueError("Nested loops sharing an endpoint exceed the ODTC's 100-step limit")
        for _ in range(stage.repeats - 1):
          offset = len(result) - start
          result.extend(
            replace(step, number=step.number + offset, goto=step.goto + offset if step.goto else 0)
            for step in block
          )
          source_steps.extend(source_block)
      else:
        result[-1] = replace(result[-1], goto=start + 1, loops=stage.repeats - 1)

  for stage in protocol.stages:
    append_stage(stage)
  for index, (step, source, incoming) in enumerate(
    zip(result, source_steps, _incoming_temperatures(result, first.temperature))
  ):
    maximum_rate = (
      2.2
      if any(previous > step.temperature for previous in incoming)
      else (4.4 if variant == 96 else 5.0)
    )
    rate = maximum_rate if source.ramp_rate is None else source.ramp_rate
    if rate > maximum_rate:
      raise ValueError("Explicit ramp rate exceeds a loop-entry transition limit")
    overshoot = source.overshoot
    if overshoot is None:
      if apply_overshoot and len(incoming) > 1:
        raise ValueError(
          "Automatic overshoot is ambiguous at this loop entry; supply explicit overshoot"
        )
      overshoot = (
        calculate_overshoot(step.temperature, next(iter(incoming)), rate, step.duration, quantity)
        if apply_overshoot
        else ODTCOvershoot(0, approach_rate=rate)
      )
    result[index] = replace(step, ramp_rate=rate, overshoot=overshoot)
  method = ODTCMethod(
    name=name or protocol.name or "plr_currentProtocol",
    variant=variant,
    plate_type=plate_type,
    fluid_quantity=quantity,
    post_heating=post_heating,
    start_temperature=first.temperature,
    start_lid_temperature=start_lid,
    steps=result,
    pid_set=list(pid_set) if pid_set is not None else [ODTCPID()],
  )
  validate_method(method)
  return method
