"""Legacy ThermoFisher thermocycler backend -- thin delegation layer.

All real SCPI logic lives in :class:`ThermoFisherThermocyclerDriver`
(``pylabrobot.thermo_fisher.thermocycler``).  This module keeps the original public interface so
that :class:`ATCBackend`, :class:`ProflexBackend` and their tests continue to work unchanged.
"""

from abc import ABCMeta
from typing import Dict, List, Optional

from pylabrobot.legacy.thermocycling.backend import ThermocyclerBackend
from pylabrobot.legacy.thermocycling.standard import (
  LidStatus,
  Protocol,
  Stage,
  Step,
  protocol_to_new,
)
from pylabrobot.thermo_fisher.thermocycler import (
  RunProgress,
  ThermoFisherThermocyclerDriver,
  _gen_protocol_data,
  _generate_run_info_files,
)

# Re-export so ``from … import _generate_run_info_files`` keeps working.
__all__ = ["ThermoFisherThermocyclerBackend", "_generate_run_info_files", "_gen_protocol_data"]


class ThermoFisherThermocyclerBackend(ThermocyclerBackend, metaclass=ABCMeta):
  """Legacy backend for ThermoFisher thermocyclers (ProFlex / ATC).

  Delegates all real work to :class:`ThermoFisherThermocyclerDriver`.
  """

  RunProgress = RunProgress  # keep nested reference for backward compat

  def __init__(
    self,
    ip: str,
    use_ssl: bool = False,
    serial_number: Optional[str] = None,
    port: Optional[int] = None,
  ):
    if port is not None:
      raise NotImplementedError("Specifying a port is deprecated. Use use_ssl instead.")
    self._driver = ThermoFisherThermocyclerDriver(
      ip=ip, use_ssl=use_ssl, serial_number=serial_number
    )

  # -- forwarding properties (tests/subclasses access these directly) ----------

  @property
  def io(self):
    return self._driver.io

  @io.setter
  def io(self, value):
    self._driver.io = value

  @property
  def num_temp_zones(self) -> int:
    return self._driver.num_temp_zones

  @num_temp_zones.setter
  def num_temp_zones(self, value: int):
    self._driver.num_temp_zones = value

  @property
  def use_ssl(self) -> bool:
    return self._driver.use_ssl

  @property
  def device_shared_secret(self) -> bytes:
    return self._driver.device_shared_secret

  @property
  def port(self) -> int:
    return self._driver.port

  @property
  def bid(self) -> str:
    return self._driver.bid

  @bid.setter
  def bid(self, value: str):
    self._driver.bid = value

  @property
  def available_blocks(self) -> List[int]:
    return self._driver.available_blocks

  @available_blocks.setter
  def available_blocks(self, value: List[int]):
    self._driver.available_blocks = value

  @property
  def current_runs(self) -> Dict[int, str]:
    return self._driver.current_runs

  @current_runs.setter
  def current_runs(self, value: Dict[int, str]):
    self._driver.current_runs = value

  @property
  def _num_blocks(self) -> Optional[int]:
    return self._driver._num_blocks

  @_num_blocks.setter
  def _num_blocks(self, value: Optional[int]):
    self._driver._num_blocks = value

  @property
  def num_blocks(self) -> int:
    return self._driver.num_blocks

  # -- methods needed by subclasses (ATCBackend / ProflexBackend) --------------

  def _parse_scpi_response(self, response: str):
    return self._driver._parse_scpi_response(response)

  async def send_command(self, data, response_timeout=1, read_once=True):
    return await self._driver.send_command(
      data, response_timeout=response_timeout, read_once=read_once
    )

  # -- ThermocyclerBackend interface -------------------------------------------

  async def setup(
    self, block_idle_temp=25, cover_idle_temp=105, blocks_to_setup: Optional[List[int]] = None
  ):
    await self._driver.setup(
      block_idle_temp=block_idle_temp,
      cover_idle_temp=cover_idle_temp,
      blocks_to_setup=blocks_to_setup,
    )

  async def stop(self):
    await self._driver.stop()

  async def set_block_temperature(
    self, temperature: List[float], block_id: Optional[int] = None, rate: float = 100
  ):
    assert block_id is not None, "block_id must be specified"
    await self._driver.set_block_temperature(temperature=temperature, block_id=block_id, rate=rate)

  async def set_lid_temperature(self, temperature: List[float], block_id: Optional[int] = None):
    assert block_id is not None, "block_id must be specified"
    await self._driver.set_lid_temperature(temperature=temperature, block_id=block_id)

  async def deactivate_lid(self, block_id: Optional[int] = None):
    assert block_id is not None, "block_id must be specified"
    await self._driver.deactivate_lid(block_id=block_id)

  async def deactivate_block(self, block_id: Optional[int] = None):
    assert block_id is not None, "block_id must be specified"
    await self._driver.deactivate_block(block_id=block_id)

  async def get_block_current_temperature(self, block_id=1) -> List[float]:
    return await self._driver.get_block_current_temperature(block_id=block_id)

  async def get_lid_current_temperature(self, block_id: Optional[int] = None) -> List[float]:
    assert block_id is not None, "block_id must be specified"
    return await self._driver.get_lid_current_temperature(block_id=block_id)

  async def run_protocol(
    self,
    protocol: Protocol,
    block_max_volume: float,
    block_id: Optional[int] = None,
    run_name="testrun",
    user="Admin",
    run_mode: str = "Fast",
    cover_temp: float = 105,
    cover_enabled=True,
    protocol_name: str = "PCR_Protocol",
    stage_name_prefixes: Optional[List[str]] = None,
  ):
    assert block_id is not None, "block_id must be specified"
    # Wrap bare Steps in Stages (matching legacy behavior)
    for i, stage in enumerate(protocol.stages):
      if isinstance(stage, Step):
        protocol.stages[i] = Stage(steps=[stage], repeats=1)
    new_protocol = protocol_to_new(protocol)
    await self._driver.run_protocol(
      protocol=new_protocol,
      block_max_volume=block_max_volume,
      block_id=block_id,
      run_name=run_name,
      user=user,
      run_mode=run_mode,
      cover_temp=cover_temp,
      cover_enabled=cover_enabled,
      protocol_name=protocol_name,
      stage_name_prefixes=stage_name_prefixes,
    )

  async def get_run_info(self, protocol: Protocol, block_id: int) -> RunProgress:
    return await self._driver.get_run_info(protocol=protocol_to_new(protocol), block_id=block_id)

  async def abort_run(self, block_id: int):
    await self._driver.abort_run(block_id=block_id)

  async def continue_run(self, block_id: int):
    await self._driver.continue_run(block_id=block_id)

  # -- convenience delegations -------------------------------------------------

  async def get_sample_temps(self, block_id=1) -> List[float]:
    return await self._driver.get_sample_temps(block_id=block_id)

  async def get_nickname(self) -> str:
    return await self._driver.get_nickname()

  async def set_nickname(self, nickname: str) -> None:
    await self._driver.set_nickname(nickname)

  async def get_log_by_runname(self, run_name: str) -> str:
    return await self._driver.get_log_by_runname(run_name)

  async def get_elapsed_run_time_from_log(self, run_name: str) -> int:
    return await self._driver.get_elapsed_run_time_from_log(run_name)

  async def set_block_idle_temp(
    self, temp: float, block_id: int, control_enabled: bool = True
  ) -> None:
    await self._driver.set_block_idle_temp(
      temp=temp, block_id=block_id, control_enabled=control_enabled
    )

  async def set_cover_idle_temp(
    self, temp: float, block_id: int, control_enabled: bool = True
  ) -> None:
    await self._driver.set_cover_idle_temp(
      temp=temp, block_id=block_id, control_enabled=control_enabled
    )

  async def block_ramp_single_temp(self, target_temp: float, block_id: int, rate: float = 100):
    await self._driver.block_ramp_single_temp(target_temp=target_temp, block_id=block_id, rate=rate)

  async def buzzer_on(self):
    await self._driver.buzzer_on()

  async def buzzer_off(self):
    await self._driver.buzzer_off()

  async def send_morse_code(self, morse_code: str):
    await self._driver.send_morse_code(morse_code)

  async def power_on(self):
    await self._driver.power_on()

  async def power_off(self):
    await self._driver.power_off()

  async def check_run_exists(self, run_name: str) -> bool:
    return await self._driver.check_run_exists(run_name)

  async def create_run(self, run_name: str):
    return await self._driver.create_run(run_name)

  async def get_run_name(self, block_id: int) -> str:
    return await self._driver.get_run_name(block_id=block_id)

  async def get_estimated_run_time(self, block_id: int):
    return await self._driver.get_estimated_run_time(block_id=block_id)

  async def get_elapsed_run_time(self, block_id: int):
    return await self._driver.get_elapsed_run_time(block_id=block_id)

  async def get_remaining_run_time(self, block_id: int):
    return await self._driver.get_remaining_run_time(block_id=block_id)

  async def get_error(self, block_id):
    return await self._driver.get_error(block_id=block_id)

  async def get_current_cycle_index(self, block_id: Optional[int] = None) -> int:
    assert block_id is not None, "block_id must be specified"
    return await self._driver.get_current_cycle_index(block_id=block_id)

  async def get_current_step_index(self, block_id: Optional[int] = None) -> int:
    assert block_id is not None, "block_id must be specified"
    return await self._driver.get_current_step_index(block_id=block_id)

  # -- stubs for abstract methods not implemented on this hardware -------------

  async def get_block_status(self, *args, **kwargs):
    raise NotImplementedError

  async def get_hold_time(self, *args, **kwargs):
    raise NotImplementedError

  async def get_lid_open(self, *args, **kwargs):
    raise NotImplementedError("Proflex thermocycler does not support lid open status check")

  async def get_lid_status(self, *args, **kwargs) -> LidStatus:
    raise NotImplementedError

  async def get_lid_target_temperature(self, *args, **kwargs):
    raise NotImplementedError

  async def get_total_cycle_count(self, *args, **kwargs):
    raise NotImplementedError

  async def get_total_step_count(self, *args, **kwargs):
    raise NotImplementedError

  async def get_block_target_temperature(self, *args, **kwargs):
    raise NotImplementedError
