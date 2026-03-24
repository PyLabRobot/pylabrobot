import asyncio
import logging
from typing import List, Optional, cast

from pylabrobot.capabilities.thermocycling import (
  Protocol,
  Stage,
  Step,
  ThermocyclingBackend,
)

from .driver import ThermoFisherThermocyclerDriver
from .utils import RunProgress, _gen_protocol_data, _generate_run_info_files

logger = logging.getLogger(__name__)


class ThermoFisherThermocyclingBackend(ThermocyclingBackend):
  """Thermocycling backend for a single block, delegating to the shared driver."""

  def __init__(
    self,
    driver: ThermoFisherThermocyclerDriver,
    block_id: int,
    supports_lid_control: bool = False,
  ):
    super().__init__()
    self._driver = driver
    self._block_id = block_id
    self._supports_lid_control = supports_lid_control

  async def setup(self):
    pass  # driver handles setup

  async def stop(self):
    pass  # driver handles stop

  # ----- Lid control -----

  async def open_lid(self) -> None:
    if not self._supports_lid_control:
      raise NotImplementedError("Lid control is not supported on this thermocycler model")
    res = await self._driver.send_command({"cmd": "lidopen"}, response_timeout=25, read_once=True)
    if self._driver._parse_scpi_response(res)["status"] != "OK":
      raise ValueError("Failed to open lid")

  async def close_lid(self) -> None:
    if not self._supports_lid_control:
      raise NotImplementedError("Lid control is not supported on this thermocycler model")
    res = await self._driver.send_command({"cmd": "lidclose"}, response_timeout=20, read_once=True)
    if self._driver._parse_scpi_response(res)["status"] != "OK":
      raise ValueError("Failed to close lid")

  async def get_lid_open(self) -> bool:
    raise NotImplementedError(
      "ThermoFisher thermocycler hardware does not support lid open status check"
    )

  # ----- Protocol execution -----

  async def run_protocol(self, protocol: Protocol, block_max_volume: float) -> None:
    await self._run_protocol_with_options(protocol=protocol, block_max_volume=block_max_volume)

  async def _run_protocol_with_options(
    self,
    protocol: Protocol,
    block_max_volume: float,
    run_name: str = "testrun",
    user: str = "Admin",
    run_mode: str = "Fast",
    cover_temp: float = 105,
    cover_enabled: bool = True,
    protocol_name: str = "PCR_Protocol",
    stage_name_prefixes: Optional[List[str]] = None,
  ) -> None:
    block_id = self._block_id

    if await self.check_run_exists(run_name):
      logger.warning(f"Run {run_name} already exists")
    else:
      await self.create_run(run_name)

    # wrap all Steps in Stage objects where necessary
    for i, stage in enumerate(protocol.stages):
      if isinstance(stage, Step):
        protocol.stages[i] = Stage(steps=[stage], repeats=1)

    for stage in protocol.stages:
      for step in stage.steps:
        if len(step.temperature) != self._driver.num_temp_zones:
          raise ValueError(
            f"Each step in the protocol must have a list of temperatures "
            f"of length {self._driver.num_temp_zones}. "
            f"Step temperatures: {step.temperature} (length {len(step.temperature)})"
          )

    stage_name_prefixes = stage_name_prefixes or ["Stage_" for _ in range(len(protocol.stages))]

    await self._scpi_write_run_info(
      protocol=protocol,
      block_id=block_id,
      run_name=run_name,
      user_name=user,
      sample_volume=block_max_volume,
      run_mode=run_mode,
      cover_temp=cover_temp,
      cover_enabled=cover_enabled,
      protocol_name=protocol_name,
    )
    await self._scpi_run_protocol(
      protocol=protocol,
      run_name=run_name,
      block_id=block_id,
      sample_volume=block_max_volume,
      run_mode=run_mode,
      cover_temp=cover_temp,
      cover_enabled=cover_enabled,
      protocol_name=protocol_name,
      user_name=user,
      stage_name_prefixes=stage_name_prefixes,
    )

  async def _scpi_write_run_info(
    self,
    protocol: Protocol,
    run_name: str,
    block_id: int,
    sample_volume: float,
    run_mode: str,
    protocol_name: str,
    cover_temp: float,
    cover_enabled: bool,
    user_name: str,
  ):
    xmlfile, tmpfile = _generate_run_info_files(
      protocol=protocol,
      block_id=block_id,
      sample_volume=sample_volume,
      run_mode=run_mode,
      protocol_name=protocol_name,
      cover_temp=cover_temp,
      cover_enabled=cover_enabled,
      user_name="LifeTechnologies",
    )
    await self._driver._write_file(f"runs:{run_name}/{protocol_name}.method", xmlfile)
    await self._driver._write_file(f"runs:{run_name}/{run_name}.tmp", tmpfile)

  async def _scpi_run_protocol(
    self,
    protocol: Protocol,
    run_name: str,
    block_id: int,
    sample_volume: float,
    run_mode: str,
    protocol_name: str,
    cover_temp: float,
    cover_enabled: bool,
    user_name: str,
    stage_name_prefixes: List[str],
  ):
    load_res = await self._driver.send_command(
      data=_gen_protocol_data(
        protocol=protocol,
        block_id=block_id,
        sample_volume=sample_volume,
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
      logger.error(load_res)
      logger.error("Protocol failed to load")
      raise ValueError("Protocol failed to load")

    start_res = await self._driver.send_command(
      {
        "cmd": f"TBC{block_id + 1}:RunProtocol",
        "params": {
          "User": user_name,
          "CoverTemperature": cover_temp,
          "CoverEnabled": "On" if cover_enabled else "Off",
        },
        "args": [protocol_name, run_name],
      },
      response_timeout=2,
      read_once=False,
    )

    if self._driver._parse_scpi_response(start_res)["status"] == "NEXT":
      logger.info("Protocol started")
    else:
      logger.error(start_res)
      logger.error("Protocol failed to start")
      raise ValueError("Protocol failed to start")

    total_time = await self.get_estimated_run_time()
    total_time = float(total_time)
    logger.info(f"Estimated run time: {total_time}")
    self._driver.current_runs[block_id] = run_name

  # ----- Abort / continue -----

  async def abort_run(self):
    block_id = self._block_id
    if not await self.is_block_running():
      logger.info("Failed to abort protocol: no run is currently running on this block")
      return
    run_name = await self.get_run_name()
    abort_res = await self._driver.send_command(
      {"cmd": f"TBC{block_id + 1}:AbortRun", "args": [run_name]}
    )
    if self._driver._parse_scpi_response(abort_res)["status"] != "OK":
      logger.error(abort_res)
      logger.error("Failed to abort protocol")
      raise ValueError("Failed to abort protocol")
    logger.info("Protocol aborted")
    await asyncio.sleep(10)

  async def continue_run(self):
    block_id = self._block_id
    for _ in range(3):
      await asyncio.sleep(1)
      res = await self._driver.send_command({"cmd": f"TBC{block_id + 1}:CONTinue"})
      if self._driver._parse_scpi_response(res)["status"] != "OK":
        raise ValueError("Failed to continue from indefinite hold")

  # ----- Block running status -----

  async def is_block_running(self) -> bool:
    run_name = await self.get_run_name()
    return run_name != "-"

  async def get_run_name(self) -> str:
    block_id = self._block_id
    res = await self._driver.send_command({"cmd": f"TBC{block_id + 1}:RUNTitle?"})
    if self._driver._parse_scpi_response(res)["status"] != "OK":
      raise ValueError("Failed to get run title")
    return cast(str, self._driver._parse_scpi_response(res)["args"][0])

  async def get_error(self) -> str:
    block_id = self._block_id
    res = await self._driver.send_command({"cmd": f"TBC{block_id + 1}:ERROR?"})
    if self._driver._parse_scpi_response(res)["status"] != "OK":
      raise ValueError("Failed to get error")
    return self._driver._parse_scpi_response(res)["args"][0]

  # ----- Run progress -----

  async def get_current_cycle_index(self) -> int:
    progress = await self._get_run_progress()
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

  async def get_current_step_index(self) -> int:
    progress = await self._get_run_progress()
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

  async def get_hold_time(self) -> float:
    raise NotImplementedError(
      "get_hold_time is not supported by ThermoFisher thermocycler hardware"
    )

  async def get_total_cycle_count(self) -> int:
    raise NotImplementedError(
      "get_total_cycle_count is not supported by ThermoFisher thermocycler hardware"
    )

  async def get_total_step_count(self) -> int:
    raise NotImplementedError(
      "get_total_step_count is not supported by ThermoFisher thermocycler hardware"
    )

  # ----- Run info / progress -----

  async def _get_run_progress(self):
    block_id = self._block_id
    res = await self._driver.send_command({"cmd": f"TBC{block_id + 1}:RUNProgress?"})
    parsed_res = self._driver._parse_scpi_response(res)
    if parsed_res["status"] != "OK":
      raise ValueError("Failed to get run status")
    if parsed_res["cmd"] == f"TBC{block_id + 1}:RunProtocol":
      await self._driver._read_response()
      return False
    return self._driver._parse_scpi_response(res)["params"]

  async def get_run_info(self, protocol: Protocol) -> RunProgress:
    block_id = self._block_id
    progress = await self._get_run_progress()
    run_name = await self.get_run_name()
    if not progress:
      logger.info("Protocol completed")
      return RunProgress(
        running=False,
        stage="completed",
        elapsed_time=await self.get_elapsed_run_time_from_log(run_name=run_name),
        remaining_time=0,
      )

    if progress["RunTitle"] == "-":
      await self._driver._read_response(timeout=5)
      logger.info("Protocol completed")
      return RunProgress(
        running=False,
        stage="completed",
        elapsed_time=await self.get_elapsed_run_time_from_log(run_name=run_name),
        remaining_time=0,
      )

    if progress["Stage"] == "POSTRun":
      logger.info("Protocol in POSTRun")
      return RunProgress(
        running=True,
        stage="POSTRun",
        elapsed_time=await self.get_elapsed_run_time_from_log(run_name=run_name),
        remaining_time=0,
      )

    time_elapsed = await self.get_elapsed_run_time()
    remaining_time = await self.get_remaining_run_time()

    if progress["Stage"] != "-" and progress["Step"] != "-":
      current_step = protocol.stages[int(progress["Stage"]) - 1].steps[int(progress["Step"]) - 1]
      if current_step.hold_seconds == float("inf"):
        while True:
          block_temps_res = await self._driver.send_command(
            {"cmd": f"TBC{block_id + 1}:TBC:BlockTemperatures?"}
          )
          block_temps = self._driver._parse_scpi_response(block_temps_res)["args"]
          target_temps = current_step.temperature
          if all(
            abs(float(block_temps[i]) - target_temps[i]) < 0.5 for i in range(len(block_temps))
          ):
            break
          await asyncio.sleep(5)
        logger.info("Infinite hold")
        return RunProgress(
          running=False,
          stage="infinite_hold",
          elapsed_time=time_elapsed,
          remaining_time=remaining_time,
        )

    logger.info(f"Elapsed time: {time_elapsed}")
    logger.info(f"Remaining time: {remaining_time}")
    return RunProgress(
      running=True,
      stage=progress["Stage"],
      elapsed_time=time_elapsed,
      remaining_time=remaining_time,
    )

  async def get_estimated_run_time(self):
    block_id = self._block_id
    res = await self._driver.send_command({"cmd": f"TBC{block_id + 1}:ESTimatedTime?"})
    if self._driver._parse_scpi_response(res)["status"] != "OK":
      raise ValueError("Failed to get estimated run time")
    return self._driver._parse_scpi_response(res)["args"][0]

  async def get_elapsed_run_time(self):
    block_id = self._block_id
    res = await self._driver.send_command({"cmd": f"TBC{block_id + 1}:ELAPsedTime?"})
    if self._driver._parse_scpi_response(res)["status"] != "OK":
      raise ValueError("Failed to get elapsed run time")
    return int(self._driver._parse_scpi_response(res)["args"][0])

  async def get_remaining_run_time(self):
    block_id = self._block_id
    res = await self._driver.send_command({"cmd": f"TBC{block_id + 1}:REMainingTime?"})
    if self._driver._parse_scpi_response(res)["status"] != "OK":
      raise ValueError("Failed to get remaining run time")
    return int(self._driver._parse_scpi_response(res)["args"][0])

  # ----- Log / run file management -----

  async def get_log_by_runname(self, run_name: str) -> str:
    return await self._driver.get_log_by_runname(run_name)

  async def get_elapsed_run_time_from_log(self, run_name: str) -> int:
    return await self._driver.get_elapsed_run_time_from_log(run_name)

  async def check_run_exists(self, run_name: str) -> bool:
    return await self._driver.check_run_exists(run_name)

  async def create_run(self, run_name: str):
    return await self._driver.create_run(run_name)
