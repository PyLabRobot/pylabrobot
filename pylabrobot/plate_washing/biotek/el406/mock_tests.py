# mypy: disable-error-code="union-attr,assignment,arg-type,attr-defined"
"""Mock FTDI IO for EL406 testing."""

from unittest.mock import patch

import anyio

from pylabrobot.plate_washing.biotek.el406 import ExperimentalBioTekEL406Backend
from pylabrobot.resources import Plate
from pylabrobot.resources.utils import create_ordered_items_2d
from pylabrobot.resources.well import Well
from pylabrobot.testing.concurrency import AnyioTestBase

_real_sleep = anyio.sleep


async def _noop(*a, **kw):
  await _real_sleep(0)


def _make_plate(name: str, num_wells: int, size_z: float = 14.0) -> Plate:
  """Create a minimal Plate for testing with the given total well count."""
  _GRID = {96: (12, 8), 384: (24, 16), 1536: (48, 32)}
  num_x, num_y = _GRID[num_wells]
  return Plate(
    name=name,
    size_x=127.0,
    size_y=85.0,
    size_z=size_z,
    ordered_items=create_ordered_items_2d(
      Well,
      num_items_x=num_x,
      num_items_y=num_y,
      dx=10.0,
      dy=7.0,
      dz=1.0,
      item_dx=9.0 if num_x == 12 else 4.5 if num_x == 24 else 2.25,
      item_dy=9.0 if num_y == 8 else 4.5 if num_y == 16 else 2.25,
      size_x=6.0,
      size_y=6.0,
      size_z=10.0,
    ),
  )


# Pre-built test plate fixtures
PT96 = _make_plate("test_96", 96)
PT384 = _make_plate("test_384", 384)
PT384PCR = _make_plate("test_384_pcr", 384, size_z=10.0)
PT1536 = _make_plate("test_1536", 1536)
PT1536F = _make_plate("test_1536_flange", 1536, size_z=10.0)


class EL406TestCase(AnyioTestBase):
  """Base test case with mock FTDI IO and patched anyio.sleep."""

  async def _enter_lifespan(self, stack):
    self._sleep_patcher = patch("anyio.sleep", side_effect=_noop)
    self._sleep_patcher.start()
    stack.callback(self._sleep_patcher.stop)

    self.backend = ExperimentalBioTekEL406Backend()
    self.backend.io = MockFTDI()

    self.backend.io.set_read_buffer(b"\x06" * 500)

    await stack.enter_async_context(self.backend)

    def _pre_cleanup():
      if self.backend.io is not None:
        self.backend.io.set_read_buffer(b"\x06" * 500)

    stack.callback(_pre_cleanup)


class MockFTDI:
  """Mock FTDI IO wrapper for testing without hardware."""

  ACK = b"\x06"

  def __init__(self):
    self.written_data: list = []
    self.read_buffer: bytes = self._default_response_buffer()

  @staticmethod
  def _default_response_buffer() -> bytes:
    """Create default buffer with proper response frames."""
    header = bytes([0x01, 0x02, 0x00, 0x00, 0x01, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00])
    single_response = b"\x06" + header
    return single_response * 200

  async def __aenter__(self):
    return self

  async def __aexit__(self, exc_type, exc_val, exc_tb):
    pass

  async def write(self, data: bytes) -> int:
    self.written_data.append(data)
    return len(data)

  async def read(self, num_bytes: int = 1) -> bytes:
    result = self.read_buffer[:num_bytes]
    self.read_buffer = self.read_buffer[num_bytes:]
    return result

  async def usb_purge_rx_buffer(self):
    pass

  async def usb_purge_tx_buffer(self):
    pass

  async def set_baudrate(self, baudrate: int):
    pass

  async def set_line_property(self, bits: int, stopbits: int, parity: int):
    pass

  async def set_flowctrl(self, flowctrl: int):
    pass

  async def set_rts(self, level: bool):
    pass

  async def set_dtr(self, level: bool):
    pass

  def set_read_buffer(self, data: bytes):
    """Set the read buffer with automatic framing detection.

    Automatically converts legacy test data formats to proper framed responses:
    1. ACK-only buffers: Convert to ACK+header frames
    2. Data ending with ACK (e.g., bytes([value, 0x06])): Wrap as query response
    3. Already framed data (starts with 0x06, 0x01, 0x02): Pass through as-is

    This allows existing tests written for the old protocol to work with
    the new framed protocol without manual updates.
    """
    if not data:
      self.read_buffer = data
      return

    # Check if already a properly framed response (ACK + header starting with 0x01, 0x02)
    if len(data) >= 12 and data[0] == 0x06 and data[1] == 0x01 and data[2] == 0x02:
      self.read_buffer = data
      return

    # Case 1: All ACKs - convert to ACK+header frames
    if all(b == 0x06 for b in data):
      header = bytes([0x01, 0x02, 0x00, 0x00, 0x01, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00])
      single_response = b"\x06" + header
      count = len(data)
      self.read_buffer = single_response * count
      return

    # Case 2: Data ending with ACK (legacy format) - wrap as query response
    if data[-1] == 0x06:
      actual_data = data[:-1]
      prefixed_data = bytes([0x01, 0x00]) + actual_data
      data_len = len(prefixed_data)
      header = bytes(
        [
          0x01,
          0x02,
          0x00,
          0x00,
          0x01,
          0x00,
          0x00,
          data_len & 0xFF,
          (data_len >> 8) & 0xFF,
          0x00,
          0x00,
        ]
      )
      self.read_buffer = b"\x06" + header + prefixed_data
      return

    # Default: pass through as-is
    self.read_buffer = data

  @staticmethod
  def build_completion_frame(data: bytes = b"") -> bytes:
    """Build a mock completion frame for action commands.

    Action commands expect:
    1. ACK (0x06)
    2. 11-byte header (bytes 7-8 = data length, little-endian)
    3. Data bytes

    Args:
      data: Optional data bytes to include in frame.

    Returns:
      Complete response bytes (ACK + header + data).
    """
    data_len = len(data)
    header = bytes(
      [
        0x01,
        0x02,
        0x00,
        0x00,
        0x01,
        0x00,
        0x00,
        data_len & 0xFF,
        (data_len >> 8) & 0xFF,
        0x00,
        0x00,
      ]
    )
    return b"\x06" + header + data

  def set_action_response(self, data: bytes = b"", count: int = 1):
    """Set up mock responses for action commands.

    Args:
      data: Data bytes to include in each completion frame.
      count: Number of action responses to queue.
    """
    response = self.build_completion_frame(data)
    self.read_buffer = response * count

  def set_query_response(self, data: bytes, count: int = 1):
    """Set up mock responses for query commands.

    This wraps the data in a proper framed response format:
    ACK + 11-byte header + 2-byte prefix + data

    The 2-byte prefix matches the real device response format:
    - Byte 0: Status (0x01)
    - Byte 1: Reserved (0x00)

    The implementation extracts data starting at byte 2, so we need
    to include this prefix.

    Args:
      data: Data bytes to include in each response.
      count: Number of responses to queue.
    """
    prefixed_data = bytes([0x01, 0x00]) + data
    response = self.build_completion_frame(prefixed_data)
    self.read_buffer = response * count
