import asyncio
import enum
import threading
import time
from abc import ABCMeta
from typing import Optional

from pylabrobot.device import Driver
from pylabrobot.io.binary import Reader, Writer
from pylabrobot.io.hid import HID


class ByonoyDevice(enum.Enum):
  ABSORBANCE_96 = enum.auto()
  LUMINESCENCE_96 = enum.auto()


class ByonoyBase(Driver, metaclass=ABCMeta):
  """Shared HID communication logic for Byonoy plate readers."""

  def __init__(self, pid: int, device_type: ByonoyDevice) -> None:
    super().__init__()
    self.io = HID(human_readable_device_name="Byonoy Plate Reader", vid=0x16D0, pid=pid)
    self._background_thread: Optional[threading.Thread] = None
    self._stop_background = threading.Event()
    self._ping_interval = 1.0
    self._sending_pings = False
    self._device_type = device_type

  async def setup(self) -> None:
    await self.io.setup()
    self._stop_background.clear()
    self._background_thread = threading.Thread(target=self._background_ping_worker, daemon=True)
    self._background_thread.start()

  async def stop(self) -> None:
    self._stop_background.set()
    if self._background_thread and self._background_thread.is_alive():
      self._background_thread.join(timeout=2.0)
    await self.io.stop()

  def _assemble_command(self, report_id: int, payload: bytes, routing_info: bytes) -> bytes:
    packet = Writer().u16(report_id).raw_bytes(payload).finish()
    packet += b"\x00" * (62 - len(packet)) + routing_info
    return packet

  async def send_command(
    self,
    report_id: int,
    payload: bytes,
    wait_for_response: bool = True,
    routing_info: bytes = b"\x00\x00",
  ) -> Optional[bytes]:
    command = self._assemble_command(report_id, payload=payload, routing_info=routing_info)
    await self.io.write(command)
    if not wait_for_response:
      return None

    t0 = time.time()
    while True:
      if time.time() - t0 > 120:
        raise TimeoutError("Reading data timed out after 2 minutes.")
      response = await self.io.read(64, timeout=30)
      if len(response) == 0:
        continue
      response_report_id = Reader(response).u16()
      if report_id == response_report_id:
        break
    return response

  def _background_ping_worker(self) -> None:
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
      loop.run_until_complete(self._ping_loop())
    finally:
      loop.close()

  async def _ping_loop(self) -> None:
    while not self._stop_background.is_set():
      if self._sending_pings:
        payload = Writer().u8(1).finish()
        cmd = self._assemble_command(
          report_id=0x0040,
          payload=payload,
          routing_info=b"\x00\x00",
        )
        await self.io.write(cmd)
      self._stop_background.wait(self._ping_interval)

  def _start_background_pings(self) -> None:
    self._sending_pings = True

  def _stop_background_pings(self) -> None:
    self._sending_pings = False
