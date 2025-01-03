import unittest

from pylabrobot.centrifuge import (
  BucketHasPlateError,
  BucketNoPlateError,
  Centrifuge,
  CentrifugeDoorError,
  Loader,
  LoaderNoPlateError,
  NotAtBucketError,
)
from pylabrobot.centrifuge.backend import CentrifugeBackend, LoaderBackend
from pylabrobot.centrifuge.chatterbox import CentrifugeChatterboxBackend, LoaderChatterboxBackend
from pylabrobot.resources import Coordinate, Cor_96_wellplate_360ul_Fb


class CentrifugeLoaderResourceModelTests(unittest.IsolatedAsyncioTestCase):
  async def asyncSetUp(self):
    self.mock_centrifuge_backend = unittest.mock.MagicMock(spec=CentrifugeBackend)
    self.mock_loader_backend = unittest.mock.MagicMock(spec=LoaderBackend)
    self.centrifuge = Centrifuge(
      backend=self.mock_centrifuge_backend, name="centrifuge", size_x=1, size_y=1, size_z=1
    )
    self.loader = Loader(
      backend=self.mock_loader_backend,
      centrifuge=self.centrifuge,
      name="loader",
      size_x=1,
      size_y=1,
      size_z=1,
      child_location=Coordinate.zero(),
    )
    self.plate = Cor_96_wellplate_360ul_Fb(name="plate")
    return await super().asyncSetUp()

  async def test_go_to_bucket(self):
    self.assertIsNone(self.centrifuge.at_bucket)
    await self.centrifuge.go_to_bucket1()
    self.assertEqual(self.centrifuge.at_bucket, self.centrifuge.bucket1)
    await self.centrifuge.go_to_bucket2()
    self.assertEqual(self.centrifuge.at_bucket, self.centrifuge.bucket2)

  async def test_load(self):
    await self.centrifuge.go_to_bucket1()
    await self.centrifuge.open_door()
    assert self.centrifuge._door_open
    assert self.centrifuge.door_open
    self.loader.assign_child_resource(self.plate)
    await self.loader.load()
    self.mock_loader_backend.load.assert_awaited_once()
    assert self.centrifuge.at_bucket is not None
    self.assertEqual(self.centrifuge.at_bucket.children[0], self.plate)
    self.assertEqual(self.loader.children, [])

  async def test_load_locked_door(self):
    self.loader.assign_child_resource(self.plate)
    with self.assertRaises(CentrifugeDoorError):
      await self.loader.load()
    self.mock_loader_backend.load.assert_not_awaited()

  async def test_load_no_plate(self):
    await self.centrifuge.go_to_bucket1()
    await self.centrifuge.open_door()
    with self.assertRaises(LoaderNoPlateError):
      await self.loader.load()
    self.mock_loader_backend.load.assert_not_awaited()

  async def test_load_bucket_has_plate(self):
    await self.centrifuge.go_to_bucket1()
    await self.centrifuge.open_door()
    assert self.centrifuge.at_bucket is not None
    self.centrifuge.at_bucket.assign_child_resource(self.plate)
    another_plate = Cor_96_wellplate_360ul_Fb(name="another_plate")
    self.loader.assign_child_resource(another_plate)
    with self.assertRaises(BucketHasPlateError):
      await self.loader.load()
    self.mock_loader_backend.load.assert_not_awaited()

  async def test_load_not_at_bucket(self):
    self.loader.assign_child_resource(self.plate)
    await self.centrifuge.open_door()
    with self.assertRaises(NotAtBucketError):
      await self.loader.load()
    self.mock_loader_backend.load.assert_not_awaited()

  async def test_unload(self):
    await self.centrifuge.go_to_bucket1()
    await self.centrifuge.open_door()
    assert self.centrifuge.at_bucket is not None
    self.centrifuge.at_bucket.assign_child_resource(self.plate)
    await self.loader.unload()
    self.mock_loader_backend.unload.assert_awaited_once()
    self.assertEqual(self.centrifuge.at_bucket.children, [])
    self.assertEqual(self.loader.children, [self.plate])

  async def test_unload_locked_door(self):
    self.loader.assign_child_resource(self.plate)
    with self.assertRaises(CentrifugeDoorError):
      await self.loader.unload()
    self.mock_loader_backend.unload.assert_not_awaited()

  async def test_unload_bucket_has_no_plate(self):
    await self.centrifuge.go_to_bucket1()
    await self.centrifuge.open_door()
    with self.assertRaises(BucketNoPlateError):
      await self.loader.unload()
    self.mock_loader_backend.unload.assert_not_awaited()

  async def test_unload_loader_has_plate(self):
    await self.centrifuge.go_to_bucket1()
    await self.centrifuge.open_door()
    self.loader.assign_child_resource(self.plate)
    with self.assertRaises(BucketNoPlateError):
      await self.loader.unload()
    self.mock_loader_backend.unload.assert_not_awaited()

  async def test_unload_not_at_bucket(self):
    self.loader.assign_child_resource(self.plate)
    await self.centrifuge.open_door()
    with self.assertRaises(NotAtBucketError):
      await self.loader.unload()
    self.mock_loader_backend.unload.assert_not_awaited()

  def test_serialize(self):
    self.loader.backend = LoaderChatterboxBackend()
    self.centrifuge.backend = CentrifugeChatterboxBackend()
    serialized = self.loader.serialize()
    self.assertEqual(Loader.deserialize(serialized), self.loader)
