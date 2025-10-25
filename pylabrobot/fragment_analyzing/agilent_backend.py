from abc import ABCMeta
from dataclasses import dataclass
from typing import Dict, List, Tuple, Type

from pylabrobot.fragment_analyzing.backend import FragmentAnalyzerBackend
from pylabrobot.io import Socket


class FragmentAnalyzerError(Exception):
  """Base exception for Fragment Analyzer errors."""


class FragmentAnalyzerInvalidCommandError(FragmentAnalyzerError):
  """Invalid command error."""


class FragmentAnalyzerMethodError(FragmentAnalyzerError):
  """Method related error."""


class FragmentAnalyzerCommandFailedError(FragmentAnalyzerError):
  """A command failed for some reason."""


class FragmentAnalyzerLowSolutionError(FragmentAnalyzerError):
  """Low solution error."""


class FragmentAnalyzerHardwareError(FragmentAnalyzerError):
  """Hardware related error."""


class FragmentAnalyzerOtherError(FragmentAnalyzerError):
  """Other error."""


ERROR_MESSAGES: Dict[str, Tuple[str, Type[FragmentAnalyzerError]]] = {
  "!1": ("Invalid command", FragmentAnalyzerInvalidCommandError),
  "!2": ("No method", FragmentAnalyzerMethodError),
  "!3": ("Method not found", FragmentAnalyzerMethodError),
  "!4": ("Command failed", FragmentAnalyzerCommandFailedError),
  "!5": ("Low solution", FragmentAnalyzerLowSolutionError),
  "!6": ("Stage error", FragmentAnalyzerHardwareError),
  "!7": ("Pump command error", FragmentAnalyzerHardwareError),
  "!8": ("Pressure error", FragmentAnalyzerHardwareError),
  "!9": ("Camera Connection error", FragmentAnalyzerHardwareError),
  "!10": ("Other", FragmentAnalyzerOtherError),
}


@dataclass
class AgilentFASolutionLevels:
  gel1: float = 0
  gel2: float = 0
  conditioningSolution: float = 0
  waste: float = 0


@dataclass
class AgilentFASensorData:
  voltage: float
  current: float
  pressure: float


class AgilentFABackend(FragmentAnalyzerBackend, metaclass=ABCMeta):
  """Backend for Agilent Fragment Analyzer. This backend connects to the server where the OEM software is running"""

  def __init__(self, host: str, port: int = 3000):
    self.host = host
    self.port = port
    self.io = Socket(host=host, port=port)

  async def setup(self):
    await self.io.setup()

  async def stop(self):
    await self.io.stop()

  def _parse_error(self, response: str):
    if response.startswith("!"):
      error_code = response.split(",")[0]
      if error_code in ERROR_MESSAGES:
        message, err_class = ERROR_MESSAGES[error_code]
        raise err_class(f"Fragment Analyzer error: {message} ({response})")
      raise FragmentAnalyzerError(f"Unknown Fragment Analyzer error: {response}")

  async def _read_res(self, timeout: int = 1, read_once=True) -> str:
    response = await self.io.read(timeout=timeout, read_once=read_once)

    self._parse_error(response)
    return response

  async def send_command(self, command: str, timeout: int = 60, read_once=True) -> str:
    """Send a command and get a single line response."""
    await self.io.write(command)
    return await self._read_res(timeout, read_once=read_once)

  async def send_command_and_await_completion(
    self, command: str, expected_response: str, timeout: int = 120
  ) -> List[str]:
    """Send a command and wait for an initial response and a completion response."""
    await self.io.write(command)

    responses = []
    response1 = await self._read_res(timeout)
    if expected_response not in response1.upper():
      raise FragmentAnalyzerError(
        f"Unexpected response to '{command}'. Expected '{expected_response}', got '{response1}'"
      )
    responses.append(response1)

    response2 = await self._read_res(timeout)
    if "*COMPLETE" not in response2.upper():
      raise FragmentAnalyzerError(
        f"Did not receive '*COMPLETE' after '{command}'. Got '{response2}'"
      )
    responses.append(response2)

    return responses

  async def get_status(self) -> str:
    response = await self.send_command("STATUS", read_once=False, timeout=1)
    if response.startswith("*STATUS:"):
      return response.split(":", 1)[1].strip()
    elif response.startswith("*Complete"):
      return "Method complete. Waiting for conditioning to finish."
    raise FragmentAnalyzerError(f"Unexpected status response: {response}")

  async def tray_out(self, tray_number: int = 5):
    if not 1 <= tray_number <= 5:
      raise ValueError("Tray number must be between 1 and 5.")

    command = "OUT" if tray_number == 5 else f"OUT{tray_number}"
    expected_response = "*OUT"

    await self.send_command_and_await_completion(command, expected_response)

  async def store_capillary(self):
    """Move the Capillary Storage Solution tray to the capillary array."""
    await self.send_command_and_await_completion("STORE", "*STORE")

  async def set_plate_name(self, plate_name: str):
    """set name of the plate used in result file naming"""
    response = await self.send_command(f"TRAY {plate_name}")
    if "*TRAY" not in response:
      raise FragmentAnalyzerError(f"Failed to set tray name. Response: {response}")

  async def run_method(self, method_name: str, nonblocking=False):
    """Run a specified Fragment Analyzer method.
    for separation methods,
    method file must be located in [parent directory of Fragment Analyzer.exe]/Methods/[capillary length]/.
    for conditioning methods,
    method file must be located in [parent directory of Fragment Analyzer.exe]/Methods/
    """
    if nonblocking:
      await self.send_command(f"RUN {method_name}", read_once=False, timeout=10)
    else:
      await self.send_command_and_await_completion(f"RUN {method_name}", "*RUN", timeout=7200)

  async def get_ladder_file(self, method_name: str, ladder_file_name: str):
    """Run a specified Fragment Analyzer method and save ladder file to [parent directory of Fragment Analyzer.exe]/Ladders/
    for separation methods,
    method file must be located in [parent directory of Fragment Analyzer.exe]/Methods/[capillary length]/.
    for conditioning methods,
    method file must be located in [parent directory of Fragment Analyzer.exe]/Methods/
    """
    command = f"CAL {method_name}, {ladder_file_name}"
    await self.send_command_and_await_completion(command, "*CAL", timeout=7200)

  async def set_ladder_file(self, ladder_file: str):
    """Set the ladder file for next run. File must be located in [parent directory of Fragment Analyzer.exe]/Ladders/"""
    response = await self.send_command(f"LAD-FILE {ladder_file}")
    if "*LAD-FILE" not in response.upper():
      raise FragmentAnalyzerError(f"Failed to set ladder file. Response: {response}")

  async def get_solution_levels(self) -> AgilentFASolutionLevels:
    response = await self.send_command("SOLUTIONS")
    if "*SOLUTIONS" not in response.upper():
      raise FragmentAnalyzerError(f"Failed to get solution levels. Response: {response}")
    levels = response.split(":")[1].strip().split(",")
    return AgilentFASolutionLevels(*[float(level) for level in levels])

  async def get_sensor_data(self) -> AgilentFASensorData:
    response = await self.send_command("VCP")
    if "*VCP" not in response.upper():
      raise FragmentAnalyzerError(f"Failed to get sensor data. Response: {response}")
    data = response.split(":")[1].strip().split(",")
    return AgilentFASensorData(*[float(value) for value in data])

  async def abort(self):
    await self.send_command_and_await_completion("ABORT", "*ABORT")
