import asyncio
import unittest
import unittest.mock

from pylabrobot.events import EventBus, PLREvent, use_event_bus
from pylabrobot.legacy.machines.machine import Machine, MachineBackend


class TestMachine(unittest.TestCase):
  class MockBackend(MachineBackend):
    def __init__(self, mock_param):
      self.mock_param = mock_param

    async def setup(self):
      pass

    async def stop(self):
      pass

    def serialize(self):
      return {**super().serialize(), "mock_param": self.mock_param}

  class MockMachine(Machine):
    pass

  def test_serialize(self):
    m = self.MockMachine(backend=self.MockBackend("mock_param"))
    self.assertEqual(
      m.serialize(),
      {
        "backend": {
          "mock_param": "mock_param",
          "type": "MockBackend",
        },
      },
    )

  def test_deserialize(self):
    m = self.MockMachine(backend=self.MockBackend("mock_param"))
    Machine.deserialize(m.serialize())  # shouldn't raise

  def test_non_resource_machine_uses_device_reference(self):
    events: list[PLREvent] = []
    event_bus = EventBus()
    event_bus.subscribe(events.append)
    machine = self.MockMachine(backend=self.MockBackend("mock_param"))

    async def setup_and_stop():
      with use_event_bus(event_bus):
        await machine.setup()
        await machine.stop()

    asyncio.run(setup_and_stop())

    self.assertEqual(events[0].data["device"], {"name": "MockMachine", "type": "MockMachine"})
