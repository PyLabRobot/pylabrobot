import asyncio
import struct
import threading
import time
from typing import List, Optional

from pylabrobot.io.hid import HID
from pylabrobot.plate_reading.backend import PlateReaderBackend
from pylabrobot.resources.plate import Plate


class Byonoy(PlateReaderBackend):
  """An abstract class for a plate reader. Plate readers are devices that can read luminescence,
  absorbance, or fluorescence from a plate."""

  def __init__(self) -> None:
    self.io = HID(vid=0x16D0, pid=0x119B)
    self._background_thread: Optional[threading.Thread] = None
    self._stop_background = threading.Event()
    self._ping_interval = 1.0  # Send ping every second
    self._sending_pings = True  # Whether to actively send pings

  async def setup(self) -> None:
    """Set up the plate reader. This should be called before any other methods."""

    await self.io.setup()

    # Start background keep alive messages
    self._stop_background.clear()
    self._background_thread = threading.Thread(target=self._background_ping_worker, daemon=True)
    self._background_thread.start()

  async def stop(self) -> None:
    """Close all connections to the plate reader and make sure setup() can be called again."""

    # Stop background keep alive messages
    self._stop_background.set()
    if self._background_thread and self._background_thread.is_alive():
      self._background_thread.join(timeout=2.0)

    await self.io.stop()

  def _background_ping_worker(self) -> None:
    """Background worker that sends periodic ping commands."""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    try:
      loop.run_until_complete(self._ping_loop())
    finally:
      loop.close()

  async def _ping_loop(self) -> None:
    """Main ping loop that runs in the background thread."""
    while not self._stop_background.is_set():
      try:
        # Only send ping if pings are enabled
        if self._sending_pings:
          # Send ping command
          cmd = "40000100000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000008040"
          print("> " + cmd + " (background ping)")
          await self.io.write(bytes.fromhex(cmd))
      except Exception as e:
        print(f"Error in background ping: {e}")

      # Wait for the ping interval or until stop is requested
      self._stop_background.wait(self._ping_interval)

  def _start_background_pings(self) -> None:
    self._sending_pings = True

  def _stop_background_pings(self) -> None:
    self._sending_pings = False

  async def _read_until_empty(self, timeout=30):
    data = b""
    while True:
      chunk = await self.io.read(64, timeout=timeout)
      if not chunk:
        break
      else:
        print("< ", chunk.hex())
      data += chunk

      if chunk.startswith(b"\x70"):
        await self.io.write(
          bytes.fromhex(
            "20007000010000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000"
          )
        )
    return data

  async def _wait_for_response(self, timeout=30):
    time.sleep(1)
    data = b""
    t0 = time.time()
    while True:
      data += await self._read_until_empty(timeout=timeout - (time.time() - t0))
      if len(data) > 64:
        break
      if time.time() - t0 > timeout:
        print("Timeout waiting for response")
        return data
      time.sleep(0.1)
    return data

  async def open(self) -> None:
    raise NotImplementedError(
      "byonoy cannot open by itself. you need to move the top module using a robot arm."
    )

  async def close(self, plate: Optional[Plate]) -> None:
    raise NotImplementedError(
      "byonoy cannot close by itself. you need to move the top module using a robot arm."
    )

  async def read_luminescence(self, plate: Plate, focal_height: float) -> List[List[float]]:
    """Read the luminescence from the plate reader. This should return a list of lists, where the
    outer list is the columns of the plate and the inner list is the rows of the plate."""

    # TODO: confirm that this particular device can read luminescence

    await self.io.write(
      bytes.fromhex(
        "10000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000040"
      )
    )
    await self.io.write(
      bytes.fromhex(
        "50000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000040"
      )
    )
    await self.io.write(
      bytes.fromhex(
        "00020700000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000008040"
      )
    )
    await self.io.write(
      bytes.fromhex(
        "40000100000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000040"
      )
    )
    await self.io.write(
      bytes.fromhex(
        "400380841e00ffffffffffffffffffffffff00000000000000000000000000000000000000000000000000000000000000000000000000000000000000000040"
      )
    )
    await self.io.write(
      bytes.fromhex(
        "40000100000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000008040"
      )
    )

    t0 = time.time()
    r = b""
    reading_data = False
    data = b""

    while True:
      # read for 2 minutes max
      if time.time() - t0 > 120:
        break

      chunk = await self._read_until_empty(timeout=30)

      if (
        bytes.fromhex(
          "30000000000034000000526573756c74732020546f7020526561646f75740a0a0000000000000000000000000000000000000000000000000000000000000000"
        )
        in chunk
      ):
        print("Received result")
        reading_data = True
        self._stop_background_pings()

      if reading_data:
        data += chunk

        if b"Finished in " in chunk:
          break

    cmd = "40000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000040"
    print("> " + cmd)
    await self.io.write(bytes.fromhex(cmd))

    self._start_background_pings()

    # split data in 64 byte chunks
    data_chunks = [data[i : i + 64] for i in range(0, len(data), 64)]

    measurements = []
    reading_measurements = False
    read_n_data_packets = 0
    for line in data_chunks:
      if reading_measurements:
        segment = line
        measurements.append(segment)
        read_n_data_packets += 1

      if b"hybrid result" in line:
        reading_measurements = True

      if read_n_data_packets == 8:
        break

    floats = [
      f
      for line in [l[12:-4] for l in measurements]
      for f in struct.unpack("f" * (len(line) // 4), line)
    ]
    return floats

  async def read_absorbance(self, plate: Plate, wavelength: int) -> List[List[float]]:
    """Read the absorbance from the plate reader. This should return a list of lists, where the
    outer list is the columns of the plate and the inner list is the rows of the plate."""

    # TODO: confirm that this particular device can read absorbance

  async def read_fluorescence(
    self,
    plate: Plate,
    excitation_wavelength: int,
    emission_wavelength: int,
    focal_height: float,
  ) -> List[List[float]]:
    """Read the fluorescence from the plate reader. This should return a list of lists, where the
    outer list is the columns of the plate and the inner list is the rows of the plate."""

    raise NotImplementedError("byonoy does not support fluorescence reading.")
