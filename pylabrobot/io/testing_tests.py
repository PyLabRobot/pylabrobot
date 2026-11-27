"""Tests for buffered Serial doubles used by driver tests."""

import unittest
from unittest.mock import AsyncMock, Mock, call, patch

from pylabrobot.io.serial import Serial
from pylabrobot.io.testing import fake_serial


class TestFakeSerial(unittest.IsolatedAsyncioTestCase):
  """Check transport behavior and native mock assertions without hardware."""

  async def test_read_slices_and_retains_remainder(self) -> None:
    """Reads consume only the requested prefix, including the default size."""
    io = fake_serial(incoming=b"ABCDE")
    self.assertEqual(await io.read(), b"A")
    self.assertEqual(await io.read(num_bytes=2), b"BC")
    self.assertEqual(await io.read(10), b"DE")
    self.assertEqual(await io.read(1), b"")

  async def test_non_positive_reads_leave_buffer_unchanged(self) -> None:
    """Pyserial's Windows and POSIX reads return empty for non-positive sizes."""
    io = fake_serial(incoming=b"ABC")
    self.assertEqual(await io.read(0), b"")
    self.assertEqual(await io.read(-1), b"")
    self.assertEqual(await io.read(3), b"ABC")

  async def test_empty_buffer_returns_immediately(self) -> None:
    """An empty read does not schedule a sleep or wait for the configured timeout."""
    io = fake_serial(read_timeout=60)
    with patch("asyncio.sleep", side_effect=AssertionError("unexpected sleep")):
      self.assertEqual(await io.read(1), b"")

  async def test_instances_have_independent_state(self) -> None:
    """Buffer contents, timeout values and call histories belong to one fixture."""
    first = fake_serial(incoming=b"ABC", read_timeout=2)
    second = fake_serial(incoming=b"XYZ", read_timeout=3)
    await first.read(1)
    await first.reset_input_buffer()
    first.set_read_timeout(4)
    self.assertEqual(await first.read(3), b"")
    self.assertEqual(await second.read(3), b"XYZ")
    self.assertEqual(second.get_read_timeout(), 3)
    second.reset_input_buffer.assert_not_called()

  async def test_write_appends_callback_response_when_awaited(self) -> None:
    """The callback sees the awaited write and appends behind unread input."""
    callback = Mock(side_effect=[b"BC", b"DE"])
    io = fake_serial(incoming=b"A", on_write=callback)
    self.assertIsNone(await io.write(b"first"))
    self.assertEqual(io.write.await_args_list, [call(b"first")])
    callback.assert_called_once_with(b"first")
    self.assertEqual(await io.read(2), b"AB")
    self.assertIsNone(await io.write(b"second"))
    self.assertEqual(await io.read(10), b"CDE")

  async def test_write_without_response_returns_none(self) -> None:
    """An absent callback and a callback returning None both leave input alone."""
    for callback in (None, Mock(return_value=None)):
      with self.subTest(callback=callback):
        io = fake_serial(incoming=b"A", on_write=callback)
        self.assertIsNone(await io.write(b"command"))
        self.assertEqual(await io.read(10), b"A")
        io.write.assert_awaited_once_with(b"command")
        if callback is not None:
          callback.assert_called_once_with(b"command")

  async def test_callback_exception_propagates_after_write_is_recorded(self) -> None:
    """Callback failures retain their identity and the native await history."""
    error = ValueError("invalid command")
    io = fake_serial(incoming=b"A", on_write=Mock(side_effect=error))
    with self.assertRaises(ValueError) as caught:
      await io.write(b"bad")
    self.assertIs(caught.exception, error)
    io.write.assert_awaited_once_with(b"bad")
    self.assertEqual(await io.read(10), b"A")

  async def test_reset_clears_current_input_but_preserves_future_responses(self) -> None:
    """Reset discards queued bytes without replacing the response callback."""
    io = fake_serial(incoming=b"stale", on_write=Mock(return_value=b"reply"))
    await io.write(b"first")
    self.assertIsNone(await io.reset_input_buffer())
    self.assertEqual(await io.read(10), b"")
    await io.write(b"second")
    self.assertEqual(await io.read(10), b"reply")

  def test_timeout_getter_and_setter(self) -> None:
    """Synchronous timeout access uses the fixture's current value."""
    io = fake_serial()
    self.assertEqual(io.get_read_timeout(), 1.0)
    self.assertIsNone(io.set_read_timeout(0.25))
    self.assertEqual(io.get_read_timeout(), 0.25)

  def test_temporary_timeout_restores_nested_values(self) -> None:
    """Each context restores the value that was active on entry."""
    io = fake_serial(read_timeout=5)
    with io.temporary_timeout(2):
      self.assertEqual(io.get_read_timeout(), 2)
      with io.temporary_timeout(0.5):
        self.assertEqual(io.get_read_timeout(), 0.5)
      self.assertEqual(io.get_read_timeout(), 2)
    self.assertEqual(io.get_read_timeout(), 5)

  def test_temporary_timeout_restores_after_exception(self) -> None:
    """An exception inside the context is propagated after timeout restoration."""
    io = fake_serial(read_timeout=5)
    with self.assertRaisesRegex(ValueError, "failure"):
      with io.temporary_timeout(0.5):
        io.set_read_timeout(0.1)
        raise ValueError("failure")
    self.assertEqual(io.get_read_timeout(), 5)

  async def test_autospec_shape_and_lifecycle_without_construction(self) -> None:
    """The concrete class supplies the spec without constructing a transport."""
    with patch.object(Serial, "__init__", side_effect=AssertionError("real constructor")):
      io = fake_serial(port="COM_TEST")
      self.assertIsNone(await io.setup())
      self.assertIsNone(await io.stop())
    self.assertIsInstance(io, Serial)
    self.assertEqual(io.port, "COM_TEST")
    self.assertEqual(fake_serial().port, "FAKE")
    for method in (io.read, io.write, io.setup, io.stop, io.reset_input_buffer, io.readline):
      self.assertIsInstance(method, AsyncMock)
    for method in (io.get_read_timeout, io.set_read_timeout, io.temporary_timeout, io.serialize):
      self.assertIsInstance(method, Mock)
      self.assertNotIsInstance(method, AsyncMock)
    with self.assertRaises(TypeError):
      io.read(size=1)
    with self.assertRaises(TypeError):
      io.write()
    with self.assertRaises(TypeError):
      io.set_read_timeout()
    with self.assertRaises(AttributeError):
      io.missing_method()
    with self.assertRaises(AttributeError):
      io.missing_attribute = 1

  async def test_unsupported_public_methods_raise_named_errors(self) -> None:
    """Unconfigured Serial and inherited file methods cannot return silent mocks."""
    io = fake_serial()
    for name, method, args in (
      ("readline", io.readline, ()),
      ("send_break", io.send_break, (0.1,)),
      ("reset_output_buffer", io.reset_output_buffer, ()),
    ):
      with self.subTest(method=name):
        with self.assertRaisesRegex(NotImplementedError, "Serial." + name):
          await method(*args)
    for name, method, sync_args in (
      ("serialize", io.serialize, ()),
      ("close", io.close, ()),
      ("fileno", io.fileno, ()),
      ("flush", io.flush, ()),
      ("isatty", io.isatty, ()),
      ("readable", io.readable, ()),
      ("readlines", io.readlines, ()),
      ("seek", io.seek, (0,)),
      ("seekable", io.seekable, ()),
      ("tell", io.tell, ()),
      ("truncate", io.truncate, ()),
      ("writable", io.writable, ()),
      ("writelines", io.writelines, ([],)),
    ):
      with self.subTest(method=name):
        with self.assertRaisesRegex(NotImplementedError, "Serial." + name):
          method(*sync_args)

  async def test_unsupported_method_can_be_configured_with_native_mock_api(self) -> None:
    """Tests can replace the default failure using side_effect or return_value."""
    io = fake_serial()
    io.readline.side_effect = None
    io.readline.return_value = b"line\n"
    self.assertEqual(await io.readline(), b"line\n")
    io.readline.assert_awaited_once_with()
    io.serialize.side_effect = lambda: {"port": "test"}
    self.assertEqual(io.serialize(), {"port": "test"})

  async def test_exact_write_assertions_detect_command_regressions(self) -> None:
    """Full await lists detect wrong bytes, terminators, extra and missing writes."""
    expected = [call(b"?\r"), call(b"A100\r")]
    for commands in (
      [b"?\r", b"A100\r"],
      [b"!\r", b"A100\r"],
      [b"?\n", b"A100\r"],
      [b"?\r", b"A100\r", b"S\r"],
      [b"?\r"],
    ):
      with self.subTest(commands=commands):
        io = fake_serial(incoming=b"reply")
        for command in commands:
          await io.write(command)
        await io.read(2)
        await io.read(10)
        self.assertEqual(io.write.call_count, io.write.await_count)
        if commands == [b"?\r", b"A100\r"]:
          self.assertEqual(io.write.await_args_list, expected)
        else:
          with self.assertRaises(AssertionError):
            self.assertEqual(io.write.await_args_list, expected)

  async def test_called_but_unawaited_write_is_detectable(self) -> None:
    """Calling write records a call but neither invokes the callback nor buffers bytes."""
    callback = Mock(return_value=b"reply")
    io = fake_serial(on_write=callback)
    pending = io.write(b"command")
    try:
      callback.assert_not_called()
      io.write.assert_called_once_with(b"command")
      io.write.assert_not_awaited()
      self.assertEqual(await io.read(10), b"")
      with self.assertRaises(AssertionError):
        self.assertEqual(io.write.call_count, io.write.await_count)
    finally:
      pending.close()


if __name__ == "__main__":
  unittest.main()
