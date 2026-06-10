import socket
import struct
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from pylabrobot.io.sila.discovery import (
  HAS_ZEROCONF,
  SiLADevice,
  _arp_scan_bsd,
  _arp_scan_linux,
  _arp_scan_windows,
  _decode_nbns_name,
  _discover_sila2,
  _parse_device_identification,
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

  @patch("pylabrobot.io.sila.discovery._interface_name_for_ip_sync", return_value="en13")
  @patch("anyio.run_process", new_callable=AsyncMock)
  async def test_parses_link_local_entries(self, mock_run_process, _mock_iface):
    mock_result = MagicMock()
    mock_result.stdout = self.ARP_OUTPUT.encode()
    mock_run_process.return_value = mock_result

    results = await _arp_scan_bsd("169.254.229.18")
    self.assertIn("169.254.245.237", results)
    self.assertIn("169.254.99.1", results)
    # Non-link-local should be excluded
    self.assertNotIn("192.168.0.1", results)
    # Incomplete entries should be excluded
    self.assertNotIn("169.254.10.20", results)
    # Our own interface IP should be excluded
    self.assertNotIn("169.254.229.18", results)
    # Entry on a different interface should be excluded
    self.assertNotIn("169.254.50.50", results)

  @patch("pylabrobot.io.sila.discovery._interface_name_for_ip_sync", return_value="en13")
  @patch("anyio.run_process", new_callable=AsyncMock)
  async def test_empty_output(self, mock_run_process, _mock_iface):
    mock_result = MagicMock()
    mock_result.stdout = b""
    mock_run_process.return_value = mock_result

    results = await _arp_scan_bsd("169.254.229.18")
    self.assertEqual(results, {})

  @patch("pylabrobot.io.sila.discovery._interface_name_for_ip_sync", return_value=None)
  async def test_returns_empty_when_interface_unknown(self, _mock_iface):
    """If we can't resolve the interface name, return empty rather than all entries."""
    results = await _arp_scan_bsd("169.254.229.18")
    self.assertEqual(results, {})


class TestArpScanLinux(AnyioTestBase):
  PROC_NET_ARP = (
    "IP address       HW type     Flags       HW address            Mask     Device\n"
    "169.254.245.237  0x1         0x2         00:05:51:0e:e5:7e     *        eth0\n"
    "192.168.1.1      0x1         0x2         aa:bb:cc:dd:ee:ff     *        eth1\n"
    "169.254.10.20    0x1         0x0         00:00:00:00:00:00     *        eth0\n"
  )

  @patch("pylabrobot.io.sila.discovery._interface_name_for_ip_sync", return_value="eth0")
  @patch("os.path.exists", return_value=True)
  async def test_parses_proc_net_arp(self, _mock_exists, _mock_iface):
    with patch("anyio.Path.read_text", new_callable=AsyncMock) as mock_read_text:
      mock_read_text.return_value = self.PROC_NET_ARP
      results = await _arp_scan_linux("169.254.229.18")

    self.assertIn("169.254.245.237", results)
    # Non-link-local excluded
    self.assertNotIn("192.168.1.1", results)
    # Incomplete (flags=0x0) excluded
    self.assertNotIn("169.254.10.20", results)


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

  @patch("anyio.run_process", new_callable=AsyncMock)
  async def test_parses_correct_interface_section(self, mock_run_process):
    mock_result = MagicMock()
    mock_result.stdout = self.ARP_OUTPUT.encode()
    mock_run_process.return_value = mock_result

    results = await _arp_scan_windows("169.254.229.18")
    self.assertIn("169.254.245.237", results)
    self.assertIn("169.254.10.20", results)
    # This is under a different interface section
    self.assertNotIn("169.254.99.1", results)
    self.assertNotIn("192.168.0.1", results)
    # Our own IP should be excluded
    self.assertNotIn("169.254.229.18", results)


class TestDiscoverSila2(AnyioTestBase):
  @patch("pylabrobot.io.sila.discovery.HAS_ZEROCONF", False)
  async def test_no_zeroconf_returns_empty(self):
    devices = await _discover_sila2(timeout=0.1)
    self.assertEqual(devices, [])

  @unittest.skipIf(not HAS_ZEROCONF, "zeroconf not installed")
  @patch("pylabrobot.io.sila.discovery.Zeroconf", create=True)
  @patch("pylabrobot.io.sila.discovery.ServiceBrowser", create=True)
  async def test_discovers_device(self, mock_browser_cls, mock_zc_cls):
    mock_zc = MagicMock()
    mock_zc_cls.return_value = mock_zc

    mock_info = MagicMock()
    mock_info.addresses = [socket.inet_aton("192.168.1.42")]
    mock_info.port = 8091
    mock_info.server = "Pico.local."
    mock_zc.get_service_info.return_value = mock_info

    def side_effect(zc, type_, listener):
      listener.add_service(zc, type_, "test._sila._tcp.local.")

    mock_browser_cls.side_effect = side_effect

    devices = await _discover_sila2(timeout=0.1)
    self.assertEqual(len(devices), 1)
    self.assertEqual(devices[0].host, "192.168.1.42")
    self.assertEqual(devices[0].port, 8091)
    self.assertEqual(devices[0].name, "Pico.local.")
    self.assertEqual(devices[0].sila_version, 2)
