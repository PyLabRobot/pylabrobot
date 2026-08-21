"""Shared golden-frame test machinery for the state-machine task layer.

``testdata/task_golden_frames.json`` holds, for each named scenario, the
ordered sequence of ``{"step", "method", "args"}`` calls a reference
implementation of the state-machine tasks issues to a
:class:`~..controllers.simulation.SimulationController` while running
through :class:`~.engine.StateMachineEngine`. Every golden-frame test module
in this package drives the same task construction through an equivalent
recording :class:`~..controllers.simulation.SimulationController` and
asserts the captured sequence matches the fixture exactly, so a change in
step order, a dropped safe-Z retract, or an altered clearance constant
fails immediately -- a unit test on an individual step method would not
catch a wrong position inside a multi-step sequence the way a full
recorded comparison does.

This module holds the recorder, task/engine driver, and fixture-comparison
base class every family's golden-frame test module reuses.
"""

from __future__ import annotations

import asyncio
import json
import unittest
from dataclasses import asdict, is_dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Callable

from ..config import BravoMachineConfig
from ..controllers.simulation import SimulationController
from ..deck.teachpoints import Teachpoints
from ..types import ALL_AXES, GripperDetectionState
from .engine import ErrorAction, StateMachineEngine, StateMachineTask

_GOLDEN_PATH = Path(__file__).parent / "testdata" / "task_golden_frames.json"
with open(_GOLDEN_PATH) as _f:
  GOLDEN: dict = json.load(_f)


def _jsonable(value: Any) -> Any:
  """Recursively convert a captured call argument to a JSON-comparable value."""
  if is_dataclass(value) and not isinstance(value, type):
    return {k: _jsonable(v) for k, v in asdict(value).items()}
  if isinstance(value, Enum):
    return value.name
  if isinstance(value, (list, tuple)):
    return [_jsonable(v) for v in value]
  if isinstance(value, dict):
    return {str(k): _jsonable(v) for k, v in value.items()}
  return value


class RecordingSimulationController(SimulationController):
  """A :class:`SimulationController` that logs every interface call in order.

  ``self.calls`` accumulates one ``{"step", "method", "args"}`` dict per
  call, in call order, for the controller's lifetime. ``current_step`` is
  set by the test driver before invoking each state-machine step, so every
  call a step makes is tagged with the step that made it.
  """

  def __init__(self, *args: Any, **kwargs: Any) -> None:
    super().__init__(*args, **kwargs)
    self.calls: list = []
    self.current_step: str = "<setup>"

  def _record(self, method: str, **kwargs: Any) -> None:
    self.calls.append(
      {
        "step": self.current_step,
        "method": method,
        "args": {k: _jsonable(v) for k, v in kwargs.items()},
      }
    )

  def move(self, moves, wait=True, timeout=30.0):
    self._record("move", moves=moves, wait=wait, timeout=timeout)
    return super().move(moves, wait=wait, timeout=timeout)

  def home_axes(self, axes, *, force=False):
    self._record("home_axes", axes=axes, force=force)
    return super().home_axes(axes, force=force)

  def jog(self, params):
    self._record("jog", params=params)
    return super().jog(params)

  def enable_motor(self, axis):
    self._record("enable_motor", axis=axis)
    return super().enable_motor(axis)

  def disable_motor(self, axis):
    self._record("disable_motor", axis=axis)
    return super().disable_motor(axis)

  def reset_faults(self, axes):
    self._record("reset_faults", axes=axes)
    return super().reset_faults(axes)

  def query_state(self):
    self._record("query_state")
    return super().query_state()

  def is_go_button_pressed(self):
    self._record("is_go_button_pressed")
    return super().is_go_button_pressed()

  def clear_go_button(self):
    self._record("clear_go_button")
    return super().clear_go_button()

  def set_light(self, command):
    self._record("set_light", command=command)
    return super().set_light(command)

  def clear_lights(self):
    self._record("clear_lights")
    return super().clear_lights()

  def read_head_adc(self):
    self._record("read_head_adc")
    return super().read_head_adc()

  def detect_smart_head(self):
    self._record("detect_smart_head")
    return super().detect_smart_head()

  def read_smart_head_type(self):
    self._record("read_smart_head_type")
    return super().read_smart_head_type()

  def detect_gripper(self):
    self._record("detect_gripper")
    return super().detect_gripper()

  def grip(self, speed, position, grip_lid=False):
    self._record("grip", speed=speed, position=position, grip_lid=grip_lid)
    return super().grip(speed, position, grip_lid=grip_lid)

  def open_gripper(self, position=None):
    self._record("open_gripper", position=position)
    return super().open_gripper(position)

  def is_plate_in_gripper(self):
    self._record("is_plate_in_gripper")
    return super().is_plate_in_gripper()

  def read_plate_sensor(self, transient=0.0):
    self._record("read_plate_sensor", transient=transient)
    return super().read_plate_sensor(transient=transient)

  def scan_stack_with_gripper(self, *, start_zg, end_zg, speed, transient=0.0):
    self._record(
      "scan_stack_with_gripper", start_zg=start_zg, end_zg=end_zg, speed=speed, transient=transient
    )
    return super().scan_stack_with_gripper(
      start_zg=start_zg, end_zg=end_zg, speed=speed, transient=transient
    )

  def send_command(self, command_id, data=b"", timeout=2.0):
    self._record("send_command", command_id=command_id, data=data.hex(), timeout=timeout)
    return super().send_command(command_id, data=data, timeout=timeout)

  def ping(self):
    self._record("ping")
    return super().ping()

  def get_firmware_version(self):
    self._record("get_firmware_version")
    return super().get_firmware_version()

  def get_position(self, axis):
    self._record("get_position", axis=axis)
    return super().get_position(axis)

  def is_axis_homed(self, axis):
    self._record("is_axis_homed", axis=axis)
    return super().is_axis_homed(axis)

  def get_park_position(self, axis):
    self._record("get_park_position", axis=axis)
    return super().get_park_position(axis)

  # get_head_type() and ul_to_mm() are deliberately not recorded here: they
  # are pure internal queries with no wire or hardware effect, unlike every
  # other override above, which corresponds to an actual command or move.


def new_controller(
  *, all_homed: bool = True, gripper: bool = True
) -> RecordingSimulationController:
  """Build a recording controller with every axis in a known homed state."""
  ctrl = RecordingSimulationController()
  if not gripper:
    ctrl.set_gripper_state(GripperDetectionState.NOT_DETECTED)
  axes: list = list(ALL_AXES) if gripper else [a for a in ALL_AXES if a not in ("g", "zg")]
  for axis in axes:
    ctrl._axes[axis].homed = all_homed
    if not all_homed:
      ctrl._axes[axis].position = 0.0
  return ctrl


def new_config(gripper: bool = True) -> BravoMachineConfig:
  config = BravoMachineConfig()
  if not gripper:
    config.axes = {k: v for k, v in config.axes.items() if k not in ("g", "zg")}
  return config


def new_teachpoints() -> Teachpoints:
  teachpoints = Teachpoints()
  teachpoints.set_default_teachpoints("96_d_70")
  return teachpoints


def _wrap_steps_with_current_step(
  task: StateMachineTask, ctrl: RecordingSimulationController
) -> None:
  """Tag every call a step makes with that step's name, for fixture grouping."""
  original = task.get_steps

  def wrapped():
    out = []
    for name, fn in original():

      def make(name=name, fn=fn):
        async def _runner():
          ctrl.current_step = name
          return await fn()

        return _runner

      out.append((name, make()))
    return out

  task.get_steps = wrapped  # type: ignore[method-assign]


def default_choice(task: StateMachineTask) -> ErrorAction:
  """Resolve InitializeTask's W-axis prompt with RETRY; anything else aborts.

  RETRY is chosen (rather than IGNORE) so the fixture captures the fuller
  sequence that actually homes W, rather than the shorter skip-W path.
  """
  prompt = task._operator_prompt or {}
  kind = prompt.get("kind")
  if kind == "initialize_home_w_axis":
    return ErrorAction.RETRY
  raise AssertionError(f"unexpected operator prompt during golden-frame run: {kind}")


async def run_task(
  task: StateMachineTask,
  ctrl: RecordingSimulationController,
  choice_fn: Callable[[StateMachineTask], ErrorAction] = default_choice,
) -> dict:
  """Run a task to completion through a real engine, returning its captured calls."""
  _wrap_steps_with_current_step(task, ctrl)
  engine = StateMachineEngine()
  errors: list = []
  engine.set_error_handler(errors.append)
  exec_task = asyncio.ensure_future(engine.execute(task))
  while not exec_task.done():
    if engine.awaiting_error_action:
      engine.resolve_error(choice_fn(task))
    await asyncio.sleep(0)
  await exec_task
  return {"status": task.status.name, "calls": ctrl.calls}


class GoldenFrameTestCase(unittest.IsolatedAsyncioTestCase):
  """Base class: asserts a captured call list against the checked-in fixture."""

  def assert_matches_golden(self, scenario: str, result: dict) -> None:
    expected = GOLDEN[scenario]
    self.assertEqual(result["status"], expected["status"], f"{scenario}: status diverges")
    self.assertEqual(result["calls"], expected["calls"], f"{scenario}: captured calls diverge")
