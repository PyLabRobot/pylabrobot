"""MT-SICS protocol-level simulator for device-free testing.

Inherits from MettlerToledoWXS205SDUBackend and overrides send_command to return
mock MT-SICS responses. All high-level methods (zero, tare, read_weight, etc.)
work unchanged because they call send_command which is intercepted here.

This follows the same pattern as STARChatterboxBackend in PLR (inherits from
the hardware backend and overrides the low-level command transmission).
"""

import logging
from typing import List, Optional, Set

from pylabrobot.io.validation_utils import LOG_LEVEL_IO
from pylabrobot.scales.mettler_toledo.backend import (
  MettlerToledoResponse,
  MettlerToledoWXS205SDUBackend,
)
from pylabrobot.scales.scale_backend import ScaleBackend

logger = logging.getLogger("pylabrobot")


class MettlerToledoSICSSimulator(MettlerToledoWXS205SDUBackend):
  """MT-SICS protocol simulator for testing without hardware.

  Inherits all MT-SICS methods from MettlerToledoWXS205SDUBackend.
  Overrides send_command to return mock MT-SICS responses.

  Set ``platform_weight`` and ``sample_weight`` to simulate placing items
  on the scale. The total sensor reading is ``platform_weight + sample_weight``.

  Example::

    backend = MettlerToledoSICSSimulator()
    scale = Scale(name="scale", backend=backend, size_x=0, size_y=0, size_z=0)
    await scale.setup()
    # backend.device_type == "WXS205SDU", backend.capacity == 220.0

    backend.platform_weight = 50.0       # place 50g container
    await scale.tare()
    backend.sample_weight = 10.0         # add 10g
    weight = await scale.read_weight()   # returns 10.0
  """

  def __init__(
    self,
    device_type: str = "WXS205SDU WXA-Bridge",
    serial_number: str = "SIM0000001",
    capacity: float = 220.0,
    supported_commands: Optional[Set[str]] = None,
  ) -> None:
    # Skip MettlerToledoWXS205SDUBackend.__init__ (which creates a Serial object)
    ScaleBackend.__init__(self)

    # Physics state
    self.platform_weight: float = 0.0
    self.sample_weight: float = 0.0
    self.zero_offset: float = 0.0
    self.tare_weight: float = 0.0
    self.temperature: float = 22.5

    # Simulated device identity
    self._human_readable_device_name = "Mettler Toledo Scale"
    self.device_type = device_type
    self.serial_number = serial_number
    self.capacity = capacity
    self.software_material_number: str = "12121306C"
    self.device_id: str = "SimScale"
    self.next_service_date: str = "16.03.2013"
    self.assortment_type_revision: str = "5"
    self.operating_mode_after_restart: str = "0"
    self.date: str = "30.03.2026"
    self.time: str = "12:00:00"

    # Simulated device configuration
    self.weighing_mode: str = "0"
    self.environment_condition: str = "2"
    self.auto_zero: str = "1"
    self.update_rate: str = "18.3"
    self.adjustment_setting: str = "0 0"
    self.serial_parameters: str = "0 6 3 1"
    self.filter_cutoff: str = "0.000"
    self.readability: str = "5"
    self.test_settings: str = "0"
    self.weighing_value_release: str = "1"
    self.operating_mode: str = "0"
    self.profact_time_criteria: str = "00 00 00 0"
    self.profact_temperature_criterion: str = "1"
    self.adjustment_weight: str = "10.00000 g"
    self.test_weight: str = "200.00000 g"
    self.profact_day: str = "0"
    self.zeroing_mode: str = "0"
    self.uptime_minutes: int = 60 * 24 # 1 day in minutes
    # Default: all commands the simulator can mock
    self._supported_commands = supported_commands or {
      "@",
      "C",
      "C0",
      "COM",
      "D",
      "DAT",
      "DW",
      "FCUT",
      "I0",
      "I1",
      "I2",
      "I3",
      "I4",
      "I5",
      "I10",
      "I11",
      "I14",
      "I15",
      "I16",
      "I21",
      "I26",
      "I50",
      "LST",
      "M01",
      "M02",
      "M03",
      "M17",
      "M18",
      "M19",
      "M20",
      "M21",
      "M27",
      "M28",
      "M29",
      "M31",
      "M32",
      "M33",
      "M35",
      "RDB",
      "S",
      "SC",
      "SI",
      "SIS",
      "SNR",
      "T",
      "TA",
      "TAC",
      "TC",
      "TI",
      "TIM",
      "TST0",
      "UPD",
      "USTB",
      "Z",
      "ZC",
      "ZI",
    }

  @property
  def _sensor_reading(self) -> float:
    return self.platform_weight + self.sample_weight

  async def setup(self) -> None:
    self.firmware_version = "1.10 18.6.4.1361.772"
    self.configuration = "Bridge" if "Bridge" in self.device_type else "Balance"
    logger.info(
      "[%s] Connected (simulation)\n"
      "Device type: %s\n"
      "Configuration: %s\n"
      "Serial number: %s\n"
      "Firmware: %s\n"
      "Capacity: %.1f g\n"
      "Supported commands (%d): %s",
      self._human_readable_device_name,
      self.device_type,
      self.configuration,
      self.serial_number,
      self.firmware_version,
      self.capacity,
      len(self._supported_commands),
      ", ".join(sorted(self._supported_commands)),
    )

  async def stop(self) -> None:
    logger.info("[%s] Disconnected (simulation)", self._human_readable_device_name)

  async def reset(self) -> str:
    responses = await self.send_command("@")
    self._validate_response(responses[0], 3, "@")
    return responses[0].data[0]

  async def send_command(self, command: str, timeout: int = 60) -> List[MettlerToledoResponse]:
    logger.log(LOG_LEVEL_IO, "[%s] Sent command: %s", self._human_readable_device_name, command)
    responses = self._build_response(command)
    for resp in responses:
      logger.log(
        LOG_LEVEL_IO,
        "[%s] Received response: %s %s %s",
        self._human_readable_device_name,
        resp.command,
        resp.status,
        " ".join(resp.data),
      )
      self._parse_basic_errors(resp)
    return responses

  def _build_response(self, command: str) -> List[MettlerToledoResponse]:
    R = MettlerToledoResponse
    cmd = command.split()[0]
    net = round(self._sensor_reading - self.zero_offset - self.tare_weight, 5)

    # Reset and cancel
    if cmd == "@":
      return [R("I4", "A", [self.serial_number])]
    if cmd == "C":
      return [R("C", "B"), R("C", "A")]

    # Device identity
    if cmd == "I0":
      cmds = sorted(self._supported_commands)
      responses = [R("I0", "B", ["0", c]) for c in cmds[:-1]]
      responses.append(R("I0", "A", ["0", cmds[-1]]))
      return responses
    if cmd == "I1":
      return [R("I1", "A", ["01"])]
    if cmd == "I2":
      return [R("I2", "A", [f"{self.device_type} {self.capacity:.5f} g"])]
    if cmd == "I3":
      return [R("I3", "A", [self.firmware_version])]
    if cmd == "I4":
      return [R("I4", "A", [self.serial_number])]
    if cmd == "I5":
      return [R("I5", "A", [self.software_material_number])]
    if cmd == "I10":
      parts = command.split(maxsplit=1)
      if len(parts) > 1:
        self.device_id = parts[1].strip('"')
        return [R("I10", "A")]
      return [R("I10", "A", [self.device_id])]
    if cmd == "I11":
      return [R("I11", "A", [self.device_type.split()[0]])]
    if cmd == "I14":
      return [
        R("I14", "B", ["0", "1", "Bridge"]),
        R("I14", "A", ["1", "1", self.device_type]),
      ]
    if cmd == "I15":
      return [R("I15", "A", [str(self.uptime_minutes)])]
    if cmd == "I16":
      d, m, y = self.next_service_date.split(".")
      return [R("I16", "A", [d, m, y])]
    if cmd == "I21":
      return [R("I21", "A", [self.assortment_type_revision])]
    if cmd == "I26":
      return [R("I26", "A", [self.operating_mode_after_restart])]
    if cmd == "DAT":
      parts = command.split()
      if len(parts) > 1:
        self.date = f"{parts[1]}.{parts[2]}.{parts[3]}"
        return [R("DAT", "A")]
      d, m, y = self.date.split(".")
      return [R("DAT", "A", [d, m, y])]
    if cmd == "TIM":
      parts = command.split()
      if len(parts) > 1:
        self.time = f"{parts[1]}:{parts[2]}:{parts[3]}"
        return [R("TIM", "A")]
      h, mi, s = self.time.split(":")
      return [R("TIM", "A", [h, mi, s])]

    # Zero
    if cmd in ("Z", "ZI", "ZC"):
      self.zero_offset = self._sensor_reading
      return [R(cmd, "A")]

    # Tare
    if cmd in ("T", "TI", "TC"):
      self.tare_weight = self._sensor_reading - self.zero_offset
      return [R(cmd, "S", [f"{self.tare_weight:.5f}", "g"])]
    if cmd == "TA":
      return [R("TA", "A", [f"{self.tare_weight:.5f}", "g"])]
    if cmd == "TAC":
      self.tare_weight = 0.0
      return [R("TAC", "A")]

    # Weight measurement
    if cmd in ("S", "SI", "SC"):
      return [R(cmd, "S", [f"{net:.5f}", "g"])]
    if cmd == "M28":
      return [R("M28", "A", ["1", str(self.temperature)])]
    if cmd == "SIS":
      state = "0"
      info = "0" if self.tare_weight == 0 else "1"
      return [R("SIS", "A", [state, f"{net:.5f}", "0", "5", "1", "0", info])]
    if cmd == "SNR":
      return [R("SNR", "S", [f"{net:.5f}", "g"])]
    if cmd == "I50":
      remaining = self.capacity - self.platform_weight - self.sample_weight
      return [
        R("I50", "B", ["0", f"{remaining:.3f}", "g"]),
        R("I50", "B", ["1", "0.000", "g"]),
        R("I50", "A", ["2", f"{remaining:.3f}", "g"]),
      ]

    # Device configuration (read-only)
    if cmd == "M01":
      return [R("M01", "A", [self.weighing_mode])]
    if cmd == "M02":
      return [R("M02", "A", [self.environment_condition])]
    if cmd == "M03":
      return [R("M03", "A", [self.auto_zero])]
    if cmd == "M17":
      return [R("M17", "A", self.profact_time_criteria.split())]
    if cmd == "M18":
      return [R("M18", "A", [self.profact_temperature_criterion])]
    if cmd == "M19":
      return [R("M19", "A", self.adjustment_weight.split())]
    if cmd == "M20":
      return [R("M20", "A", self.test_weight.split())]
    if cmd == "M27":
      return [
        R("M27", "B", ["1", "1", "1", "2026", "8", "0", "0", ""]),
        R("M27", "A", ["2", "15", "3", "2026", "10", "30", "1", "200.1234 g"]),
      ]
    if cmd == "M29":
      return [R("M29", "A", [self.weighing_value_release])]
    if cmd == "M31":
      return [R("M31", "A", [self.operating_mode])]
    if cmd == "M32":
      return [
        R("M32", "B", ["1", "00", "00", "0"]),
        R("M32", "B", ["2", "00", "00", "0"]),
        R("M32", "A", ["3", "00", "00", "0"]),
      ]
    if cmd == "M33":
      return [R("M33", "A", [self.profact_day])]
    if cmd == "M35":
      return [R("M35", "A", [self.zeroing_mode])]
    if cmd == "UPD":
      return [R("UPD", "A", [self.update_rate])]
    if cmd == "C0":
      return [R("C0", "A", self.adjustment_setting.split())]
    if cmd == "COM":
      return [R("COM", "A", self.serial_parameters.split())]
    if cmd == "FCUT":
      return [R("FCUT", "A", [self.filter_cutoff])]
    if cmd == "RDB":
      return [R("RDB", "A", [self.readability])]
    if cmd == "USTB":
      return [
        R("USTB", "B", ["0", "3.600", "1.100"]),
        R("USTB", "B", ["1", "0.000", "0.000"]),
        R("USTB", "A", ["2", "0.000", "0.000"]),
      ]
    if cmd == "TST0":
      return [R("TST0", "A", [self.test_settings])]
    if cmd == "LST":
      return [
        R("LST", "B", ["C0"] + self.adjustment_setting.split()),
        R("LST", "B", ["FCUT", self.filter_cutoff]),
        R("LST", "B", ["M01", self.weighing_mode]),
        R("LST", "B", ["M02", self.environment_condition]),
        R("LST", "B", ["M03", self.auto_zero]),
        R("LST", "B", ["M21", "0", "0"]),
        R("LST", "A", ["UPD", self.update_rate]),
      ]

    # Display
    if cmd in ("D", "DW"):
      return [R(cmd, "A")]

    # Configuration (write)
    if cmd == "M21":
      return [R("M21", "A")]

    # Unknown command
    return [R("ES", "")]
