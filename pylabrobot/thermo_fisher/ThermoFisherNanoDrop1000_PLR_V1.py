import usb.core
import usb.util
import asyncio
import numpy as np
from typing import Tuple, List
# PyLabRobot core imports (Assuming standard PLR v1b1 structure)
from pylabrobot.device import Device, Driver
from pylabrobot.capabilities import CapabilityBackend
# Assuming an Absorbance Capability exists in PLR; if not, you would define it in capabilities/
# from pylabrobot.capabilities.absorbance import AbsorbanceBackend, Absorbance 

class ThermoFisherNanoDrop1000Driver(Driver):
    """
    Pure transport layer for the NanoDrop 1000.
    Handles USB connections, endpoints, and raw byte transfers.
    """
    VID = 0x2457
    PID = 0x1002
    EP_OUT = 0x02
    EP_IN_HEAVY = 0x82
    EP_IN_COMM = 0x87

    def __init__(self):
        super().__init__()
        self.dev = None

    async def setup(self):
        """Initializes the USB connection."""
        print("Connecting to NanoDrop...")
        self.dev = usb.core.find(idVendor=self.VID, idProduct=self.PID)
        if self.dev is None:
            raise RuntimeError("NanoDrop not found. Is Zadig set to libusb-win32?")
        
        self.dev.set_configuration()
        self.dev.clear_halt(self.EP_OUT)
        self.dev.clear_halt(self.EP_IN_HEAVY)
        self.dev.clear_halt(self.EP_IN_COMM)

        # Wake & Init
        await self.send_command([0x08])
        await asyncio.sleep(0.1)
        await self.send_command([0x01])
        await asyncio.sleep(0.2)

    async def stop(self):
        """Safely powers down hardware and releases the USB port."""
        if self.dev:
            try:
                # Ensure lamp and magnet are off before disconnect
                await self.send_command([0x03, 0x00])
                await self.send_command([0x0F, 0x00])
                self.dev.reset()
                usb.util.dispose_resources(self.dev)
            except Exception:
                pass
            print("NanoDrop safely disconnected.")

    async def send_command(self, payload: List[int]):
        """Generic transport method for writing to the command mailbox."""
        self.dev.write(self.EP_OUT, payload)

    async def read_comm(self, timeout=500) -> bytes:
        """Reads from the 64-byte text/status endpoint."""
        return self.dev.read(self.EP_IN_COMM, 64, timeout=timeout)

    async def read_heavy(self, packets=64, timeout=1000) -> bytearray:
        """Reads bulk interleaved blocks from the main camera endpoint."""
        data_buffer = bytearray()
        for _ in range(packets):
            data_buffer.extend(self.dev.read(self.EP_IN_HEAVY, 64, timeout=timeout))
        return data_buffer

    def flush_comm(self):
        try:
            while True: self.dev.read(self.EP_IN_COMM, 64, timeout=50)
        except usb.core.USBTimeoutError: pass

    def flush_heavy(self):
        try:
            while True: self.dev.read(self.EP_IN_HEAVY, 512, timeout=50)
        except usb.core.USBTimeoutError: pass


class ThermoFisherNanoDrop1000AbsorbanceBackend(CapabilityBackend): # Ideally inherits from AbsorbanceBackend
    """
    Translates scientific workflow methods into raw driver commands.
    Holds state for coefficients, dark spectra, and blank spectra.
    """
    def __init__(self, driver: ThermoFisherNanoDrop1000Driver):
        super().__init__()
        self.driver = driver
        self.coefficients = {}
        self.wavelengths = None
        
        self.dark_spectrum = None
        self.blank_spectrum = None

    async def _on_setup(self):
        """Lifecycle hook to download factory calibration on boot."""
        await self._download_all_coefficients()
        self._calculate_x_axis()
        print("NanoDrop Initialized and Calibrated.")

    async def _on_stop(self):
        """Lifecycle hook to clean up state on teardown."""
        self.coefficients.clear()

    async def set_lamp(self, state: bool):
        cmd = 0xFF if state else 0x00
        await self.driver.send_command([0x03, cmd])

    async def set_magnet(self, state: bool):
        cmd = 0xFF if state else 0x00
        await self.driver.send_command([0x0F, cmd])

    async def set_integration_time(self, ms: int):
        if ms < 3:
            ms = 3
            print('Integration too low, setting to 3 ms')
        elif ms > 65535:
            ms = 65535
            print('Integration too high, setting to 65535 ms')
            
        lsb = ms & 0xFF
        msb = (ms >> 8) & 0xFF
        await self.driver.send_command([0x02, lsb, msb])

    async def _download_all_coefficients(self):
        print("Downloading Factory Memory Map...")
        self.driver.flush_comm()

        for index in range(1, 15):
            if index == 5: continue 
            await self.driver.send_command([0x05, index])
            await asyncio.sleep(0.05)
            try:
                data = await self.driver.read_comm()
                text = bytearray(data[2:]).decode('ascii', errors='ignore').split('\x00')[0]
                self.coefficients[index] = float(text)
            except Exception:
                print(f"Warning: Failed to read coefficient index {index}")

    def _calculate_x_axis(self):
        pixels = np.arange(2048)
        c0, c1 = self.coefficients.get(1, 0), self.coefficients.get(2, 0)
        c2, c3 = self.coefficients.get(3, 0), self.coefficients.get(4, 0)
        self.wavelengths = c0 + (c1 * pixels) + (c2 * (pixels**2)) + (c3 * (pixels**3))

    async def get_raw_spectrum(self) -> np.ndarray:
        self.driver.flush_heavy()
        await self.driver.send_command([0x09]) 
        
        data_buffer = await self.driver.read_heavy()
            
        pixels = []
        for i in range(0, 4096, 128):
            lsb_block = data_buffer[i : i+64]
            msb_block = data_buffer[i+64 : i+128]
            for j in range(64):
                pixels.append((msb_block[j] << 8) | lsb_block[j])
                
        raw_intensities = np.array(pixels, dtype=float)
        
        # TODO [Future Work]: Optical Black Pixel Subtraction
        # The first 25 pixels (0-24) are optically black. Calculate their average
        # and subtract it from the entire array to correct for thermal baseline drift.
        
        # TODO [Future Work]: Non-Linearity Correction
        # Apply the 7th-order polynomial using coefficients 6 through 13 to `raw_intensities`
        # to ensure perfect photometric accuracy across the dynamic range.
        
        return raw_intensities

    async def take_blank(self, integration_ms=20):
        await self.set_integration_time(integration_ms)
        
        await self.set_lamp(False)
        await self.set_magnet(True) 
        await asyncio.sleep(0.2)
        print("Acquiring Dark baseline...")
        self.dark_spectrum = await self.get_raw_spectrum()
        
        await self.set_lamp(True)
        await asyncio.sleep(0.2)
        print("Acquiring Blank baseline...")
        self.blank_spectrum = await self.get_raw_spectrum()
        
        await self.set_lamp(False)
        await self.set_magnet(False)
        print("Blanking complete.")

    async def measure_absorbance(self, integration_ms=20) -> Tuple[np.ndarray, np.ndarray]:
        if self.blank_spectrum is None or self.dark_spectrum is None:
            raise ValueError("You must run take_blank() before measuring!")
            
        # TODO [Future Work]: Auto-Exposure Bracketing (HDR)
        # Replace the static `integration_ms` with a loop that fires 8ms, 16ms, 32ms, etc.
        # and mathematically stitches the optimal exposures together.
        
        await self.set_integration_time(integration_ms)
        await self.set_magnet(True)
        await self.set_lamp(True)
        await asyncio.sleep(0.2)
        
        print("Measuring sample...")
        sample_spectrum = await self.get_raw_spectrum()
        
        await self.set_lamp(False)
        await self.set_magnet(False)

        numerator = np.clip(sample_spectrum - self.dark_spectrum, 1, None)
        denominator = np.clip(self.blank_spectrum - self.dark_spectrum, 1, None)
        
        transmittance = numerator / denominator
        absorbance = -np.log10(transmittance)
        
        return self.wavelengths, absorbance


class ThermoFisherNanoDrop1000(Device):
    """
    Main PyLabRobot Device Class. 
    Constructs the driver and registers the absorbance capability.
    """
    def __init__(self, name: str = "NanoDrop1000"):
        super().__init__(name=name)
        
        # Construct ONE driver
        self.driver = ThermoFisherNanoDrop1000Driver()
        
        # Construct backends sharing the single driver
        self.absorbance_backend = ThermoFisherNanoDrop1000AbsorbanceBackend(driver=self.driver)
        
        # Append to capabilities
        self._capabilities.append(self.absorbance_backend)

    # setup() and stop() are completely removed! Inherited behavior takes over.

    # CAUTION: Convenience methods below map to Capability operations. 
    async def take_blank(self, integration_ms=20):
        await self.absorbance_backend.take_blank(integration_ms)

    async def measure_absorbance(self, integration_ms=20):
        return await self.absorbance_backend.measure_absorbance(integration_ms)