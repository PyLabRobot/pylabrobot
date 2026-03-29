"""STARChatterboxDriver: prints commands instead of sending them over USB."""

from typing import List, Optional

from .driver import (
  DriveConfiguration,
  ExtendedConfiguration,
  MachineConfiguration,
  STARDriver,
)

_DEFAULT_MACHINE_CONF = MachineConfiguration(
  pip_type_1000ul=True,
  kb_iswap_installed=True,
  auto_load_installed=True,
  num_pip_channels=8,
)

_DEFAULT_EXTENDED_CONF = ExtendedConfiguration(
  left_x_drive_large=True,
  iswap_gripper_wide=True,
  instrument_size_slots=30,
  auto_load_size_slots=30,
  tip_waste_x_position=800.0,
  left_x_drive=DriveConfiguration(iswap_installed=True, core_96_head_installed=True),
  min_iswap_collision_free_position=350.0,
  max_iswap_collision_free_position=600.0,
)


class STARChatterboxDriver(STARDriver):
  """Chatterbox driver for STAR. Prints firmware commands instead of sending them over USB."""

  def __init__(
    self,
    num_channels: int = 8,
    machine_configuration: MachineConfiguration = _DEFAULT_MACHINE_CONF,
    extended_configuration: ExtendedConfiguration = _DEFAULT_EXTENDED_CONF,
  ):
    super().__init__()
    self._num_channels = num_channels
    self._machine_configuration = machine_configuration
    self._extended_configuration = extended_configuration

  @property
  def num_channels(self) -> int:
    return self._num_channels

  # -- lifecycle: skip USB, use canned config --------------------------------

  async def setup(self):
    # No USB — just set config and create backends.
    self.id_ = 0
    self.machine_conf = self._machine_configuration
    self.extended_conf = self._extended_configuration

    from .pip_backend import STARPIPBackend

    self.pip = STARPIPBackend(self)

    if self.extended_conf.left_x_drive.core_96_head_installed:
      from .head96_backend import STARHead96Backend

      self.head96 = STARHead96Backend(self)
    else:
      self.head96 = None

    if self.extended_conf.left_x_drive.iswap_installed:
      from .iswap import iSWAP

      self.iswap = iSWAP(driver=self)
      self.iswap._version = "chatterbox"
      self.iswap._parked = True
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

    from .cover import STARCover

    self.cover = STARCover(driver=self)

    if (self.machine_conf.wash_station_1_installed or
        self.machine_conf.wash_station_2_installed):
      from .wash_station import STARWashStation

      self.wash_station = STARWashStation(driver=self)
    else:
      self.wash_station = None

  async def stop(self):
    self.machine_conf = None
    self.extended_conf = None
    self.head96 = None
    self.iswap = None
    self.autoload = None
    self.left_x_arm = None
    self.right_x_arm = None
    self.cover = None
    self.wash_station = None

  # -- I/O: print instead of USB --------------------------------------------

  async def send_command(self, module, command, auto_id=True, tip_pattern=None,
                         write_timeout=None, read_timeout=None, wait=True,
                         fmt=None, **kwargs):
    cmd, _ = self._assemble_command(
      module=module, command=command, auto_id=auto_id,
      tip_pattern=tip_pattern, **kwargs,
    )
    print(cmd)
    return None

  async def send_raw_command(self, command, write_timeout=None, read_timeout=None,
                             wait=True):
    print(command)
    return None
