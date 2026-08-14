import asyncio
import unittest

from pylabrobot.manual_operator import (
  ConsoleOperatorActionProvider,
  ManualOperator,
  OperatorActionCancelledError,
  OperatorActionFailedError,
  OperatorActionRequest,
  OperatorActionResult,
)


class RecordingProvider:
  def __init__(self, result: OperatorActionResult):
    self.result = result
    self.requests = []

  async def request(self, action: OperatorActionRequest) -> OperatorActionResult:
    self.requests.append(action)
    return self.result


class TestManualOperator(unittest.IsolatedAsyncioTestCase):
  async def test_perform_sends_structured_request_and_returns_completion(self):
    provider = RecordingProvider(OperatorActionResult.completed(confirmed_by="operator-1"))
    operator = ManualOperator(provider, name="cell_operator")

    result = await operator.perform(
      action="centrifuge.spin",
      title="Spin sample plate",
      instructions="Spin plate_1 at 300 x g for 180 seconds.",
      confirmation_text="Confirm spin completed",
      details={"relative_centrifugal_force_g": 300, "duration_seconds": 180},
    )

    self.assertEqual(result.confirmed_by, "operator-1")
    self.assertEqual(len(provider.requests), 1)
    request = provider.requests[0]
    self.assertEqual(request.operator_name, "cell_operator")
    self.assertEqual(request.action, "centrifuge.spin")
    self.assertEqual(request.details["duration_seconds"], 180)

  async def test_cancelled_result_raises_specific_error(self):
    provider = RecordingProvider(OperatorActionResult.cancelled(message="Protocol stopped"))
    operator = ManualOperator(provider)

    with self.assertRaisesRegex(OperatorActionCancelledError, "Protocol stopped"):
      await operator.perform(action="inspect", title="Inspect plate", instructions="Inspect it.")

  async def test_failed_result_raises_specific_error(self):
    provider = RecordingProvider(OperatorActionResult.failed(message="Plate was damaged"))
    operator = ManualOperator(provider)

    with self.assertRaisesRegex(OperatorActionFailedError, "Plate was damaged"):
      await operator.perform(action="inspect", title="Inspect plate", instructions="Inspect it.")

  async def test_provider_exception_propagates(self):
    class FailingProvider:
      async def request(self, action: OperatorActionRequest) -> OperatorActionResult:
        del action
        raise ConnectionError("provider disconnected")

    with self.assertRaisesRegex(ConnectionError, "provider disconnected"):
      await ManualOperator(FailingProvider()).perform(
        action="inspect", title="Inspect plate", instructions="Inspect it."
      )

  async def test_request_copies_details(self):
    details = {"duration_seconds": 60}
    request = OperatorActionRequest(
      operator_name="operator",
      action="centrifuge.spin",
      title="Spin",
      instructions="Spin the plate.",
      details=details,
    )

    details["duration_seconds"] = 120

    self.assertEqual(request.details["duration_seconds"], 60)


class TestConsoleOperatorActionProvider(unittest.IsolatedAsyncioTestCase):
  async def test_enter_completes_action_without_blocking_event_loop(self):
    output = []
    provider = ConsoleOperatorActionProvider(
      input_fn=lambda prompt: output.append(prompt) or "",
      output_fn=output.append,
    )
    request = OperatorActionRequest(
      operator_name="operator",
      action="inspect",
      title="Inspect plate",
      instructions="Check that the plate is seated.",
    )
    event_loop_progressed = asyncio.Event()

    async def mark_progress() -> None:
      await asyncio.sleep(0)
      event_loop_progressed.set()

    progress_task = asyncio.create_task(mark_progress())
    result = await provider.request(request)
    await progress_task

    self.assertTrue(event_loop_progressed.is_set())
    self.assertEqual(result, OperatorActionResult.completed())
    self.assertIn("Inspect plate", output[0])
    self.assertEqual(output[1], "Confirm action completed: ")
