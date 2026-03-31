from pylabrobot.bulk_dispensers.backend import BulkDispenserBackend


class BulkDispenserChatterboxBackend(BulkDispenserBackend):
  """A backend that prints operations for testing without hardware."""

  async def setup(self) -> None:
    print("Setting up bulk dispenser.")

  async def stop(self) -> None:
    print("Stopping bulk dispenser.")

  async def dispense(self) -> None:
    print("Dispensing.")

  async def prime(self, volume: float) -> None:
    print(f"Priming with {volume} uL.")

  async def empty(self, volume: float) -> None:
    print(f"Emptying with {volume} uL.")

  async def shake(self, time: float, distance: int, speed: int) -> None:
    print(f"Shaking for {time}s, distance={distance}mm, speed={speed}Hz.")

  async def move_plate_out(self) -> None:
    print("Moving plate out.")

  async def set_plate_type(self, plate_type: int) -> None:
    print(f"Setting plate type to {plate_type}.")

  async def set_cassette_type(self, cassette_type: int) -> None:
    print(f"Setting cassette type to {cassette_type}.")

  async def set_column_volume(self, column: int, volume: float) -> None:
    print(f"Setting column {column} volume to {volume} uL.")

  async def set_dispensing_height(self, height: int) -> None:
    print(f"Setting dispensing height to {height}.")

  async def set_pump_speed(self, speed: int) -> None:
    print(f"Setting pump speed to {speed}%.")

  async def set_dispensing_order(self, order: int) -> None:
    print(f"Setting dispensing order to {order}.")

  async def abort(self) -> None:
    print("Aborting.")
