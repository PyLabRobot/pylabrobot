"""Offline tests for the M1000 Pro full-plate absorbance operation."""

import unittest
from struct import pack
from unittest.mock import AsyncMock, patch

from pylabrobot.resources.biorad.plates import biorad_96_wellplate_200uL_Vb
from pylabrobot.tecan.infinite_m1000_pro import InfiniteM1000Pro
from pylabrobot.tecan.infinite_m1000_pro_tests import reader_frame


def binary_block(data, sequence=0):
  checksum = 0
  for byte in data:
    checksum ^= byte
  return (
    reader_frame(f"{len(data)},BIN:", sequence)
    + data
    + bytes([sequence])
    + len(data).to_bytes(2, "big")
    + bytes([checksum])
  )


def burst(kind, blocks, sequence=0):
  header = pack(
    ">HHBBIIHI", 256, 0, 20, 0, sum(map(len, blocks)), max(map(len, blocks)), kind, 25000
  )
  return (
    binary_block(header, sequence)
    + b"".join(binary_block(b, sequence) for b in blocks)
    + reader_frame("ST", sequence)
  )


class AbsorbanceOperationTests(unittest.IsolatedAsyncioTestCase):
  async def test_full_plate_synthetic_usb_exchange(self):
    """Exercise configuration, fragmented binary packets and 96 mapped results."""
    cal = bytes.fromhex(
      "0001 1770 00000000 0000 0001 0001 0001 0001 0064 0064 0064 0064 076c 076c 076c 076c 0001 0064 07d0"
    )
    packet = bytes.fromhex("0001 1770 00000000 0000 03e8 03e8 03e8 03e8 07d0")
    scans = [[packet] * 12, [packet] * 12]
    pending = bytearray()
    commands = []

    async def write(data):
      command = data[1 : data.index(b"\x03")].decode("ascii")
      sequence = data[-5]
      commands.append(command)
      if command == "?ABSOLUTE MTP,POS":
        response = reader_frame("IN", sequence)
      elif command == "PREPARE REF":
        response = burst(1024, [cal], sequence)
      elif command.startswith("SCANX "):
        response = burst(1040, scans.pop(0), sequence)
      elif command.startswith("ABSOLUTE MTP,") or command in ("MODE ABS", "TERMINATE"):
        response = reader_frame("BY#T5000", sequence) + reader_frame("ST", sequence)
      elif (command.startswith("POSITION ") and command != "POSITION CLEAR") or command.startswith(
        ("FLASH ", "BEAM ")
      ):
        response = reader_frame("ST", sequence)
      else:
        response = reader_frame("+", sequence)
      pending.extend(response)

    async def read(timeout):
      del timeout
      if not pending:
        raise TimeoutError("test transport has no response")
      chunk = bytes(pending[:13])
      del pending[:13]
      return chunk

    with patch("pylabrobot.tecan.infinite_m1000_pro.USB") as usb_class:
      usb = usb_class.return_value
      usb.write = AsyncMock(side_effect=write)
      usb.read = AsyncMock(side_effect=read)
      device = InfiniteM1000Pro()
      device._ready = True
      device._initialized = True
      plate = biorad_96_wellplate_200uL_Vb("plate")
      result = await device.read_absorbance(
        plate, plate.get_all_items(), 600, plate_definition="GRE96ft"
      )
      self.assertEqual(len(result.data), 96)
      self.assertAlmostEqual(result.data["A1"], 0.3010299956639812)
      self.assertAlmostEqual(result.data["H12"], 0.3010299956639812)
      self.assertEqual(commands.count("PREPARE REF"), 1)
      self.assertEqual(sum(c.startswith("SCANX ") for c in commands), 2)
      self.assertEqual(commands[-1], "TERMINATE")
      self.assertFalse(pending)
      scans.extend([[packet] * 12, [packet] * 12])
      omitted_result = await device.read_absorbance(plate, plate_definition="GRE96ft")
      self.assertEqual(omitted_result.data, result.data)
      self.assertFalse(pending)

  async def test_bad_command_response_faults_without_further_commands(self):
    with patch("pylabrobot.tecan.infinite_m1000_pro.USB") as usb_class:
      usb = usb_class.return_value
      usb.write = AsyncMock()
      usb.read = AsyncMock(side_effect=[reader_frame("IN"), reader_frame("ER", 1)])
      device = InfiniteM1000Pro()
      device._ready = True
      device._initialized = True
      plate = biorad_96_wellplate_200uL_Vb("plate")
      with self.assertRaisesRegex(RuntimeError, "Unexpected response"):
        await device.read_absorbance(plate, wavelength=600, plate_definition="GRE96ft")
      self.assertTrue(device._faulted)
      self.assertEqual(usb.write.await_count, 2)

  async def test_corrupt_binary_burst_is_rejected(self):
    data = bytearray(burst(1024, [bytes(40)]))
    data[-10] ^= 1
    with patch("pylabrobot.tecan.infinite_m1000_pro.USB") as usb_class:
      usb = usb_class.return_value
      usb.write = AsyncMock()
      usb.read = AsyncMock(return_value=bytes(data))
      device = InfiniteM1000Pro()
      with self.assertRaises(ValueError):
        await device._absorbance_burst("PREPARE REF", 1024)

  async def test_absorbance_rejects_partial_wells_without_io(self):
    with patch("pylabrobot.tecan.infinite_m1000_pro.USB") as usb_class:
      usb = usb_class.return_value
      usb.write = AsyncMock()
      device = InfiniteM1000Pro()
      device._ready = True
      device._initialized = True
      plate = biorad_96_wellplate_200uL_Vb("plate")
      with self.assertRaisesRegex(ValueError, "all 96"):
        await device.read_absorbance(plate, [plate.get_item("A1")], 600, plate_definition="GRE96ft")
      usb.write.assert_not_awaited()

  async def test_empty_wells_are_rejected_before_io(self):
    with patch("pylabrobot.tecan.infinite_m1000_pro.USB") as usb_class:
      device = InfiniteM1000Pro()
      plate = biorad_96_wellplate_200uL_Vb("plate")
      with self.assertRaisesRegex(ValueError, "96 wells"):
        await device.read_absorbance(plate, [], plate_definition="GRE96ft")
      usb_class.return_value.write.assert_not_called()

  async def test_unsupported_plate_profile_does_not_write(self):
    with patch("pylabrobot.tecan.infinite_m1000_pro.USB") as usb_class:
      usb = usb_class.return_value
      usb.write = AsyncMock()
      device = InfiniteM1000Pro()
      device._ready = True
      device._initialized = True
      plate = biorad_96_wellplate_200uL_Vb("plate")
      with self.assertRaises(ValueError):
        await device.read_absorbance(plate, wavelength=600, plate_definition="other")
      usb.write.assert_not_awaited()
