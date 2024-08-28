import logging
from typing import Optional, Union
import time
import asyncio

from .backend import CentrifugeBackend
from pylabrobot import utils

try:
    from pylibftdi import Device
    USE_FTDI = True
except ImportError:
    USE_FTDI = False

logger = logging.getLogger("pylabrobot")

class DoorOperationError(Exception):
    """Custom exception for door operation errors."""
    pass

class AgilentCentrifuge():
    """A centrifuge backend for the Agilent Centrifuge. Note that this is not a complete implementation
    and many commands and parameters are not implemented yet."""

    def __init__(self):
        self.dev: Optional[Device] = None
        # self.door_status = True  # starts off opened - closed = False
        # self.lock_status = True   # starts off unlocked - locked = True
        self.position = 0
        self.homing_position = 0

    async def setup(self):
        if not USE_FTDI:
            raise RuntimeError("pylibftdi is not installed.")

        self.dev = Device()
        self.dev.open()
        print(self.dev, "open")
        # TODO: add functionality where if robot has been intialized before nothing needs to happen
        for _ in range(3):
            await self.configure_and_initialize()
            await self.send(b"\xaa\x00\x21\x01\xff\x21")
        await self.finish_setup()
        await self.status_check()

    async def stop(self):
        await self.com()
        await self.configure_and_initialize()
        if self.dev:
            self.dev.close()

    async def read_resp(self, timeout=20) -> bytes:
        """Read a response from the centrifuge. If the timeout is reached, return the data that has
        been read so far."""
        if not self.dev:
            raise RuntimeError("Device not initialized")

        data = b""
        end_byte_found = False
        start_time = time.time()

        while True:
            chunk = self.dev.read(25)
            if chunk:
                data += chunk
                end_byte_found = data[-1] == 0x0d
                if len(chunk) < 25 and end_byte_found:
                    break
            else:
                if end_byte_found or time.time() - start_time > timeout:
                    logger.warning("Timed out reading response")
                    break
                await asyncio.sleep(0.0001)

        logger.debug("Read %s", data.hex())
        return data

    async def send(self, cmd: Union[bytearray, bytes], read_timeout=0.2) -> bytes:
        """Send a command to the centrifuge and return the response."""
        if not self.dev:
            raise RuntimeError("Device not initialized")

        logger.debug("Sending %s", cmd.hex())
        written = self.dev.write(cmd.decode('latin-1'))
        logger.debug("Wrote %s bytes", written)

        if written != len(cmd):
            raise RuntimeError("Failed to write all bytes")
        resp = await self.read_resp(timeout=read_timeout)
        s = ""
        c = ""
        for i, byte in enumerate(resp):
            s += f" {byte:02x} "  # Format byte as two-digit hexadecimal
        for i, byte in enumerate(cmd):
            c += f" {byte:02x} "  # Format byte as two-digit hexadecimal
        print(f"TX: {len(cmd)}-bytes -\t {c}")
        print(f"RX: {len(resp)}-bytes -\t {s}\n")
        return resp

    async def configure_and_initialize(self):
        await self.set_configuration_data()
        await self.initialize()

    async def set_configuration_data(self):
        """Set the device configuration data."""
        self.dev.ftdi_fn.ftdi_set_latency_timer(16)
        self.dev.ftdi_fn.ftdi_set_line_property(8, 1, 0)
        self.dev.ftdi_fn.ftdi_setflowctrl(0)
        self.dev.baudrate = 19200

    async def initialize(self):
        """Initialize the device."""
        self.dev.write(b"\x00" * 20)
        for i in range(33):
            packet = b"\xaa" + bytes([i & 0xFF, 0x0e, 0x0e + (i & 0xFF)]) + b"\x00" * 8
            self.dev.write(packet)
        await self.send(b"\xaa\xff\x0f\x0e")

    async def com(self):
        """start/end command, i think..."""
        await self.send(b"\xaa\x02\x0e\x10")

    async def finish_setup(self):
        """Finish the setup process."""
        await self.send(b"\xaa\x00\x21\x01\xff\x21")
        await self.send(b"\xaa\x01\x13\x20\x34")
        await self.send(b"\xaa\x00\x21\x02\xff\x22")
        await self.send(b"\xaa\x02\x13\x20\x35")
        await self.send(b"\xaa\x00\x21\x03\xff\x23")
        await self.send(b"\xaa\xff\x1a\x14\x2d")

        self.dev.baudrate = 57600
        self.dev.ftdi_fn.ftdi_setrts(1)
        self.dev.ftdi_fn.ftdi_setdtr(1)

        await self.status_check()
        await self.send(b"\xaa\x01\x12\x1f\x32")
        for _ in range(8):
            await self.send(b"\xaa\x02\x20\xff\x0f\x30")
        await self.send(b"\xaa\x02\x20\xdf\x0f\x10")
        await self.send(b"\xaa\x02\x20\xdf\x0e\x0f")
        await self.send(b"\xaa\x02\x20\xdf\x0c\x0d")
        await self.send(b"\xaa\x02\x20\xdf\x08\x09")
        for _ in range(4):
            await self.send(b"\xaa\x02\x26\x00\x00\x28")
        await self.send(b"\xaa\x02\x12\x03\x17")
        for _ in range(5):
            await self.send(b"\xaa\x02\x26\x20\x00\x48")
            await self.com()
            await self.send(b"\xaa\x02\x26\x00\x00\x28")
            await self.com()
        await self.com()
        await self.lock_door()

        await self.status_check()
        await self.com()

        await self.status_check()
        await self.com()

        await self.status_check()
        await self.com()

        await self.com()
        await self.status_check()

        await self.com()
        await self.send(b"\xaa\x02\x26\x00\x00\x28")
        await self.com()

        await self.com()
        await self.status_check()
        await self.com()

        await self.send(b"\xaa\x01\x17\x02\x1a")
        await self.send(b"\xaa\x01\x0e\x0f")
        await self.send(b"\xaa\x01\xe6\xc8\x00\xb0\x04\x96\x00\x0f\x00\x4b\x00\xa0\x0f\x05\x00\x07")
        await self.send(b"\xaa\x01\x17\x04\x1c")
        await self.send(b"\xaa\x01\x17\x01\x19")

        await self.send(b"\xaa\x01\x0b\x0c")
        await self.send(b"\xaa\x01\x00\x01")
        await self.send(b"\xaa\x01\xe6\x05\x00\x64\x00\x00\x00\x00\x00\x32\x00\xe8\x03\x01\x00\x6e")
        await self.send(b"\xaa\x01\x94\xb6\x12\x83\x00\x00\x12\x01\x00\x00\xf3")
        await self.send(b"\xaa\x01\x19\x28\x42")
        await self.send(b"\xaa\x01\x0e\x0f")

        resp = "89"
        while resp == "89":
            await self.com()
            resp = await self.status_check()
            resp = f"{resp[0]:02x}"

        await self.send(b"\xaa\x01\x0e\x0f")
        await self.send(b"\xaa\x01\x0e\x0f")

        await self.send(b"\xaa\x01\x17\x02\x1a")
        await self.send(b"\xaa\x01\x0e\x0f")
        await self.send(b"\xaa\x01\xe6\xc8\x00\xb0\x04\x96\x00\x0f\x00\x4b\x00\xa0\x0f\x05\x00\x07")
        await self.send(b"\xaa\x01\x17\x04\x1c")
        await self.send(b"\xaa\x01\x17\x01\x19")

        await self.send(b"\xaa\x01\x0b\x0c")
        await self.send(b"\xaa\x01\x0e\x0f")
        await self.send(b"\xaa\x01\xe6\xc8\x00\xb0\x04\x96\x00\x0f\x00\x4b\x00\xa0\x0f\x05\x00\x07")
        # send 17 byte - different depedning on homing position - bytes index 4:7 = homing position + 8000
        st = self.homing_position + 8000
        byte_string = st.to_bytes(4, byteorder='little')
        await self.send(b"\xaa\x01\xd4\x97" + byte_string + b"\xc3\xf5\x28\x00\xd7\x1a\x00\x00\x49")
        await self.send(b"\xaa\x01\x0e\x0f")
        await self.send(b"\xaa\x01\x0e\x0f")

        resp = "08"
        while resp != "09":
            resp = await self.send(b"\xaa\x01\x0e\x0f")
            await self.status_check()
            resp = f"{resp[0]:02x}"
        await self.send(b"\xaa\x01\x0e\x0f")
        await self.send(b"\xaa\x01\x0e\x0f")

        await self.send(b"\xaa\x01\x17\x02\x1a")

        await self.com()
        await self.lock_door()

        # for _ in range(14):
        #     await self.status_check()
        #     await self.com()
        # await self.com()
        # await self.close_door()
        # await self.status_check()
        # await self.com()
        # await self.open_door()
        # await self.status_check()
        # await self.com()
        # await self.status_check()
        # await self.com()
        # await self.com()
        # await self.status_check()
        # await self.com()
        # await self.send(b"\xaa\x01\x0e\x0f")
    #     tx_data = [
    #     "aa 01 0e 0f",
    #     "aa 02 0e 10",
    #     "aa 02 0e 10",
    #     "aa 01 0e 0f",
    #     "aa 02 0e 10",
    #     "aa 02 0e 10",
    #     "aa 01 0e 0f",
    #     "aa 02 0e 10",
    #     "aa 02 0e 10",
    #     "aa 01 0e 0f",
    #     "aa 02 0e 10",
    #     "aa 02 0e 10",
    #     "aa 01 0e 0f",
    #     "aa 02 0e 10",
    #     "aa 02 0e 10",
    #     "aa 01 0e 0f",
    #     "aa 02 0e 10",
    #     "aa 02 0e 10",
    #     "aa 01 0e 0f",
    #     "aa 02 0e 10",
    #     "aa 02 0e 10",
    #     "aa 01 0e 0f",
    #     "aa 02 0e 10",
    #     "aa 02 0e 10",
    #     "aa 01 0e 0f",
    #     "aa 02 0e 10",
    #     "aa 02 0e 10",
    #     "aa 01 0e 0f",
    #     "aa 02 0e 10",
    #     "aa 02 0e 10",
    #     "aa 01 0e 0f",
    #     "aa 02 0e 10",
    #     "aa 02 0e 10",
    #     "aa 01 0e 0f",
    #     "aa 02 0e 10",
    #     "aa 02 0e 10",
    #     "aa 01 0e 0f",
    #     "aa 02 0e 10",
    #     "aa 02 0e 10",
    #     "aa 01 0e 0f",
    #     "aa 02 0e 10",
    #     "aa 02 0e 10",
    #     "aa 01 0e 0f",
    #     "aa 02 0e 10",
    #     "aa 02 0e 10",
    #     "aa 01 0e 0f",
    #     "aa 02 0e 10",
    #     "aa 02 0e 10",
    #     "aa 01 0e 0f",
    #     "aa 02 0e 10",
    #     "aa 02 0e 10",
    #     "aa 01 0e 0f",
    #     "aa 02 0e 10",
    #     "aa 02 0e 10",
    #     "aa 01 0e 0f",
    #     "aa 02 0e 10",
    #     "aa 02 0e 10",
    #     "aa 01 0e 0f",
    #     "aa 02 0e 10",
    #     "aa 02 0e 10",
    #     "aa 01 0e 0f",
    #     "aa 02 0e 10",
    #     "aa 02 0e 10",
    #     "aa 01 0e 0f",
    #     "aa 02 0e 10",
    #     "aa 02 0e 10",
    #     "aa 01 0e 0f",
    #     "aa 02 0e 10",
    #     "aa 02 0e 10",
    #     "aa 01 0e 0f",
    #     "aa 02 0e 10",
    #     "aa 02 0e 10",
    #     "aa 01 0e 0f",
    #     "aa 02 0e 10",
    #     "aa 02 0e 10",
    #     "aa 01 0e 0f",
    #     "aa 02 0e 10",
    #     "aa 02 0e 10",
    #     "aa 01 0e 0f",
    #     "aa 02 0e 10",
    #     "aa 02 0e 10",
    #     "aa 01 0e 0f",
    #     "aa 02 0e 10",
    #     "aa 02 0e 10",
    #     "aa 01 0e 0f",
    #     "aa 02 0e 10",
    #     "aa 02 0e 10",
    #     "aa 01 0e 0f",
    #     "aa 02 0e 10",
    #     "aa 02 0e 10",
    #     "aa 01 0e 0f",
    #     "aa 02 0e 10",
    #     "aa 02 0e 10",
    #     "aa 01 0e 0f",
    #     "aa 02 0e 10",
    #     "aa 02 0e 10",
    #     "aa 01 0e 0f",
    #     "aa 02 0e 10",
    #     "aa 02 0e 10",
    #     "aa 01 0e 0f",
    #     "aa 02 0e 10",
    #     "aa 02 0e 10",
    #     "aa 01 0e 0f",
    #     "aa 02 0e 10",
    #     "aa 02 0e 10",
    #     "aa 01 0e 0f",
    #     "aa 02 0e 10",
    #     "aa 02 0e 10",
    #     "aa 01 0e 0f",
    #     "aa 02 0e 10",
    #     "aa 02 0e 10",
    #     "aa 01 0e 0f",
    #     "aa 02 0e 10",
    #     "aa 02 0e 10",
    #     "aa 01 0e 0f",
    #     "aa 02 0e 10",
    #     "aa 02 0e 10",
    #     "aa 01 0e 0f",
    #     "aa 02 0e 10",
    #     "aa 02 0e 10",
    #     "aa 01 0e 0f",
    #     "aa 02 0e 10",
    #     "aa 02 0e 10",
    #     "aa 01 0e 0f",
    #     "aa 02 0e 10",
    #     "aa 02 0e 10",
    #     "aa 01 0e 0f",
    #     "aa 02 0e 10",
    #     "aa 02 0e 10",
    #     "aa 01 0e 0f",
    #     "aa 02 0e 10",
    #     "aa 02 0e 10",
    #     "aa 01 0e 0f",
    #     "aa 02 0e 10",
    #     "aa 02 0e 10",
    #     "aa 01 0e 0f",
    #     "aa 02 0e 10",
    #     "aa 02 0e 10",
    #     "aa 01 0e 0f",
    #     "aa 02 0e 10",
    #     "aa 02 0e 10",
    #     "aa 01 0e 0f",
    #     "aa 02 0e 10",
    #     "aa 02 0e 10",
    #     "aa 01 0e 0f",
    #     "aa 02 0e 10",
    #     "aa 02 0e 10",
    #     "aa 01 0e 0f",
    #     "aa 02 0e 10",
    #     "aa 02 0e 10",
    #     "aa 01 0e 0f",
    #     "aa 02 0e 10",
    #     "aa 02 0e 10",
    #     "aa 01 0e 0f",
    #     "aa 02 0e 10",
    #     "aa 02 0e 10",
    # ]

    #     # Generate the lines for each TX payload
    #     for tx in tx_data:
    #         # Convert the hex string into a byte literal
    #         bytes_str = " ".join(tx.split())  # Split by spaces and join to ensure correct formatting
    #         byte_literal = bytes.fromhex(bytes_str)  # Convert hex string to byte object

    #         # Print the Python line
    #         await self.send(byte_literal)




    async def status_check(self):
        """Check the status of the centrifuge."""
        resp = await self.send(b"\xaa\x01\x0e\x0f")
        s = []
        for byte in resp:
            s.append(f"{byte:02x}")  # Append the bytes as hexadecimal strings
        await self.com()
        # Convert the hexadecimal strings to integers before slicing and converting
        self.position = int.from_bytes(bytes.fromhex(''.join(s[1:4])), byteorder='little')
        self.homing_position = int.from_bytes(bytes.fromhex(''.join(s[9:12])), byteorder='little')
        stat = f"{resp[0]:02x}"
        print(f"status: {stat} position: {self.position}   homing position: {self.homing_position}")
        return resp


    async def open_door(self):
        await self.send(b"\xaa\x02\x26\x00\x07\x2f")
        await self.com()

    async def close_door(self):
        await self.send(b"\xaa\x02\x26\x00\x05\x2d")
        await self.com()

    async def lock_door(self):
        await self.send(b"\xaa\x02\x26\x00\x01\x29")
        await self.com()

    async def unlock_door(self):
        await self.send(b"\xaa\x02\x26\x00\x05\x2d")
        await self.com()

    async def lock_bucket(self):
        await self.send(b"\xaa\x02\x26\x00\x07\x2f")
        await self.com()

    async def unlock_bucket(self):
        await self.send(b"\xaa\x02\x26\x00\x06\x2e")
        await self.com()

    async def go_to_bucket2(self):
        st = self.position + 4000
        byte_string = st.to_bytes(4, byteorder='little')
        byte_string = b"\xaa\x01\xd4\x97" + byte_string + b"\xc3\xf5\x28\x00\xd7\x1a\x00\x00"
        last_byte = (sum(byte_string)-0xaa)&0xff
        byte_string += last_byte.to_bytes(1, byteorder='little')
        tx_payloads = [
    "aa 01 0e 0f",
    "aa 02 0e 10",
    "aa 01 0e 0f",
    "aa 02 0e 10",
    "aa 01 0e 0f",
    "aa 02 0e 10",
    "aa 01 0e 0f",
    "aa 02 0e 10",
    "aa 02 0e 10",
    "aa 02 26 00 05 2d",
    "aa 02 0e 10",
    "aa 01 0e 0f",
    "aa 02 0e 10",
    "aa 02 0e 10",
    "aa 01 0e 0f",
    "aa 02 0e 10",
    "aa 01 0e 0f",
    "aa 02 0e 10",
    "aa 01 0e 0f",
    "aa 02 0e 10",
    "aa 01 0e 0f",
    "aa 02 0e 10",
    "aa 01 0e 0f",
    "aa 02 0e 10",
    "aa 02 0e 10",
    "aa 02 26 00 01 29",
    "aa 02 0e 10",
    "aa 01 0e 0f",
    "aa 02 0e 10",
    "aa 02 0e 10",
    "aa 02 0e 10",
    "aa 02 26 00 00 28",
    "aa 02 0e 10",
    "aa 01 0e 0f",
    "aa 02 0e 10",
    "aa 02 0e 10",
    "aa 01 0e 0f",
    "aa 02 0e 10",
    "aa 02 0e 10",
    "aa 01 0e 0f",
    "aa 02 0e 10",
    "aa 01 17 02 1a",
    "aa 01 0e 0f",
    "aa 01 e6 c8 00 b0 04 96 00 0f 00 4b 00 a0 0f 05 00 07",
    "aa 01 17 04 1c",
    "aa 01 17 01 19",
    "aa 01 0e 0f",
    "aa 02 0e 10",
    "aa 01 0b 0c",
    "aa 01 0e 0f",
    "aa 02 0e 10",
    "aa 01 17 02 1a",
    "aa 01 0e 0f",
    "aa 01 e6 c8 00 b0 04 96 00 0f 00 4b 00 a0 0f 05 00 07",
    "aa 01 17 04 1c",
    "aa 01 17 01 19",
    "aa 01 0e 0f",
    "aa 02 0e 10",
    "aa 01 0b 0c",
    "aa 01 0e 0f",
    "aa 01 e6 c8 00 b0 04 96 00 0f 00 4b 00 a0 0f 05 00 07",
    byte_string,
    "aa 01 0e 0f",
    "aa 01 0e 0f",
    "aa 01 0e 0f",
    "aa 02 0e 10",
    "aa 01 0e 0f",
    "aa 01 0e 0f",
    "aa 02 0e 10",
    "aa 01 0e 0f",
    "aa 01 0e 0f",
    "aa 02 0e 10",
    "aa 01 0e 0f",
    "aa 01 0e 0f",
    "aa 01 0e 0f",
    "aa 02 0e 10",
    "aa 01 0e 0f",
    "aa 01 0e 0f",
    "aa 02 0e 10",
    "aa 01 0e 0f",
    "aa 01 0e 0f",
    "aa 01 0e 0f",
    "aa 02 0e 10",
    "aa 01 0e 0f",
    "aa 01 0e 0f",
    "aa 02 0e 10",
    "aa 01 0e 0f",
    "aa 01 0e 0f",
    "aa 01 0e 0f",
    "aa 01 0e 0f",
    "aa 02 0e 10",
    "aa 01 0e 0f",
    "aa 02 0e 10",
    "aa 01 0e 0f",
    "aa 02 0e 10",
    "aa 01 0e 0f",
    "aa 02 0e 10",
    "aa 01 0e 0f",
    "aa 02 0e 10",
    "aa 01 0e 0f",
    "aa 02 0e 10",
    "aa 01 0e 0f",
    "aa 02 0e 10",
    "aa 01 0e 0f",
    "aa 02 0e 10",
    "aa 01 17 02 1a",
    "aa 01 0e 0f",
    "aa 02 0e 10",
    "aa 02 0e 10",
    "aa 02 26 00 01 29",
    "aa 02 0e 10",
    "aa 01 0e 0f",
    "aa 02 0e 10",
    "aa 02 0e 10",
    "aa 02 0e 10",
    "aa 01 0e 0f",
    "aa 02 0e 10",
    "aa 02 0e 10",
    "aa 01 0e 0f",
    "aa 02 0e 10",
    "aa 02 0e 10",
    "aa 01 0e 0f",
    "aa 02 0e 10",
    "aa 02 0e 10",
    "aa 02 0e 10",
    "aa 01 0e 0f",
    "aa 02 0e 10",
    "aa 02 0e 10",
    "aa 02 0e 10",
    "aa 02 26 00 05 2d",
    "aa 02 0e 10",
    "aa 01 0e 0f",
    "aa 02 0e 10",
    "aa 02 0e 10",
    "aa 01 0e 0f",
    "aa 02 0e 10",
    "aa 01 0e 0f",
    "aa 02 0e 10",
    "aa 02 0e 10",
    "aa 02 26 00 07 2f",
    "aa 02 0e 10",
    "aa 01 0e 0f",
    "aa 02 0e 10",
    "aa 02 0e 10",
    "aa 02 0e 10",
    "aa 01 0e 0f",
    "aa 02 0e 10",
    "aa 02 0e 10",
    "aa 01 0e 0f",
    "aa 02 0e 10",
    "aa 02 0e 10",
    "aa 01 0e 0f",
    "aa 02 0e 10",
    "aa 02 0e 10",
    "aa 02 0e 10",
    "aa 01 0e 0f",
    "aa 02 0e 10",
    "aa 02 0e 10",
    "aa 02 0e 10",
    "aa 01 0e 0f",
    "aa 01 0e 0f",
    "aa 02 0e 10",
    "aa 01 0e 0f",
    "aa 02 0e 10",
    "aa 01 0e 0f",
        ]



        for tx in tx_payloads:
            if isinstance(tx, str):
                # Convert the hex string into a byte literal
                byte_literal = bytes.fromhex(tx)
                await self.send(byte_literal)
            else:
                await self.send(tx)




    async def start_spin_cycle(self, plate, rpm, time_seconds, acceleration, deceleration):
        """Start a spin cycle."""
        await self.com()
        if self.door_status:
            await self.close_door()
        await self.status_check()

        await self.com()
        if not self.lock_status:
            await self.lock_door()
        await self.status_check()

        await self.com()
        await self.com()
        await self.send(b"\xaa\x02\x26\x00\x00\x28")
        await self.com()
        await self.status_check()

        await self.send(b"\xaa\x01\x17\x02\x1a")
        await self.send(b"\xaa\x01\x0e\x0f")
        await self.send(b"\xaa\x01\xe6\xc8\x00\xb0\x04\x96\x00\x0f\x00\x4b\x00\xa0\x0f\x05\x00\x07")
        await self.send(b"\xaa\x01\x17\x04\x1c")
        await self.send(b"\xaa\x01\x17\x01\x19")

        await self.status_check()
        print('status check')
        while True:
            await self.status_check()
            await self.send(b"\xaa\x01\x0e\x0f")
            await self.send(b"\xaa\x01\x0e\x0f")
            # s = ""
            # for i, byte in enumerate(resp):
            #     s += f"{i}: {byte:02x} "  # Format byte as two-digit hexadecimal
            # print(s)
        # await self.send(b"\xaa\x01\x0e\x0f")
        # await self.send(b"\xaa\x01\x0e\x0f")

        # resp = await self.status_check()
        # print(resp)
        # while len(resp) != 14:
        #     print('position tracking')
        #     await self.send(b"\xaa\x01\x0e\x0f")
        #     await self.send(b"\xaa\x01\x0e\x0f")

        #     resp = await self.status_check()

