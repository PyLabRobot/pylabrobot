"""Legacy ThermoFisher thermocycler backend -- thin delegation layer.

All real SCPI logic lives in :class:`ThermoFisherThermocyclerDriver`
(``pylabrobot.thermo_fisher.thermocycler``).  This module keeps the original public interface so
that :class:`ATCBackend`, :class:`ProflexBackend` and their tests continue to work unchanged.
"""

import asyncio
import re
from abc import ABCMeta
from base64 import b64decode
from typing import Dict, List, Optional, cast

from pylabrobot.legacy.thermocycling.backend import ThermocyclerBackend
from pylabrobot.legacy.thermocycling.standard import (
  LidStatus,
  Protocol,
  Stage,
  Step,
  protocol_to_new,
)
from pylabrobot.thermo_fisher.thermocyclers import (
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
    if block_id not in self._driver.available_blocks:
      raise ValueError(f"Block {block_id} not available")
    res = await self._driver.send_command(
      {"cmd": f"TBC{block_id + 1}:RAMP", "params": {"rate": rate}, "args": temperature},
      response_timeout=60,
    )
    if self._driver._parse_scpi_response(res)["status"] != "OK":
      raise ValueError("Failed to ramp block temperature")

  async def set_lid_temperature(self, temperature: List[float], block_id: Optional[int] = None):
    assert block_id is not None, "block_id must be specified"
    assert len(set(temperature)) == 1, "Lid temperature must be the same for all zones"
    target_temp = temperature[0]
    if block_id not in self._driver.available_blocks:
      raise ValueError(f"Block {block_id} not available")
    res = await self._driver.send_command(
      {"cmd": f"TBC{block_id + 1}:CoverRAMP", "params": {}, "args": [target_temp]},
      response_timeout=60,
    )
    if self._driver._parse_scpi_response(res)["status"] != "OK":
      raise ValueError("Failed to ramp cover temperature")

  async def deactivate_lid(self, block_id: Optional[int] = None):
    assert block_id is not None, "block_id must be specified"
    await self._driver.deactivate_lid(block_id=block_id)

  async def deactivate_block(self, block_id: Optional[int] = None):
    assert block_id is not None, "block_id must be specified"
    await self._driver.deactivate_block(block_id=block_id)

  async def get_block_current_temperature(self, block_id=1) -> List[float]:
    res = await self._driver.send_command({"cmd": f"TBC{block_id + 1}:TBC:BlockTemperatures?"})
    return cast(List[float], self._driver._parse_scpi_response(res)["args"])

  async def get_lid_current_temperature(self, block_id: Optional[int] = None) -> List[float]:
    assert block_id is not None, "block_id must be specified"
    res = await self._driver.send_command({"cmd": f"TBC{block_id + 1}:TBC:CoverTemperatures?"})
    return cast(List[float], self._driver._parse_scpi_response(res)["args"])

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
    await self._run_protocol_impl(
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

  async def _run_protocol_impl(
    self,
    protocol,
    block_max_volume: float,
    block_id: int,
    run_name: str = "testrun",
    user: str = "Admin",
    run_mode: str = "Fast",
    cover_temp: float = 105,
    cover_enabled: bool = True,
    protocol_name: str = "PCR_Protocol",
    stage_name_prefixes: Optional[List[str]] = None,
  ):
    from pylabrobot.capabilities.thermocycling import Stage as NewStage, Step as NewStep

    if await self.check_run_exists(run_name):
      pass  # run already exists
    else:
      await self.create_run(run_name)

    # wrap all Steps in Stage objects where necessary
    for i, stage in enumerate(protocol.stages):
      if isinstance(stage, NewStep):
        protocol.stages[i] = NewStage(steps=[stage], repeats=1)

    for stage in protocol.stages:
      for step in stage.steps:
        if len(step.temperature) != self._driver.num_temp_zones:
          raise ValueError(
            f"Each step in the protocol must have a list of temperatures "
            f"of length {self._driver.num_temp_zones}. "
            f"Step temperatures: {step.temperature} (length {len(step.temperature)})"
          )

    stage_name_prefixes = stage_name_prefixes or ["Stage_" for _ in range(len(protocol.stages))]

    # write run info files
    xmlfile, tmpfile = _generate_run_info_files(
      protocol=protocol,
      block_id=block_id,
      sample_volume=block_max_volume,
      run_mode=run_mode,
      protocol_name=protocol_name,
      cover_temp=cover_temp,
      cover_enabled=cover_enabled,
      user_name="LifeTechnologies",
    )
    await self._driver._write_file(f"runs:{run_name}/{protocol_name}.method", xmlfile)
    await self._driver._write_file(f"runs:{run_name}/{run_name}.tmp", tmpfile)

    # load and run protocol via SCPI
    load_res = await self._driver.send_command(
      data=_gen_protocol_data(
        protocol=protocol,
        block_id=block_id,
        sample_volume=block_max_volume,
        run_mode=run_mode,
        cover_temp=cover_temp,
        cover_enabled=cover_enabled,
        protocol_name=protocol_name,
        stage_name_prefixes=stage_name_prefixes,
      ),
      response_timeout=5,
      read_once=False,
    )
    if self._driver._parse_scpi_response(load_res)["status"] != "OK":
      raise ValueError("Protocol failed to load")

    start_res = await self._driver.send_command(
      {
        "cmd": f"TBC{block_id + 1}:RunProtocol",
        "params": {
          "User": user,
          "CoverTemperature": cover_temp,
          "CoverEnabled": "On" if cover_enabled else "Off",
        },
        "args": [protocol_name, run_name],
      },
      response_timeout=2,
      read_once=False,
    )

    if self._driver._parse_scpi_response(start_res)["status"] != "NEXT":
      raise ValueError("Protocol failed to start")

    total_time = await self.get_estimated_run_time(block_id=block_id)
    total_time = float(total_time)
    self._driver.current_runs[block_id] = run_name

  async def get_run_info(self, protocol: Protocol, block_id: int) -> RunProgress:
    new_protocol = protocol_to_new(protocol)
    progress = await self._get_run_progress(block_id=block_id)
    run_name = await self.get_run_name(block_id=block_id)
    if not progress:
      return RunProgress(
        running=False,
        stage="completed",
        elapsed_time=await self.get_elapsed_run_time_from_log(run_name=run_name),
        remaining_time=0,
      )

    if progress["RunTitle"] == "-":
      await self._driver._read_response(timeout=5)
      return RunProgress(
        running=False,
        stage="completed",
        elapsed_time=await self.get_elapsed_run_time_from_log(run_name=run_name),
        remaining_time=0,
      )

    if progress["Stage"] == "POSTRun":
      return RunProgress(
        running=True,
        stage="POSTRun",
        elapsed_time=await self.get_elapsed_run_time_from_log(run_name=run_name),
        remaining_time=0,
      )

    time_elapsed = await self.get_elapsed_run_time(block_id=block_id)
    remaining_time = await self.get_remaining_run_time(block_id=block_id)

    if progress["Stage"] != "-" and progress["Step"] != "-":
      current_step = new_protocol.stages[int(progress["Stage"]) - 1].steps[
        int(progress["Step"]) - 1
      ]
      if current_step.hold_seconds == float("inf"):
        while True:
          block_temps = await self.get_block_current_temperature(block_id=block_id)
          target_temps = current_step.temperature
          if all(
            abs(float(block_temps[i]) - target_temps[i]) < 0.5 for i in range(len(block_temps))
          ):
            break
          await asyncio.sleep(5)
        return RunProgress(
          running=False,
          stage="infinite_hold",
          elapsed_time=time_elapsed,
          remaining_time=remaining_time,
        )

    return RunProgress(
      running=True,
      stage=progress["Stage"],
      elapsed_time=time_elapsed,
      remaining_time=remaining_time,
    )

  async def abort_run(self, block_id: int):
    await self._driver.abort_run(block_id=block_id)

  async def continue_run(self, block_id: int):
    for _ in range(3):
      await asyncio.sleep(1)
      res = await self._driver.send_command({"cmd": f"TBC{block_id + 1}:CONTinue"})
      if self._driver._parse_scpi_response(res)["status"] != "OK":
        raise ValueError("Failed to continue from indefinite hold")

  # -- convenience delegations -------------------------------------------------

  async def get_sample_temps(self, block_id=1) -> List[float]:
    res = await self._driver.send_command({"cmd": f"TBC{block_id + 1}:TBC:SampleTemperatures?"})
    return cast(List[float], self._driver._parse_scpi_response(res)["args"])

  async def get_nickname(self) -> str:
    return await self._driver.get_nickname()

  async def set_nickname(self, nickname: str) -> None:
    await self._driver.set_nickname(nickname)

  async def get_log_by_runname(self, run_name: str) -> str:
    res = await self._driver.send_command(
      {"cmd": "FILe:READ?", "args": [f"RUNS:{run_name}/{run_name}.log"]},
      response_timeout=5,
      read_once=False,
    )
    if self._driver._parse_scpi_response(res)["status"] != "OK":
      raise ValueError("Failed to get log")
    res.replace("\n", "")
    encoded_log_match = re.search(r"<quote>(.*?)</quote>", res, re.DOTALL)
    if not encoded_log_match:
      raise ValueError("Failed to parse log content")
    encoded_log = encoded_log_match.group(1).strip()
    log = b64decode(encoded_log).decode("utf-8")
    return log

  async def get_elapsed_run_time_from_log(self, run_name: str) -> int:
    """Parse a log to find the elapsed run time in hh:mm:ss format and convert to total seconds."""
    log = await self.get_log_by_runname(run_name)
    elapsed_time_match = re.search(r"Run Time:\s*(\d+):(\d+):(\d+)", log)
    if not elapsed_time_match:
      raise ValueError("Failed to parse elapsed time from log. Expected hh:mm:ss format.")
    hours = int(elapsed_time_match.group(1))
    minutes = int(elapsed_time_match.group(2))
    seconds = int(elapsed_time_match.group(3))
    total_seconds = (hours * 3600) + (minutes * 60) + seconds
    return total_seconds

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
    if block_id not in self._driver.available_blocks:
      raise ValueError(f"Block {block_id} not available")
    res = await self._driver.send_command(
      {"cmd": f"TBC{block_id + 1}:BlockRAMP", "params": {"rate": rate}, "args": [target_temp]},
      response_timeout=60,
    )
    if self._driver._parse_scpi_response(res)["status"] != "OK":
      raise ValueError("Failed to ramp block temperature")

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
    res = await self._driver.send_command(
      {"cmd": "RUNS:EXISTS?", "args": [run_name], "params": {"type": "folders"}}
    )
    if self._driver._parse_scpi_response(res)["status"] != "OK":
      raise ValueError("Failed to check if run exists")
    return cast(str, self._driver._parse_scpi_response(res)["args"][1]) == "True"

  async def create_run(self, run_name: str):
    res = await self._driver.send_command(
      {"cmd": "RUNS:NEW", "args": [run_name]}, response_timeout=10
    )
    if self._driver._parse_scpi_response(res)["status"] != "OK":
      raise ValueError("Failed to create run")
    return self._driver._parse_scpi_response(res)["args"][0]

  async def get_run_name(self, block_id: int) -> str:
    return await self._driver.get_run_name(block_id=block_id)

  async def get_estimated_run_time(self, block_id: int):
    res = await self._driver.send_command({"cmd": f"TBC{block_id + 1}:ESTimatedTime?"})
    if self._driver._parse_scpi_response(res)["status"] != "OK":
      raise ValueError("Failed to get estimated run time")
    return self._driver._parse_scpi_response(res)["args"][0]

  async def get_elapsed_run_time(self, block_id: int):
    res = await self._driver.send_command({"cmd": f"TBC{block_id + 1}:ELAPsedTime?"})
    if self._driver._parse_scpi_response(res)["status"] != "OK":
      raise ValueError("Failed to get elapsed run time")
    return int(self._driver._parse_scpi_response(res)["args"][0])

  async def get_remaining_run_time(self, block_id: int):
    res = await self._driver.send_command({"cmd": f"TBC{block_id + 1}:REMainingTime?"})
    if self._driver._parse_scpi_response(res)["status"] != "OK":
      raise ValueError("Failed to get remaining run time")
    return int(self._driver._parse_scpi_response(res)["args"][0])

  async def get_error(self, block_id):
    return await self._driver.get_error(block_id=block_id)

  async def get_current_cycle_index(self, block_id: Optional[int] = None) -> int:
    assert block_id is not None, "block_id must be specified"
    progress = await self._get_run_progress(block_id=block_id)
    if progress is None:
      raise RuntimeError("No progress information available")
    if progress["RunTitle"] == "-":
      await self._driver._read_response(timeout=5)
      raise RuntimeError("Protocol completed or not started")
    if progress["Stage"] == "POSTRun":
      raise RuntimeError("Protocol in POSTRun stage, no current cycle index")
    if progress["Stage"] != "-" and progress["Step"] != "-":
      return int(progress["Stage"]) - 1
    raise RuntimeError("Current cycle index is not available, protocol may not be running")

  async def get_current_step_index(self, block_id: Optional[int] = None) -> int:
    assert block_id is not None, "block_id must be specified"
    progress = await self._get_run_progress(block_id=block_id)
    if progress is None:
      raise RuntimeError("No progress information available")
    if progress["RunTitle"] == "-":
      await self._driver._read_response(timeout=5)
      raise RuntimeError("Protocol completed or not started")
    if progress["Stage"] == "POSTRun":
      raise RuntimeError("Protocol in POSTRun stage, no current cycle index")
    if progress["Stage"] != "-" and progress["Step"] != "-":
      return int(progress["Step"]) - 1
    raise RuntimeError("Current step index is not available, protocol may not be running")

  async def _get_run_progress(self, block_id: int):
    res = await self._driver.send_command({"cmd": f"TBC{block_id + 1}:RUNProgress?"})
    parsed_res = self._driver._parse_scpi_response(res)
    if parsed_res["status"] != "OK":
      raise ValueError("Failed to get run status")
    if parsed_res["cmd"] == f"TBC{block_id + 1}:RunProtocol":
      await self._driver._read_response()
      return False
    return self._driver._parse_scpi_response(res)["params"]

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
