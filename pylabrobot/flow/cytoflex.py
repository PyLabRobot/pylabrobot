import array
import asyncio
import struct
from typing import List, Tuple

import flowkit as fk
import numpy as np

from pylabrobot.io.usb import USB
from pylabrobot.machines.backend import MachineBackend

# === Wire format for bulk-IN event packets (internal; not public API). =========
# Each event is 33 little-endian int32s (132 bytes). Bytes 0..14 are H-channel
# ADC readings, byte 15 is unused, bytes 16..30 are A-channel readings (paired
# with byte n-16), byte 31 is unused, byte 32 is a 50 kHz tick counter.
# Verified bit-exact (≤1 LSB) against FCS files produced by CytExpert across
# 14 wells.

# FCS column index -> byte index inside each 33-int event.
_H_BYTE_FOR_FCS = {
  0: 14,
  2: 11,
  4: 12,
  6: 13,
  8: 8,
  10: 9,
  12: 10,
  14: 0,
  16: 1,
  18: 2,
  20: 3,
  22: 5,
  24: 4,
  26: 6,
  28: 7,
}
_A_BYTE_FOR_FCS = {
  1: 30,
  3: 27,
  5: 28,
  7: 29,
  9: 24,
  11: 25,
  13: 26,
  15: 16,
  17: 17,
  19: 18,
  21: 19,
  23: 21,
  25: 20,
  27: 22,
  29: 23,
}
_H_GAIN = 8.0
_A_GAIN = 0.5
_TIMESTEP_SEC = 2e-5  # device tick period (= FCS $TIMESTEP)

# FCS PnN order. FSC-Width is derived from FSC-A / FSC-H; Time is the byte-32 tick.
FCS_CHANNEL_NAMES: List[str] = [
  "FSC-H",
  "FSC-A",
  "SSC-H",
  "SSC-A",
  "FL5-H",
  "FL5-A",
  "FL6-H",
  "FL6-A",
  "FL11-H",
  "FL11-A",
  "FL12-H",
  "FL12-A",
  "FL13-H",
  "FL13-A",
  "FL1-H",
  "FL1-A",
  "FL2-H",
  "FL2-A",
  "FL3-H",
  "FL3-A",
  "FL4-H",
  "FL4-A",
  "FL7-H",
  "FL7-A",
  "FL8-H",
  "FL8-A",
  "FL9-H",
  "FL9-A",
  "FL10-H",
  "FL10-A",
  "FSC-Width",
  "Time",
]


def _decode_raw_events(flow_packets: List[bytes]) -> np.ndarray:
  """Return an (N, 33) int64 array of unique raw events parsed from bulk-IN packets.

  Each packet starts with a 20-byte preamble (55 55 55 55 ff ff ...) followed
  by a stream of 132-byte event records. The device retransmits its buffer
  until new events arrive, so this function deduplicates byte-identical rows
  while preserving the order in which each row first appeared.

  Not part of the public API; exported for test access.
  """
  rows: List[Tuple[int, ...]] = []
  for packet in flow_packets:
    body = packet[20:]
    for k in range(len(body) // 132):
      section = body[k * 132 : (k + 1) * 132]
      if not any(section):
        continue
      rows.append(struct.unpack("<33i", section))
  raw = np.asarray(rows, dtype=np.int64)
  if not len(raw):
    return raw
  _, first_idx = np.unique(raw, axis=0, return_index=True)
  return raw[np.sort(first_idx)]


def _events_from_raw(raw: np.ndarray) -> np.ndarray:
  """Transform an (N, 33) raw-int array into an (N, 32) float64 array in
  FCS PnN column order, with H/A gains applied, FSC-Width derived from
  FSC-A/FSC-H, and Time expressed in seconds (raw tick × $TIMESTEP).

  Warmup events at the start of the stream carry non-monotonic tick values;
  callers are expected to filter them upstream.

  Not part of the public API; exported for test access.
  """
  n = len(raw)
  events = np.zeros((n, len(FCS_CHANNEL_NAMES)), dtype=np.float64)
  if not n:
    return events
  for fci, byte_i in _H_BYTE_FOR_FCS.items():
    events[:, fci] = _H_GAIN * raw[:, byte_i]
  for fci, byte_i in _A_BYTE_FOR_FCS.items():
    events[:, fci] = _A_GAIN * raw[:, byte_i]
  with np.errstate(divide="ignore", invalid="ignore"):
    events[:, 30] = np.where(events[:, 0] != 0, events[:, 1] / events[:, 0] * 1024.0, 0.0)
  events[:, 31] = raw[:, 32] * _TIMESTEP_SEC
  return events


def _decode_flow_packets(flow_packets: List[bytes]) -> Tuple[np.ndarray, np.ndarray]:
  """Convenience wrapper: returns (raw_int_array, events_in_fcs_order)."""
  raw = _decode_raw_events(flow_packets)
  return raw, _events_from_raw(raw)


class CytoFlex(MachineBackend):
  _t = 0.005

  def __init__(self):
    super().__init__()
    self.io = USB(id_vendor=0x4321, id_product=0x0001)

  async def setup(self):
    await self.io.setup()

    await self._home()
    await self._initialize()

  async def stop(self):
    await self._deinitialize()
    await self.io.stop()

  async def _read(self, wValue: int, wIndex: int) -> bytes:
    status = self.io.ctrl_transfer(
      bmRequestType=0xC0,
      bRequest=188,
      wValue=wValue,  # num bytes to read
      wIndex=wIndex,
      data_or_wLength=512,
    )
    assert status == array.array("B", [188]), f"CONTROL response data: bc but got {status}"

    await asyncio.sleep(self._t)
    data = self.io.read()
    return data

  async def _write(self, data: bytes, wIndex: int):
    status = self.io.ctrl_transfer(
      bmRequestType=0xC0, bRequest=187, wValue=len(data), wIndex=wIndex, data_or_wLength=512
    )
    assert status == array.array("B", [187]), f"CONTROL response data: bb but got {status}"

    await asyncio.sleep(self._t)
    self.io.write(data)

  async def _do_b55(self):
    b55 = await self._read(wValue=0x1, wIndex=0x0)
    assert b55 == array.array("B", [0x55]), f"CONTROL response data: 55 but got {b55}"

  async def write_at_address(self, data: bytes, wIndex: int, verify=True):
    await self._write(data, wIndex=wIndex)
    await self._do_b55()

    if verify:
      read_data = await self._read(wValue=len(data), wIndex=wIndex)
      assert (
        bytes(read_data) == data
      ), f"CONTROL response data: {data.hex()} but got {read_data.hex()}"
      await self._do_b55()

  async def send_command(self, command: bytes):
    assert len(command) == 64, f"Command length should be 64 bytes but got {len(command)}"

    await self._write(command, wIndex=0x600)
    await self._do_b55()
    await self._write(
      bytes.fromhex(
        "00000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000"
      ),
      wIndex=0x600,
    )
    await self._do_b55()
    data = await self._read(wValue=0x40, wIndex=0x600)
    await self._do_b55()
    return data

  async def send_frame(self, opcode: int, payload: bytes = b"", flag: int = 0x00):
    return await self.send_command(self._frame(opcode, payload, flag))

  # Status word at response[10:12]: 5a5a = pending, a5a5 = ready.
  # Convention shared across opcodes 0x36, 0x38, 0x39, 0x3a, 0x3b.
  _STATUS_PENDING = b"\x5a\x5a"
  _STATUS_READY = b"\xa5\xa5"

  async def get_is_done(self) -> bool:
    response = await self.send_frame(0x3B)
    status = response[10:12]
    if status == self._STATUS_PENDING:
      return False
    if status == self._STATUS_READY:
      return True
    raise ValueError(f"Unexpected status word {status.hex()} in: {response.hex()}")

  async def _wait_for_done(self, timeout: float = 30.0, poll_interval: float = 0.1):
    deadline = asyncio.get_event_loop().time() + timeout
    while not await self.get_is_done():
      if asyncio.get_event_loop().time() > deadline:
        raise TimeoutError(f"Operation did not complete within {timeout}s")
      await asyncio.sleep(poll_interval)

  async def _home(self):
    status = self.io.ctrl_transfer(
      bmRequestType=0x80, bRequest=0, wValue=0x0, wIndex=0x0, data_or_wLength=2
    )

    status = self.io.ctrl_transfer(
      bmRequestType=0xC0, bRequest=182, wValue=0x0, wIndex=0x0, data_or_wLength=512
    )

    assert status == array.array("B", [0xB6]), f"CONTROL response data: b6 but got {status}"

    status = self.io.ctrl_transfer(
      bmRequestType=0xC0, bRequest=180, wValue=0x3C, wIndex=0x20, data_or_wLength=33
    )

    assert (
      status
      == array.array(
        "B",
        [
          0x01,
          0x0A,
          0x43,
          0x79,
          0x74,
          0x6F,
          0x46,
          0x4C,
          0x45,
          0x58,
          0x20,
          0x53,
          0x00,
          0x00,
          0x00,
          0x00,
          0x00,
          0x00,
          0x00,
          0x00,
          0x00,
          0x00,
          0x00,
          0x00,
          0x00,
          0x00,
          0x00,
          0x00,
          0x00,
          0x00,
          0x00,
          0x00,
          0x00,
        ],
      )
    ), f"CONTROL response data: 010a4379746f464c45582053000000000000000000000000000000000000000000 but got {status}"

    status = self.io.ctrl_transfer(
      bmRequestType=0xC0, bRequest=180, wValue=0x203C, wIndex=0x20, data_or_wLength=33
    )

    assert (
      status
      == array.array(
        "B",
        [
          0x01,
          0x0F,
          0x42,
          0x65,
          0x63,
          0x6B,
          0x6D,
          0x61,
          0x6E,
          0x20,
          0x43,
          0x6F,
          0x75,
          0x6C,
          0x74,
          0x65,
          0x72,
          0x00,
          0x00,
          0x00,
          0x00,
          0x00,
          0x00,
          0x00,
          0x00,
          0x00,
          0x00,
          0x00,
          0x00,
          0x00,
          0x00,
          0x00,
          0x00,
        ],
      )
    ), f"CONTROL response data: 010f4265636b6d616e20436f756c74657200000000000000000000000000000000 but got {status}"

    status = self.io.ctrl_transfer(
      bmRequestType=0xC0, bRequest=180, wValue=0x403C, wIndex=0x20, data_or_wLength=33
    )

    assert (
      status
      == array.array(
        "B",
        [
          0x01,
          0x07,
          0x41,
          0x57,
          0x34,
          0x34,
          0x31,
          0x32,
          0x32,
          0x00,
          0x00,
          0x00,
          0x00,
          0x00,
          0x00,
          0x00,
          0x00,
          0x00,
          0x00,
          0x00,
          0x00,
          0x00,
          0x00,
          0x00,
          0x00,
          0x00,
          0x00,
          0x00,
          0x00,
          0x00,
          0x00,
          0x00,
          0x00,
        ],
      )
    ), f"CONTROL response data: 010741573434313232000000000000000000000000000000000000000000000000 but got {status}"

    await self._read(wValue=0x1, wIndex=0x0)
    await self._read(wValue=0x1, wIndex=0x0)
    await self._read(wValue=0x1, wIndex=0x0)

    await self.write_at_address(bytes.fromhex("00"), wIndex=0x300)
    await self.write_at_address(bytes.fromhex("03"), wIndex=0x300)
    await self.write_at_address(bytes.fromhex("00"), wIndex=0x3000)
    await self.write_at_address(bytes.fromhex("00"), wIndex=0x4100)
    await self.write_at_address(bytes.fromhex("49"), wIndex=0x4000)
    await self.write_at_address(bytes.fromhex("06800000"), wIndex=0x400)
    await self.write_at_address(bytes.fromhex("06800100"), wIndex=0x400)
    await self.write_at_address(bytes.fromhex("06"), wIndex=0x4100)
    await self.write_at_address(bytes.fromhex("42"), wIndex=0x4000)
    await self.write_at_address(bytes.fromhex("80"), wIndex=0x4100)
    await self.write_at_address(bytes.fromhex("43"), wIndex=0x4000)
    await self.write_at_address(bytes.fromhex("06"), wIndex=0x4100)
    await self.write_at_address(bytes.fromhex("44"), wIndex=0x4000)
    await self.write_at_address(bytes.fromhex("80"), wIndex=0x4100)
    await self.write_at_address(bytes.fromhex("45"), wIndex=0x4000)
    await self.write_at_address(bytes.fromhex("015d0000"), wIndex=0x500)
    await self.write_at_address(bytes.fromhex("01620000"), wIndex=0x500)
    await self.write_at_address(bytes.fromhex("01630000"), wIndex=0x500)
    await self.write_at_address(bytes.fromhex("08680000"), wIndex=0x500)
    await self.write_at_address(bytes.fromhex("186c0000"), wIndex=0x500)
    await self.write_at_address(bytes.fromhex("006d0000"), wIndex=0x500)
    await self.write_at_address(bytes.fromhex("006e0000"), wIndex=0x500)
    await self.write_at_address(bytes.fromhex("d87e0000"), wIndex=0x500)
    await self.write_at_address(bytes.fromhex("007f0000"), wIndex=0x500)
    await self.write_at_address(bytes.fromhex("00"), wIndex=0x4100)
    await self.write_at_address(bytes.fromhex("48"), wIndex=0x4000)
    await self.write_at_address(bytes.fromhex("01"), wIndex=0x4100)
    await self.write_at_address(bytes.fromhex("48"), wIndex=0x4000)
    await self.write_at_address(bytes.fromhex("01"), wIndex=0x4100)
    await self.write_at_address(bytes.fromhex("46"), wIndex=0x4000)
    await self.write_at_address(bytes.fromhex("5d"), wIndex=0x4100)
    await self.write_at_address(bytes.fromhex("47"), wIndex=0x4000)
    await self.write_at_address(bytes.fromhex("01"), wIndex=0x4100)
    await self.write_at_address(bytes.fromhex("46"), wIndex=0x4000)
    await self.write_at_address(bytes.fromhex("62"), wIndex=0x4100)
    await self.write_at_address(bytes.fromhex("47"), wIndex=0x4000)
    await self.write_at_address(bytes.fromhex("01"), wIndex=0x4100)
    await self.write_at_address(bytes.fromhex("46"), wIndex=0x4000)
    await self.write_at_address(bytes.fromhex("63"), wIndex=0x4100)
    await self.write_at_address(bytes.fromhex("47"), wIndex=0x4000)
    await self.write_at_address(bytes.fromhex("08"), wIndex=0x4100)
    await self.write_at_address(bytes.fromhex("46"), wIndex=0x4000)
    await self.write_at_address(bytes.fromhex("68"), wIndex=0x4100)
    await self.write_at_address(bytes.fromhex("47"), wIndex=0x4000)
    await self.write_at_address(bytes.fromhex("18"), wIndex=0x4100)
    await self.write_at_address(bytes.fromhex("46"), wIndex=0x4000)
    await self.write_at_address(bytes.fromhex("6c"), wIndex=0x4100)
    await self.write_at_address(bytes.fromhex("47"), wIndex=0x4000)
    await self.write_at_address(bytes.fromhex("00"), wIndex=0x4100)
    await self.write_at_address(bytes.fromhex("46"), wIndex=0x4000)
    await self.write_at_address(bytes.fromhex("6d"), wIndex=0x4100)
    await self.write_at_address(bytes.fromhex("47"), wIndex=0x4000)
    await self.write_at_address(bytes.fromhex("00"), wIndex=0x4100)
    await self.write_at_address(bytes.fromhex("46"), wIndex=0x4000)
    await self.write_at_address(bytes.fromhex("6e"), wIndex=0x4100)
    await self.write_at_address(bytes.fromhex("47"), wIndex=0x4000)
    await self.write_at_address(bytes.fromhex("d8"), wIndex=0x4100)
    await self.write_at_address(bytes.fromhex("46"), wIndex=0x4000)
    await self.write_at_address(bytes.fromhex("7e"), wIndex=0x4100)
    await self.write_at_address(bytes.fromhex("47"), wIndex=0x4000)
    await self.write_at_address(bytes.fromhex("00"), wIndex=0x4100)
    await self.write_at_address(bytes.fromhex("46"), wIndex=0x4000)
    await self.write_at_address(bytes.fromhex("7f"), wIndex=0x4100)
    await self.write_at_address(bytes.fromhex("47"), wIndex=0x4000)
    await self.write_at_address(bytes.fromhex("ffff0000"), wIndex=0x1500)
    await self.write_at_address(bytes.fromhex("e003"), wIndex=0x1600)

    await self.get_status()

    await self.send_frame(0x04, bytes.fromhex("02"))
    await self.send_frame(0x03, bytes.fromhex("0200000000ff"), flag=0x80)
    await self.send_frame(0x03, bytes.fromhex("0200010000ff"), flag=0x80)
    await self.send_frame(0x04, bytes.fromhex("07"))
    await self.send_frame(0x03, bytes.fromhex("0700000000ff"), flag=0x80)
    await self.send_frame(0x03, bytes.fromhex("0700010000ff"), flag=0x80)
    await self.send_frame(0x14, bytes.fromhex("5a5a"), flag=0x80)
    await self.send_frame(0x14, b"", flag=0x80)
    await self.send_frame(0x14, bytes.fromhex("5a5a"), flag=0x80)
    await self.send_frame(0x14, b"", flag=0x80)
    await self.send_frame(0x14, bytes.fromhex("5a5a"), flag=0x80)
    await self.send_frame(0x14, b"", flag=0x80)

    data = await self._read(wValue=0x1, wIndex=0x0)

    data = await self._read(wValue=0x2, wIndex=0x100)
    data = await self._read(wValue=0x1, wIndex=0x0)
    data = await self._read(wValue=0x4, wIndex=0x200)
    data = await self._read(wValue=0x1, wIndex=0x0)

    await self.write_at_address(bytes.fromhex("01"), wIndex=0x4000)

    data = await self._read(wValue=0x1, wIndex=0x4200)
    data = await self._read(wValue=0x1, wIndex=0x0)

    await self.get_status()

    await self.send_frame(0x04, bytes.fromhex("0100000008"))

    await self.write_at_address(
      bytes.fromhex(
        "c200c200c200c20019fe19fe19fe19fef7fef7fef7fe000000000000000000000000000000000000000000000000000000000000000000000000000000000000"
      ),
      wIndex=0x1200,
    )
    await self.write_at_address(
      bytes.fromhex("1818181818181818181818181818180000000000000000000000000000000000"),
      wIndex=0x1400,
    )
    await self.write_at_address(bytes.fromhex("18"), wIndex=0x1300)
    await self.write_at_address(
      bytes.fromhex("1818181818181818181818181818180000000000000000000000000000000000"),
      wIndex=0x1400,
    )
    await self.write_at_address(bytes.fromhex("18"), wIndex=0x1300)
    await self.write_at_address(
      bytes.fromhex("1818181818181818181818181818180000000000000000000000000000000000"),
      wIndex=0x1400,
    )
    await self.write_at_address(bytes.fromhex("18"), wIndex=0x1300)
    await self.write_at_address(
      bytes.fromhex("1818181818181818181818181818180000000000000000000000000000000000"),
      wIndex=0x1400,
    )
    await self.write_at_address(bytes.fromhex("18"), wIndex=0x1300)
    await self.write_at_address(
      bytes.fromhex("1818181818181818181818181818180000000000000000000000000000000000"),
      wIndex=0x1400,
    )
    await self.write_at_address(bytes.fromhex("18"), wIndex=0x1300)
    await self.write_at_address(
      bytes.fromhex("1818181818181818181818181818180000000000000000000000000000000000"),
      wIndex=0x1400,
    )
    await self.write_at_address(bytes.fromhex("18"), wIndex=0x1300)
    await self.write_at_address(
      bytes.fromhex("1818181818181818181818181818180000000000000000000000000000000000"),
      wIndex=0x1400,
    )
    await self.write_at_address(bytes.fromhex("18"), wIndex=0x1300)
    await self.write_at_address(
      bytes.fromhex("1818181818181818181818181818180000000000000000000000000000000000"),
      wIndex=0x1400,
    )
    await self.write_at_address(bytes.fromhex("18"), wIndex=0x1300)
    await self.write_at_address(
      bytes.fromhex("1818181818181818181818181818180000000000000000000000000000000000"),
      wIndex=0x1400,
    )
    await self.write_at_address(bytes.fromhex("18"), wIndex=0x1300)
    await self.write_at_address(
      bytes.fromhex("1818181818181818181818181818180000000000000000000000000000000000"),
      wIndex=0x1400,
    )
    await self.write_at_address(bytes.fromhex("18"), wIndex=0x1300)
    await self.write_at_address(
      bytes.fromhex("1818181818181818181818181818180000000000000000000000000000000000"),
      wIndex=0x1400,
    )
    await self.write_at_address(bytes.fromhex("18"), wIndex=0x1300)
    await self.write_at_address(
      bytes.fromhex("1818181818181818181818181818180000000000000000000000000000000000"),
      wIndex=0x1400,
    )
    await self.write_at_address(bytes.fromhex("18"), wIndex=0x1300)
    await self.write_at_address(
      bytes.fromhex("1818181818181818181818181818180000000000000000000000000000000000"),
      wIndex=0x1400,
    )
    await self.write_at_address(bytes.fromhex("18"), wIndex=0x1300)
    await self.write_at_address(
      bytes.fromhex("1818181818181818181818181818180000000000000000000000000000000000"),
      wIndex=0x1400,
    )
    await self.write_at_address(bytes.fromhex("18"), wIndex=0x1300)
    await self.write_at_address(
      bytes.fromhex("1818181818181818181818181818180000000000000000000000000000000000"),
      wIndex=0x1400,
    )
    await self.write_at_address(bytes.fromhex("18"), wIndex=0x1300)
    await self.write_at_address(bytes.fromhex("02"), wIndex=0x4100)
    await self.write_at_address(bytes.fromhex("42"), wIndex=0x4000)
    await self.write_at_address(bytes.fromhex("65"), wIndex=0x4100)
    await self.write_at_address(bytes.fromhex("43"), wIndex=0x4000)
    await self.write_at_address(bytes.fromhex("75"), wIndex=0x4100)
    await self.write_at_address(bytes.fromhex("42"), wIndex=0x4000)
    await self.write_at_address(bytes.fromhex("39"), wIndex=0x4100)
    await self.write_at_address(bytes.fromhex("43"), wIndex=0x4000)
    await self.write_at_address(bytes.fromhex("a8"), wIndex=0x4100)
    await self.write_at_address(bytes.fromhex("42"), wIndex=0x4000)
    await self.write_at_address(bytes.fromhex("4a"), wIndex=0x4100)
    await self.write_at_address(bytes.fromhex("43"), wIndex=0x4000)
    await self.write_at_address(bytes.fromhex("44"), wIndex=0x4100)
    await self.write_at_address(bytes.fromhex("42"), wIndex=0x4000)
    await self.write_at_address(bytes.fromhex("5a"), wIndex=0x4100)
    await self.write_at_address(bytes.fromhex("43"), wIndex=0x4000)
    await self.write_at_address(bytes.fromhex("08"), wIndex=0x4100)
    await self.write_at_address(bytes.fromhex("42"), wIndex=0x4000)
    await self.write_at_address(bytes.fromhex("0b"), wIndex=0x4100)
    await self.write_at_address(bytes.fromhex("43"), wIndex=0x4000)
    await self.write_at_address(bytes.fromhex("d5"), wIndex=0x4100)
    await self.write_at_address(bytes.fromhex("42"), wIndex=0x4000)
    await self.write_at_address(bytes.fromhex("1a"), wIndex=0x4100)
    await self.write_at_address(bytes.fromhex("43"), wIndex=0x4000)
    await self.write_at_address(bytes.fromhex("6d"), wIndex=0x4100)
    await self.write_at_address(bytes.fromhex("42"), wIndex=0x4000)
    await self.write_at_address(bytes.fromhex("2a"), wIndex=0x4100)
    await self.write_at_address(bytes.fromhex("43"), wIndex=0x4000)
    await self.write_at_address(bytes.fromhex("400a0000"), wIndex=0x400)
    await self.write_at_address(bytes.fromhex("46190000"), wIndex=0x400)
    await self.write_at_address(bytes.fromhex("5d2b0000"), wIndex=0x400)
    await self.write_at_address(bytes.fromhex("f0390000"), wIndex=0x400)
    await self.write_at_address(bytes.fromhex("9a5a0000"), wIndex=0x400)
    await self.write_at_address(bytes.fromhex("be4a0000"), wIndex=0x400)
    await self.write_at_address(bytes.fromhex("23690000"), wIndex=0x400)
    await self.write_at_address(bytes.fromhex("5a7a0000"), wIndex=0x400)
    await self.write_at_address(bytes.fromhex("000000004000000e"), wIndex=0x1000)
    await self.write_at_address(bytes.fromhex("0000000008000000"), wIndex=0x1100)
    await self.write_at_address(bytes.fromhex("000000004000000e"), wIndex=0x1000)
    await self.write_at_address(bytes.fromhex("0000000008000000"), wIndex=0x1100)
    await self.write_at_address(bytes.fromhex("000000004000000e"), wIndex=0x1000)
    await self.write_at_address(bytes.fromhex("0000000008000000"), wIndex=0x1100)
    await self.write_at_address(bytes.fromhex("000000004000000e"), wIndex=0x1000)
    await self.write_at_address(bytes.fromhex("0000000008000000"), wIndex=0x1100)

    await self.send_frame(0x25, bytes.fromhex("5a5a"), flag=0x80)
    await self.send_frame(0x04, bytes.fromhex("0100000108"))
    await self.send_frame(0x26)
    await self.send_frame(0x04, bytes.fromhex("0100000108"))
    await self.send_frame(0x26)
    await self.send_frame(0x12, b"", flag=0x80)
    await self.send_frame(0x13, b"", flag=0x80)
    await self.send_frame(0x19, b"", flag=0x80)
    await self.send_frame(0x1B, b"", flag=0x80)
    await self.send_frame(0x0F, b"", flag=0x80)
    await self.send_frame(
      0x01, bytes.fromhex("0000000000000000000000000000000000000008"), flag=0x80
    )
    await self.send_frame(0x0A, b"", flag=0x80)
    await self.send_frame(0x0C, b"", flag=0x80)
    await self.send_frame(0x0E, b"", flag=0x80)
    await self.send_frame(0x0D, b"", flag=0x80)
    await self.send_frame(0x0B, b"", flag=0x80)
    await self.send_frame(0x11, b"", flag=0x80)
    await self.send_frame(0x10, b"", flag=0x80)
    await self.send_frame(0x1C, b"", flag=0x80)
    await self.send_frame(0x30, bytes.fromhex("5a5a"), flag=0x80)

    await self._wait_for_done()

  async def _initialize(self):
    await self.get_status()

    await self.send_frame(0x04, bytes.fromhex("0100000108"))
    await self.send_frame(0x26)
    await self.get_status()

    await self.send_frame(0x03, bytes.fromhex("020001005501"), flag=0x80)
    await self.send_frame(0x03, bytes.fromhex("020000005afa"), flag=0x80)
    await self.send_frame(0x03, bytes.fromhex("070001009287"), flag=0x80)
    await self.send_frame(0x03, bytes.fromhex("070000006bfa"), flag=0x80)
    await self.send_frame(0x0D, b"", flag=0x80)
    await self.send_frame(0x11, b"", flag=0x80)
    await self.send_frame(
      0x01, bytes.fromhex("0000000000000000000000000000000000000008"), flag=0x80
    )
    await self.send_frame(0x0A, b"", flag=0x80)
    await self.send_frame(0x10, b"", flag=0x80)
    await self.send_frame(0x0F, bytes.fromhex("5a5a"), flag=0x80)
    await self.send_frame(0x0E, b"", flag=0x80)
    await self.send_frame(0x0C, bytes.fromhex("5a5a"), flag=0x80)
    await self.send_frame(0x0B, bytes.fromhex("5a5a"), flag=0x80)
    await self.send_frame(0x12, bytes.fromhex("5a5a"), flag=0x80)
    await self.send_frame(0x13, bytes.fromhex("5a5a"), flag=0x80)
    await self.send_frame(0x1B, bytes.fromhex("5a5a"), flag=0x80)
    await self.send_frame(0x19, bytes.fromhex("5a5a"), flag=0x80)

    await self.write_at_address(bytes.fromhex("02"), wIndex=0x4100)
    await self.write_at_address(bytes.fromhex("42"), wIndex=0x4000)
    await self.write_at_address(bytes.fromhex("65"), wIndex=0x4100)
    await self.write_at_address(bytes.fromhex("43"), wIndex=0x4000)
    await self.write_at_address(bytes.fromhex("75"), wIndex=0x4100)
    await self.write_at_address(bytes.fromhex("42"), wIndex=0x4000)
    await self.write_at_address(bytes.fromhex("39"), wIndex=0x4100)
    await self.write_at_address(bytes.fromhex("43"), wIndex=0x4000)
    await self.write_at_address(bytes.fromhex("a8"), wIndex=0x4100)
    await self.write_at_address(bytes.fromhex("42"), wIndex=0x4000)
    await self.write_at_address(bytes.fromhex("4a"), wIndex=0x4100)
    await self.write_at_address(bytes.fromhex("43"), wIndex=0x4000)
    await self.write_at_address(bytes.fromhex("44"), wIndex=0x4100)
    await self.write_at_address(bytes.fromhex("42"), wIndex=0x4000)
    await self.write_at_address(bytes.fromhex("5a"), wIndex=0x4100)
    await self.write_at_address(bytes.fromhex("43"), wIndex=0x4000)
    await self.write_at_address(bytes.fromhex("08"), wIndex=0x4100)
    await self.write_at_address(bytes.fromhex("42"), wIndex=0x4000)
    await self.write_at_address(bytes.fromhex("0b"), wIndex=0x4100)
    await self.write_at_address(bytes.fromhex("43"), wIndex=0x4000)
    await self.write_at_address(bytes.fromhex("d5"), wIndex=0x4100)
    await self.write_at_address(bytes.fromhex("42"), wIndex=0x4000)
    await self.write_at_address(bytes.fromhex("1a"), wIndex=0x4100)
    await self.write_at_address(bytes.fromhex("43"), wIndex=0x4000)
    await self.write_at_address(bytes.fromhex("6d"), wIndex=0x4100)
    await self.write_at_address(bytes.fromhex("42"), wIndex=0x4000)
    await self.write_at_address(bytes.fromhex("2a"), wIndex=0x4100)
    await self.write_at_address(bytes.fromhex("43"), wIndex=0x4000)
    await self.write_at_address(bytes.fromhex("400a0000"), wIndex=0x400)
    await self.write_at_address(bytes.fromhex("46190000"), wIndex=0x400)
    await self.write_at_address(bytes.fromhex("5d2b0000"), wIndex=0x400)
    await self.write_at_address(bytes.fromhex("f0390000"), wIndex=0x400)
    await self.write_at_address(bytes.fromhex("9a5a0000"), wIndex=0x400)
    await self.write_at_address(bytes.fromhex("be4a0000"), wIndex=0x400)
    await self.write_at_address(bytes.fromhex("23690000"), wIndex=0x400)
    await self.write_at_address(bytes.fromhex("5a7a0000"), wIndex=0x400)
    await self.write_at_address(
      bytes.fromhex(
        "c200c200c200c20019fe19fe19fe19fef7fef7fef7fe000000000000000000000000000000000000000000000000000000000000000000000000000000000000"
      ),
      wIndex=0x1200,
    )
    await self.write_at_address(
      bytes.fromhex("1818181818181818181818181818180000000000000000000000000000000000"),
      wIndex=0x1400,
    )
    await self.write_at_address(bytes.fromhex("18"), wIndex=0x1300)
    await self.write_at_address(
      bytes.fromhex("1818181818181818181818181818180000000000000000000000000000000000"),
      wIndex=0x1400,
    )
    await self.write_at_address(bytes.fromhex("18"), wIndex=0x1300)
    await self.write_at_address(
      bytes.fromhex("1818181818181818181818181818180000000000000000000000000000000000"),
      wIndex=0x1400,
    )
    await self.write_at_address(bytes.fromhex("18"), wIndex=0x1300)
    await self.write_at_address(
      bytes.fromhex("1818181818181818181818181818180000000000000000000000000000000000"),
      wIndex=0x1400,
    )
    await self.write_at_address(bytes.fromhex("18"), wIndex=0x1300)
    await self.write_at_address(
      bytes.fromhex("1818181818181818181818181818180000000000000000000000000000000000"),
      wIndex=0x1400,
    )
    await self.write_at_address(bytes.fromhex("18"), wIndex=0x1300)
    await self.write_at_address(
      bytes.fromhex("1818181818181818181818181818180000000000000000000000000000000000"),
      wIndex=0x1400,
    )
    await self.write_at_address(bytes.fromhex("18"), wIndex=0x1300)
    await self.write_at_address(
      bytes.fromhex("1818181818181818181818181818180000000000000000000000000000000000"),
      wIndex=0x1400,
    )
    await self.write_at_address(bytes.fromhex("18"), wIndex=0x1300)
    await self.write_at_address(
      bytes.fromhex("1818181818181818181818181818180000000000000000000000000000000000"),
      wIndex=0x1400,
    )
    await self.write_at_address(bytes.fromhex("18"), wIndex=0x1300)
    await self.write_at_address(
      bytes.fromhex("1818181818181818181818181818180000000000000000000000000000000000"),
      wIndex=0x1400,
    )
    await self.write_at_address(bytes.fromhex("18"), wIndex=0x1300)
    await self.write_at_address(
      bytes.fromhex("1818181818181818181818181818180000000000000000000000000000000000"),
      wIndex=0x1400,
    )
    await self.write_at_address(bytes.fromhex("18"), wIndex=0x1300)
    await self.write_at_address(
      bytes.fromhex("1818181818181818181818181818180000000000000000000000000000000000"),
      wIndex=0x1400,
    )
    await self.write_at_address(bytes.fromhex("18"), wIndex=0x1300)
    await self.write_at_address(
      bytes.fromhex("1818181818181818181818181818180000000000000000000000000000000000"),
      wIndex=0x1400,
    )
    await self.write_at_address(bytes.fromhex("18"), wIndex=0x1300)
    await self.write_at_address(
      bytes.fromhex("1818181818181818181818181818180000000000000000000000000000000000"),
      wIndex=0x1400,
    )
    await self.write_at_address(bytes.fromhex("18"), wIndex=0x1300)
    await self.write_at_address(
      bytes.fromhex("1818181818181818181818181818180000000000000000000000000000000000"),
      wIndex=0x1400,
    )
    await self.write_at_address(bytes.fromhex("18"), wIndex=0x1300)
    await self.write_at_address(
      bytes.fromhex("1818181818181818181818181818180000000000000000000000000000000000"),
      wIndex=0x1400,
    )
    await self.write_at_address(bytes.fromhex("18"), wIndex=0x1300)
    await self.write_at_address(bytes.fromhex("000000004000000e"), wIndex=0x1000)
    await self.write_at_address(bytes.fromhex("0000000008000000"), wIndex=0x1100)

    await self.send_frame(0x15, bytes.fromhex("5a5a"), flag=0x80)
    await self.send_frame(0x15, b"", flag=0x80)
    await self.send_frame(0x14, b"", flag=0x80)

    await self.write_at_address(bytes.fromhex("000000004000000e"), wIndex=0x1000)
    await self.write_at_address(bytes.fromhex("0000000008000000"), wIndex=0x1100)
    await self.write_at_address(bytes.fromhex("000000004000000e"), wIndex=0x1000)
    await self.write_at_address(bytes.fromhex("0000000008000000"), wIndex=0x1100)
    await self.write_at_address(bytes.fromhex("000000004000000e"), wIndex=0x1000)
    await self.write_at_address(bytes.fromhex("0000000008000000"), wIndex=0x1100)

    self._running = True

  async def _deinitialize(self):
    await self.send_frame(0x12, b"", flag=0x80)
    await self.send_frame(0x13, b"", flag=0x80)
    await self.send_frame(0x19, b"", flag=0x80)
    await self.send_frame(0x1B, b"", flag=0x80)
    await self.send_frame(0x0F, b"", flag=0x80)
    await self.send_frame(
      0x01, bytes.fromhex("0000000000000000000000000000000000000008"), flag=0x80
    )
    await self.send_frame(0x0A, b"", flag=0x80)
    await self.send_frame(0x0C, b"", flag=0x80)
    await self.send_frame(0x0E, b"", flag=0x80)
    await self.send_frame(0x0D, b"", flag=0x80)
    await self.send_frame(0x0B, b"", flag=0x80)
    await self.send_frame(0x11, b"", flag=0x80)
    await self.send_frame(0x10, b"", flag=0x80)
    await self.send_frame(0x1C, b"", flag=0x80)
    await self.send_frame(0x30, bytes.fromhex("5a5a"), flag=0x80)

    self._running = False

  async def get_status(self):
    return await self.send_frame(0x05)

  async def get_location(self):
    """
    Return (x, y, z) tuple in internal unit. x is positive to the right, y is positive towards the back, z is positive downwards.
    """

    resp = await self.send_frame(0x3A)
    return (
      struct.unpack("h", resp[14:16]),
      struct.unpack("h", resp[12:14]),
      struct.unpack("h", resp[16:18]),
    )

  async def open(self):
    # TODO: it's just move_y
    data = await self.send_frame(0x36, bytes.fromhex("5a5a"), flag=0x80)
    await self._wait_for_done()

  async def close(self):
    data = await self.send_frame(0x04, bytes.fromhex("0100000108"))
    data = await self.send_frame(0x26)

    # TODO: it's just move_y
    data = await self.send_frame(0x36, bytes.fromhex("a5a5"), flag=0x80)
    await self._wait_for_done()

  @staticmethod
  def _checksum(body: bytes) -> bytes:
    s = sum(int.from_bytes(body[i : i + 2], "little") for i in range(0, len(body), 2)) & 0xFFFF
    return s.to_bytes(2, "little")

  @classmethod
  def _frame(cls, opcode: int, payload: bytes = b"", flag: int = 0x00) -> bytes:
    """Build a 64-byte frame: sync + flag + length + opcode + payload + checksum."""
    if len(payload) > 52:
      raise ValueError(f"payload {len(payload)}B exceeds 52B limit")
    body = (
      b"\x55\x55"
      + bytes([0x00, flag])
      + b"\x20\x00\x00\x00"
      + bytes([opcode, 0x00])
      + payload.ljust(52, b"\x00")
    )
    return body + cls._checksum(body)

  async def _move_axis(self, opcode: int, value: int):
    await self.send_frame(opcode, struct.pack("<h", value), flag=0x80)
    await self._wait_for_done()

  async def move_x(self, x: int):
    """positive x is right"""
    await self._move_axis(0x33, x)

  async def move_y(self, y: int):
    """positive y is forward"""
    await self._move_axis(0x32, y)

  async def move_z(self, z: int):
    """positive z is down"""
    await self._move_axis(0x34, z)

  async def start_stirring(self):
    await self.send_frame(0x1C, bytes.fromhex("5a5a"), flag=0x80)

  async def stop_stirring(self):
    await self.send_frame(0x1C, b"", flag=0x80)

  async def run_flow(self, path: str, row: int, col: int, n=4):  # 0-indexed
    if not self._running:
      raise RuntimeError("Not running.")

    await self.send_frame(0x04, bytes.fromhex("0100000108"))
    await self.send_frame(0x26)
    await self.send_frame(0x14, bytes.fromhex("5a5a"), flag=0x80)

    # move
    await self.move_x(0x24 + 0x6B * col)
    await self.move_y(0x28 + 0x6B * row)

    # move z down
    await self.move_z(0x8F)

    # move head up
    await self.move_z(-7)  # f9ff

    await self.start_stirring()
    await asyncio.sleep(2)
    await self.stop_stirring()

    # move head down again
    await self.move_z(
      0x05
    )  # 5555008020000000340005000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000aed5

    # start sipping
    await self.send_frame(
      0x01, bytes.fromhex("00000000000000000000000000000000000100f8"), flag=0x80
    )
    await self.send_frame(
      0x01, bytes.fromhex("000000000000000000000000000000000001409c"), flag=0x80
    )
    await self.send_frame(
      0x01, bytes.fromhex("000000000000000000000000000000000001204e"), flag=0x80
    )
    await self.send_frame(
      0x01, bytes.fromhex("0000000000000000000000000000000000000008"), flag=0x80
    )

    await self.write_at_address(bytes.fromhex("02"), wIndex=0x4100)
    await self.write_at_address(bytes.fromhex("42"), wIndex=0x4000)
    await self.write_at_address(bytes.fromhex("65"), wIndex=0x4100)
    await self.write_at_address(bytes.fromhex("43"), wIndex=0x4000)
    await self.write_at_address(bytes.fromhex("75"), wIndex=0x4100)
    await self.write_at_address(bytes.fromhex("42"), wIndex=0x4000)
    await self.write_at_address(bytes.fromhex("39"), wIndex=0x4100)
    await self.write_at_address(bytes.fromhex("43"), wIndex=0x4000)
    await self.write_at_address(bytes.fromhex("a8"), wIndex=0x4100)
    await self.write_at_address(bytes.fromhex("42"), wIndex=0x4000)
    await self.write_at_address(bytes.fromhex("4a"), wIndex=0x4100)
    await self.write_at_address(bytes.fromhex("43"), wIndex=0x4000)
    await self.write_at_address(bytes.fromhex("44"), wIndex=0x4100)
    await self.write_at_address(bytes.fromhex("42"), wIndex=0x4000)
    await self.write_at_address(bytes.fromhex("5a"), wIndex=0x4100)
    await self.write_at_address(bytes.fromhex("43"), wIndex=0x4000)
    await self.write_at_address(bytes.fromhex("08"), wIndex=0x4100)
    await self.write_at_address(bytes.fromhex("42"), wIndex=0x4000)
    await self.write_at_address(bytes.fromhex("0b"), wIndex=0x4100)
    await self.write_at_address(bytes.fromhex("43"), wIndex=0x4000)
    await self.write_at_address(bytes.fromhex("d5"), wIndex=0x4100)
    await self.write_at_address(bytes.fromhex("42"), wIndex=0x4000)
    await self.write_at_address(bytes.fromhex("1a"), wIndex=0x4100)
    await self.write_at_address(bytes.fromhex("43"), wIndex=0x4000)
    await self.write_at_address(bytes.fromhex("6d"), wIndex=0x4100)
    await self.write_at_address(bytes.fromhex("42"), wIndex=0x4000)
    await self.write_at_address(bytes.fromhex("2a"), wIndex=0x4100)
    await self.write_at_address(bytes.fromhex("43"), wIndex=0x4000)

    await self.write_at_address(bytes.fromhex("400a0000"), wIndex=0x400, verify=False)
    await self.write_at_address(bytes.fromhex("46190000"), wIndex=0x400, verify=False)
    await self.write_at_address(bytes.fromhex("5d2b0000"), wIndex=0x400, verify=False)
    await self.write_at_address(bytes.fromhex("f0390000"), wIndex=0x400, verify=False)
    await self.write_at_address(bytes.fromhex("9a5a0000"), wIndex=0x400, verify=False)
    await self.write_at_address(bytes.fromhex("be4a0000"), wIndex=0x400, verify=False)
    await self.write_at_address(bytes.fromhex("23690000"), wIndex=0x400, verify=False)
    await self.write_at_address(bytes.fromhex("5a7a0000"), wIndex=0x400, verify=False)
    await self.write_at_address(bytes.fromhex("e20400004000000e"), wIndex=0x1000, verify=False)
    await self.write_at_address(bytes.fromhex("0000000008000000"), wIndex=0x1100, verify=False)
    await self.write_at_address(
      bytes.fromhex(
        "c200c200c200c20019fe19fe19fe19fef7fef7fef7fe000000000000000000000000000000000000000000000000000000000000000000000000000000000000"
      ),
      wIndex=0x1200,
      verify=False,
    )
    await self.write_at_address(
      bytes.fromhex("1818181818181818181818181818180000000000000000000000000000000000"),
      wIndex=0x1400,
      verify=False,
    )
    await self.write_at_address(bytes.fromhex("18"), wIndex=0x1300)
    # data = await self._read(wValue=1, wIndex=0x1300)
    await self.write_at_address(
      bytes.fromhex("1818181818181818181818181818180000000000000000000000000000000000"),
      wIndex=0x1400,
      verify=False,
    )
    await self.write_at_address(bytes.fromhex("18"), wIndex=0x1300)
    # data = await self._read(wValue=1, wIndex=0x1300)
    await self.write_at_address(
      bytes.fromhex("1818181818181818181818181818180000000000000000000000000000000000"),
      wIndex=0x1400,
      verify=False,
    )
    await self.write_at_address(bytes.fromhex("18"), wIndex=0x1300)
    # data = await self._read(wValue=1, wIndex=0x1300)
    await self.write_at_address(
      bytes.fromhex("1818181818181818181818181818180000000000000000000000000000000000"),
      wIndex=0x1400,
      verify=False,
    )
    await self.write_at_address(bytes.fromhex("18"), wIndex=0x1300)
    # data = await self._read(wValue=1, wIndex=0x1300)
    await self.write_at_address(
      bytes.fromhex("1818181818181818181818181818180000000000000000000000000000000000"),
      wIndex=0x1400,
      verify=False,
    )
    await self.write_at_address(bytes.fromhex("18"), wIndex=0x1300)
    # data = await self._read(wValue=1, wIndex=0x1300)
    await self.write_at_address(
      bytes.fromhex("1818181818181818181818181818180000000000000000000000000000000000"),
      wIndex=0x1400,
      verify=False,
    )
    await self.write_at_address(bytes.fromhex("18"), wIndex=0x1300)
    # data = await self._read(wValue=1, wIndex=0x1300)
    await self.write_at_address(
      bytes.fromhex("1818181818181818181818181818180000000000000000000000000000000000"),
      wIndex=0x1400,
      verify=False,
    )
    await self.write_at_address(bytes.fromhex("18"), wIndex=0x1300)
    # data = await self._read(wValue=1, wIndex=0x1300)
    await self.write_at_address(
      bytes.fromhex("1818181818181818181818181818180000000000000000000000000000000000"),
      wIndex=0x1400,
      verify=False,
    )
    await self.write_at_address(bytes.fromhex("18"), wIndex=0x1300)
    # data = await self._read(wValue=1, wIndex=0x1300)
    await self.write_at_address(
      bytes.fromhex("1818181818181818181818181818180000000000000000000000000000000000"),
      wIndex=0x1400,
      verify=False,
    )
    await self.write_at_address(bytes.fromhex("18"), wIndex=0x1300)
    # data = await self._read(wValue=1, wIndex=0x1300)
    await self.write_at_address(
      bytes.fromhex("1818181818181818181818181818180000000000000000000000000000000000"),
      wIndex=0x1400,
      verify=False,
    )
    await self.write_at_address(bytes.fromhex("18"), wIndex=0x1300)
    # data = await self._read(wValue=1, wIndex=0x1300)
    await self.write_at_address(
      bytes.fromhex("1818181818181818181818181818180000000000000000000000000000000000"),
      wIndex=0x1400,
      verify=False,
    )
    await self.write_at_address(bytes.fromhex("18"), wIndex=0x1300)
    # data = await self._read(wValue=1, wIndex=0x1300)
    await self.write_at_address(
      bytes.fromhex("1818181818181818181818181818180000000000000000000000000000000000"),
      wIndex=0x1400,
      verify=False,
    )
    await self.write_at_address(bytes.fromhex("18"), wIndex=0x1300)
    # data = await self._read(wValue=1, wIndex=0x1300)
    await self.write_at_address(
      bytes.fromhex("1818181818181818181818181818180000000000000000000000000000000000"),
      wIndex=0x1400,
      verify=False,
    )
    await self.write_at_address(bytes.fromhex("18"), wIndex=0x1300)
    # data = await self._read(wValue=1, wIndex=0x1300)
    await self.write_at_address(
      bytes.fromhex("1818181818181818181818181818180000000000000000000000000000000000"),
      wIndex=0x1400,
      verify=False,
    )
    await self.write_at_address(bytes.fromhex("18"), wIndex=0x1300)
    # data = await self._read(wValue=1, wIndex=0x1300)
    await self.write_at_address(
      bytes.fromhex("1818181818181818181818181818180000000000000000000000000000000000"),
      wIndex=0x1400,
      verify=False,
    )
    await self.write_at_address(bytes.fromhex("18"), wIndex=0x1300)
    # data = await self._read(wValue=1, wIndex=0x1300)
    await self.write_at_address(
      bytes.fromhex(
        "c200c200c200c20019fe19fe19fe19fef7fef7fef7fe000000000000000000000000000000000000000000000000000000000000000000000000000000000000"
      ),
      wIndex=0x1200,
      verify=False,
    )
    await self.write_at_address(
      bytes.fromhex("1818181818181818181818181818180000000000000000000000000000000000"),
      wIndex=0x1400,
      verify=False,
    )
    await self.write_at_address(bytes.fromhex("18"), wIndex=0x1300)
    # data = await self._read(wValue=1, wIndex=0x1300)
    await self.write_at_address(
      bytes.fromhex("1818181818181818181818181818180000000000000000000000000000000000"),
      wIndex=0x1400,
      verify=False,
    )
    await self.write_at_address(bytes.fromhex("18"), wIndex=0x1300)
    # data = await self._read(wValue=1, wIndex=0x1300)
    await self.write_at_address(
      bytes.fromhex("1818181818181818181818181818180000000000000000000000000000000000"),
      wIndex=0x1400,
      verify=False,
    )
    await self.write_at_address(bytes.fromhex("18"), wIndex=0x1300)
    # data = await self._read(wValue=1, wIndex=0x1300)
    await self.write_at_address(
      bytes.fromhex("1818181818181818181818181818180000000000000000000000000000000000"),
      wIndex=0x1400,
      verify=False,
    )
    await self.write_at_address(bytes.fromhex("18"), wIndex=0x1300)
    # data = await self._read(wValue=1, wIndex=0x1300)
    await self.write_at_address(
      bytes.fromhex("1818181818181818181818181818180000000000000000000000000000000000"),
      wIndex=0x1400,
      verify=False,
    )
    await self.write_at_address(bytes.fromhex("18"), wIndex=0x1300)
    # data = await self._read(wValue=1, wIndex=0x1300)
    await self.write_at_address(
      bytes.fromhex("1818181818181818181818181818180000000000000000000000000000000000"),
      wIndex=0x1400,
      verify=False,
    )
    await self.write_at_address(bytes.fromhex("18"), wIndex=0x1300)
    # data = await self._read(wValue=1, wIndex=0x1300)
    await self.write_at_address(
      bytes.fromhex("1818181818181818181818181818180000000000000000000000000000000000"),
      wIndex=0x1400,
      verify=False,
    )
    await self.write_at_address(bytes.fromhex("18"), wIndex=0x1300)
    # data = await self._read(wValue=1, wIndex=0x1300)
    await self.write_at_address(
      bytes.fromhex("1818181818181818181818181818180000000000000000000000000000000000"),
      wIndex=0x1400,
      verify=False,
    )
    await self.write_at_address(bytes.fromhex("18"), wIndex=0x1300)
    # data = await self._read(wValue=1, wIndex=0x1300)
    await self.write_at_address(
      bytes.fromhex("1818181818181818181818181818180000000000000000000000000000000000"),
      wIndex=0x1400,
      verify=False,
    )
    await self.write_at_address(bytes.fromhex("18"), wIndex=0x1300)
    # data = await self._read(wValue=1, wIndex=0x1300)
    await self.write_at_address(
      bytes.fromhex("1818181818181818181818181818180000000000000000000000000000000000"),
      wIndex=0x1400,
      verify=False,
    )
    await self.write_at_address(bytes.fromhex("18"), wIndex=0x1300)
    # data = await self._read(wValue=1, wIndex=0x1300)
    await self.write_at_address(
      bytes.fromhex("1818181818181818181818181818180000000000000000000000000000000000"),
      wIndex=0x1400,
      verify=False,
    )
    await self.write_at_address(bytes.fromhex("18"), wIndex=0x1300)
    # data = await self._read(wValue=1, wIndex=0x1300)
    await self.write_at_address(
      bytes.fromhex("1818181818181818181818181818180000000000000000000000000000000000"),
      wIndex=0x1400,
      verify=False,
    )
    await self.write_at_address(bytes.fromhex("18"), wIndex=0x1300)
    # data = await self._read(wValue=1, wIndex=0x1300)
    await self.write_at_address(
      bytes.fromhex("1818181818181818181818181818180000000000000000000000000000000000"),
      wIndex=0x1400,
      verify=False,
    )
    await self.write_at_address(bytes.fromhex("18"), wIndex=0x1300)
    # data = await self._read(wValue=1, wIndex=0x1300)
    await self.write_at_address(
      bytes.fromhex("1818181818181818181818181818180000000000000000000000000000000000"),
      wIndex=0x1400,
      verify=False,
    )
    await self.write_at_address(bytes.fromhex("18"), wIndex=0x1300)
    # data = await self._read(wValue=1, wIndex=0x1300)
    await self.write_at_address(
      bytes.fromhex("1818181818181818181818181818180000000000000000000000000000000000"),
      wIndex=0x1400,
      verify=False,
    )
    await self.write_at_address(bytes.fromhex("18"), wIndex=0x1300)
    # data = await self._read(wValue=1, wIndex=0x1300)
    await self.write_at_address(bytes.fromhex("e20400004000000e"), wIndex=0x1000, verify=False)
    await self.write_at_address(bytes.fromhex("0000000008000000"), wIndex=0x1100, verify=False)
    await self.write_at_address(bytes.fromhex("02"), wIndex=0x4100)
    # data = await self._read(wValue=1, wIndex=0x4100)
    await self.write_at_address(bytes.fromhex("42"), wIndex=0x4000)
    # data = await self._read(wValue=1, wIndex=0x4000)
    await self.write_at_address(bytes.fromhex("65"), wIndex=0x4100)
    # data = await self._read(wValue=1, wIndex=0x4100)
    await self.write_at_address(bytes.fromhex("43"), wIndex=0x4000)
    # data = await self._read(wValue=1, wIndex=0x4000)
    await self.write_at_address(bytes.fromhex("75"), wIndex=0x4100)
    # data = await self._read(wValue=1, wIndex=0x4100)
    await self.write_at_address(bytes.fromhex("42"), wIndex=0x4000)
    # data = await self._read(wValue=1, wIndex=0x4000)
    await self.write_at_address(bytes.fromhex("39"), wIndex=0x4100)
    # data = await self._read(wValue=1, wIndex=0x4100)
    await self.write_at_address(bytes.fromhex("43"), wIndex=0x4000)
    # data = await self._read(wValue=1, wIndex=0x4000)
    await self.write_at_address(bytes.fromhex("a8"), wIndex=0x4100)
    # data = await self._read(wValue=1, wIndex=0x4100)
    await self.write_at_address(bytes.fromhex("42"), wIndex=0x4000)
    # data = await self._read(wValue=1, wIndex=0x4000)
    await self.write_at_address(bytes.fromhex("4a"), wIndex=0x4100)
    # data = await self._read(wValue=1, wIndex=0x4100)
    await self.write_at_address(bytes.fromhex("43"), wIndex=0x4000)
    # data = await self._read(wValue=1, wIndex=0x4000)
    await self.write_at_address(bytes.fromhex("44"), wIndex=0x4100)
    # data = await self._read(wValue=1, wIndex=0x4100)
    await self.write_at_address(bytes.fromhex("42"), wIndex=0x4000)
    # data = await self._read(wValue=1, wIndex=0x4000)
    await self.write_at_address(bytes.fromhex("5a"), wIndex=0x4100)
    # data = await self._read(wValue=1, wIndex=0x4100)
    await self.write_at_address(bytes.fromhex("43"), wIndex=0x4000)
    # data = await self._read(wValue=1, wIndex=0x4000)
    await self.write_at_address(bytes.fromhex("08"), wIndex=0x4100)
    # data = await self._read(wValue=1, wIndex=0x4100)
    await self.write_at_address(bytes.fromhex("42"), wIndex=0x4000)
    # data = await self._read(wValue=1, wIndex=0x4000)
    await self.write_at_address(bytes.fromhex("0b"), wIndex=0x4100)
    # data = await self._read(wValue=1, wIndex=0x4100)
    await self.write_at_address(bytes.fromhex("43"), wIndex=0x4000)
    # data = await self._read(wValue=1, wIndex=0x4000)
    await self.write_at_address(bytes.fromhex("d5"), wIndex=0x4100)
    # data = await self._read(wValue=1, wIndex=0x4100)
    await self.write_at_address(bytes.fromhex("42"), wIndex=0x4000)
    # data = await self._read(wValue=1, wIndex=0x4000)
    await self.write_at_address(bytes.fromhex("1a"), wIndex=0x4100)
    # data = await self._read(wValue=1, wIndex=0x4100)
    await self.write_at_address(bytes.fromhex("43"), wIndex=0x4000)
    # data = await self._read(wValue=1, wIndex=0x4000)
    await self.write_at_address(bytes.fromhex("6d"), wIndex=0x4100)
    # data = await self._read(wValue=1, wIndex=0x4100)
    await self.write_at_address(bytes.fromhex("42"), wIndex=0x4000)
    # data = await self._read(wValue=1, wIndex=0x4000)
    await self.write_at_address(bytes.fromhex("2a"), wIndex=0x4100)
    # data = await self._read(wValue=1, wIndex=0x4100)
    await self.write_at_address(bytes.fromhex("43"), wIndex=0x4000)
    # data = await self._read(wValue=1, wIndex=0x4000)
    await self.write_at_address(bytes.fromhex("400a0000"), wIndex=0x400, verify=False)
    await self.write_at_address(bytes.fromhex("46190000"), wIndex=0x400, verify=False)
    await self.write_at_address(bytes.fromhex("5d2b0000"), wIndex=0x400, verify=False)
    await self.write_at_address(bytes.fromhex("f0390000"), wIndex=0x400, verify=False)
    await self.write_at_address(bytes.fromhex("9a5a0000"), wIndex=0x400, verify=False)
    await self.write_at_address(bytes.fromhex("be4a0000"), wIndex=0x400, verify=False)
    await self.write_at_address(bytes.fromhex("23690000"), wIndex=0x400, verify=False)
    await self.write_at_address(bytes.fromhex("5a7a0000"), wIndex=0x400, verify=False)

    await self.send_frame(
      0x01, bytes.fromhex("0000000000000000000000000000000000017f1b"), flag=0x80
    )
    await self.send_frame(0x14, bytes.fromhex("5a5a"), flag=0x80)
    await self.write_at_address(bytes.fromhex("ffff0000"), wIndex=0x1500, verify=False)
    await self.write_at_address(bytes.fromhex("e003"), wIndex=0x1600, verify=False)
    await self.write_at_address(bytes.fromhex("01"), wIndex=0x1700)
    await self.write_at_address(bytes.fromhex("e20400004000000e"), wIndex=0x1000, verify=False)
    await self.write_at_address(bytes.fromhex("0000000008000000"), wIndex=0x1100, verify=False)
    await self.write_at_address(bytes.fromhex("e20400004000000e"), wIndex=0x1000, verify=False)
    await self.write_at_address(bytes.fromhex("0000000008000000"), wIndex=0x1100, verify=False)
    await self.write_at_address(bytes.fromhex("e20400004000000e"), wIndex=0x1000, verify=False)
    await self.write_at_address(bytes.fromhex("0000000008000000"), wIndex=0x1100, verify=False)
    await self.write_at_address(bytes.fromhex("e20400004000000e"), wIndex=0x1000, verify=False)
    await self.write_at_address(bytes.fromhex("0000000008000000"), wIndex=0x1100, verify=False)
    await self.write_at_address(bytes.fromhex("e20400004000000e"), wIndex=0x1000, verify=False)
    await self.write_at_address(bytes.fromhex("0000000008000000"), wIndex=0x1100, verify=False)
    await self.write_at_address(bytes.fromhex("e20400004000000e"), wIndex=0x1000, verify=False)
    await self.write_at_address(bytes.fromhex("0000000008000000"), wIndex=0x1100, verify=False)

    # flow bit is now on.

    flow_packets = []

    # collect data
    await self.get_status()
    skipped = 0
    while len(flow_packets) < n:
      print("read", len(flow_packets), "of", n)
      data = await self._read(wValue=0x0, wIndex=0x1B02)
      # if packet header ends with 4 zero bytes, it has already been read
      header = data[:20]
      if header.endswith(b"\x00" * 4):
        print("skipping1", header)
        skipped += 1
        print(data[:40])
        if skipped > 10:
          print("skipping too many packets, stopping")
          break
        continue

      data = data.rstrip(b"\x00")
      if (len(data) - 20) % 132 != 0:
        missing = 132 - (len(data) - 20) % 132
        print("missing", missing)
        # pad with 0s that we just removed
        data += b"\x00" * missing
      print("read ", len(data), "bytes")
      flow_packets.append(data)

      # TODO: remove the shared events at the end: they are in alternate packets, in groups of 132
      duplicate = None

    import io as vibes

    with vibes.open(path, "w") as f:
      import json

      f.write(json.dumps([fp.hex() for fp in flow_packets], indent=2))

    sample = None

    try:
      _, events = _decode_flow_packets(flow_packets)
      sample = fk.Sample(
        fcs_path_or_data=events,
        channel_labels=FCS_CHANNEL_NAMES,
        sample_id="sample id",
      )
    except Exception as e:
      print("error", e)
      import traceback

      traceback.print_exc()
    finally:
      await self._stop_flow()

    return sample

  async def _stop_flow(self):
    # stop sipping
    await self.write_at_address(bytes.fromhex("00"), wIndex=0x1700)
    await self.send_frame(
      0x01, bytes.fromhex("0000000000000000000000000000000000007f1b"), flag=0x80
    )
    await self.send_frame(0x14, b"", flag=0x80)
    await self.send_frame(0x0F, bytes.fromhex("5a5a"), flag=0x80)
    await self.send_frame(0x0C, bytes.fromhex("5a5a"), flag=0x80)
    await self.send_frame(0x0B, bytes.fromhex("5a5a"), flag=0x80)
    await self.send_frame(0x10, bytes.fromhex("5a5a"), flag=0x80)
    await self.send_frame(0x0E, bytes.fromhex("5a5a"), flag=0x80)
    await self.send_frame(0x39, bytes.fromhex("5a5a"), flag=0x80)
    await self._wait_for_done()

    await self.send_frame(
      0x01, bytes.fromhex("00000000000000000000000000000000010100f8"), flag=0x80
    )
    await self.send_frame(
      0x01, bytes.fromhex("0000000000000000000000000000000000000008"), flag=0x80
    )
    await self.send_frame(0x0E, b"", flag=0x80)
    await self.send_frame(0x10, b"", flag=0x80)
    await self.send_frame(0x38, bytes.fromhex("5a5a"), flag=0x80)
    await self._wait_for_done()

    await self.send_frame(0x14, b"", flag=0x80)

    await self._wait_for_done()

    # stop flowing
    await self._deinitialize()
