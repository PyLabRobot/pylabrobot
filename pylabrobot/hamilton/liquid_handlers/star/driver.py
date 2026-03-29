"""STARDriver: inherits HamiltonLiquidHandler, adds STAR-specific config and error handling."""

import asyncio
import datetime
import enum
import math
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Dict, List, Optional

from pylabrobot.capabilities.liquid_handling.head96_backend import Head96Backend
from pylabrobot.capabilities.liquid_handling.pip_backend import PIPBackend
from pylabrobot.hamilton.liquid_handlers.base import HamiltonLiquidHandler
from pylabrobot.legacy.liquid_handling.backends.hamilton.STAR_backend import (
  STARFirmwareError,
  parse_star_fw_string,
  star_firmware_string_to_error,
)
from pylabrobot.resources.hamilton import TipPickupMethod, TipSize

if TYPE_CHECKING:
  from .autoload import STARAutoload
  from .cover import STARCover
  from .iswap import iSWAPBackend
  from .wash_station import STARWashStation
  from .x_arm import STARXArm


# ---------------------------------------------------------------------------
# Configuration dataclasses
# ---------------------------------------------------------------------------


@dataclass
class DriveConfiguration:
  """Configuration for an X drive (left or right)."""

  pip_installed: bool = False
  iswap_installed: bool = False
  core_96_head_installed: bool = False
  nano_pipettor_installed: bool = False
  dispensing_head_384_installed: bool = False
  xl_channels_installed: bool = False
  tube_gripper_installed: bool = False
  imaging_channel_installed: bool = False
  robotic_channel_installed: bool = False


@dataclass
class MachineConfiguration:
  """Response from RM (Request Machine Configuration) command."""

  pip_type_1000ul: bool = False
  kb_iswap_installed: bool = False
  main_front_cover_monitoring_installed: bool = False
  auto_load_installed: bool = False
  wash_station_1_installed: bool = False
  wash_station_2_installed: bool = False
  temp_controlled_carrier_1_installed: bool = False
  temp_controlled_carrier_2_installed: bool = False
  num_pip_channels: int = 0


@dataclass
class ExtendedConfiguration:
  """Response from QM (Request Extended Configuration) command."""

  left_x_drive_large: bool = False
  ka_core_96_head_installed: bool = False
  right_x_drive_large: bool = False
  pump_station_1_installed: bool = False
  pump_station_2_installed: bool = False
  wash_station_1_type_cr: bool = False
  wash_station_2_type_cr: bool = False
  left_cover_installed: bool = False
  right_cover_installed: bool = False
  additional_front_cover_monitoring_installed: bool = False
  pump_station_3_installed: bool = False
  multi_channel_nano_pipettor_installed: bool = False
  dispensing_head_384_installed: bool = False
  xl_channels_installed: bool = False
  tube_gripper_installed: bool = False
  waste_direction_left: bool = False
  iswap_gripper_wide: bool = False
  additional_channel_nano_pipettor_installed: bool = False
  imaging_channel_installed: bool = False
  robotic_channel_installed: bool = False
  channel_order_ox_first: bool = False
  x0_interface_ham_can: bool = False
  park_heads_with_iswap_off: bool = False
  configuration_data_3: int = 0
  instrument_size_slots: int = 54
  auto_load_size_slots: int = 54
  tip_waste_x_position: float = 1340.0
  left_x_drive: DriveConfiguration = field(default_factory=DriveConfiguration)
  right_x_drive: DriveConfiguration = field(default_factory=DriveConfiguration)
  min_iswap_collision_free_position: float = 350.0
  max_iswap_collision_free_position: float = 1140.0
  left_x_arm_width: float = 370.0
  right_x_arm_width: float = 370.0
  num_xl_channels: int = 0
  num_robotic_channels: int = 0
  min_raster_pitch_pip_channels: float = 9.0
  min_raster_pitch_xl_channels: float = 36.0
  min_raster_pitch_robotic_channels: float = 36.0
  pip_maximal_y_position: float = 606.5
  left_arm_min_y_position: float = 6.0
  right_arm_min_y_position: float = 6.0


# ---------------------------------------------------------------------------
# STARDriver
# ---------------------------------------------------------------------------


class STARDriver(HamiltonLiquidHandler):
  """Driver for Hamilton STAR liquid handlers.

  Inherits USB I/O, command assembly, and background reading from HamiltonLiquidHandler.
  Adds STAR-specific firmware parsing, error handling, and machine configuration.
  """

  def __init__(
    self,
    device_address: Optional[int] = None,
    serial_number: Optional[str] = None,
    packet_read_timeout: int = 3,
    read_timeout: int = 30,
    write_timeout: int = 30,
  ):
    super().__init__(
      id_product=0x8000,
      device_address=device_address,
      serial_number=serial_number,
      packet_read_timeout=packet_read_timeout,
      read_timeout=read_timeout,
      write_timeout=write_timeout,
    )

    # Populated during setup().
    self.machine_conf: Optional[MachineConfiguration] = None
    self.extended_conf: Optional[ExtendedConfiguration] = None
    self._channels_minimum_y_spacing: List[float] = []
    self.pip: PIPBackend  # set in setup()
    self.head96: Optional[Head96Backend] = None  # set in setup() if installed
    self.iswap: Optional["iSWAPBackend"] = None  # set in setup() if installed
    self.autoload: Optional["STARAutoload"] = None  # set in setup() if installed
    self.left_x_arm: Optional["STARXArm"] = None  # set in setup()
    self.right_x_arm: Optional["STARXArm"] = None  # set in setup()
    self.cover: Optional["STARCover"] = None  # set in setup()
    self.wash_station: Optional["STARWashStation"] = None  # set in setup()

  # -- HamiltonLiquidHandler abstract methods --------------------------------

  @property
  def module_id_length(self) -> int:
    return 2

  @property
  def num_channels(self) -> int:
    if self.machine_conf is None:
      raise RuntimeError("Driver not set up — call setup() first.")
    return self.machine_conf.num_pip_channels

  def get_id_from_fw_response(self, resp: str) -> Optional[int]:
    parsed = parse_star_fw_string(resp, "id####")
    if "id" in parsed and parsed["id"] is not None:
      return int(parsed["id"])
    return None

  def check_fw_string_error(self, resp: str) -> None:
    module = resp[:2]
    if module == "C0":
      exp = r"er(?P<C0>[0-9]{2}/[0-9]{2})"
      for mod in [
        "X0", "I0", "W1", "W2", "T1", "T2", "R0",
        "P1", "P2", "P3", "P4", "P5", "P6", "P7", "P8",
        "P9", "PA", "PB", "PC", "PD", "PE", "PF", "PG",
        "H0", "HW", "HU", "HV", "N0", "D0", "NP", "M1",
      ]:
        exp += f" ?(?:{mod}(?P<{mod}>[0-9]{{2}}/[0-9]{{2}}))?"
      errors = re.search(exp, resp)
    else:
      exp = f"er(?P<{module}>[0-9]{{2}})"
      errors = re.search(exp, resp)

    if errors is None:
      return

    errors_dict = {k: v for k, v in errors.groupdict().items() if v is not None}
    errors_dict = {k: v for k, v in errors_dict.items() if v not in ("00", "00/00")}

    if len(errors_dict) > 0:
      raise star_firmware_string_to_error(error_code_dict=errors_dict, raw_response=resp)

  def _parse_response(self, resp: str, fmt: Any) -> dict:
    return parse_star_fw_string(resp, fmt)

  async def define_tip_needle(
    self,
    tip_type_table_index: int,
    has_filter: bool,
    tip_length: int,
    maximum_tip_volume: int,
    tip_size: TipSize,
    pickup_method: TipPickupMethod,
  ) -> None:
    assert 0 <= tip_type_table_index <= 99
    assert 1 <= tip_length <= 1999
    assert 1 <= maximum_tip_volume <= 56000

    await self.send_command(
      module="C0",
      command="TT",
      tt=f"{tip_type_table_index:02}",
      tf=has_filter,
      tl=f"{tip_length:04}",
      tv=f"{maximum_tip_volume:05}",
      tg=tip_size.value,
      tu=pickup_method.value,
    )

  # -- lifecycle ------------------------------------------------------------

  async def setup(self):
    await super().setup()
    self.id_ = 0
    self.machine_conf = await self._request_machine_configuration()
    self.extended_conf = await self._request_extended_configuration()

    # Create backends based on discovered config.
    from .pip_backend import STARPIPBackend  # deferred to avoid circular imports

    self.pip = STARPIPBackend(self)

    self._channels_minimum_y_spacing = await self.channels_request_y_minimum_spacing()

    if self.extended_conf.left_x_drive.core_96_head_installed:
      from .head96_backend import STARHead96Backend

      self.head96 = STARHead96Backend(self)
    else:
      self.head96 = None

    if self.extended_conf.left_x_drive.iswap_installed:
      from .iswap import iSWAPBackend

      self.iswap = iSWAPBackend(driver=self)
    else:
      self.iswap = None

    if self.machine_conf.auto_load_installed:
      from .autoload import STARAutoload

      self.autoload = STARAutoload(
        driver=self,
        instrument_size_slots=self.extended_conf.instrument_size_slots,
      )
    else:
      self.autoload = None

    from .x_arm import STARXArm

    self.left_x_arm = STARXArm(driver=self, side="left")
    if self.extended_conf.right_x_drive_large:
      self.right_x_arm = STARXArm(driver=self, side="right")
    else:
      self.right_x_arm = None

    # Cover is always present.
    from .cover import STARCover

    self.cover = STARCover(driver=self)

    if (self.machine_conf.wash_station_1_installed or
        self.machine_conf.wash_station_2_installed):
      from .wash_station import STARWashStation

      self.wash_station = STARWashStation(driver=self)
    else:
      self.wash_station = None

  async def stop(self):
    await super().stop()
    self.machine_conf = None
    self.extended_conf = None
    self._channels_minimum_y_spacing = []
    self.head96 = None
    self.iswap = None
    self.autoload = None
    self.left_x_arm = None
    self.right_x_arm = None
    self.cover = None
    self.wash_station = None

  # -- liquid level probing ---------------------------------------------------

  async def probe_liquid_heights(self, containers, use_channels, resource_offsets=None,
                                  move_to_z_safety_after=True, **kwargs):
    """Probe liquid heights using cLLD. Override in subclasses with real implementation."""
    raise NotImplementedError(
      "probe_liquid_heights is not implemented on STARDriver. "
      "Use STARBackend (legacy) or implement probing on your driver subclass."
    )

  # -- core gripper tool management ------------------------------------------

  async def pick_up_core_gripper_tools(
    self,
    x_position: float,
    back_channel_y: float,
    front_channel_y: float,
    back_channel: int,
    front_channel: int,
    begin_z: float = 235.0,
    end_z: float = 225.0,
    traversal_height: float = 280.0,
  ):
    """Pick up CoRe gripper tools from the mount (C0ZT)."""
    await self.send_command(
      module="C0",
      command="ZT",
      xs=f"{round(x_position * 10):05}",
      xd="0",
      ya=f"{round(back_channel_y * 10):04}",
      yb=f"{round(front_channel_y * 10):04}",
      pa=f"{back_channel + 1:02}",
      pb=f"{front_channel + 1:02}",
      tp=f"{round(begin_z * 10):04}",
      tz=f"{round(end_z * 10):04}",
      th=round(traversal_height * 10),
      tt="14",
    )

  async def return_core_gripper_tools(
    self,
    x_position: float,
    back_channel_y: float,
    front_channel_y: float,
    begin_z: float = 215.0,
    end_z: float = 205.0,
    traversal_height: float = 280.0,
  ):
    """Return CoRe gripper tools to the mount (C0ZS)."""
    await self.send_command(
      module="C0",
      command="ZS",
      xs=f"{round(x_position * 10):05}",
      xd="0",
      ya=f"{round(back_channel_y * 10):04}",
      yb=f"{round(front_channel_y * 10):04}",
      tp=f"{round(begin_z * 10):04}",
      tz=f"{round(end_z * 10):04}",
      th=round(traversal_height * 10),
      te=round(traversal_height * 10),
    )

  # -- machine configuration ------------------------------------------------

  async def _request_machine_configuration(self) -> MachineConfiguration:
    resp = await self.send_command(module="C0", command="RM", fmt="kb**kp##")
    kb = resp["kb"]
    return MachineConfiguration(
      pip_type_1000ul=bool(kb & (1 << 0)),
      kb_iswap_installed=bool(kb & (1 << 1)),
      main_front_cover_monitoring_installed=bool(kb & (1 << 2)),
      auto_load_installed=bool(kb & (1 << 3)),
      wash_station_1_installed=bool(kb & (1 << 4)),
      wash_station_2_installed=bool(kb & (1 << 5)),
      temp_controlled_carrier_1_installed=bool(kb & (1 << 6)),
      temp_controlled_carrier_2_installed=bool(kb & (1 << 7)),
      num_pip_channels=resp["kp"],
    )

  async def _request_extended_configuration(self) -> ExtendedConfiguration:
    resp = await self.send_command(
      module="C0",
      command="QM",
      fmt="ka******ke********xt##xa##xw#####xl**xn**xr**xo**xm#####xx#####xu####xv####kc#kr#"
      + "ys###kl###km###ym####yu####yx####",
    )

    def _parse_drive(byte1: int, byte2: int) -> DriveConfiguration:
      return DriveConfiguration(
        pip_installed=bool(byte1 & (1 << 0)),
        iswap_installed=bool(byte1 & (1 << 1)),
        core_96_head_installed=bool(byte1 & (1 << 2)),
        nano_pipettor_installed=bool(byte1 & (1 << 3)),
        dispensing_head_384_installed=bool(byte1 & (1 << 4)),
        xl_channels_installed=bool(byte1 & (1 << 5)),
        tube_gripper_installed=bool(byte1 & (1 << 6)),
        imaging_channel_installed=bool(byte1 & (1 << 7)),
        robotic_channel_installed=bool(byte2 & (1 << 0)),
      )

    ka = resp["ka"]
    return ExtendedConfiguration(
      left_x_drive_large=bool(ka & (1 << 0)),
      ka_core_96_head_installed=bool(ka & (1 << 1)),
      right_x_drive_large=bool(ka & (1 << 2)),
      pump_station_1_installed=bool(ka & (1 << 3)),
      pump_station_2_installed=bool(ka & (1 << 4)),
      wash_station_1_type_cr=bool(ka & (1 << 5)),
      wash_station_2_type_cr=bool(ka & (1 << 6)),
      left_cover_installed=bool(ka & (1 << 7)),
      right_cover_installed=bool(ka & (1 << 8)),
      additional_front_cover_monitoring_installed=bool(ka & (1 << 9)),
      pump_station_3_installed=bool(ka & (1 << 10)),
      multi_channel_nano_pipettor_installed=bool(ka & (1 << 11)),
      dispensing_head_384_installed=bool(ka & (1 << 12)),
      xl_channels_installed=bool(ka & (1 << 13)),
      tube_gripper_installed=bool(ka & (1 << 14)),
      waste_direction_left=bool(ka & (1 << 15)),
      iswap_gripper_wide=bool(ka & (1 << 16)),
      additional_channel_nano_pipettor_installed=bool(ka & (1 << 17)),
      imaging_channel_installed=bool(ka & (1 << 18)),
      robotic_channel_installed=bool(ka & (1 << 19)),
      channel_order_ox_first=bool(ka & (1 << 20)),
      x0_interface_ham_can=bool(ka & (1 << 21)),
      park_heads_with_iswap_off=bool(ka & (1 << 22)),
      configuration_data_3=resp["ke"],
      instrument_size_slots=resp["xt"],
      auto_load_size_slots=resp["xa"],
      tip_waste_x_position=resp["xw"] / 10,
      left_x_drive=_parse_drive(resp["xl"], resp["xn"]),
      right_x_drive=_parse_drive(resp["xr"], resp["xo"]),
      min_iswap_collision_free_position=resp["xm"] / 10,
      max_iswap_collision_free_position=resp["xx"] / 10,
      left_x_arm_width=resp["xu"] / 10,
      right_x_arm_width=resp["xv"] / 10,
      num_xl_channels=resp["kc"],
      num_robotic_channels=resp["kr"],
      min_raster_pitch_pip_channels=resp["ys"] / 10,
      min_raster_pitch_xl_channels=resp["kl"] / 10,
      min_raster_pitch_robotic_channels=resp["km"] / 10,
      pip_maximal_y_position=resp["ym"] / 10,
      left_arm_min_y_position=resp["yu"] / 10,
      right_arm_min_y_position=resp["yx"] / 10,
    )

  # -- generic instrument operations --

  class BoardType(enum.Enum):
    C167CR_SINGLE_PROCESSOR_BOARD = 0
    C167CR_DUAL_PROCESSOR_BOARD = 1
    LPC2468_XE167_DUAL_PROCESSOR_BOARD = 2
    LPC2468_SINGLE_PROCESSOR_BOARD = 5
    UNKNOWN = -1

  # --- Firmware queries ---

  async def request_error_code(self):
    """Request error code (C0:RE).

    Retrieves the last saved error messages. The error buffer is automatically voided
    when a new command is started. All configured nodes are displayed.
    """

    return await self.send_command(module="C0", command="RE")

  async def request_firmware_version(self):
    """Request firmware version (C0:RF)."""

    return await self.send_command(module="C0", command="RF")

  async def request_parameter_value(self):
    """Request parameter value (C0:RA)."""

    return await self.send_command(module="C0", command="RA")

  async def request_master_status(self):
    """Request master status (C0:RQ)."""

    return await self.send_command(module="C0", command="RQ")

  async def request_eeprom_data_correctness(self):
    """Request EEPROM data correctness (C0:QV)."""

    return await self.send_command(module="C0", command="QV")

  # --- Hardware config queries ---

  async def request_electronic_board_type(self):
    """Request electronic board type (C0:QB).

    Returns:
      The board type.
    """

    resp = await self.send_command(module="C0", command="QB", fmt="qb#")
    try:
      return STARDriver.BoardType(resp["qb"])
    except ValueError:
      return STARDriver.BoardType.UNKNOWN

  async def request_supply_voltage(self):
    """Request supply voltage (C0:MU).

    Request supply voltage (for LDPB only).
    """

    return await self.send_command(module="C0", command="MU")

  async def request_number_of_presence_sensors_installed(self):
    """Request number of presence sensors installed (C0:SR).

    Returns:
      Number of sensors installed (1...103).
    """

    resp = await self.send_command(module="C0", command="SR", fmt="sr###")
    return resp["sr"]

  # --- Init status + diagnostics ---

  async def request_instrument_initialization_status(self) -> bool:
    """Request instrument initialization status (C0:QW)."""

    resp = await self.send_command(module="C0", command="QW", fmt="qw#")
    return resp is not None and resp["qw"] == 1

  async def request_name_of_last_faulty_parameter(self):
    """Request name of last faulty parameter (C0:VP).

    Returns:
      Name of last parameter with syntax error, optionally followed by the received value,
      minimal permitted value, and maximal permitted value.
    """

    return await self.send_command(module="C0", command="VP", fmt="vp&&")

  # --- Runtime control ---

  async def set_single_step_mode(self, single_step_mode: bool = False):
    """Set single step mode (C0:AM).

    Args:
      single_step_mode: Single Step Mode. Default False.
    """

    return await self.send_command(
      module="C0",
      command="AM",
      am=single_step_mode,
    )

  async def trigger_next_step(self):
    """Trigger next step in single step mode (C0:NS)."""

    return await self.send_command(module="C0", command="NS")

  async def halt(self):
    """Halt (C0:HD).

    Intermediate sequences not yet carried out and the commands in the command stack are
    discarded. The sequence already in process is completed.
    """

    return await self.send_command(module="C0", command="HD")

  async def set_not_stop(self, non_stop):
    """Set not stop mode (C0:AB/AW).

    Args:
      non_stop: True if non stop mode should be turned on after command is sent.
    """

    if non_stop:
      return await self.send_command(module="C0", command="AB")
    else:
      return await self.send_command(module="C0", command="AW")

  async def save_all_cycle_counters(self):
    """Save all cycle counters of the instrument (C0:AZ)."""

    return await self.send_command(module="C0", command="AZ")

  # --- X-drive queries ---

  async def request_maximal_ranges_of_x_drives(self):
    """Request maximal ranges of X drives (C0:RU)."""

    return await self.send_command(module="C0", command="RU")

  async def request_present_wrap_size_of_installed_arms(self):
    """Request present wrap size of installed arms (C0:UA)."""

    return await self.send_command(module="C0", command="UA")

  # -- EEPROM operations --

  async def store_installation_data(
    self,
    date: datetime.datetime = datetime.datetime.now(),
    serial_number: str = "0000",
  ):
    """Store installation data (C0:SI).

    Args:
      date: installation date.
      serial_number: 4-character serial number string.
    """

    assert len(serial_number) == 4, "serial number must be 4 chars long"

    return await self.send_command(module="C0", command="SI", si=date, sn=serial_number)

  async def store_verification_data(
    self,
    verification_subject: int = 0,
    date: datetime.datetime = datetime.datetime.now(),
    verification_status: bool = False,
  ):
    """Store verification data (C0:AV).

    Args:
      verification_subject: verification subject. Default 0. Must be between 0 and 24.
      date: verification date.
      verification_status: verification status.
    """

    assert 0 <= verification_subject <= 24, "verification_subject must be between 0 and 24"

    return await self.send_command(
      module="C0",
      command="AV",
      vo=verification_subject,
      vd=date,
      vs=verification_status,
    )

  async def additional_time_stamp(self):
    """Additional time stamp (C0:AT)."""

    return await self.send_command(module="C0", command="AT")

  async def save_download_date(self, date: datetime.datetime = datetime.datetime.now()):
    """Save Download date (C0:AO).

    Args:
      date: download date. Default now.
    """

    return await self.send_command(
      module="C0",
      command="AO",
      ao=date,
    )

  async def save_technical_status_of_assemblies(self, processor_board: str, power_supply: str):
    """Save technical status of assemblies (C0:BT).

    Args:
      processor_board: Processor board. Art.Nr./Rev./Ser.No. (000000/00/0000)
      power_supply: Power supply. Art.Nr./Rev./Ser.No. (000000/00/0000)
    """

    return await self.send_command(
      module="C0",
      command="BT",
      qt=processor_board + " " + power_supply,
    )

  async def set_x_offset_x_axis_iswap(self, x_offset: int):
    """Set X-offset X-axis <-> iSWAP (C0:AG).

    Args:
      x_offset: X-offset [0.1mm]
    """

    return await self.send_command(module="C0", command="AG", x_offset=x_offset)

  async def set_x_offset_x_axis_core_96_head(self, x_offset: int):
    """Set X-offset X-axis <-> CoRe 96 head (C0:AF).

    Args:
      x_offset: X-offset [0.1mm]
    """

    return await self.send_command(module="C0", command="AF", x_offset=x_offset)

  async def set_x_offset_x_axis_core_nano_pipettor_head(self, x_offset: int):
    """Set X-offset X-axis <-> CoRe 96 head (C0:AF).

    Args:
      x_offset: X-offset [0.1mm]
    """

    return await self.send_command(module="C0", command="AF", x_offset=x_offset)

  async def save_pip_channel_validation_status(self, validation_status: bool = False):
    """Save PIP channel validation status (C0:AJ).

    Args:
      validation_status: PIP channel validation status. Default False.
    """

    return await self.send_command(
      module="C0",
      command="AJ",
      tq=validation_status,
    )

  async def save_xl_channel_validation_status(self, validation_status: bool = False):
    """Save XL channel validation status (C0:AE).

    Args:
      validation_status: XL channel validation status. Default False.
    """

    return await self.send_command(
      module="C0",
      command="AE",
      tx=validation_status,
    )

  async def configure_node_names(self):
    """Configure node names (C0:AJ)."""

    return await self.send_command(module="C0", command="AJ")

  async def set_deck_data(self, data_index: int = 0, data_stream: str = "0"):
    """Set deck data (C0:DD).

    Args:
      data_index: data index. Must be between 0 and 9. Default 0.
      data_stream: data stream (12 characters). Default <class 'str'>.
    """

    assert 0 <= data_index <= 9, "data_index must be between 0 and 9"
    assert len(data_stream) == 12, "data_stream must be 12 chars"

    return await self.send_command(
      module="C0",
      command="DD",
      vi=data_index,
      vj=data_stream,
    )

  async def request_technical_status_of_assemblies(self):
    """Request Technical status of assemblies (C0:QT)."""

    # TODO: parse res
    return await self.send_command(module="C0", command="QT")

  async def request_installation_data(self):
    """Request installation data (C0:RI)."""

    # TODO: parse res
    return await self.send_command(module="C0", command="RI")

  async def request_device_serial_number(self) -> str:
    """Request device serial number (C0:RI)."""
    return (await self.send_command("C0", "RI", fmt="si####sn&&&&sn&&&&"))["sn"]  # type: ignore

  async def request_download_date(self):
    """Request download date (C0:RO)."""

    # TODO: parse res
    return await self.send_command(module="C0", command="RO")

  async def request_verification_data(self, verification_subject: int = 0):
    """Request download date (C0:RO).

    Args:
      verification_subject: verification subject. Must be between 0 and 24. Default 0.
    """

    assert 0 <= verification_subject <= 24, "verification_subject must be between 0 and 24"

    # TODO: parse results.
    return await self.send_command(module="C0", command="RO", vo=verification_subject)

  async def request_additional_timestamp_data(self):
    """Request additional timestamp data (C0:RS)."""

    # TODO: parse res
    return await self.send_command(module="C0", command="RS")

  async def request_pip_channel_validation_status(self):
    """Request PIP channel validation status (C0:RJ)."""

    # TODO: parse res
    return await self.send_command(module="C0", command="RJ")

  async def request_xl_channel_validation_status(self):
    """Request XL channel validation status (C0:UJ)."""

    # TODO: parse res
    return await self.send_command(module="C0", command="UJ")

  async def request_node_names(self):
    """Request node names (C0:RK)."""

    # TODO: parse res
    return await self.send_command(module="C0", command="RK")

  async def request_deck_data(self):
    """Request deck data (C0:VD)."""

    # TODO: parse res
    return await self.send_command(module="C0", command="VD")

  # -- area reservation and configuration --

  async def occupy_and_provide_area_for_external_access(
    self,
    taken_area_identification_number: int = 0,
    taken_area_left_margin: int = 0,
    taken_area_left_margin_direction: int = 0,
    taken_area_size: int = 0,
    arm_preposition_mode_related_to_taken_areas: int = 0,
  ):
    """Occupy and provide area for external access

    Args:
      taken_area_identification_number: taken area identification number. Must be between 0 and
        9999. Default 0.
      taken_area_left_margin: taken area left margin. Must be between 0 and 99. Default 0.
      taken_area_left_margin_direction: taken area left margin direction. 1 = negative. Must be
        between 0 and 1. Default 0.
      taken_area_size: taken area size. Must be between 0 and 50000. Default 0.
      arm_preposition_mode_related_to_taken_areas: 0) left arm to left & right arm to right.
        1) all arms left.  2) all arms right.
    """

    assert 0 <= taken_area_identification_number <= 9999, (
      "taken_area_identification_number must be between 0 and 9999"
    )
    assert 0 <= taken_area_left_margin <= 99, "taken_area_left_margin must be between 0 and 99"
    assert 0 <= taken_area_left_margin_direction <= 1, (
      "taken_area_left_margin_direction must be between 0 and 1"
    )
    assert 0 <= taken_area_size <= 50000, "taken_area_size must be between 0 and 50000"
    assert 0 <= arm_preposition_mode_related_to_taken_areas <= 2, (
      "arm_preposition_mode_related_to_taken_areas must be between 0 and 2"
    )

    return await self.send_command(
      module="C0",
      command="BA",
      aq=taken_area_identification_number,
      al=taken_area_left_margin,
      ad=taken_area_left_margin_direction,
      ar=taken_area_size,
      ap=arm_preposition_mode_related_to_taken_areas,
    )

  async def release_occupied_area(self, taken_area_identification_number: int = 0):
    """Release occupied area

    Args:
      taken_area_identification_number: taken area identification number.
                                        Must be between 0 and 99. Default 0.
    """

    assert 0 <= taken_area_identification_number <= 99, (
      "taken_area_identification_number must be between 0 and 99"
    )

    return await self.send_command(
      module="C0",
      command="BB",
      aq=taken_area_identification_number,
    )

  async def release_all_occupied_areas(self):
    """Release all occupied areas"""

    return await self.send_command(module="C0", command="BC")

  async def set_instrument_configuration(
    self,
    configuration_data_1: Optional[str] = None,  # TODO: configuration byte
    configuration_data_2: Optional[str] = None,  # TODO: configuration byte
    configuration_data_3: Optional[str] = None,  # TODO: configuration byte
    instrument_size_in_slots_x_range: int = 54,
    auto_load_size_in_slots: int = 54,
    tip_waste_x_position: int = 13400,
    right_x_drive_configuration_byte_1: int = 0,
    right_x_drive_configuration_byte_2: int = 0,
    minimal_iswap_collision_free_position: int = 3500,
    maximal_iswap_collision_free_position: int = 11400,
    left_x_arm_width: int = 3700,
    right_x_arm_width: int = 3700,
    num_pip_channels: int = 0,
    num_xl_channels: int = 0,
    num_robotic_channels: int = 0,
    minimal_raster_pitch_of_pip_channels: int = 90,
    minimal_raster_pitch_of_xl_channels: int = 360,
    minimal_raster_pitch_of_robotic_channels: int = 360,
    pip_maximal_y_position: int = 6065,
    left_arm_minimal_y_position: int = 60,
    right_arm_minimal_y_position: int = 60,
  ):
    """Set instrument configuration

    Args:
      configuration_data_1: configuration data 1.
      configuration_data_2: configuration data 2.
      configuration_data_3: configuration data 3.
      instrument_size_in_slots_x_range: instrument size in slots (X range).
                                          Must be between 10 and 99. Default 54.
      auto_load_size_in_slots: auto load size in slots. Must be between 10
                                and 54. Default 54.
      tip_waste_x_position: tip waste X-position. Must be between 1000 and
                            25000. Default 13400.
      right_x_drive_configuration_byte_1: right X drive configuration byte 1 (see
        xl parameter bits). Must be between 0 and 1.  Default 0. # TODO: this.
      right_x_drive_configuration_byte_2: right X drive configuration byte 2 (see
        xn parameter bits). Must be between 0 and 1.  Default 0. # TODO: this.
      minimal_iswap_collision_free_position: minimal iSWAP collision free position for
        direct X access. For explanation of calculation see Fig. 4. Must be between 0 and 30000.
        Default 3500.
      maximal_iswap_collision_free_position: maximal iSWAP collision free position for
        direct X access. For explanation of calculation see Fig. 4. Must be between 0 and 30000.
        Default 11400
      left_x_arm_width: width of left X arm [0.1 mm]. Must be between 0 and 9999. Default 3700.
      right_x_arm_width: width of right X arm [0.1 mm]. Must be between 0 and 9999. Default 3700.
      num_pip_channels: number of PIP channels. Must be between 0 and 16. Default 0.
      num_xl_channels: number of XL channels. Must be between 0 and 8. Default 0.
      num_robotic_channels: number of Robotic channels. Must be between 0 and 8. Default 0.
      minimal_raster_pitch_of_pip_channels: minimal raster pitch of PIP channels [0.1 mm]. Must
                                            be between 0 and 999. Default 90.
      minimal_raster_pitch_of_xl_channels: minimal raster pitch of XL channels [0.1 mm]. Must be
                                            between 0 and 999. Default 360.
      minimal_raster_pitch_of_robotic_channels: minimal raster pitch of Robotic channels [0.1 mm].
                                                Must be between 0 and 999. Default 360.
      pip_maximal_y_position: PIP maximal Y position [0.1 mm]. Must be between 0 and 9999.
                              Default 6065.
      left_arm_minimal_y_position: left arm minimal Y position [0.1 mm]. Must be between 0 and 9999.
                                    Default 60.
      right_arm_minimal_y_position: right arm minimal Y position [0.1 mm]. Must be between 0
                                    and 9999. Default 60.
    """

    assert 10 <= instrument_size_in_slots_x_range <= 99, (
      "instrument_size_in_slots_x_range must be between 10 and 99"
    )
    assert 1 <= auto_load_size_in_slots <= 54, "auto_load_size_in_slots must be between 1 and 54"
    assert 1000 <= tip_waste_x_position <= 25000, "tip_waste_x_position must be between 1 and 25000"
    assert 0 <= right_x_drive_configuration_byte_1 <= 1, (
      "right_x_drive_configuration_byte_1 must be between 0 and 1"
    )
    assert 0 <= right_x_drive_configuration_byte_2 <= 1, (
      "right_x_drive_configuration_byte_2 must be between 0 and  must1"
    )
    assert 0 <= minimal_iswap_collision_free_position <= 30000, (
      "minimal_iswap_collision_free_position must be between 0 and 30000"
    )
    assert 0 <= maximal_iswap_collision_free_position <= 30000, (
      "maximal_iswap_collision_free_position must be between 0 and 30000"
    )
    assert 0 <= left_x_arm_width <= 9999, "left_x_arm_width must be between 0 and 9999"
    assert 0 <= right_x_arm_width <= 9999, "right_x_arm_width must be between 0 and 9999"
    assert 0 <= num_pip_channels <= 16, "num_pip_channels must be between 0 and 16"
    assert 0 <= num_xl_channels <= 8, "num_xl_channels must be between 0 and 8"
    assert 0 <= num_robotic_channels <= 8, "num_robotic_channels must be between 0 and 8"
    assert 0 <= minimal_raster_pitch_of_pip_channels <= 999, (
      "minimal_raster_pitch_of_pip_channels must be between 0 and 999"
    )
    assert 0 <= minimal_raster_pitch_of_xl_channels <= 999, (
      "minimal_raster_pitch_of_xl_channels must be between 0 and 999"
    )
    assert 0 <= minimal_raster_pitch_of_robotic_channels <= 999, (
      "minimal_raster_pitch_of_robotic_channels must be between 0 and 999"
    )
    assert 0 <= pip_maximal_y_position <= 9999, "pip_maximal_y_position must be between 0 and 9999"
    assert 0 <= left_arm_minimal_y_position <= 9999, (
      "left_arm_minimal_y_position must be between 0 and 9999"
    )
    assert 0 <= right_arm_minimal_y_position <= 9999, (
      "right_arm_minimal_y_position must be between 0 and 9999"
    )

    return await self.send_command(
      module="C0",
      command="AK",
      kb=configuration_data_1,
      ka=configuration_data_2,
      ke=configuration_data_3,
      xt=instrument_size_in_slots_x_range,
      xa=auto_load_size_in_slots,
      xw=tip_waste_x_position,
      xr=right_x_drive_configuration_byte_1,
      xo=right_x_drive_configuration_byte_2,
      xm=minimal_iswap_collision_free_position,
      xx=maximal_iswap_collision_free_position,
      xu=left_x_arm_width,
      xv=right_x_arm_width,
      kp=num_pip_channels,
      kc=num_xl_channels,
      kr=num_robotic_channels,
      ys=minimal_raster_pitch_of_pip_channels,
      kl=minimal_raster_pitch_of_xl_channels,
      km=minimal_raster_pitch_of_robotic_channels,
      ym=pip_maximal_y_position,
      yu=left_arm_minimal_y_position,
      yx=right_arm_minimal_y_position,
    )

  async def pre_initialize_instrument(self):
    """Pre-initialize instrument"""
    return await self.send_command(module="C0", command="VI", read_timeout=300)

  # -- PIP channel helpers ---------------------------------------------------

  y_drive_mm_per_increment = 0.046302082

  @staticmethod
  def channel_id(channel_idx: int) -> str:
    """Return the firmware module identifier for a PIP channel.

    Args:
      channel_idx: 0-indexed channel index (0 = backmost).

    Returns:
      Module string like ``"P1"`` ... ``"PG"``.
    """
    channel_ids = "123456789ABCDEFG"
    return "P" + channel_ids[channel_idx]

  @staticmethod
  def y_drive_increment_to_mm(value_increments: int) -> float:
    """Convert Y-axis hardware increments to mm."""
    return round(value_increments * STARDriver.y_drive_mm_per_increment, 2)

  async def channel_request_y_minimum_spacing(self, channel_idx: int) -> float:
    """Query the minimum Y spacing for a single channel.

    Args:
      channel_idx: 0-indexed channel index.

    Returns:
      The minimum Y spacing in mm.
    """
    if not 0 <= channel_idx <= self.num_channels - 1:
      raise ValueError(
        f"channel_idx must be between 0 and {self.num_channels - 1}, got {channel_idx}."
      )

    resp = await self.send_command(
      module=self.channel_id(channel_idx),
      command="VY",
      fmt="yc### (n)",
    )
    return self.y_drive_increment_to_mm(resp["yc"][1])

  async def channels_request_y_minimum_spacing(self) -> List[float]:
    """Query the minimum Y spacing for all channels in parallel.

    Returns:
      A list of minimum Y spacings in mm, one per channel.
    """
    return list(
      await asyncio.gather(
        *(
          self.channel_request_y_minimum_spacing(channel_idx=idx)
          for idx in range(self.num_channels)
        )
      )
    )

  def _min_spacing_between(self, i: int, j: int) -> float:
    """Return the conservative minimum Y spacing required between channels *i* and *j*.

    For adjacent channels, the constraint is the larger of the two channels' individual minimum
    spacings, ceiling'd to 1 decimal place for safe movement.

    For non-adjacent channels, the spacing is the sum of all intermediate adjacent-pair spacings.
    """
    if not self._channels_minimum_y_spacing:
      if self.extended_conf is not None:
        return abs(j - i) * self.extended_conf.min_raster_pitch_pip_channels
      return abs(j - i) * 9.0

    lo, hi = min(i, j), max(i, j)
    if hi - lo == 1:
      spacing = max(self._channels_minimum_y_spacing[lo], self._channels_minimum_y_spacing[hi])
      return math.ceil(spacing * 10) / 10
    return sum(self._min_spacing_between(k, k + 1) for k in range(lo, hi))
