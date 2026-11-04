import unittest.mock
from typing import List, Union, cast

import anyio
import pytest

pytest.importorskip("pylibftdi")

from pylibftdi import FtdiError

from pylabrobot.io.ftdi import FTDI
from pylabrobot.legacy.plate_reading.agilent.biotek_synergyh1_backend import SynergyH1Backend
from pylabrobot.testing.concurrency import AnyioTestBase


class SynergyReadFTDI:
  """Typed FTDI double providing finite reads and transient failures."""

  def __init__(self, reads: List[Union[bytes, BaseException]]) -> None:
    self.reads = reads
    self.read_count = 0

  async def read(self, size: int) -> bytes:
    assert size == 512
    self.read_count += 1
    await anyio.lowlevel.checkpoint()
    if not self.reads:
      return b""
    result = self.reads.pop(0)
    if isinstance(result, BaseException):
      raise result
    return result


class BlockingReadFTDI:
  """Typed FTDI double exposing cancellation of an in-flight read."""

  def __init__(self) -> None:
    self.started = anyio.Event()

  async def read(self, size: int) -> bytes:
    assert size == 512
    self.started.set()
    await anyio.sleep_forever()
    raise AssertionError("sleep_forever returned")


def make_synergy_backend(io: Union[SynergyReadFTDI, BlockingReadFTDI]) -> SynergyH1Backend:
  """Construct a Synergy backend with software-only reader I/O."""
  with unittest.mock.patch("pylabrobot.legacy.plate_reading.agilent.biotek_backend.FTDI"):
    backend = SynergyH1Backend(timeout=0.1)
  backend.io = cast(FTDI, io)
  return backend


class TestSynergyH1Reader(AnyioTestBase):
  async def test_empty_reads_use_backend_neutral_sleep(self) -> None:
    backend = make_synergy_backend(SynergyReadFTDI([b"", b"result\x03"]))

    assert await backend._read_until(b"\x03") == b"result\x03"

  async def test_ftdi_retries_share_one_deadline(self) -> None:
    io = SynergyReadFTDI([FtdiError("first"), FtdiError("second")])
    backend = make_synergy_backend(io)

    with (
      unittest.mock.patch("time.time", return_value=0),
      anyio.fail_after(0.3),
      pytest.raises(TimeoutError, match="_read_until timed out waiting"),
    ):
      await backend._read_until(b"\x03", timeout=0.06)

    assert io.read_count == 2

  async def test_reader_cancellation_is_not_swallowed(self) -> None:
    io = BlockingReadFTDI()
    backend = make_synergy_backend(io)
    scopes: List[anyio.CancelScope] = []
    cancelled = anyio.Event()

    async def read() -> None:
      with anyio.CancelScope() as scope:
        scopes.append(scope)
        try:
          await backend._read_until(b"\x03")
        except anyio.get_cancelled_exc_class():
          cancelled.set()
          raise

    async with anyio.create_task_group() as task_group:
      task_group.start_soon(read)
      await io.started.wait()
      scopes[0].cancel()
      await cancelled.wait()
