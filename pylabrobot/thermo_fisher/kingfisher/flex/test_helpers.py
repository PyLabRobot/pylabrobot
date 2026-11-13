"""Synthetic container data for offline tests; not executable laboratory methods."""

import gzip
import struct
import xml.etree.ElementTree as ET

from .protocol import FLEX_TYPE_ID


def bdz_bytes(
  script: str = 'DTL 0 "unit test"\nTIM 1\nMOR 6\nPAU 1 "plate"\nPAU 2 "plate"\n',
  *,
  steps: str = "",
  version: str = "4.1.0.2",
) -> bytes:
  """Construct minimal BDZ sections for parser and transport tests only."""
  name = "unit test"
  props = (
    '<Properties><ExportedObject name="unit test">'
    f"<InstrumentTypeId>{FLEX_TYPE_ID}</InstrumentTypeId>"
    '</ExportedObject><InstrumentParameters type="711" /></Properties>'
  ).encode()
  root = ET.fromstring(
    "<ExportedData><Protocol><Run><PlateLayout><Plates>"
    '<Plate id="plate" name="plate" plateTypeID="8b7d7c98-275f-4285-8129-5f8ed46fb01e">'
    '<Wells><Group name="Plate"><Region x="0" y="0" columns="12" rows="8" />'
    "</Group></Wells></Plate></Plates></PlateLayout>"
    '<Protocol name="unit test" IsExecutable="true" enabled="true"><Steps>'
    '<Tip name="tips" id="2ab24e9a-b88e-4d3b-8fc3-babbc5a7c742">'
    '<Plates><Plate id="plate" /></Plates><Steps>'
    f"{steps}</Steps></Tip></Steps></Protocol></Run></Protocol></ExportedData>"
  )
  ET.SubElement(root, "CommandScript").text = script

  def string(value: str) -> bytes:
    """Pack a container string with its byte length."""
    encoded = value.encode()
    return struct.pack("<H", len(encoded)) + encoded

  strings = string("BindIt Software") + string(version)
  first, second = gzip.compress(props, mtime=0), gzip.compress(ET.tostring(root), mtime=0)
  trailer = (
    struct.pack("<III", 2, len(name.encode()) + 24, 711)
    + b"\x00" * 14
    + b"\x01\x00\x00\x00"
    + string(name)
    + bytes.fromhex("01000100000000000000")
  )
  members = (
    bytes.fromhex("00000001")
    + struct.pack("<I", len(first))
    + first
    + bytes.fromhex("01000001")
    + struct.pack("<I", len(second))
    + second
    + trailer
  )
  return (
    bytes.fromhex("b6751cf201000a0001000000")
    + struct.pack("<I", len(strings))
    + strings
    + bytes.fromhex("0b0001000000")
    + struct.pack("<I", len(members) - 10)
    + members
  )
