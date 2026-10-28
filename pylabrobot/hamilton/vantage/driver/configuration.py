from dataclasses import dataclass


@dataclass
class DeviceConfiguration:
  """What a Vantage turns out to be, read from it by `VantageDriver.discover`.

  Only what the machine can be asked is held here, and nothing is assumed about the rest.
  """

  num_channels: int
  """How many pipetting channels the machine has: one for each entry of the `A1PM QA` reply."""
