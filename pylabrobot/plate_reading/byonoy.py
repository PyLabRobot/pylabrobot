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
    self.io = HID(vid=0x16D0, pid=0x1199)  # 16d0:119B for fluorescence
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
      if self._sending_pings:
        # TODO: are they the same?
        # cmd = "40000100000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000008040" # fluor?
        cmd = "40000100000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000040" # abs?
        await self.io.write(bytes.fromhex(cmd))
        # don't read in background thread, data might get lost here

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
      data += await self.io.read(64, timeout=timeout - (time.time() - t0))
      if len(data) >= 64:
        break
      if time.time() - t0 > timeout:
        raise TimeoutError("Timeout waiting for response")
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

  def _get_floats(self, data):
    """Extract floats from a 8 * 64 byte chunk.
    Then for each 64 byte chunk, the first 12 and last 4 bytes are ignored,
    """
    chunks64 = [data[i : i + 64] for i in range(0, len(data), 64)]
    floats = []
    for chunk in chunks64:
      float_bytes = chunk[12:-4] # fluor is 8?
      floats.extend(
        [struct.unpack("f", float_bytes[i : i + 4])[0] for i in range(0, len(float_bytes), 4)]
      )
    return floats

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
        reading_data = True
        self._stop_background_pings()

      if reading_data:
        data += chunk

        if b"Finished in " in chunk:
          break

    cmd = "40000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000040"
    await self.io.write(bytes.fromhex(cmd))

    self._start_background_pings()

    # split data in 64 byte chunks
    start = 64 * 5
    blob_size = 64 * 9
    num_blobs = 8
    blobs = [data[start + i * blob_size : start + (i + 1) * blob_size] for i in range(num_blobs)]
    (
      hybrid_result_b,
      counting_result_b,
      sampling_result_b,
      micro_counting_result_b,
      micro_integration_result_b,
      repetition_count_b,
      integration_time_b,
      below_breakdown_measurement_b,
    ) = blobs

    hybrid_result = self._get_floats(hybrid_result_b[64:])
    _ = self._get_floats(counting_result_b[64:])
    _ = self._get_floats(sampling_result_b[64:])
    _ = self._get_floats(micro_counting_result_b[64:])  # don't know if they are floats
    _ = self._get_floats(micro_integration_result_b[64:])  # don't know if they are floats
    _ = self._get_floats(repetition_count_b[64:])
    _ = self._get_floats(integration_time_b[64:])
    _ = self._get_floats(below_breakdown_measurement_b[64:])

    return hybrid_result

  async def send_command(self, command: bytes, wait_for_response: bool = True) -> Optional[bytes]:
    await self.io.write(command)
    if wait_for_response:
      response = b""

      if command.startswith(bytes.fromhex("004000")):
        should_start = bytes.fromhex("0005")
      elif command.startswith(bytes.fromhex("002003")):
        should_start = bytes.fromhex("3000")
      else:
        should_start = command[1:3] # ignore the Report ID byte. FIXME

      # responses that start with 0x20 are just status, we ignore those
      while len(response) == 0 or response.startswith(b"\x20"):
        response = await self.io.read(64, timeout=30)
        if len(response) == 0:
          continue

        # if the first 2 bytes do not match, we continue reading
        if not response.startswith(should_start):
          response = b""
          continue
      return response

  async def get_available_absorbance_wavelengths(self) -> List[float]:
    """Get the available absorbance wavelengths from the plate reader. Assumes this plate reader can read absorbance."""

    available_wavelengths_r = await self.send_command(
      bytes.fromhex(
        "0030030000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000008040"
      ),
      wait_for_response=True,
    )
    assert available_wavelengths_r is not None, "Failed to get available wavelengths."
    # cut out the first 2 bytes, then read the next 2 bytes as an integer
    # 64 - 4 = 60. 60/2 = 30 16 bit integers
    assert available_wavelengths_r.startswith(bytes.fromhex("3003"))
    available_wavelengths = [
      struct.unpack("H", available_wavelengths_r[i : i + 2])[0]
      for i in range(2, 62, 2)
    ]
    available_wavelengths = [w for w in available_wavelengths if w != 0]
    return available_wavelengths

  async def read_absorbance(self, plate: Plate, wavelength: int) -> List[List[float]]:
    """Read the absorbance from the plate reader. This should return a list of lists, where the
    outer list is the columns of the plate and the inner list is the rows of the plate."""

    await self.send_command(bytes.fromhex("0010000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000040"))
    await self.send_command(bytes.fromhex("0050000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000040"))
    await self.send_command(bytes.fromhex("0000020700000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000008040"))
    # above this it checks if absorbance is supported. which command?

    available_wavelengths = await self.get_available_absorbance_wavelengths()
    if wavelength not in available_wavelengths:
      raise ValueError(
        f"Wavelength {wavelength} nm is not supported by this plate reader. "
        f"Available wavelengths: {available_wavelengths}"
      )
    wavelength_b = struct.pack("<H", wavelength)

    await self.send_command(bytes.fromhex("0000030000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000040"))
    await self.send_command(bytes.fromhex("002003") + wavelength_b  + bytes.fromhex("000001000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000040"))
    # first b"Received REP_ABS_TRIGGER_MEASUREMENT_OUT" response
    await self.send_command(bytes.fromhex("0040000100000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000008040"))
    await self.send_command(bytes.fromhex("0000030000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000040"))
    await self.send_command(bytes.fromhex("0000030000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000040"))
    await self.send_command(bytes.fromhex("0000020700000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000008040"))
    await self.send_command(bytes.fromhex("002003") + wavelength_b + bytes.fromhex("000000010000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000040"))
    await self.send_command(bytes.fromhex("0040000100000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000008040"), wait_for_response=False)
    self._stop_background_pings()

    t0 = time.time()
    data = b""

    while True:
      # read for 2 minutes max
      if time.time() - t0 > 120:
        break

      chunk = await self.io.read(64, timeout=30)
      data += chunk

      if b"Slots" in chunk:
        break

    await self.send_command(bytes.fromhex("40000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000040"), wait_for_response=False)

    self._start_background_pings()

    # split into 64 byte chunks
    # get the 8 blobs that start with 0x0005
    # splitting and then joining is not a great pattern.
    blobs = [data[i : i + 64] for i in range(0, len(data), 64) if data[i:i+2] == b"\x00\x05"]
    if len(blobs) != 8:
      raise ValueError("Not enough blobs received. Expected 8, got {}".format(len(blobs)))
    floats = self._get_floats(b"".join(blobs))
    return floats

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
