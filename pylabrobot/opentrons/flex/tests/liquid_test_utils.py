"""Read the pipetting destination from captured coordinate and Z moves."""

from pylabrobot.opentrons import ChatterboxHTTP
from pylabrobot.resources import Container


def pipetting_location(io: ChatterboxHTTP, target: Container, channels: int = 1) -> dict:
  """Express the last pipetting destination relative to a cavity's primary nozzle."""
  commands = io.commands
  index = max(i for i, c in enumerate(commands) if c["commandType"] == "moveToCoordinates")
  xy = commands[index]["params"]["coordinates"]
  descent = next(c for c in commands[index + 1 :] if c["commandType"] == "moveRelative")
  assert descent["params"]["axis"] == "z"
  z = (io.saved_position or {"z": 100})["z"] + descent["params"]["distance"]
  origin = target.get_absolute_location(x="c", y="c", z="cavity_bottom")
  return {
    "origin": "bottom",
    "offset": {
      "x": round(xy["x"] - origin.x + (49.5 if channels == 96 else 0), 6),
      "y": round(xy["y"] - origin.y - (31.5 if channels > 1 else 0), 6),
      "z": round(z - origin.z, 6),
    },
  }
