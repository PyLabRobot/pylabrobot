"""Read the pipetting destination from captured coordinate and Z moves."""

from unittest.mock import AsyncMock

from pylabrobot.resources import Container


def pipetting_location(io: AsyncMock, target: Container, channels: int = 1) -> dict:
  """Express the last pipetting destination relative to a cavity's primary nozzle."""
  commands = io.submit_command.await_args_list
  index = max(i for i, c in enumerate(commands) if c.args[1] == "moveToCoordinates")
  xy = commands[index].args[2]["coordinates"]
  descent = next(c for c in commands[index + 1 :] if c.args[1] == "moveRelative")
  assert descent.args[2]["axis"] == "z"
  z = (io.get_command.return_value.result["position"] or {"z": 100})["z"] + descent.args[2][
    "distance"
  ]
  origin = target.get_absolute_location(x="c", y="c", z="cavity_bottom")
  return {
    "origin": "bottom",
    "offset": {
      "x": round(xy["x"] - origin.x + (49.5 if channels == 96 else 0), 6),
      "y": round(xy["y"] - origin.y - (31.5 if channels > 1 else 0), 6),
      "z": round(z - origin.z, 6),
    },
  }
