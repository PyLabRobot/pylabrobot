import logging
from enum import Enum
from .base_control import baseControl
from .spark_enums import MeasurementMode, ModuleType

class ScanDirection(Enum):
    UP = "UP"
    DOWN = "DOWN"
    ALTERNATE_UP = "ALTERNATE_UP"
    ALTERNATE_DOWN = "ALTERNATE_DOWN"

class ScanDarkState(Enum):
    TRUE = "TRUE"
    FALSE = "FALSE"

class measurement_control(baseControl):
    """
    This class provides methods for controlling measurement operations on the device.
    It includes functionalities to start/end measurements, set/get measurement modes,
    prepare the instrument, and perform various types of scans.
    """

    async def start_measurement(self):
        """Starts the measurement process."""
        return await self.send_command("MEASUREMENT START")

    async def end_measurement(self):
        """Ends the measurement process."""
        return await self.send_command("MEASUREMENT END")

    async def set_measurement_mode(self, mode: MeasurementMode):
        """Sets the measurement mode (e.g., ABS, LUM, FLUOR)."""
        return await self.send_command(f"MODE MEASUREMENT={mode.value}")

    async def get_available_measurement_modes(self):
        """Gets the available measurement modes."""
        response = await self.send_command("#MODE MEASUREMENT")
        return response

    async def get_current_measurement_mode(self):
        """Gets the current measurement mode."""
        return await self.send_command("?MODE MEASUREMENT")

    async def get_label_range(self):
        """Gets the range of available labels for scans."""
        return await self.send_command("#SCAN LABEL")

    async def prepare_instrument(self, measure_reference=True, mode: MeasurementMode=None, labels=None):
        """Prepares the instrument for measurement."""
        command = f"PREPARE REFERENCE={'YES' if measure_reference else 'NO'}"
        if mode and labels:
            command += f" MODE={mode.value} LABEL={'|'.join(map(str, labels))}"
        return await self.send_command(command)

    async def set_scan_direction(self, direction: ScanDirection):
        """Sets the scan direction."""
        return await self.send_command(f"SCAN DIRECTION={direction.value}")

    async def get_available_scan_directions(self):
        """Gets the available scan directions."""
        response = await self.send_command("#SCAN DIRECTION")
        return response

    async def get_current_scan_direction(self):
        """Gets the current scan direction."""
        return await self.send_command("?SCAN DIRECTION")

    def _format_scan_range(self, coordinate, from_val, to_val, step_type_is_delta, steps):
        if all(v is not None for v in [coordinate, from_val, to_val, step_type_is_delta, steps]):
            step_char = ':' if step_type_is_delta else '%'
            return f" {coordinate}={from_val}~{to_val}{step_char}{steps}"
        return ""

    async def _measure_in(self, scale, from_val=None, to_val=None, x=None, y=None, z=None, steps=None, step_type_is_delta=None, mode: MeasurementMode=None, labels=None):
        command = "SCAN"
        if scale == 'X':
            command += self._format_scan_range('X', from_val, to_val, step_type_is_delta, steps)
            if y is not None: command += f" Y={y}"
            if z is not None: command += f" Z={z}"
        elif scale == 'Y':
            if x is not None: command += f" X={x}"
            command += self._format_scan_range('Y', from_val, to_val, step_type_is_delta, steps)
            if z is not None: command += f" Z={z}"
        elif scale == 'Z':
            if x is not None: command += f" X={x}"
            if y is not None: command += f" Y={y}"
            command += self._format_scan_range('Z', from_val, to_val, step_type_is_delta, steps)
        elif scale == 'T':
            if x is not None: command += f" X={x}"
            if y is not None: command += f" Y={y}"
            if z is not None: command += f" Z={z}"
            command += self._format_scan_range('T', from_val, to_val, step_type_is_delta, steps)
        else: # None
            if x is not None: command += f" X={x}"
            if y is not None: command += f" Y={y}"
            if z is not None: command += f" Z={z}"

        if mode and labels:
            command += f" MODE={mode.value} LABEL={'|'.join(map(str, labels))}"

        return await self.send_command(command)

    async def measure_range_in_x_pointwise(self, from_x, to_x, position_y, position_z, num_points, mode: MeasurementMode=None, labels=None):
        return await self._measure_in('X', from_x, to_x, y=position_y, z=position_z, steps=num_points, step_type_is_delta=False, mode=mode, labels=labels)

    async def measure_range_in_x_by_delta(self, from_x, to_x, position_y, position_z, delta, mode: MeasurementMode=None, labels=None):
        return await self._measure_in('X', from_x, to_x, y=position_y, z=position_z, steps=delta, step_type_is_delta=True, mode=mode, labels=labels)

    async def measure_range_in_y_pointwise(self, position_x, from_y, to_y, position_z, num_points, mode: MeasurementMode=None, labels=None):
        return await self._measure_in('Y', from_y, to_y, x=position_x, z=position_z, steps=num_points, step_type_is_delta=False, mode=mode, labels=labels)

    async def measure_range_in_y_by_delta(self, position_x, from_y, to_y, position_z, delta, mode: MeasurementMode=None, labels=None):
        return await self._measure_in('Y', from_y, to_y, x=position_x, z=position_z, steps=delta, step_type_is_delta=True, mode=mode, labels=labels)

    async def measure_range_in_z_pointwise(self, position_x, position_y, from_z, to_z, num_points, mode: MeasurementMode=None, labels=None):
        return await self._measure_in('Z', from_z, to_z, x=position_x, y=position_y, steps=num_points, step_type_is_delta=False, mode=mode, labels=labels)

    async def measure_range_in_z_by_delta(self, position_x, position_y, from_z, to_z, delta, mode: MeasurementMode=None, labels=None):
        return await self._measure_in('Z', from_z, to_z, x=position_x, y=position_y, steps=delta, step_type_is_delta=True, mode=mode, labels=labels)

    async def measure_range_in_t_pointwise(self, position_x, position_y, position_z, from_t, to_t, num_points, mode: MeasurementMode=None, labels=None):
        return await self._measure_in('T', from_t, to_t, x=position_x, y=position_y, z=position_z, steps=num_points, step_type_is_delta=False, mode=mode, labels=labels)

    async def measure_range_in_t_by_delta(self, position_x, position_y, position_z, from_t, to_t, delta, mode: MeasurementMode=None, labels=None):
        return await self._measure_in('T', from_t, to_t, x=position_x, y=position_y, z=position_z, steps=delta, step_type_is_delta=True, mode=mode, labels=labels)

    async def measure_position(self, x=None, y=None, z=None, mode: MeasurementMode=None, labels=None):
        return await self._measure_in(None, x=x, y=y, z=z, mode=mode, labels=labels)

    async def measure_current_position(self, mode: MeasurementMode=None, labels=None):
        return await self._measure_in(None, mode=mode, labels=labels)

    async def ensure_measurement_mode(self, mode: MeasurementMode):
        """Ensures the measurement mode is set, only if different from current."""
        current_mode = await self.get_current_measurement_mode()
        if current_mode != f"MEASUREMENT={mode.value.upper()}":
            return await self.set_measurement_mode(mode)
        logging.info(f"Measurement Mode already set to: {mode.value}")
        return "OK"

    async def set_scan_dark(self, state: ScanDarkState, module: ModuleType=ModuleType.FLUORESCENCE):
        """Sets the scan dark state for a module."""
        return await self.send_command(f"SCAN DARK={state.value} MODULE={module.value}")

    async def set_parallel_excitation_polarisation(self):
        """Sets the excitation polarisation to parallel."""
        return await self.send_command("POLARISATION EXCITATION=PARALLEL")

    async def set_perpendicular_excitation_polarisation(self):
        """Sets the excitation polarisation to perpendicular."""
        return await self.send_command("POLARISATION EXCITATION=PERPENDICULAR")

    async def set_parallel_emission_polarisation(self):
        """Sets the emission polarisation to parallel."""
        return await self.send_command("POLARISATION EMISSION=PARALLEL")

    async def set_perpendicular_emission_polarisation(self):
        """Sets the emission polarisation to perpendicular."""
        return await self.send_command("POLARISATION EMISSION=PERPENDICULAR")


    async def set_polarisation_emission_mode(self, mode):
        """Sets the polarisation emission mode."""
        return await self.send_command(f"POLARISATION MODE_EMISSION={mode.upper()}")

    async def get_possible_number_of_reads_range(self):
        """Gets the possible range for the number of reads."""
        return await self.send_command("#READ COUNT")

    async def set_number_of_reads(self, count, label=None):
        """Sets the number of reads."""
        command = "READ"
        if label is not None:
            command += f" LABEL={label}"
        command += f" COUNT={count}"
        return await self.send_command(command)

    async def get_current_number_of_reads(self, label=None):
        """Gets the current number of reads."""
        command = "?READ"
        if label is not None:
            command += f" LABEL={label}"
        command += " COUNT"
        return await self.send_command(command)

    async def get_allowed_read_speed_range(self):
        """Gets the allowed range for read speed."""
        return await self.send_command("#READ SPEED")

    async def set_read_speed(self, speed):
        """Sets the read speed."""
        return await self.send_command(f"READ SPEED={speed}")

    async def get_current_read_speed(self):
        """Gets the current read speed."""
        return await self.send_command("?READ SPEED")


