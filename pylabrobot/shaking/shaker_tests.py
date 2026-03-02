import asyncio
import unittest

from pylabrobot.resources.coordinate import Coordinate
from pylabrobot.shaking import Shaker, ShakerBackend, ShakerChatterboxBackend


class LegacyShakerBackend(ShakerBackend):
  def __init__(self):
    self.started_speed = None
    self.stopped = False

  async def setup(self):
    return

  async def stop(self):
    return

  async def shake(self, speed: float):
    self.started_speed = speed

  async def stop_shaking(self):
    self.stopped = True

  @property
  def supports_locking(self) -> bool:
    return False

  async def lock_plate(self):
    return

  async def unlock_plate(self):
    return


class ModernShakerBackend(ShakerBackend):
  def __init__(self):
    self.started_speed = None
    self.stopped = False

  async def setup(self):
    return

  async def stop(self):
    return

  async def start_shaking(self, speed: float):
    self.started_speed = speed

  async def stop_shaking(self):
    self.stopped = True

  @property
  def supports_locking(self) -> bool:
    return False

  async def lock_plate(self):
    return

  async def unlock_plate(self):
    return


class ShakerTests(unittest.TestCase):
  def test_serialization(self):
    s = Shaker(
      name="test_shaker",
      size_x=10,
      size_y=10,
      size_z=10,
      backend=ShakerChatterboxBackend(),
      child_location=Coordinate(0, 0, 0),
    )

    serialized = s.serialize()
    deserialized = Shaker.deserialize(serialized)
    self.assertEqual(s, deserialized)

  def test_backend_start_shaking_accepts_legacy_shake_implementation(self):
    backend = LegacyShakerBackend()
    asyncio.run(backend.start_shaking(speed=300.0))
    self.assertEqual(backend.started_speed, 300.0)

  def test_backend_shake_deprecated_alias_calls_start_shaking(self):
    backend = ModernShakerBackend()
    with self.assertWarns(DeprecationWarning):
      asyncio.run(backend.shake(speed=400.0))
    self.assertEqual(backend.started_speed, 400.0)

  def test_shaker_frontend_uses_backend_start_shaking(self):
    backend = ModernShakerBackend()
    shaker = Shaker(
      name="test_shaker",
      size_x=10,
      size_y=10,
      size_z=10,
      backend=backend,
      child_location=Coordinate(0, 0, 0),
    )
    asyncio.run(shaker.shake(speed=500.0))
    self.assertEqual(backend.started_speed, 500.0)
