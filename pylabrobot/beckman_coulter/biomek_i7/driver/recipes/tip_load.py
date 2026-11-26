"""Loading tips on a Multichannel head, as the Master Controller does it: `LT`.

The head presses its mandrels into a rack of tips with Z force-limited, so Z stops where the tips
are seated, not at its target. The recipe, as coordinated moves (`Pod.move_axes`):

  1. D to `load_d` at full speed (the plunger where tips are loaded).
  2. Z to `load_z` at `load_speed`, force-limited to `load_force`: Z stops on the tips.
  3. Wait `settling_time` seconds.
  4. Read Z (the press left it unknown): the loaded Z, which `LT` replies.
  5. Z to `relieve_z` at `relieve_speed`, still force-limited, then the same move again with no
     force (Z was force-limited, so it is read first; the drive has eased off a few counts).
  6. Z to `leave_z` and D to `leave_d` together at `leave_speed`.
  7. D to `end_d` at full speed.

Every argument comes from the host; the Master Controller only checks them. Biomek computes them
from the pod's instrument-file settings, the height the tips seat at and how many tips are loaded: `TipLoad.from_configuration`.

As the Master Controller does, the settling wait ends with an error if the interlock trips
(`_settle`).
"""

import asyncio
import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

from pylabrobot.beckman_coulter.biomek_i7.driver.configuration import PodConfiguration
from pylabrobot.beckman_coulter.biomek_i7.driver.errors import (
  INTERLOCK_TRIPPED_CODE,
  InterlockTrippedError,
)
from pylabrobot.beckman_coulter.biomek_i7.driver.protocol import MAX_FORCE
from pylabrobot.beckman_coulter.biomek_i7.units import percent_to_field

if TYPE_CHECKING:
  from pylabrobot.beckman_coulter.biomek_i7.driver.features.pod import Pod

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class TipLoad:
  """The arguments of an `LT`, in its order: positions in counts, speeds 1..1000 (Biomek's speed
  field), the force in the Master Controller's units (its `SF`), the settling time in seconds."""

  load_d: int
  load_z: int
  load_force: float
  load_speed: int
  settling_time: float
  relieve_z: int
  relieve_speed: int
  leave_d: int
  leave_z: int
  leave_speed: int
  end_d: int

  def check(self) -> None:
    """The Master Controller's checks of the arguments (it refuses others as invalid).

    Raises:
      ValueError: for a force outside 0..4294967.295, a speed outside 1..1000, or a negative
        settling time.
    """
    if not 0 <= self.load_force <= MAX_FORCE:
      raise ValueError(f"load_force {self.load_force} is outside 0..{MAX_FORCE}")
    for name in ("load_speed", "relieve_speed", "leave_speed"):
      if not 1 <= getattr(self, name) <= 1000:
        raise ValueError(f"{name} {getattr(self, name)} is outside 1..1000")
    if self.settling_time < 0:
      raise ValueError(f"settling_time {self.settling_time} is negative")

  @classmethod
  def from_configuration(cls, pod: PodConfiguration, seat_z: float, tips: int) -> "TipLoad":
    """The `LT` Biomek sends to load `tips` tips seated at Z `seat_z` (cm, Biomek's Z), from the
    pod's settings, as every logged `LT` has them:

    - force: `TipLoadForceLimitOffset` + `TipLoadForceLimitSlope` x the share of the head's
      mandrels loaded (`tips` / rows x columns);
    - Z: pressed to `seat_z` + `TipLoadZOffset`, relieved to `seat_z` + `TipLoadRelieveZOffset`,
      and left `TipLoadPushOffDistance` above that;
    - D: 0 uL to load and to end; while Z leaves, the plunger follows it down by the same
      distance (-`TipLoadPushOffDistance` cm x `DAxisULPerCM`);
    - speeds: `TipLoadSpeed`, `TipLoadRelieveSpeed`, `TipLoadPushOffSpeed`, percent, as Biomek's
      speed field (x10); `TipLoadSettlingTime`.

    Raises:
      ValueError: for a pod with no 96-channel head settings, or `tips` outside 1..mandrels.
    """
    s = pod.settings
    mandrels = (pod.geometry.mandrels_x or 0) * (pod.geometry.mandrels_y or 0)
    if not mandrels or pod.module_type != "multichannel":
      raise ValueError(f"{pod.component} has no 96-channel head")
    if not 1 <= tips <= mandrels:
      raise ValueError(f"tips {tips} is outside 1..{mandrels}")
    relieve = seat_z + s["TipLoadRelieveZOffset"]
    push_off = s["TipLoadPushOffDistance"]
    empty = pod.to_counts("D", 0.0)
    return cls(
      load_d=empty,
      load_z=pod.to_counts("Z", seat_z + s["TipLoadZOffset"]),
      load_force=s["TipLoadForceLimitOffset"] + tips / mandrels * s["TipLoadForceLimitSlope"],
      load_speed=percent_to_field(s["TipLoadSpeed"]),
      settling_time=s["TipLoadSettlingTime"],
      relieve_z=pod.to_counts("Z", relieve),
      relieve_speed=percent_to_field(s["TipLoadRelieveSpeed"]),
      leave_d=pod.to_counts("D", -1.0 * push_off * s["DAxisULPerCM"]),
      leave_z=pod.to_counts("Z", relieve + push_off),
      leave_speed=percent_to_field(s["TipLoadPushOffSpeed"]),
      end_d=empty,
    )

  @classmethod
  def parse(cls, command: str) -> "TipLoad":
    """From a logged `LT` host command, such as `LT 43176 -50081 518.750 100 2.000 ...`."""
    name, *f = command.split()
    if name != "LT" or len(f) != 11:
      raise ValueError(f"not an LT command: {command!r}")
    return cls(
      int(f[0]),
      int(f[1]),
      float(f[2]),
      int(f[3]),
      float(f[4]),
      int(f[5]),
      int(f[6]),
      int(f[7]),
      int(f[8]),
      int(f[9]),
      int(f[10]),
    )


async def _settle(pod: "Pod", seconds: float) -> None:
  """The settling wait, as the Master Controller's: it ends with an error if the
  interlock trips, or is tripped when the wait begins.

  Raises:
    InterlockTrippedError: then; the press is left as it is.
  """
  tripped = pod.interlock_tripped
  if tripped is None:
    await asyncio.sleep(seconds)
    return
  if not tripped.is_set():
    try:
      await asyncio.wait_for(tripped.wait(), seconds)
    except asyncio.TimeoutError:
      return
  raise InterlockTrippedError(INTERLOCK_TRIPPED_CODE, None, f"{pod} LT settling")


async def load_tips(pod: "Pod", load: TipLoad) -> int:
  """Run `load` on a Multichannel pod. Returns the loaded Z: where the press left Z, in counts.

  Raises:
    ValueError: for arguments the Master Controller refuses, a target outside an axis's limits, or
      a pod that is not a Multichannel; nothing is sent.
  """
  if pod.module_type != "multichannel":
    raise ValueError(f"{pod} has no 96-channel head to load tips on")
  load.check()
  for axis, target in (
    ("D", load.load_d),
    ("Z", load.load_z),
    ("Z", load.relieve_z),
    ("D", load.leave_d),
    ("Z", load.leave_z),
    ("D", load.end_d),
  ):
    pod.axis(axis).check_position(target)
  logger.info(
    "%s LT D %d, Z %d force %s speed %d, settle %s s, relieve Z %d speed %d, leave D %d Z %d "
    "speed %d, end D %d",
    pod.component,
    load.load_d,
    load.load_z,
    load.load_force,
    load.load_speed,
    load.settling_time,
    load.relieve_z,
    load.relieve_speed,
    load.leave_d,
    load.leave_z,
    load.leave_speed,
    load.end_d,
  )
  await pod.move_axes({"D": load.load_d}, 1000)
  await pod.move_axes({"Z": load.load_z}, load.load_speed, forces={"Z": load.load_force})
  await _settle(pod, load.settling_time)
  await pod._read_unknown_positions()
  loaded_z = pod.cpositions["Z"][1]
  await pod.move_axes({"Z": load.relieve_z}, load.relieve_speed, forces={"Z": load.load_force})
  await pod.move_axes({"Z": load.relieve_z}, load.relieve_speed)
  await pod.move_axes({"Z": load.leave_z, "D": load.leave_d}, load.leave_speed)
  await pod.move_axes({"D": load.end_d}, 1000)
  return loaded_z
