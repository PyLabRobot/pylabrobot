import asyncio
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from pylabrobot.io.command_line import (
  CommandLineCommand,
  CommandLineResult,
  CommandLineTransport,
  CommandLineValidator,
)
from pylabrobot.io.errors import ValidationError


class CommandLineTransportTests(unittest.IsolatedAsyncioTestCase):
  """Tests for command-line I/O without starting a real process."""

  def make_transport(self) -> CommandLineTransport:
    """Return a command-line transport configured for the test executable."""
    return CommandLineTransport(
      human_readable_device_name="Test scanner",
      executable="scanner",
    )

  async def setup_transport(self, transport: CommandLineTransport) -> None:
    """Resolve the test executable without reading the host ``PATH``."""
    with patch("pylabrobot.io.command_line.shutil.which", return_value="/usr/bin/scanner"):
      await transport.setup()

  async def test_setup_resolves_executable(self) -> None:
    transport = self.make_transport()

    await self.setup_transport(transport)

    self.assertTrue(transport.is_setup)
    self.assertEqual(transport.executable, "/usr/bin/scanner")

  async def test_setup_raises_when_executable_is_missing(self) -> None:
    transport = self.make_transport()

    with (
      patch("pylabrobot.io.command_line.shutil.which", return_value=None),
      self.assertRaisesRegex(FileNotFoundError, "scanner"),
    ):
      await transport.setup()

    self.assertFalse(transport.is_setup)

  async def test_run_requires_setup(self) -> None:
    with self.assertRaisesRegex(RuntimeError, "call setup"):
      await self.make_transport().run(["--acquire"], timeout=1.0)

  async def test_run_returns_and_captures_decoded_process_result(self) -> None:
    transport = self.make_transport()
    await self.setup_transport(transport)
    process = MagicMock()
    process.returncode = 0
    process.communicate = AsyncMock(return_value=(b"output\n", b"warning\n"))

    with (
      patch(
        "pylabrobot.io.command_line.asyncio.create_subprocess_exec",
        AsyncMock(return_value=process),
      ) as create_process,
      patch("pylabrobot.io.command_line.capturer.record") as record,
    ):
      result = await transport.run(["--acquire"], timeout=2.0)

    create_process.assert_awaited_once_with(
      "/usr/bin/scanner",
      "--acquire",
      stdout=asyncio.subprocess.PIPE,
      stderr=asyncio.subprocess.PIPE,
    )
    self.assertEqual(result.returncode, 0)
    self.assertEqual(result.stdout, "output\n")
    self.assertEqual(result.stderr, "warning\n")
    command = record.call_args.args[0]
    self.assertIsInstance(command, CommandLineCommand)
    self.assertEqual(command.device_id, "/usr/bin/scanner")
    self.assertEqual(command.arguments, ["--acquire"])
    self.assertEqual(command.returncode, 0)

  async def test_timeout_kills_process(self) -> None:
    transport = self.make_transport()
    await self.setup_transport(transport)
    release = asyncio.Event()

    async def communicate():
      await release.wait()
      return b"", b""

    process = MagicMock()
    process.returncode = None
    process.communicate = communicate
    process.kill.side_effect = release.set

    with patch(
      "pylabrobot.io.command_line.asyncio.create_subprocess_exec",
      AsyncMock(return_value=process),
    ):
      with self.assertRaises(asyncio.TimeoutError):
        await transport.run([], timeout=0.001)

    process.kill.assert_called_once_with()

  async def test_cancellation_kills_process(self) -> None:
    transport = self.make_transport()
    await self.setup_transport(transport)
    started = asyncio.Event()
    release = asyncio.Event()

    async def communicate():
      started.set()
      await release.wait()
      return b"", b""

    process = MagicMock()
    process.returncode = None
    process.communicate = communicate
    process.kill.side_effect = release.set

    with patch(
      "pylabrobot.io.command_line.asyncio.create_subprocess_exec",
      AsyncMock(return_value=process),
    ):
      task = asyncio.create_task(transport.run([], timeout=10.0))
      await started.wait()
      task.cancel()
      with self.assertRaises(asyncio.CancelledError):
        await task

    process.kill.assert_called_once_with()

  async def test_stop_marks_transport_closed(self) -> None:
    transport = self.make_transport()
    await self.setup_transport(transport)

    await transport.stop()

    self.assertFalse(transport.is_setup)

  async def test_rejects_non_positive_timeout(self) -> None:
    transport = self.make_transport()
    await self.setup_transport(transport)

    with self.assertRaisesRegex(ValueError, "positive"):
      await transport.run([], timeout=0)

  def test_rejects_empty_identity(self) -> None:
    with self.assertRaisesRegex(ValueError, "human_readable_device_name"):
      CommandLineTransport("", "scanner")
    with self.assertRaisesRegex(ValueError, "executable"):
      CommandLineTransport("Test scanner", "")

  def test_serializes_configuration(self) -> None:
    self.assertEqual(
      self.make_transport().serialize(),
      {
        "human_readable_device_name": "Test scanner",
        "executable": "scanner",
      },
    )

  async def test_validator_replays_captured_result(self) -> None:
    capture_reader = MagicMock()
    capture_reader.next_command.return_value = {
      "module": "command_line",
      "device_id": "/usr/bin/scanner",
      "action": "run",
      "arguments": ["--acquire"],
      "timeout": 2.0,
      "returncode": 0,
      "stdout": "output",
      "stderr": "",
    }
    validator = CommandLineValidator(
      cr=capture_reader,
      human_readable_device_name="Test scanner",
      executable="/usr/bin/scanner",
    )
    await validator.setup()

    result = await validator.run(["--acquire"], timeout=2.0)

    self.assertEqual(result, CommandLineResult(0, "output", ""))

  async def test_validator_rejects_argument_mismatch(self) -> None:
    capture_reader = MagicMock()
    capture_reader.next_command.return_value = {
      "module": "command_line",
      "device_id": "/usr/bin/scanner",
      "action": "run",
      "arguments": ["--expected"],
      "timeout": 2.0,
      "returncode": 0,
      "stdout": "",
      "stderr": "",
    }
    validator = CommandLineValidator(
      cr=capture_reader,
      human_readable_device_name="Test scanner",
      executable="/usr/bin/scanner",
    )
    await validator.setup()

    with self.assertRaisesRegex(ValidationError, "arguments differ"):
      await validator.run(["--actual"], timeout=2.0)
