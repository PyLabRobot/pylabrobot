import logging
from typing import List, Optional, Union
import time 
import asyncio

from .backend import CentrifugeBackend
from pylabrobot import utils # might need to uses plates as resources


try:
  from pylibftdi import Device
  USE_FTDI = True
except ImportError:
  USE_FTDI = False


logger = logging.getLogger("pylabrobot")

class AgilentCentrifuge(CentrifugeBackend):
    """ A centrifuge backend for the Agilent Centrifuge. Note that this is not a complete implementation
  and many commands and parameters are not implemented yet. """
  
    def __init__(self):
        self.dev: Optional[Device] = None
        
    async def setup(self):
        if not USE_FTDI:
            raise RuntimeError("tbd")    
        
        self.dev = Device()
        self.dev.open()
        self.dev.baudrate = 1200 # TODO: this is standard baud rate, but not the one that the robot uses I believe - alternatively can try all baud rates
        self.dev.ftdi_fn.ftdi_set_line_property(8, 1, 0) # 8 bit size, 1 stop bit, no parity
        #self.dev.ftdi_fn.ftdi_setflowctrl(0) # TODO: check 
        self.dev.ftdi_fn.ftdi_set_latency_timer(16)

        await self.initialize() # TODO: test initialize()
        #await self.request_eeprom_data() # TODO: write request eeprom data()
        
    async def stop(self):
        if self.dev is not None:
            self.dev.close()

    async def read_resp(self, timeout=20) -> bytes:
        """ Read a response from the plate reader. If the timeout is reached, return the data that has
    been read so far. """

        if self.dev is None:
            raise RuntimeError("device not initialized")

        d = b""
        last_read = b""
        end_byte_found = False
        t = time.time()

    # Commands are terminated with 0x0d, but this value may also occur as a part of the response.
    # Therefore, we read until we read a 0x0d, but if that's the last byte we read in a full packet,
    # we keep reading for at least one more cycle. We only check the timeout if the last read was
    # unsuccessful (i.e. keep reading if we are still getting data).
        while True:
            last_read = self.dev.read(25) # 25 is max length observed in pcap
            if len(last_read) > 0:
                d += last_read
                end_byte_found = d[-1] == 0x0d
                if len(last_read) < 25 and end_byte_found: # if we read less than 25 bytes, we're at the end
                    break
            else:
                # If we didn't read any data, check if the last read ended in an end byte. If so, we're done
                if end_byte_found:
                    break

                # Check if we've timed out.
                if time.time() - t > timeout:
                    logger.warning("timed out reading response")
                    break

                # If we read data, we don't wait and immediately try to read more.
                await asyncio.sleep(0.0001)

        logger.debug("read %s", d.hex())

        return d

    async def send(self, cmd: Union[bytearray, bytes], read_timeout=20):
        """ Send a command to the centrifuge and return the response. """

        if self.dev is None:
            raise RuntimeError("Device not initialized")

        logger.debug("sending %s", cmd.hex())
        
        # TODO: cmd = 4 start bytes + cmd (content / unique) + 4 bytes + 5 bytes

        # w = self.dev.write(cmd)
        w = self.dev.write(cmd.decode('latin-1'))


        logger.debug("wrote %s bytes", w)

        assert w == len(cmd)

        resp = await self.read_resp(timeout=read_timeout)
        return resp

    async def read_command_status(self): # TODO: fix after fixing send()
        status = await self.send(bytearray([0xaa, 0x01, 0x0e, 0x0f]))
        return status
    
    async def initialize(self):
        # packets = bytearray([])
        # packet = bytearray([0x00]*20)
        # packets.append(packet)
        # for i in range(33):
        #     packet = bytearray([0xaa])  # First byte is constant - aa
        #     packet.append(i & 0xFF)  # Second byte increments from 00
        #     packet.append(0x0e)  # Third byte is constant - 0e
        #     packet.append(0x0e + (i & 0xFF))  # Fourth byte increments from 0x0e
        #     packet.extend([0x00] * 8)  # Remaining 8 bytes are zeros
        #     packets.append(packet)

        # packet = bytearray([0xaa, 0xff, 0x0f, 0x0e])
        # packets.append(packet)
        # packets = bytearray(packets)
        check = await self.send(bytearray([0xaa, 0xff, 0x0f, 0x0e]))
        print(check)
        if check == 0x89:
            print("Initialization successful")
        else:
            print("Initialization failed")
        return check