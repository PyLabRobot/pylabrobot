import socket
import struct
import threading
import time
import unittest
from types import SimpleNamespace
from unittest import mock

import anyio
import pytest

from pylabrobot.io.sila import discovery as discovery_module
from pylabrobot.io.sila.discovery import (
  SiLADevice,
  _arp_scan_bsd,
  _arp_scan_linux,
  _arp_scan_windows,
  _decode_nbns_name,
  _discover_sila1,
  _discover_sila2,
  _get_device_identification,
  _netbios_scan,
  _parse_device_identification,
  _ping_broadcast,
  discover,
  discover_iter,
)
from pylabrobot.testing.concurrency import AnyioTestBase


class TestSiLADevice(unittest.TestCase):
  def test_str(self):
    d = SiLADevice(host="192.168.1.42", port=8091, name="Pico", sila_version=2)
    self.assertEqual(str(d), "Pico @ 192.168.1.42:8091 (SiLA 2)")

  def test_defaults(self):
    d = SiLADevice(host="1.2.3.4", port=80, name="X")
    self.assertIsNone(d.serial_number)
    self.assertIsNone(d.firmware_version)
    self.assertEqual(d.sila_version, 2)

  def test_sila1_fields(self):
    d = SiLADevice(
      host="169.254.1.1",
      port=8080,
      name="ODTC",
      serial_number="SN123",
      firmware_version="1.0",
      sila_version=1,
    )
    self.assertEqual(d.serial_number, "SN123")
    self.assertEqual(d.firmware_version, "1.0")
    self.assertEqual(d.sila_version, 1)

  def test_frozen(self):
    d = SiLADevice(host="1.2.3.4", port=80, name="X")
    with self.assertRaises(AttributeError):
      d.host = "5.6.7.8"  # type: ignore[misc]


class TestDecodeNbnsName(unittest.TestCase):
  def _make_nbstat_response(self, name: str) -> bytes:
    """Build a minimal NBSTAT response with one name entry."""
    # Transaction header (12 bytes)
    header = b"\x00\x01" + b"\x84\x00" + b"\x00\x00" + b"\x00\x01" + b"\x00\x00" + b"\x00\x00"
    # Answer name section (skip to type marker)
    answer_name = b"\x20" + b"CKAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA" + b"\x00"
    type_class = b"\x00\x21\x00\x01"  # NBSTAT, IN
    ttl = struct.pack(">I", 0)
    # Name entry: 15-byte padded name + 1-byte suffix + 2-byte flags
    padded = name.encode("ascii").ljust(15, b" ")
    name_entry = padded + b"\x00" + b"\x04\x00"
    rdlength = struct.pack(">H", 1 + len(name_entry))  # num_names byte + entry
    num_names = b"\x01"
    return header + answer_name + type_class + ttl + rdlength + num_names + name_entry

  def test_valid_response(self):
    data = self._make_nbstat_response("ODTC_1A3C93")
    self.assertEqual(_decode_nbns_name(data), "ODTC_1A3C93")

  def test_trailing_spaces_stripped(self):
    data = self._make_nbstat_response("FOO")
    self.assertEqual(_decode_nbns_name(data), "FOO")

  def test_no_nbstat_marker(self):
    self.assertIsNone(_decode_nbns_name(b"\x00" * 50))

  def test_empty_data(self):
    self.assertIsNone(_decode_nbns_name(b""))

  def test_zero_names(self):
    data = self._make_nbstat_response("X")
    # Patch num_names to 0
    idx = data.rfind(b"\x01")
    data = data[:idx] + b"\x00" + data[idx + 1 :]
    self.assertIsNone(_decode_nbns_name(data))


class TestParseDeviceIdentification(unittest.TestCase):
  def test_full_response(self):
    xml = b"""\
<?xml version="1.0" encoding="utf-8"?>
<soap:Envelope xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/"
               xmlns:sila="http://sila.coop">
  <soap:Body>
    <sila:GetDeviceIdentificationResponse>
      <sila:DeviceName>ODTC_1A3C93</sila:DeviceName>
      <sila:DeviceSerialNumber>SN12345</sila:DeviceSerialNumber>
      <sila:DeviceFirmwareVersion>2.1.0</sila:DeviceFirmwareVersion>
    </sila:GetDeviceIdentificationResponse>
  </soap:Body>
</soap:Envelope>"""
    result = _parse_device_identification("169.254.1.1", 8080, xml)
    assert result is not None
    self.assertEqual(result.host, "169.254.1.1")
    self.assertEqual(result.port, 8080)
    self.assertEqual(result.name, "ODTC_1A3C93")
    self.assertEqual(result.serial_number, "SN12345")
    self.assertEqual(result.firmware_version, "2.1.0")
    self.assertEqual(result.sila_version, 1)

  def test_missing_optional_fields(self):
    xml = b"""\
<?xml version="1.0" encoding="utf-8"?>
<soap:Envelope xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/">
  <soap:Body>
    <DeviceName>MyDevice</DeviceName>
  </soap:Body>
</soap:Envelope>"""
    result = _parse_device_identification("10.0.0.1", 8080, xml)
    assert result is not None
    self.assertEqual(result.name, "MyDevice")
    self.assertIsNone(result.serial_number)
    self.assertIsNone(result.firmware_version)

  def test_no_device_name(self):
    xml = b"""\
<?xml version="1.0" encoding="utf-8"?>
<soap:Envelope xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/">
  <soap:Body>
    <DeviceSerialNumber>SN999</DeviceSerialNumber>
  </soap:Body>
</soap:Envelope>"""
    self.assertIsNone(_parse_device_identification("10.0.0.1", 8080, xml))

  def test_invalid_xml(self):
    self.assertIsNone(_parse_device_identification("10.0.0.1", 8080, b"not xml"))


class TestArpScanBsd(AnyioTestBase):
  ARP_OUTPUT = (
    "? (169.254.245.237) at 0:5:51:e:e5:7e on en13 [ethernet]\n"
    "? (192.168.0.1) at aa:bb:cc:dd:ee:ff on en0 ifscope [ethernet]\n"
    "? (169.254.10.20) at (incomplete) on en13 [ethernet]\n"
    "? (169.254.99.1) at 11:22:33:44:55:66 on en13 [ethernet]\n"
    "? (169.254.50.50) at 22:33:44:55:66:77 on en7 [ethernet]\n"
  )

  async def test_parses_link_local_entries(self) -> None:
    completed = SimpleNamespace(stdout=self.ARP_OUTPUT.encode())
    with (
      mock.patch.object(discovery_module, "_interface_name_for_ip_sync", return_value="en13"),
      mock.patch.object(discovery_module.anyio, "run_process", return_value=completed),
    ):
      results = await _arp_scan_bsd("169.254.229.18")
    assert "169.254.245.237" in results
    assert "169.254.99.1" in results
    # Non-link-local should be excluded
    assert "192.168.0.1" not in results
    # Incomplete entries should be excluded
    assert "169.254.10.20" not in results
    # Our own interface IP should be excluded
    assert "169.254.229.18" not in results
    # Entry on a different interface should be excluded
    assert "169.254.50.50" not in results

  async def test_empty_output(self) -> None:
    completed = SimpleNamespace(stdout=b"")
    with (
      mock.patch.object(discovery_module, "_interface_name_for_ip_sync", return_value="en13"),
      mock.patch.object(discovery_module.anyio, "run_process", return_value=completed),
    ):
      results = await _arp_scan_bsd("169.254.229.18")
    assert results == {}

  async def test_returns_empty_when_interface_unknown(self) -> None:
    """If we can't resolve the interface name, return empty rather than all entries."""
    with mock.patch.object(discovery_module, "_interface_name_for_ip_sync", return_value=None):
      results = await _arp_scan_bsd("169.254.229.18")
    assert results == {}


class TestArpScanLinux(AnyioTestBase):
  PROC_NET_ARP = (
    "IP address       HW type     Flags       HW address            Mask     Device\n"
    "169.254.245.237  0x1         0x2         00:05:51:0e:e5:7e     *        eth0\n"
    "192.168.1.1      0x1         0x2         aa:bb:cc:dd:ee:ff     *        eth1\n"
    "169.254.10.20    0x1         0x0         00:00:00:00:00:00     *        eth0\n"
  )

  async def test_parses_proc_net_arp(self) -> None:
    with (
      mock.patch.object(discovery_module, "_interface_name_for_ip_sync", return_value="eth0"),
      mock.patch.object(discovery_module.os.path, "exists", return_value=True),
      mock.patch(
        "builtins.open",
        mock.MagicMock(
          return_value=mock.MagicMock(
            __enter__=lambda s: s,
            __exit__=mock.MagicMock(return_value=False),
            read=mock.MagicMock(return_value=self.PROC_NET_ARP),
          )
        ),
      ),
    ):
      results = await _arp_scan_linux("169.254.229.18")

    assert "169.254.245.237" in results
    # Non-link-local excluded
    assert "192.168.1.1" not in results
    # Incomplete (flags=0x0) excluded
    assert "169.254.10.20" not in results


class TestArpScanWindows(AnyioTestBase):
  ARP_OUTPUT = (
    "\r\n"
    "Interface: 169.254.229.18 --- 0x5\r\n"
    "  Internet Address      Physical Address      Type\r\n"
    "  169.254.245.237       00-05-51-0e-e5-7e     dynamic\r\n"
    "  169.254.10.20         00-aa-bb-cc-dd-ee     dynamic\r\n"
    "\r\n"
    "Interface: 192.168.0.100 --- 0x3\r\n"
    "  Internet Address      Physical Address      Type\r\n"
    "  192.168.0.1           aa-bb-cc-dd-ee-ff     dynamic\r\n"
    "  169.254.99.1          11-22-33-44-55-66     dynamic\r\n"
  )

  async def test_parses_correct_interface_section(self) -> None:
    completed = SimpleNamespace(stdout=self.ARP_OUTPUT.encode())
    with mock.patch.object(discovery_module.anyio, "run_process", return_value=completed):
      results = await _arp_scan_windows("169.254.229.18")
    assert "169.254.245.237" in results
    assert "169.254.10.20" in results
    # This is under a different interface section
    assert "169.254.99.1" not in results
    assert "192.168.0.1" not in results
    # Our own IP should be excluded
    assert "169.254.229.18" not in results


class TestDiscoveryLifecycle(AnyioTestBase):
  async def test_netbios_poll_uses_finite_blocking_timeout(self) -> None:
    class FakeSocket:
      blocking_timeout: float | None = None

      def setsockopt(self, *args: object) -> None:
        pass

      def bind(self, address: object) -> None:
        pass

      def settimeout(self, timeout: float) -> None:
        self.blocking_timeout = timeout

      def sendto(self, data: bytes, address: object) -> int:
        return len(data)

      def recvfrom(self, size: int) -> tuple[bytes, tuple[str, int]]:
        raise socket.timeout

      def close(self) -> None:
        pass

    fake_socket = FakeSocket()
    with mock.patch.object(discovery_module.socket, "socket", return_value=fake_socket):
      await _netbios_scan("169.254.1.2", timeout=0.01)

    assert fake_socket.blocking_timeout is not None
    assert 0 < fake_socket.blocking_timeout <= 0.01

  async def test_netbios_finishes_short_budget_after_worker_releases_socket(self) -> None:
    class FakeSocket:
      blocking_timeout = 0.0
      receiving = False
      closed = False
      receives = 0

      def setsockopt(self, *args: object) -> None:
        pass

      def bind(self, address: object) -> None:
        pass

      def settimeout(self, timeout: float) -> None:
        self.blocking_timeout = timeout

      def sendto(self, data: bytes, address: object) -> int:
        return len(data)

      def recvfrom(self, size: int) -> tuple[bytes, tuple[str, int]]:
        self.receiving = True
        self.receives += 1
        try:
          time.sleep(self.blocking_timeout)
          raise socket.timeout
        finally:
          self.receiving = False

      def close(self) -> None:
        assert not self.receiving
        self.closed = True

    fake_socket = FakeSocket()
    started = anyio.current_time()
    with mock.patch.object(discovery_module.socket, "socket", return_value=fake_socket):
      result = await _netbios_scan("169.254.1.2", timeout=0.04)

    assert result == {}
    assert anyio.current_time() - started < 0.3
    assert fake_socket.closed
    assert fake_socket.receives >= 1

  async def test_netbios_send_uses_remaining_budget_and_closes_after_worker(self) -> None:
    class FakeSocket:
      blocking_timeout: float | None = None
      sending = False
      closed = False

      def setsockopt(self, *args: object) -> None:
        pass

      def bind(self, address: object) -> None:
        pass

      def settimeout(self, timeout: float) -> None:
        self.blocking_timeout = timeout

      def sendto(self, data: bytes, address: object) -> int:
        self.sending = True
        try:
          time.sleep(self.blocking_timeout if self.blocking_timeout is not None else 0.5)
          raise socket.timeout
        finally:
          self.sending = False

      def recvfrom(self, size: int) -> tuple[bytes, tuple[str, int]]:
        raise AssertionError("recv after failed send")

      def close(self) -> None:
        assert not self.sending
        self.closed = True

    fake_socket = FakeSocket()
    started = anyio.current_time()
    with mock.patch.object(discovery_module.socket, "socket", return_value=fake_socket):
      assert await _netbios_scan("169.254.1.2", timeout=0.04) == {}

    assert fake_socket.blocking_timeout is not None
    assert 0 < fake_socket.blocking_timeout <= 0.04
    assert anyio.current_time() - started < 0.3
    assert fake_socket.closed

  async def test_netbios_recv_error_does_not_spin(self) -> None:
    class FakeSocket:
      receives = 0
      closed = False

      def setsockopt(self, *args: object) -> None:
        pass

      def bind(self, address: object) -> None:
        pass

      def settimeout(self, timeout: float) -> None:
        pass

      def sendto(self, data: bytes, address: object) -> int:
        return len(data)

      def recvfrom(self, size: int) -> tuple[bytes, tuple[str, int]]:
        self.receives += 1
        raise OSError("socket failed")

      def close(self) -> None:
        self.closed = True

    fake_socket = FakeSocket()
    with mock.patch.object(discovery_module.socket, "socket", return_value=fake_socket):
      assert await _netbios_scan("169.254.1.2", timeout=0.1) == {}

    assert fake_socket.receives == 1
    assert fake_socket.closed

  async def test_netbios_socket_closes_when_setup_fails(self) -> None:
    class FakeSocket:
      closed = False

      def setsockopt(self, *args: object) -> None:
        pass

      def bind(self, address: object) -> None:
        raise OSError("bind failed")

      def close(self) -> None:
        self.closed = True

    fake_socket = FakeSocket()
    with (
      mock.patch.object(discovery_module.socket, "socket", return_value=fake_socket),
      pytest.raises(OSError, match="bind failed"),
    ):
      await _netbios_scan("169.254.1.2", timeout=0.01)

    assert fake_socket.closed

  async def test_sila1_scans_concurrently_and_preserves_fallback_names(self) -> None:
    netbios_started = anyio.Event()
    arp_started = anyio.Event()

    async def netbios(interface: str, timeout: float, results: dict[str, str]) -> dict[str, str]:
      netbios_started.set()
      with anyio.fail_after(1):
        await arp_started.wait()
      return {"169.254.1.10": "NETBIOS"}

    async def arp(interface: str) -> dict[str, str]:
      arp_started.set()
      with anyio.fail_after(1):
        await netbios_started.wait()
      return {"169.254.1.10": "", "169.254.1.11": ""}

    async def identify(*args: object, **kwargs: object) -> None:
      return None

    with (
      mock.patch.object(discovery_module, "_netbios_scan", netbios),
      mock.patch.object(discovery_module, "_arp_scan", arp),
      mock.patch.object(discovery_module, "_get_device_identification", identify),
    ):
      devices = await _discover_sila1(timeout=1, interface="169.254.1.2")

    assert [(device.host, device.name) for device in devices] == [
      ("169.254.1.10", "NETBIOS"),
      ("169.254.1.11", "169.254.1.11"),
    ]

  async def test_sila1_probe_timeout_keeps_hosts_without_starting_identification(self) -> None:
    cleaned = anyio.Event()
    identification_started = False

    async def netbios(interface: str, timeout: float, results: dict[str, str]) -> dict[str, str]:
      return {"169.254.1.10": "NETBIOS", "169.254.1.11": "NEXT"}

    async def arp(interface: str) -> dict[str, str]:
      try:
        await anyio.sleep_forever()
      finally:
        with anyio.CancelScope(shield=True):
          await anyio.sleep(0)
          cleaned.set()
      raise AssertionError("sleep_forever returned")

    async def identify(*args: object, **kwargs: object) -> SiLADevice:
      nonlocal identification_started
      identification_started = True
      raise AssertionError("identification started after deadline")

    with (
      mock.patch.object(discovery_module, "_netbios_scan", netbios),
      mock.patch.object(discovery_module, "_arp_scan", arp),
      mock.patch.object(discovery_module, "_get_device_identification", identify),
      anyio.fail_after(0.3),
    ):
      devices = await _discover_sila1(timeout=0.03, interface="169.254.1.2")

    assert cleaned.is_set()
    assert not identification_started
    assert [(device.host, device.name) for device in devices] == [
      ("169.254.1.10", "NETBIOS"),
      ("169.254.1.11", "NEXT"),
    ]

  async def test_netbios_records_received_names_in_shared_results(self) -> None:
    response = (
      b"\x00" * 12 + b"\x00\x21\x00\x01" + b"\x00" * 6 + b"\x01" + b"NETBIOS".ljust(15, b" ")
    )

    class FakeSocket:
      blocking_timeout = 0.0
      receives = 0
      closed = False

      def setsockopt(self, *args: object) -> None:
        pass

      def bind(self, address: object) -> None:
        pass

      def settimeout(self, timeout: float) -> None:
        self.blocking_timeout = timeout

      def sendto(self, data: bytes, address: object) -> int:
        return len(data)

      def recvfrom(self, size: int) -> tuple[bytes, tuple[str, int]]:
        self.receives += 1
        if self.receives == 1:
          return response, ("169.254.1.10", 137)
        time.sleep(self.blocking_timeout + 0.02)
        raise socket.timeout

      def close(self) -> None:
        self.closed = True

    fake_socket = FakeSocket()
    shared_results: dict[str, str] = {}
    with mock.patch.object(discovery_module.socket, "socket", return_value=fake_socket):
      await _netbios_scan("169.254.1.2", timeout=0.04, results=shared_results)

    assert fake_socket.closed
    assert fake_socket.receives >= 2
    assert shared_results == {"169.254.1.10": "NETBIOS"}

  async def test_sila1_preserves_partial_netbios_results_when_probe_is_cancelled(self) -> None:
    cleaned = anyio.Event()

    async def netbios(interface: str, timeout: float, results: dict[str, str]) -> dict[str, str]:
      results["169.254.1.10"] = "NETBIOS"
      try:
        await anyio.sleep_forever()
      finally:
        with anyio.CancelScope(shield=True):
          await anyio.sleep(0)
          cleaned.set()
      raise AssertionError("sleep_forever returned")

    async def arp(interface: str) -> dict[str, str]:
      return {"169.254.1.10": "", "169.254.1.11": ""}

    with (
      mock.patch.object(discovery_module, "_netbios_scan", netbios),
      mock.patch.object(discovery_module, "_arp_scan", arp),
      anyio.fail_after(0.3),
    ):
      devices = await _discover_sila1(timeout=0.03, interface="169.254.1.2")

    assert cleaned.is_set()
    assert [(device.host, device.name) for device in devices] == [
      ("169.254.1.10", "NETBIOS"),
      ("169.254.1.11", "169.254.1.11"),
    ]

  async def test_sila1_identification_timeout_keeps_completed_and_fallback_results(self) -> None:
    cleaned = anyio.Event()

    async def netbios(interface: str, timeout: float, results: dict[str, str]) -> dict[str, str]:
      return {"169.254.1.10": "FIRST", "169.254.1.11": "SECOND"}

    async def arp(interface: str) -> dict[str, str]:
      return {}

    async def identify(host: str, *args: object, **kwargs: object) -> SiLADevice:
      if host == "169.254.1.10":
        return SiLADevice(host, 8080, "identified", sila_version=1)
      try:
        await anyio.sleep_forever()
      finally:
        with anyio.CancelScope(shield=True):
          await anyio.sleep(0)
          cleaned.set()
      raise AssertionError("sleep_forever returned")

    with (
      mock.patch.object(discovery_module, "_netbios_scan", netbios),
      mock.patch.object(discovery_module, "_arp_scan", arp),
      mock.patch.object(discovery_module, "_get_device_identification", identify),
      anyio.fail_after(0.3),
    ):
      devices = await _discover_sila1(timeout=0.03, interface="169.254.1.2")

    assert cleaned.is_set()
    assert [(device.host, device.name) for device in devices] == [
      ("169.254.1.10", "identified"),
      ("169.254.1.11", "SECOND"),
    ]

  async def test_identification_uses_one_overall_deadline(self) -> None:
    class SlowStream:
      closed = False

      async def send(self, data: bytes) -> None:
        await anyio.sleep(0.04)

      async def receive(self, size: int) -> bytes:
        await anyio.sleep(0.04)
        return b""

      async def aclose(self) -> None:
        self.closed = True

    stream = SlowStream()

    async def connect_tcp(**kwargs: object) -> SlowStream:
      await anyio.sleep(0.04)
      return stream

    started = anyio.current_time()
    with mock.patch.object(discovery_module.anyio, "connect_tcp", connect_tcp):
      result = await _get_device_identification("169.254.1.10", 8080, timeout=0.07)

    assert result is None
    assert anyio.current_time() - started < 0.11
    assert stream.closed

  async def test_discover_iter_bare_break_observes_completed_producers(self) -> None:
    cleaned: set[str] = set()
    first = SiLADevice("169.254.1.10", 8080, "first", sila_version=1)
    second = SiLADevice("192.168.1.10", 8091, "second", sila_version=2)

    async def sila1(*args: object, **kwargs: object) -> list[SiLADevice]:
      try:
        return [first]
      finally:
        cleaned.add("sila1")

    async def sila2(*args: object, **kwargs: object) -> list[SiLADevice]:
      try:
        await anyio.sleep(0.02)
        return [second]
      finally:
        cleaned.add("sila2")

    with (
      mock.patch.object(discovery_module, "_discover_sila1", sila1),
      mock.patch.object(discovery_module, "_discover_sila2", sila2),
    ):
      async for device in discover_iter(timeout=1, interface="169.254.1.2"):
        assert device == first
        break

    assert cleaned == {"sila1", "sila2"}

  async def test_discover_iter_detection_uses_budget_and_keeps_sila2_result(self) -> None:
    second = SiLADevice("192.168.1.10", 8091, "second", sila_version=2)
    sila1_started = False

    def detect() -> list[str]:
      time.sleep(0.06)
      return ["169.254.1.2"]

    async def sila1(*args: object) -> list[SiLADevice]:
      nonlocal sila1_started
      sila1_started = True
      return []

    async def sila2(*args: object) -> list[SiLADevice]:
      return [second]

    started = anyio.current_time()
    with (
      mock.patch.object(discovery_module, "_get_link_local_interfaces", detect),
      mock.patch.object(discovery_module, "_discover_sila1", sila1),
      mock.patch.object(discovery_module, "_discover_sila2", sila2),
      anyio.fail_after(0.3),
    ):
      result = await discover(timeout=0.03)

    assert result == [second]
    assert not sila1_started
    assert anyio.current_time() - started < 0.12

  async def test_discover_iter_passes_only_remaining_time_to_sila1(self) -> None:
    budgets: list[float] = []

    def detect() -> list[str]:
      time.sleep(0.04)
      return ["169.254.1.2"]

    async def sila1(timeout: float, interface: str) -> list[SiLADevice]:
      budgets.append(timeout)
      return [SiLADevice("169.254.1.10", 8080, "first", sila_version=1)]

    async def sila2(*args: object) -> list[SiLADevice]:
      return []

    with (
      mock.patch.object(discovery_module, "_get_link_local_interfaces", detect),
      mock.patch.object(discovery_module, "_discover_sila1", sila1),
      mock.patch.object(discovery_module, "_discover_sila2", sila2),
    ):
      result = await discover(timeout=0.15)

    assert [device.host for device in result] == ["169.254.1.10"]
    assert len(budgets) == 1
    assert 0 < budgets[0] < 0.13

  async def test_discover_iter_deadline_starts_on_first_iteration(self) -> None:
    first = SiLADevice("169.254.1.10", 8080, "first", sila_version=1)

    async def sila1(*args: object) -> list[SiLADevice]:
      return [first]

    async def sila2(*args: object) -> list[SiLADevice]:
      return []

    with (
      mock.patch.object(discovery_module, "_discover_sila1", sila1),
      mock.patch.object(discovery_module, "_discover_sila2", sila2),
    ):
      iterator = discover_iter(timeout=0.03, interface="169.254.1.2")
      await anyio.sleep(0.05)
      assert [device async for device in iterator] == [first]

  async def test_discover_iter_zero_budget_starts_no_probes(self) -> None:
    calls: list[tuple[object, ...]] = []

    def detect() -> list[str]:
      calls.append(())
      return ["169.254.1.2"]

    async def probe(*args: object) -> list[SiLADevice]:
      calls.append(args)
      raise AssertionError("probe started with no budget")

    with (
      mock.patch.object(discovery_module, "_get_link_local_interfaces", detect),
      mock.patch.object(discovery_module, "_discover_sila1", probe),
      mock.patch.object(discovery_module, "_discover_sila2", probe),
    ):
      assert await discover(timeout=0) == []

    assert calls == []

  async def test_discover_iter_full_consumption_preserves_completion_order(self) -> None:
    first = SiLADevice("169.254.1.10", 8080, "first", sila_version=1)
    second = SiLADevice("192.168.1.10", 8091, "second", sila_version=2)

    async def sila1(*args: object, **kwargs: object) -> list[SiLADevice]:
      await anyio.sleep(0.02)
      return [first]

    async def sila2(*args: object, **kwargs: object) -> list[SiLADevice]:
      return [second]

    with (
      mock.patch.object(discovery_module, "_discover_sila1", sila1),
      mock.patch.object(discovery_module, "_discover_sila2", sila2),
    ):
      devices = [device async for device in discover_iter(timeout=1, interface="169.254.1.2")]

    assert devices == [second, first]

  async def test_discover_preserves_completion_order(self) -> None:
    first = SiLADevice("169.254.1.10", 8080, "first", sila_version=1)
    second = SiLADevice("192.168.1.10", 8091, "second", sila_version=2)

    async def sila1(*args: object, **kwargs: object) -> list[SiLADevice]:
      await anyio.sleep(0.02)
      return [first]

    async def sila2(*args: object, **kwargs: object) -> list[SiLADevice]:
      return [second]

    with (
      mock.patch.object(discovery_module, "_discover_sila1", sila1),
      mock.patch.object(discovery_module, "_discover_sila2", sila2),
    ):
      devices = await discover(timeout=1, interface="169.254.1.2")

    assert devices == [second, first]

  async def test_duplicate_host_port_is_emitted_once(self) -> None:
    first = SiLADevice("169.254.1.10", 8080, "first", sila_version=1)
    duplicate = SiLADevice("169.254.1.10", 8080, "second", sila_version=2)

    async def sila1(*args: object, **kwargs: object) -> list[SiLADevice]:
      return [first]

    async def sila2(*args: object, **kwargs: object) -> list[SiLADevice]:
      return [duplicate]

    with (
      mock.patch.object(discovery_module, "_discover_sila1", sila1),
      mock.patch.object(discovery_module, "_discover_sila2", sila2),
    ):
      devices = await discover(timeout=1, interface="169.254.1.2")

    assert len(devices) == 1
    assert devices[0] in (first, duplicate)

  async def test_zeroconf_closes_before_callback_results_are_transferred(self) -> None:
    listener: object | None = None

    class FakeZeroconf:
      closed = False

      def get_service_info(self, type_: str, name: str) -> object:
        host = "192.168.1.42" if name.startswith("first") else "192.168.1.43"
        return SimpleNamespace(
          addresses=[socket.inet_aton(host)], port=8091, server=f"{name}.local."
        )

      def close(self) -> None:
        self.closed = True
        assert listener is not None
        listener.add_service(self, "_sila._tcp.local.", "closing")  # type: ignore[attr-defined]

    zc = FakeZeroconf()

    def browser(instance: FakeZeroconf, type_: str, callback: object) -> None:
      nonlocal listener
      listener = callback
      callback.add_service(instance, type_, "first")  # type: ignore[attr-defined]

    with (
      mock.patch.object(discovery_module, "HAS_ZEROCONF", True),
      mock.patch.object(discovery_module, "ServiceListener", object, create=True),
      mock.patch.object(discovery_module, "Zeroconf", return_value=zc, create=True),
      mock.patch.object(discovery_module, "ServiceBrowser", side_effect=browser, create=True),
    ):
      devices = await _discover_sila2(timeout=0.1)

    assert zc.closed
    assert [device.host for device in devices] == ["192.168.1.42", "192.168.1.43"]
    assert listener is not None
    listener.add_service(zc, "_sila._tcp.local.", "late")  # type: ignore[attr-defined]
    assert [device.host for device in devices] == ["192.168.1.42", "192.168.1.43"]

  async def test_sila2_startup_consumes_budget_and_returns_callback_before_close(self) -> None:
    class FakeZeroconf:
      closed = False

      def get_service_info(self, type_: str, name: str) -> object:
        return SimpleNamespace(
          addresses=[socket.inet_aton("192.168.1.42")], port=8091, server="instrument.local."
        )

      def close(self) -> None:
        self.closed = True

    zc = FakeZeroconf()

    def create_zeroconf() -> FakeZeroconf:
      time.sleep(0.02)
      return zc

    def browser(instance: FakeZeroconf, type_: str, callback: object) -> None:
      callback.add_service(instance, type_, "instrument")  # type: ignore[attr-defined]
      time.sleep(0.06)

    started = anyio.current_time()
    with (
      mock.patch.object(discovery_module, "HAS_ZEROCONF", True),
      mock.patch.object(discovery_module, "ServiceListener", object, create=True),
      mock.patch.object(discovery_module, "Zeroconf", side_effect=create_zeroconf, create=True),
      mock.patch.object(discovery_module, "ServiceBrowser", side_effect=browser, create=True),
      anyio.fail_after(0.3),
    ):
      devices = await _discover_sila2(timeout=0.04)

    assert zc.closed
    assert [device.host for device in devices] == ["192.168.1.42"]
    assert anyio.current_time() - started < 0.105

  async def test_sila2_expired_deadline_does_not_construct_zeroconf(self) -> None:
    with (
      mock.patch.object(discovery_module, "HAS_ZEROCONF", True),
      mock.patch.object(discovery_module, "ServiceListener", object, create=True),
      mock.patch.object(discovery_module, "Zeroconf", create=True) as zeroconf,
      mock.patch.object(discovery_module, "ServiceBrowser", create=True) as browser,
    ):
      devices = await _discover_sila2(timeout=0, deadline=anyio.current_time())

    assert devices == []
    zeroconf.assert_not_called()
    browser.assert_not_called()

  async def test_zeroconf_closes_when_browser_start_fails(self) -> None:
    zc = mock.MagicMock()
    with (
      mock.patch.object(discovery_module, "HAS_ZEROCONF", True),
      mock.patch.object(discovery_module, "ServiceListener", object, create=True),
      mock.patch.object(discovery_module, "Zeroconf", return_value=zc, create=True),
      mock.patch.object(
        discovery_module, "ServiceBrowser", side_effect=RuntimeError("browser failed"), create=True
      ),
      pytest.raises(RuntimeError, match="browser failed"),
    ):
      await _discover_sila2(timeout=1)

    zc.close.assert_called_once_with()

  async def test_zeroconf_closes_on_cancellation(self) -> None:
    browser_started = threading.Event()
    zc = mock.MagicMock()

    def browser(*args: object) -> None:
      browser_started.set()

    with (
      mock.patch.object(discovery_module, "HAS_ZEROCONF", True),
      mock.patch.object(discovery_module, "ServiceListener", object, create=True),
      mock.patch.object(discovery_module, "Zeroconf", return_value=zc, create=True),
      mock.patch.object(discovery_module, "ServiceBrowser", side_effect=browser, create=True),
    ):
      async with anyio.create_task_group() as task_group:
        task_group.start_soon(_discover_sila2, 10)
        await anyio.to_thread.run_sync(browser_started.wait, abandon_on_cancel=False)
        task_group.cancel_scope.cancel()

    zc.close.assert_called_once_with()

  async def test_ping_cancellation_awaits_subprocess_cleanup(self) -> None:
    started = anyio.Event()
    cleaned = anyio.Event()

    async def run_process(*args: object, **kwargs: object) -> object:
      started.set()
      try:
        await anyio.sleep_forever()
      finally:
        with anyio.CancelScope(shield=True):
          await anyio.sleep(0)
          cleaned.set()
      raise AssertionError("sleep_forever returned")

    with (
      mock.patch.object(discovery_module.sys, "platform", "win32"),
      mock.patch.object(discovery_module.anyio, "run_process", run_process),
    ):
      async with anyio.create_task_group() as task_group:
        task_group.start_soon(_ping_broadcast, "169.254.1.2")
        await started.wait()
        task_group.cancel_scope.cancel()

    assert cleaned.is_set()

  async def test_arp_timeout_awaits_subprocess_cleanup(self) -> None:
    cleaned = anyio.Event()
    fail_after = anyio.fail_after

    async def run_process(*args: object, **kwargs: object) -> object:
      try:
        await anyio.sleep_forever()
      finally:
        with anyio.CancelScope(shield=True):
          await anyio.sleep(0)
          cleaned.set()
      raise AssertionError("sleep_forever returned")

    with (
      mock.patch.object(discovery_module.anyio, "run_process", run_process),
      mock.patch.object(
        discovery_module.anyio, "fail_after", side_effect=lambda _: fail_after(0.01)
      ),
    ):
      assert await _arp_scan_windows("169.254.1.2") == {}

    assert cleaned.is_set()


if __name__ == "__main__":
  unittest.main()
