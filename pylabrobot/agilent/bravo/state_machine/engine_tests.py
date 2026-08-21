import asyncio
import unittest

from pylabrobot.agilent.bravo.state_machine.engine import (
  StateMachineEngine,
  StateMachineTask,
  TaskError,
  TaskStatus,
)


class _RecordingTask(StateMachineTask):
  """A task whose steps are supplied by the test."""

  def __init__(self, name, steps):
    super().__init__(name)
    self._steps = steps

  def get_steps(self):
    return self._steps


class StateMachineEngineTests(unittest.IsolatedAsyncioTestCase):
  async def test_successful_task_runs_every_step_in_order(self):
    calls = []

    async def step_a():
      calls.append("a")

    async def step_b():
      calls.append("b")

    task = _RecordingTask("t", [("a", step_a), ("b", step_b)])
    engine = StateMachineEngine()
    await engine.execute(task)

    self.assertEqual(calls, ["a", "b"])
    self.assertEqual(task.status, TaskStatus.COMPLETED)
    self.assertIsNone(engine.current_task)
    self.assertFalse(engine.is_busy)

  async def test_step_completion_and_task_completion_handlers_fire(self):
    step_events = []
    completed = []

    async def step_a():
      pass

    task = _RecordingTask("t", [("a", step_a)])
    engine = StateMachineEngine()
    engine.set_step_handler(lambda task_name, step_name: step_events.append((task_name, step_name)))
    engine.set_completion_handler(lambda t: completed.append(t))
    await engine.execute(task)

    self.assertEqual(step_events, [("t", "a")])
    self.assertEqual(completed, [task])

  async def test_failure_without_error_handler_raises(self):
    async def failing():
      raise RuntimeError("boom")

    task = _RecordingTask("t", [("a", failing)])
    engine = StateMachineEngine()
    with self.assertRaises(RuntimeError):
      await engine.execute(task)
    self.assertEqual(task.status, TaskStatus.FAILED)

  async def test_abort_stops_the_task(self):
    async def failing():
      raise RuntimeError("boom")

    task = _RecordingTask("t", [("a", failing)])
    engine = StateMachineEngine()
    engine.set_error_handler(lambda err: None)

    async def resolve_soon():
      # Give execute() a chance to reach the wait point.
      while not engine.awaiting_error_action:
        await asyncio.sleep(0)
      engine.abort()

    await asyncio.gather(engine.execute(task), resolve_soon())
    self.assertEqual(task.status, TaskStatus.ABORTED)
    self.assertIsNone(engine.current_task)

  async def test_retry_reruns_the_failed_step(self):
    attempts = {"n": 0}

    async def flaky():
      attempts["n"] += 1
      if attempts["n"] < 2:
        raise RuntimeError("transient")

    task = _RecordingTask("t", [("a", flaky)])
    engine = StateMachineEngine()
    engine.set_error_handler(lambda err: None)

    async def resolve_soon():
      while not engine.awaiting_error_action:
        await asyncio.sleep(0)
      engine.retry()

    await asyncio.gather(engine.execute(task), resolve_soon())
    self.assertEqual(attempts["n"], 2)
    self.assertEqual(task.status, TaskStatus.COMPLETED)

  async def test_ignore_skips_the_failed_step_and_continues(self):
    calls = []

    async def failing():
      raise RuntimeError("boom")

    async def step_b():
      calls.append("b")

    task = _RecordingTask("t", [("a", failing), ("b", step_b)])
    engine = StateMachineEngine()
    engine.set_error_handler(lambda err: None)

    async def resolve_soon():
      while not engine.awaiting_error_action:
        await asyncio.sleep(0)
      engine.ignore()

    await asyncio.gather(engine.execute(task), resolve_soon())
    self.assertEqual(calls, ["b"])
    self.assertEqual(task.status, TaskStatus.COMPLETED)

  async def test_generic_prompt_is_synthesized_when_task_sets_none(self):
    async def failing():
      raise RuntimeError("boom")

    task = _RecordingTask("t", [("a", failing)])
    engine = StateMachineEngine()
    errors: list[TaskError] = []
    engine.set_error_handler(errors.append)
    captured_payload = {}

    async def resolve_soon():
      while not engine.awaiting_error_action:
        await asyncio.sleep(0)
      # Capture the payload while the task is still FAILED — status_payload()
      # only surfaces the prompt in that state.
      captured_payload.update(task.status_payload())
      engine.abort()

    await asyncio.gather(engine.execute(task), resolve_soon())
    self.assertEqual(captured_payload["operator_prompt"]["kind"], "step_failed")
    self.assertEqual(captured_payload["operator_prompt"]["step"], "a")
    self.assertEqual(errors[0].step_name, "a")

  async def test_task_specific_prompt_is_preserved(self):
    class PromptingTask(StateMachineTask):
      def __init__(self):
        super().__init__("t")

      def get_steps(self):
        return [("a", self._fail)]

      async def _fail(self):
        self._operator_prompt = {"kind": "custom", "choices": ["retry", "ignore", "abort"]}
        raise RuntimeError("boom")

    task = PromptingTask()
    engine = StateMachineEngine()
    engine.set_error_handler(lambda err: None)
    captured_payload = {}

    async def resolve_soon():
      while not engine.awaiting_error_action:
        await asyncio.sleep(0)
      captured_payload.update(task.status_payload())
      engine.abort()

    await asyncio.gather(engine.execute(task), resolve_soon())
    self.assertEqual(captured_payload["operator_prompt"]["kind"], "custom")

  async def test_on_error_action_hook_failure_aborts_and_propagates(self):
    class BrokenHookTask(StateMachineTask):
      def __init__(self):
        super().__init__("t")

      def get_steps(self):
        return [("a", self._fail)]

      async def _fail(self):
        raise RuntimeError("boom")

      def on_error_action(self, action):
        raise ValueError("hook exploded")

    task = BrokenHookTask()
    engine = StateMachineEngine()
    engine.set_error_handler(lambda err: None)

    async def resolve_soon():
      while not engine.awaiting_error_action:
        await asyncio.sleep(0)
      engine.abort()

    with self.assertRaises(ValueError):
      await asyncio.gather(engine.execute(task), resolve_soon())
    self.assertEqual(task.status, TaskStatus.ABORTED)

  async def test_resolve_error_returns_false_when_not_awaiting(self):
    engine = StateMachineEngine()
    self.assertFalse(engine.abort())
    self.assertFalse(engine.retry())
    self.assertFalse(engine.ignore())

  async def test_second_resolution_is_rejected(self):
    async def failing():
      raise RuntimeError("boom")

    task = _RecordingTask("t", [("a", failing)])
    engine = StateMachineEngine()
    engine.set_error_handler(lambda err: None)
    results = []

    async def resolve_twice():
      while not engine.awaiting_error_action:
        await asyncio.sleep(0)
      results.append(engine.abort())
      results.append(engine.retry())

    await asyncio.gather(engine.execute(task), resolve_twice())
    self.assertEqual(results, [True, False])


if __name__ == "__main__":
  unittest.main()
