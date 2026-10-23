"""USB transfer boundaries tested without opening a hardware connection."""

import unittest
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from unittest.mock import Mock, patch

from pylabrobot.io import usb as usb_module
from pylabrobot.io.usb import USB


class USBReconnectTests(unittest.IsolatedAsyncioTestCase):
  """Follow a restarted device by physical port without selecting a different instrument."""

  async def asyncSetUp(self) -> None:
    """Provide two matching USB devices, including one at the stale address."""
    self.old = Mock(bus=1, address=6, port_numbers=(1, 4))
    self.replacement = Mock(bus=1, address=7, port_numbers=(1, 4))
    self.replacement.get_active_configuration.return_value = {(0, 0): Mock()}
    self.other = Mock(bus=1, address=6, port_numbers=(1, 5))
    self.find = Mock(return_value=[self.other, self.replacement])
    backend = SimpleNamespace(
      util=SimpleNamespace(dispose_resources=Mock(), find_descriptor=Mock())
    )
    for name, value in (
      ("usb", backend),
      ("libusb_package", SimpleNamespace(find=self.find)),
      ("USE_USB", True),
    ):
      patcher = patch.object(usb_module, name, value, create=True)
      patcher.start()
      self.addCleanup(patcher.stop)
    self.configure = Mock()
    self.io = USB(1, 2, "restarting USB", device_address=6, configuration_callback=self.configure)
    self.io.dev = self.old
    self.io._read_packet = Mock(return_value=None)  # type: ignore[method-assign]

  async def asyncTearDown(self) -> None:
    """Release any transport workers created by the actual setup implementation."""
    await self.io.stop()

  async def test_reconnect_follows_port_when_address_changes(self) -> None:
    """Do not select another device that inherited the old numeric address."""
    await self.io.reconnect()
    self.assertIs(self.io.dev, self.replacement)
    self.configure.assert_called_once_with(self.replacement)
    self.assertEqual(self.io.serialize()["device_address"], 7)

  async def test_missing_port_does_not_fall_back_to_another_device(self) -> None:
    """Fail closed even when another instrument matches the original address."""
    self.find.return_value = [self.other, Mock(bus=2, address=7, port_numbers=(1, 4))]
    with self.assertRaisesRegex(RuntimeError, "USB device not found"):
      await self.io.reconnect()
    self.configure.assert_not_called()
    self.assertIsNone(self.io.dev)

  async def test_missing_topology_keeps_existing_handle(self) -> None:
    """A backend without physical identity cannot safely choose the replacement."""
    self.old.port_numbers = None
    with self.assertRaisesRegex(RuntimeError, "bus and port numbers"):
      await self.io.reconnect()
    self.assertIs(self.io.dev, self.old)
    self.configure.assert_not_called()


class USBReadTests(unittest.IsolatedAsyncioTestCase):
  """Keep USB transfer completion separate from individual endpoint packets."""

  async def asyncSetUp(self) -> None:
    """Attach a mock device and an executor without device discovery."""
    backend = SimpleNamespace(
      util=SimpleNamespace(find_descriptor=Mock()), core=SimpleNamespace(USBError=OSError)
    )
    patcher = patch.object(usb_module, "usb", backend, create=True)
    patcher.start()
    self.addCleanup(patcher.stop)
    self.io = USB(1, 2, "mock USB")
    self.device = Mock()
    self.io.dev = self.device
    self.io.read_endpoint = Mock(wMaxPacketSize=64, bEndpointAddress=0x81)
    self.io._read_executor = ThreadPoolExecutor(max_workers=1)

  async def asyncTearDown(self) -> None:
    """Release the fixture worker after all reads finish."""
    self.io._shutdown_executors()

  async def test_packet_aligned_reply_does_not_consume_next_reply(self) -> None:
    """A 64-byte result from a larger request has already consumed its ZLP."""
    self.device.read.side_effect = [b"a" * 64, b"next reply"]
    self.assertEqual(await self.io.read(), b"a" * 64)
    self.device.read.assert_called_once_with(self.io.read_endpoint, 4032, timeout=3000)
    self.assertEqual(await self.io.read(), b"next reply")

  async def test_multiple_packets_fit_one_transfer(self) -> None:
    """Read the complete 384-byte reply and its terminator in one request."""
    self.device.read.return_value = b"a" * 384
    self.assertEqual(await self.io.read(), b"a" * 384)
    self.device.read.assert_called_once_with(self.io.read_endpoint, 4032, timeout=3000)

  async def test_full_transfer_continues_until_short_transfer(self) -> None:
    """A filled buffer is not a response boundary."""
    self.device.read.side_effect = [b"a" * 4032, b"b" * 65]
    self.assertEqual(await self.io.read(), b"a" * 4032 + b"b" * 65)
    self.assertEqual([call.args[1] for call in self.device.read.call_args_list], [4032, 4032])

  async def test_full_transfer_followed_by_zlp(self) -> None:
    """Preserve a response that exactly fills the host buffer."""
    self.device.read.side_effect = [b"a" * 4032, b"", b"next reply"]
    self.assertEqual(await self.io.read(), b"a" * 4032)
    self.assertEqual(self.device.read.call_count, 2)
    self.assertEqual(await self.io.read(), b"next reply")

  async def test_high_speed_endpoint_uses_larger_default_buffer(self) -> None:
    """A 512-byte endpoint uses a packet-aligned buffer without combining responses."""
    self.io.read_endpoint = Mock(wMaxPacketSize=512, bEndpointAddress=0x81)
    self.device.read.side_effect = [b"a" * 512, b"next reply"]
    self.assertEqual(await self.io.read(), b"a" * 512)
    self.device.read.assert_called_once_with(self.io.read_endpoint, 4096, timeout=3000)
    self.assertEqual(await self.io.read(), b"next reply")

  async def test_explicit_size_uses_transfer_length_for_completion(self) -> None:
    """A short transfer must not concatenate the next response to a packet-sized reply."""
    self.device.read.side_effect = [b"a" * 64, b"next reply"]
    self.assertEqual(await self.io.read(size=512), b"a" * 64)
    self.assertEqual(self.device.read.call_count, 1)
    self.assertEqual(await self.io.read(), b"next reply")

  async def test_explicit_size_is_an_upper_bound(self) -> None:
    """An explicitly filled byte limit returns without starting another read."""
    self.device.read.side_effect = [b"a" * 128, b"next reply"]
    self.assertEqual(await self.io.read(size=128), b"a" * 128)
    self.device.read.assert_called_once_with(self.io.read_endpoint, 128, timeout=3000)

  async def test_empty_completion_is_not_an_empty_response(self) -> None:
    """Skip an empty completion before a response has started."""
    self.device.read.side_effect = [b"", b"reply"]
    self.assertEqual(await self.io.read(), b"reply")

  async def test_alternate_endpoint_determines_buffer_alignment(self) -> None:
    """Round the requested buffer for the selected endpoint's packet size."""
    endpoint = Mock(wMaxPacketSize=512, bEndpointAddress=0x83)
    self.device.get_active_configuration.return_value = {(0, 0): [endpoint]}
    self.device.read.return_value = b"reply"
    with patch("pylabrobot.io.usb.usb.util.find_descriptor", return_value=endpoint):
      self.assertEqual(await self.io.read(endpoint=0x83), b"reply")
    self.device.read.assert_called_once_with(0x83, 4096, timeout=3000)

  async def test_drain_uses_default_transfer_size(self) -> None:
    """Draining uses the same buffer and continues until the endpoint times out."""
    self.device.read.side_effect = [b"stale reply", b"a" * 384, OSError("timeout")]
    await self.io.drain()
    self.assertEqual([call.args[1] for call in self.device.read.call_args_list], [4032] * 3)

  async def test_drain_respects_explicit_transfer_size(self) -> None:
    """An explicit drain buffer size overrides the shared default."""
    self.device.read.side_effect = [b"stale reply", OSError("timeout")]
    await self.io.drain(size=128)
    self.assertEqual([call.args[1] for call in self.device.read.call_args_list], [128, 128])
