"""What the package's tests build their simulated Biomek i7 from: the generic one in `defaults/`,
named explicitly (never through `$BIOMEK_I7_SIMULATOR_RECORDING`). Not a test module itself."""

import atexit
import copy
import json
import shutil
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Sequence

from pylabrobot.beckman_coulter.biomek_i7.deck import BiomekI7Deck
from pylabrobot.beckman_coulter.biomek_i7.defaults import GENERIC_CONFIGURATION, GENERIC_RECORDING
from pylabrobot.beckman_coulter.biomek_i7.driver.board import AceBoard
from pylabrobot.beckman_coulter.biomek_i7.driver.configuration import DeviceConfiguration
from pylabrobot.beckman_coulter.biomek_i7.driver.master import BiomekI7Driver
from pylabrobot.beckman_coulter.biomek_i7.driver.simulator import ReplayAceUSB, simulated_boards

if TYPE_CHECKING:
  from pylabrobot.beckman_coulter.biomek_i7.device import BiomekI7Device

_GENERIC: Optional[Dict[str, Any]] = None

# The serial numbers the generic cold start answers: placeholders as long as real ones.
GENERIC_SERIAL_NUMBERS = {
  "chassis": "SIMULATED00100000000000000000000",
  "left": "SIMULATED00200000000000000000000",
  "right": "SIMULATED00900000000000000000000",
}


def generic_recording() -> Dict[str, Any]:
  """The generic cold-start recording, a fresh copy a test may change: `boards` maps each module
  (`chassis`, `multichannel`, `span8`) to its [request key, [replies]] pairs."""
  global _GENERIC
  if _GENERIC is None:
    with open(GENERIC_RECORDING, encoding="utf-8") as fh:
      _GENERIC = json.load(fh)
  return copy.deepcopy(_GENERIC)


def generic_boards(
  recording: Optional[Dict[str, Any]] = None,
  modules: Optional[Sequence[str]] = None,
  default_timeout: float = 1.0,
) -> List[AceBoard]:
  """A replayed board per module of `recording` (by default the generic one), as
  `simulator.replay_boards` builds them; `modules` picks and orders them."""
  data = recording if recording is not None else generic_recording()
  names = list(data["boards"]) if modules is None else list(modules)
  return [
    AceBoard(
      io=ReplayAceUSB(dict(data["boards"][module]), name=f"replayed {module}"),
      name=f"replayed {module}",
      default_timeout=default_timeout,
    )
    for module in names
  ]


def generic_configuration() -> DeviceConfiguration:
  """The generic declared configuration, loaded afresh (a test may change its copy)."""
  return DeviceConfiguration.load(GENERIC_CONFIGURATION)


def sealed(configuration: DeviceConfiguration) -> DeviceConfiguration:
  """A test's changed copy, with its fingerprints recorded again so that it loads. Only tests do
  this: the package makes a configuration from Biomek's files, never from another."""
  configuration.sources = {
    name: {**configuration.sources.get(name, {}), "fingerprint": f}
    for name, f in configuration.fingerprints().items()
  }
  return configuration


def as_instrument(configuration: DeviceConfiguration) -> DeviceConfiguration:
  """`configuration` as the generic instrument's own: not generic, and naming the serial numbers
  its cold start answers (sealed)."""
  configuration.generic = False
  configuration.chassis_serial_number = GENERIC_SERIAL_NUMBERS["chassis"]
  for side, pod in configuration.pods.items():
    pod.module_serial_number = GENERIC_SERIAL_NUMBERS[side]
  return sealed(configuration)


def configuration_file(configuration: DeviceConfiguration) -> str:
  """`configuration` saved to a new temporary file, for `declared_configuration_json`; removed
  when the tests end."""
  folder = Path(tempfile.mkdtemp(prefix="biomek_i7_test_"))
  atexit.register(shutil.rmtree, folder, ignore_errors=True)
  path = folder / "configuration.json"
  configuration.save(path)
  return str(path)


def generic_simulated_boards(**kwargs: Any) -> List[AceBoard]:
  """The generic instrument simulated (`simulator.simulated_boards`), with the generic
  configuration's motor tables and the generic cold start; `kwargs` as `simulated_boards` takes
  them (`homed`, `configured`)."""
  kwargs.setdefault("path", GENERIC_RECORDING)
  return simulated_boards(generic_configuration().motor_tables, **kwargs)


def generic_driver(
  boards: Optional[Sequence[AceBoard]] = None,
  configuration: Optional[DeviceConfiguration] = None,
  **kwargs: Any,
) -> BiomekI7Driver:
  """A driver over `boards` (by default the generic instrument replayed), declared by
  `configuration` (by default the generic configuration's file); `kwargs` as `BiomekI7Driver`
  takes them."""
  return BiomekI7Driver(
    declared_configuration_json=(
      str(GENERIC_CONFIGURATION) if configuration is None else configuration_file(configuration)
    ),
    boards=generic_boards() if boards is None else boards,
    **kwargs,
  )


def generic_deck(name: str = "deck") -> BiomekI7Deck:
  """The deck the generic configuration lays out (`BiomekI7Deck.from_configuration`)."""
  return BiomekI7Deck.from_configuration(generic_configuration(), name=name)


def generic_biomek(**kwargs: Any) -> "BiomekI7Device":
  """`BiomekI7(simulation=True, ...)` of the generic configuration and cold start, named
  explicitly; `kwargs` as `BiomekI7` takes them."""
  from pylabrobot.beckman_coulter.biomek_i7.device import BiomekI7

  kwargs.setdefault("simulation", True)
  kwargs.setdefault("declared_configuration_json", str(GENERIC_CONFIGURATION))
  kwargs.setdefault("simulator_recording", str(GENERIC_RECORDING))
  return BiomekI7(**kwargs)
