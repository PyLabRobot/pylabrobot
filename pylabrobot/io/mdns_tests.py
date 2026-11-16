"""Offline tests of mDNS discovery and resource cleanup."""

import asyncio
import threading
import unittest
from unittest.mock import MagicMock, patch

try:
  from zeroconf import IPVersion, ServiceStateChange

  HAS_ZEROCONF = True
except ImportError:
  HAS_ZEROCONF = False

from pylabrobot.io.mdns import MDNSService, discover_mdns


class MDNSValidationTests(unittest.TestCase):
  """Invalid timeouts must fail before starting network discovery."""

  def test_invalid_timeouts(self) -> None:
    """Reject nonpositive and nonfinite durations."""
    for timeout in (0, -1, float("nan"), float("inf")):
      with self.subTest(timeout=timeout), self.assertRaises(ValueError):
        discover_mdns("_http._tcp.local.", timeout)

  def test_missing_dependency(self) -> None:
    """An optional dependency failure explains how to install it."""
    with patch.dict("sys.modules", {"zeroconf": None}):
      with self.assertRaisesRegex(ImportError, "pip install zeroconf"):
        discover_mdns("_http._tcp.local.")


@unittest.skipUnless(HAS_ZEROCONF, "zeroconf is not installed")
class MDNSDiscoveryTests(unittest.TestCase):
  """Exercise browser callbacks without opening sockets."""

  def setUp(self) -> None:
    """Mock only the external network implementation and browse delay."""
    for target, attribute in (
      ("zeroconf.Zeroconf", "zeroconf_factory"),
      ("zeroconf.ServiceBrowser", "browser_factory"),
      ("pylabrobot.io.mdns.time.sleep", "sleep"),
    ):
      patcher = patch(target)
      mocked = patcher.start()
      self.addCleanup(patcher.stop)
      if attribute == "zeroconf_factory":
        self.zeroconf_factory = mocked
      elif attribute == "browser_factory":
        self.browser_factory = mocked
      else:
        self.sleep = mocked
    self.zc = self.zeroconf_factory.return_value
    self.browser = self.browser_factory.return_value

  def test_resolve_updates_removals_and_ipv4(self) -> None:
    """Resolve surviving service names once, preserving TXT data and addresses."""
    service_type = "_http._tcp.local."

    def announce(zc, type_, handlers):
      """Emit the service events the browser would receive."""
      for name, state in (
        ("Gone", ServiceStateChange.Added),
        ("Gone", ServiceStateChange.Removed),
        ("Studio45", ServiceStateChange.Added),
        ("Studio45", ServiceStateChange.Updated),
        ("Unresolved", ServiceStateChange.Added),
      ):
        handlers[0](zc, type_, name + "." + type_, state)
      return self.browser

    self.browser_factory.side_effect = announce
    info = MagicMock()
    info.port = 31950
    info.properties = {b"robotModel": b"OT-3 Standard"}
    info.parsed_addresses.return_value = ["192.0.2.1"]

    def resolve(type_, name, timeout):
      """Return one resolved record and one expired record."""
      self.browser.cancel.assert_called_once()
      return info if name.startswith("Studio45.") else None

    self.zc.get_service_info.side_effect = resolve
    self.assertEqual(
      discover_mdns(service_type, 0.1),
      [MDNSService("Studio45", "192.0.2.1", 31950, info.properties)],
    )
    self.assertEqual(self.zc.get_service_info.call_count, 2)
    info.parsed_addresses.assert_called_once_with(IPVersion.V4Only)
    self.zc.close.assert_called_once()

  def test_cleanup_on_interruption(self) -> None:
    """Close the browser and sockets when browsing fails."""
    self.sleep.side_effect = RuntimeError("interrupted")
    with self.assertRaisesRegex(RuntimeError, "interrupted"):
      discover_mdns("_http._tcp.local.")
    self.browser.cancel.assert_called_once()
    self.zc.close.assert_called_once()

  def test_cleanup_when_browser_creation_fails(self) -> None:
    """Close sockets even if the browser never starts."""
    self.browser_factory.side_effect = RuntimeError("browser failed")
    with self.assertRaisesRegex(RuntimeError, "browser failed"):
      discover_mdns("_http._tcp.local.")
    self.zc.close.assert_called_once()

  def test_can_be_called_from_running_event_loop(self) -> None:
    """Create Zeroconf off the caller's event loop, as needed in notebooks."""
    caller_thread = threading.get_ident()

    def create(**kwargs):
      """Verify Zeroconf gets its own thread without a running event loop."""
      self.assertNotEqual(threading.get_ident(), caller_thread)
      with self.assertRaises(RuntimeError):
        asyncio.get_running_loop()
      return self.zc

    self.zeroconf_factory.side_effect = create

    async def discover_in_notebook():
      """Call the synchronous helper from an active event loop."""
      return discover_mdns("_http._tcp.local.")

    self.assertEqual(asyncio.run(discover_in_notebook()), [])
    self.zc.close.assert_called_once()
