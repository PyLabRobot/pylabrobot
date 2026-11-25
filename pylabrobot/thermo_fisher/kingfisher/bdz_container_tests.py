"""Instrument-independent BindIt container validation regressions."""

import gzip
import struct
import unittest
from unittest.mock import patch

from pylabrobot.thermo_fisher.kingfisher.bdz_container import parse_xml, read_container


def container_bytes(
  parameters_type: int = 713,
  version: str = "4.0.0.45",
  properties: bytes = b"<Properties />",
  exported: bytes = b"<ExportedData />",
) -> bytes:
  """Build a synthetic container without depending on any device's codec."""

  def string(value: str) -> bytes:
    """Encode a length-prefixed UTF-8 string."""
    encoded = value.encode("utf-8")
    return struct.pack("<H", len(encoded)) + encoded

  strings = string("BindIt Software") + string(version)
  name = "Wash μ"
  trailer = (
    struct.pack("<III", 2, len(name.encode("utf-8")) + 24, parameters_type)
    + b"\x00" * 14
    + struct.pack("<I", 1)
    + string(name)
    + bytes.fromhex("01000100000000000000")
  )
  first, second = gzip.compress(properties), gzip.compress(exported)
  body = first + bytes.fromhex("01000001") + struct.pack("<I", len(second)) + second + trailer
  prefix = (
    bytes.fromhex("b6751cf201000a0001000000")
    + struct.pack("<I", len(strings))
    + strings
    + bytes.fromhex("0b0001000000")
  )
  size = len(prefix) + 12 + len(body)
  return (
    prefix
    + struct.pack("<I", size - len(prefix) - 14)
    + bytes.fromhex("00000001")
    + struct.pack("<I", len(first))
    + body
  )


class BdzContainerTests(unittest.TestCase):
  """Keep structural validation independent of instrument identity and XML semantics."""

  def test_variants_versions_and_original_payloads(self) -> None:
    """Support all current parameter types and variable-length version strings losslessly."""
    for parameters_type in (706, 711, 713, 999):
      for version in ("4.1", "4.0.0.45", "4.123.456.789"):
        with self.subTest(parameters_type=parameters_type, version=version):
          container = read_container(container_bytes(parameters_type, version))
          self.assertEqual(container.parameters_type, parameters_type)
          self.assertEqual(container.version, version)
          self.assertEqual(container.name, "Wash μ")
          self.assertEqual(container.properties, b"<Properties />")
          self.assertEqual(container.exported, b"<ExportedData />")

  def test_truncation_corruption_and_extra_bytes(self) -> None:
    """Reject truncated sections and corruption even when a gzip member still decodes."""
    raw = container_bytes()
    for size in range(len(raw)):
      with self.subTest(size=size), self.assertRaises(ValueError):
        read_container(raw[:size])
    for offset in (0, 12, 16, 49, 57, 61 + len(gzip.compress(b"<Properties />")) - 8, len(raw) - 1):
      broken = bytearray(raw)
      broken[offset] ^= 0xFF
      with self.subTest(offset=offset), self.assertRaises(ValueError):
        read_container(bytes(broken))
    with self.assertRaises(ValueError):
      read_container(raw + b"extra")

  def test_decompression_and_xml_limits(self) -> None:
    """Reject compressed expansion and unsafe XML for every instrument."""
    raw = container_bytes(properties=b" " * 1025)
    with patch("pylabrobot.thermo_fisher.kingfisher.bdz_container.MAX_XML_SIZE", 1024):
      with self.assertRaisesRegex(ValueError, "XML exceeds"):
        read_container(raw)
    for xml in (
      b"<!DOCTYPE x><Properties />",
      b"<!ENTITY x 'y'><Properties />",
      b"<Wrong />",
      b"\xff",
      b"<Properties>\x00</Properties>",
    ):
      with self.subTest(xml=xml), self.assertRaises(ValueError):
        parse_xml(xml, "Properties")
