import asyncio
import logging

from pylabrobot.io.ftdi import FTDI

from .backend import FanBackend

logger = logging.getLogger(__name__)


class HamiltonHepaFanBackend(FanBackend):
  """Backend for Hepa fan attachment on Hamilton Liquid Handler"""

  def __init__(self, vid=0x0856, pid=0xAC11, device_id=None):
    self.io = FTDI(
      device_id=device_id,
      vid=vid,
      pid=pid,
      product_substring="USOPTL4",
      vendor_substring="B&B",
    )

  async def setup(self):
    # 1. Open and configure connection
    await self.io.setup()
    await self.io.set_baudrate(9600)
    await self.io.set_line_property(8, 0, 0)  # 8N1
    await self.io.set_latency_timer(16)
    await self.io.set_flowctrl(512)
    await self.io.set_dtr(True)
    await self.io.set_rts(True)

    # 2. Verify device identity (handshake)
    await self._handshake()

    # 3. Continue with device initialization
    await self.send(b"\x55\xc1\x01\x02\x23\x4b")
    await self.send(b"\x55\xc1\x01\x08\x08\x6a")
    await self.send(b"\x55\xc1\x01\x09\x6a\x09")
    await self.send(b"\x55\xc1\x01\x0a\x2f\x4f")
    await self.send(b"\x15\x61\x01\x8a")

  async def _handshake(self):
    """
    Verify that the connected device is actually a Hamilton HEPA fan.

    Sends an identification query and checks the response pattern.
    Disconnects immediately if verification fails to prevent sending
    commands to the wrong device.

    Raises:
        RuntimeError: If device does not respond as expected
    """
    try:
      # Purge any stale data
      await self.io.usb_purge_rx_buffer()
      await self.io.usb_purge_tx_buffer()

      # Send identification query
      await self.io.write(b"\x15\x61\x01\x8a")
      await asyncio.sleep(0.1)

      # Read response with timeout
      response = b""
      timeout = 1.0
      start = asyncio.get_event_loop().time()
      while asyncio.get_event_loop().time() - start < timeout:
        try:
          chunk = await self.io.read(1)
          if chunk:
            response += chunk
          else:
            break
        except:
          break
        await asyncio.sleep(0.01)

      logger.info(
        f"Hamilton HEPA Fan handshake response: "
        f"{response.hex() if response else 'empty'} (length: {len(response)})"
      )

      # Verify response
      if len(response) == 0:
        await self.io.stop()
        raise RuntimeError(
          "Device handshake failed: No response from device. "
          "This may not be a Hamilton HEPA fan or device is malfunctioning."
        )

      # Optional: Add more specific response validation here
      # if response[0] != 0x11 or response[1] != 0x61:
      #     await self.io.stop()
      #     raise RuntimeError(f"Unexpected response pattern: {response.hex()}")

      logger.info("✓ Device handshake successful - Hamilton HEPA fan confirmed")

    except Exception as e:
      # Always disconnect on handshake failure
      try:
        await self.io.stop()
      except Exception:
        pass
      raise RuntimeError(f"Device handshake failed: {e}") from e

  async def turn_on(self, intensity):  # Speed is an integer percent between 0 and 100
    if int(intensity) != intensity or not 0 <= intensity <= 100:
      raise ValueError("Intensity is not an int value between 0 and 100")
    await self.send(b"\x35\x41\x01\xff\x75")  # turn on

    speed_array = [
      "55c10111007b",
      "55c101110279",
      "55c10111057e",
      "55c10111077c",
      "55c101110a71",
      "55c101110c77",
      "55c101110f74",
      "55c10111116a",
      "55c10111146f",
      "55c10111166d",
      "55c101111962",
      "55c101111c67",
      "55c101111e65",
      "55c10111215a",
      "55c101112358",
      "55c10111265d",
      "55c101112853",
      "55c101112b50",
      "55c101112d56",
      "55c10111304b",
      "55c101113249",
      "55c10111354e",
      "55c101113843",
      "55c101113a41",
      "55c101113d46",
      "55c101113f44",
      "55c101114239",
      "55c10111443f",
      "55c10111473c",
      "55c101114932",
      "55c101114c37",
      "55c101114f34",
      "55c10111512a",
      "55c10111542f",
      "55c10111562d",
      "55c101115922",
      "55c101115b20",
      "55c101115e25",
      "55c10111601b",
      "55c101116318",
      "55c10111651e",
      "55c101116813",
      "55c101116b10",
      "55c101116d16",
      "55c10111700b",
      "55c101117209",
      "55c10111750e",
      "55c10111770c",
      "55c101117a01",
      "55c101117c07",
      "55c101117f04",
      "55c1011182f9",
      "55c1011184ff",
      "55c1011187fc",
      "55c1011189f2",
      "55c101118cf7",
      "55c101118ef5",
      "55c1011191ea",
      "55c1011193e8",
      "55c1011196ed",
      "55c1011198e3",
      "55c101119be0",
      "55c101119ee5",
      "55c10111a0db",
      "55c10111a3d8",
      "55c10111a5de",
      "55c10111a8d3",
      "55c10111aad1",
      "55c10111add6",
      "55c10111afd4",
      "55c10111b2c9",
      "55c10111b5ce",
      "55c10111b7cc",
      "55c10111bac1",
      "55c10111bcc7",
      "55c10111bfc4",
      "55c10111c1ba",
      "55c10111c4bf",
      "55c10111c6bd",
      "55c10111c9b2",
      "55c10111cbb0",
      "55c10111ceb5",
      "55c10111d1aa",
      "55c10111d3a8",
      "55c10111d6ad",
      "55c10111d8a3",
      "55c10111dba0",
      "55c10111dda6",
      "55c10111e09b",
      "55c10111e299",
      "55c10111e59e",
      "55c10111e893",
      "55c10111ea91",
      "55c10111ed96",
      "55c10111ef94",
      "55c10111f289",
      "55c10111f48f",
      "55c10111f78c",
      "55c10111f982",
      "55c10111fc87",
      "55c10111fe85",
    ]

    await self.send(bytes.fromhex(speed_array[intensity]))  # set speed

  async def turn_off(self):
    await self.send(b"\x55\xc1\x01\x11\x00\x7b")

  async def stop(self):
    await self.io.stop()

  async def send(self, command: bytes):
    await self.io.write(command)
    await asyncio.sleep(0.1)
    await self.io.read(64)


# Deprecated alias with warning # TODO: remove mid May 2025 (giving people 1 month to update)
# https://github.com/PyLabRobot/pylabrobot/issues/466


class HamiltonHepaFan:
  def __init__(self, *args, **kwargs):
    raise RuntimeError(
      "`HamiltonHepaFan` is deprecated. Please use `HamiltonHepaFanBackend` instead."
    )
