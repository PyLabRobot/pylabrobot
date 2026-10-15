"""USB transfer boundaries tested without opening a hardware connection."""

import unittest
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from unittest.mock import Mock, patch

from pylabrobot.hamilton.star.driver.master import STARDriver
from pylabrobot.io import usb as usb_module
from pylabrobot.io.usb import USB


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
    self.io.read_transfer_size = 4000
    self.device.read.side_effect = [b"a" * 64, b"next reply"]
    self.assertEqual(await self.io.read(), b"a" * 64)
    self.device.read.assert_called_once_with(self.io.read_endpoint, 4032, timeout=3000)
    self.assertEqual(await self.io.read(), b"next reply")

  async def test_multiple_packets_fit_one_transfer(self) -> None:
    """Read the complete 384-byte reply and its terminator in one request."""
    self.io.read_transfer_size = 4000
    self.device.read.return_value = b"a" * 384
    self.assertEqual(await self.io.read(), b"a" * 384)
    self.device.read.assert_called_once_with(self.io.read_endpoint, 4032, timeout=3000)

  async def test_full_transfer_continues_until_short_transfer(self) -> None:
    """A filled buffer is not a response boundary."""
    self.io.read_transfer_size = 4000
    self.device.read.side_effect = [b"a" * 4032, b"b" * 65]
    self.assertEqual(await self.io.read(), b"a" * 4032 + b"b" * 65)
    self.assertEqual([call.args[1] for call in self.device.read.call_args_list], [4032, 4032])

  async def test_full_transfer_followed_by_zlp(self) -> None:
    """Preserve a response that exactly fills the host buffer."""
    self.io.read_transfer_size = 4000
    self.device.read.side_effect = [b"a" * 4032, b"", b"next reply"]
    self.assertEqual(await self.io.read(), b"a" * 4032)
    self.assertEqual(self.device.read.call_count, 2)
    self.assertEqual(await self.io.read(), b"next reply")

  async def test_default_still_reads_endpoint_packets(self) -> None:
    """Devices without a configured buffer keep their existing read size."""
    self.device.read.side_effect = [b"a" * 64, b"b" * 64, b""]
    self.assertEqual(await self.io.read(), b"a" * 64 + b"b" * 64)
    self.assertEqual([call.args[1] for call in self.device.read.call_args_list], [64, 64, 64])

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
    self.io.read_transfer_size = 4000
    endpoint = Mock(wMaxPacketSize=512, bEndpointAddress=0x83)
    self.device.get_active_configuration.return_value = {(0, 0): [endpoint]}
    self.device.read.return_value = b"reply"
    with patch("pylabrobot.io.usb.usb.util.find_descriptor", return_value=endpoint):
      self.assertEqual(await self.io.read(endpoint=0x83), b"reply")
    self.device.read.assert_called_once_with(0x83, 4096, timeout=3000)

  async def test_star_uses_and_serializes_larger_transfer_size(self) -> None:
    """The STAR opts in while the shared USB default remains unchanged."""
    driver = STARDriver()
    self.assertIsInstance(driver.io, USB)
    assert isinstance(driver.io, USB)
    self.assertEqual(driver.io.read_transfer_size, 4000)
    self.assertEqual(driver.io.serialize()["read_transfer_size"], 4000)
    self.assertNotIn("read_transfer_size", self.io.serialize())
