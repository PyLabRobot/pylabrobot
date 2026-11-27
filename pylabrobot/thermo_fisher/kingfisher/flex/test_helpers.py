"""Synthetic container data for offline tests; not executable laboratory methods."""

import asyncio
import gzip
import struct
import unittest
import xml.etree.ElementTree as ET
from typing import Optional
from unittest.mock import MagicMock, create_autospec

from pylabrobot.io.hid import HID
from pylabrobot.resources import thermo_TS_kingfisher_96_wellplate_2000uL_Vb
from pylabrobot.thermo_fisher.kingfisher.hid import decode_report, encode_reports

from .actions import FlexAction
from .flex import KingFisherFlex
from .protocol import FLEX_TYPE_ID


def bdz_bytes(
  script: str = 'DTL 0 "unit test"\nTIM 1\nMOR 6\nPAU 1 "plate"\nPAU 2 "plate"\n',
  *,
  steps: str = "",
  version: str = "4.1.0.2",
  plates: tuple[tuple[str, str], ...] = (("plate", "plate"),),
) -> bytes:
  """Construct minimal BDZ sections for parser and transport tests only."""
  name = "unit test"
  props = (
    '<Properties><ExportedObject name="unit test">'
    f"<InstrumentTypeId>{FLEX_TYPE_ID}</InstrumentTypeId>"
    '</ExportedObject><InstrumentParameters type="711" /></Properties>'
  ).encode()
  root = ET.fromstring(
    "<ExportedData><Protocol><Run><PlateLayout><Plates /></PlateLayout>"
    '<Protocol name="unit test" IsExecutable="true" enabled="true"><Steps>'
    '<Tip name="tips" id="2ab24e9a-b88e-4d3b-8fc3-babbc5a7c742">'
    '<Plates><Plate id="plate" /></Plates><Steps>'
    f"{steps}</Steps></Tip></Steps></Protocol></Run></Protocol></ExportedData>"
  )
  layout = root.find("./Protocol/Run/PlateLayout/Plates")
  assert layout is not None
  for plate_id, plate_name in plates:
    plate = ET.SubElement(
      layout,
      "Plate",
      id=plate_id,
      name=plate_name,
      plateTypeID="8b7d7c98-275f-4285-8129-5f8ed46fb01e",
    )
    group = ET.SubElement(ET.SubElement(plate, "Wells"), "Group", name="Plate")
    ET.SubElement(group, "Region", x="0", y="0", columns="12", rows="8")
  tip_plate = root.find("./Protocol/Run/Protocol/Steps/Tip/Plates/Plate")
  assert tip_plate is not None
  if plates:
    tip_plate.set("id", plates[0][0])
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


class HIDTestCase(unittest.IsolatedAsyncioTestCase):
  """Configure autospecced HID AsyncMocks with deterministic command reply side effects."""

  io: MagicMock

  def complete_transfer(self, action: FlexAction) -> None:
    """Model a synthetic operator transfer; used only by explicit test handlers."""
    if action.action == "load" and action.plate is not None:
      if action.holder.resource is not action.plate:
        action.plate.unassign()
        action.holder.assign_child_resource(action.plate)
    elif action.action == "unload" and action.plate is not None:
      action.plate.unassign()

  def mock_hid(self) -> None:
    """Mock the real transport API; keep response controls in the test fixture."""
    self.io = create_autospec(HID, instance=True, spec_set=True)
    self.plate_bindings = {"plate": thermo_TS_kingfisher_96_wellplate_2000uL_Vb("fixture_plate")}
    self.physical_flex: Optional[KingFisherFlex] = None
    self.incoming: asyncio.Queue[bytes | Exception] = asyncio.Queue()
    self.commands: list[str] = []
    self.responses: dict[str, str | None] = {}
    self.sent: asyncio.Queue[str] = asyncio.Queue()
    self.pending_output = bytearray()
    self.auto_abort = True
    self.io.setup.side_effect = self.open_session
    self.io.read.side_effect = self.read_report
    self.io.write.side_effect = self.write_report
    self.io.send_feature_report.return_value = 3

  async def open_session(self) -> None:
    """Discard stale incoming and partial outgoing bytes on each connection."""
    self.incoming = asyncio.Queue()
    self.pending_output.clear()

  def emit(self, text: str) -> None:
    """Queue framed input for the mocked HID read method."""
    for report in encode_reports(text.encode("ascii")):
      self.incoming.put_nowait(report)

  async def read_report(self, size: int, timeout: int) -> bytes:
    """Block the read mock until a response or injected transport failure arrives."""
    self.assertEqual((size, timeout), (64, 100))
    result = await self.incoming.get()
    if isinstance(result, Exception):
      raise result
    return result

  async def write_report(self, data: bytes, report_id: bytes = b"\x00") -> int:
    """Reply to complete commands while unittest.mock records actual HID calls."""
    self.assertEqual(report_id, b"\x00")
    self.assertEqual(len(data), 64)
    payload = decode_report(data)
    if payload == b"\x1b":
      if self.auto_abort:
        self.emit("ABO END 0\r\n")
      return 65
    self.pending_output.extend(payload)
    while b"\r\n" in self.pending_output:
      end = self.pending_output.index(b"\r\n")
      line = self.pending_output[:end].decode("ascii")
      del self.pending_output[: end + 2]
      self.commands.append(line)
      self.sent.put_nowait(line)
      if self.physical_flex is not None and line.startswith(("PAU 1 ", "PAU 2 ")):
        holder = self.physical_flex.loading_holder
        assert holder is not None
        plate = self.plate_bindings[line.split('"')[1]]
        if line.startswith("PAU 1 ") and holder.resource is not plate:
          plate.unassign()
          holder.assign_child_resource(plate)
        elif line.startswith("PAU 2 "):
          plate.unassign()
      if line in self.responses:
        response = self.responses[line]
      elif line == "VER":
        response = "VER\r\nVER KingFisherFlex 1.01.0 fixture\r\nVER END 0\r\n"
      else:
        response = f"{line[:3]}\r\n{line[:3]} END 0\r\n"
      if response is not None:
        self.emit(response)
    return 65

  async def wait_sent(self, expected: str) -> None:
    """Synchronize with a transmitted command without arbitrary sleeps."""
    while await asyncio.wait_for(self.sent.get(), 10) != expected:
      pass
