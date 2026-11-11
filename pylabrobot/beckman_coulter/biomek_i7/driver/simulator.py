"""Simulated boards: boards that answer from what a real Biomek i7's boards answered.

`ReplayAceUSB` replays a recording; nothing it does reaches a USB device. A replayed board holds a
recording of request -> replies, keyed on everything in a request but its sequence number, and
answers a request it has a recording for with those replies under the request's sequence number.
A request it has no recording for gets no answer, as a board that ignored it would, and is kept in
`unanswered`.
"""

import asyncio
import gzip
import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from pylabrobot.beckman_coulter.biomek_i7.driver.board import AceBoard
from pylabrobot.beckman_coulter.biomek_i7.driver.framing import frame, split
from pylabrobot.io.usb import USB

# Names the recording the simulator answers from (identity and sensor reads, recorded at an
# instrument's cold start) when no path is given. A generic one ships in `defaults/`
# (`biomek_i7_generic_coldstart.json`).
RECORDING_ENV = "BIOMEK_I7_SIMULATOR_RECORDING"


def recording_path(path: Optional[Union[str, Path]] = None) -> Path:
  """`path`, else the file `$BIOMEK_I7_SIMULATOR_RECORDING` names.

  Raises:
    ValueError: with neither.
  """
  path = path or os.environ.get(RECORDING_ENV)
  if not path:
    raise ValueError(
      f"no recording for the simulator to answer from: pass its path or set ${RECORDING_ENV}"
    )
  return Path(path)


def load_recording(path: Optional[Union[str, Path]] = None) -> Dict[str, Any]:
  """A recording (`recording_path`), gzipped (`.gz`) or not."""
  where = recording_path(path)
  if where.suffix == ".gz":
    with gzip.open(where, "rt", encoding="utf-8") as fh:
      return dict(json.load(fh))
  with open(where, encoding="utf-8") as fh:
    return dict(json.load(fh))


def _request_key(message: bytes) -> str:
  return (message[:3] + message[4:]).hex()


class ReplayAceUSB(USB):
  """One board's USB link, answered from a recording."""

  def __init__(self, recording: Dict[str, List[str]], name: str = "replayed ACE board"):
    super().__init__(0x0AB5, 0xACE0, name)
    self.recording = {k: [bytes.fromhex(r) for r in v] for k, v in recording.items()}
    self.requests: List[bytes] = []
    self.unanswered: List[bytes] = []
    self._packets: "asyncio.Queue[bytes]" = asyncio.Queue()

  async def setup(self, empty_buffer=True):
    pass

  async def stop(self):
    pass

  async def write(self, data: bytes, timeout: Optional[float] = None):
    for message in split(data):
      self.requests.append(message)
      replies = self.recording.get(_request_key(message))
      if replies is None:
        self.unanswered.append(message)
        continue
      for reply in replies:
        framed = frame(reply[:3] + message[3:4] + reply[4:])
        for i in range(0, len(framed), 16):
          self._packets.put_nowait(framed[i : i + 16])

  async def read(self, timeout=None, size=None, endpoint=None) -> bytes:
    try:
      return await asyncio.wait_for(self._packets.get(), timeout=0.05)
    except asyncio.TimeoutError:
      raise TimeoutError("no packet") from None


def replay_boards(
  path: Optional[Union[str, Path]] = None, default_timeout: float = 1.0
) -> List[AceBoard]:
  """One replayed board per module in a recording (`recording_path`), as
  `BiomekI7Driver(boards=...)` takes them."""
  data = load_recording(path)
  return [
    AceBoard(
      io=ReplayAceUSB(dict(entries), name=f"replayed {module}"),
      name=f"replayed {module}",
      default_timeout=default_timeout,
    )
    for module, entries in data["boards"].items()
  ]
