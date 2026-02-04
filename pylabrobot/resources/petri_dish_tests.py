import unittest
from typing import cast

from pylabrobot.resources import (
  Coordinate,
  PetriDish,
  PetriDishHolder,
)


class TestPetriDish(unittest.TestCase):
  """Test the PetriDish and PetriDishHolder classes"""

  def test_petri_dish_serialization(self):
    petri_dish = PetriDish("petri_dish", diameter=90.0, height=15.0)
    serialized = petri_dish.serialize()
    self.assertEqual(
      serialized,
      {
        "name": "petri_dish",
        "category": "petri_dish",
        "diameter": 90.0,
        "height": 15.0,
        "material_z_thickness": None,
        "compute_volume_from_height": None,
        "compute_height_from_volume": None,
        "parent_name": None,
        "type": "PetriDish",
        "children": [],
        "location": None,
        "rotation": {"type": "Rotation", "x": 0, "y": 0, "z": 0},
        "max_volume": 121500.0,
        "model": None,
        "barcode": None,
        "preferred_pickup_location": None,
      },
    )

  def test_petri_dish_holder_serialization(self):
    petri_dish_holder = PetriDishHolder("petri_dish_holder")
    serialized = petri_dish_holder.serialize()
    self.assertEqual(
      serialized,
      {
        "name": "petri_dish_holder",
        "category": "petri_dish_holder",
        "size_x": 127.76,
        "size_y": 85.48,
        "size_z": 14.5,
        "parent_name": None,
        "type": "PetriDishHolder",
        "children": [],
        "location": None,
        "rotation": {"type": "Rotation", "x": 0, "y": 0, "z": 0},
        "model": None,
        "barcode": None,
        "preferred_pickup_location": None,
      },
    )

  def test_petri_dish_holder_deserialization_without_dish(self):
    petri_dish_holder = PetriDishHolder("petri_dish_holder")
    petri_dish_holder = PetriDishHolder.deserialize(petri_dish_holder.serialize())

    self.assertEqual(petri_dish_holder.name, "petri_dish_holder")
    self.assertEqual(petri_dish_holder.get_absolute_size_x(), 127.76)
    self.assertEqual(petri_dish_holder.get_absolute_size_y(), 85.48)

  def test_petri_dish_holder_deserialization_with_dish(self):
    petri_dish_holder = PetriDishHolder("petri_dish_holder")
    petri_dish_holder.assign_child_resource(
      PetriDish("petri_dish", 90.0, 15.0), location=Coordinate.zero()
    )
    petri_dish_holder = PetriDishHolder.deserialize(petri_dish_holder.serialize())

    self.assertEqual(petri_dish_holder.name, "petri_dish_holder")
    self.assertEqual(petri_dish_holder.get_absolute_size_x(), 127.76)
    self.assertEqual(petri_dish_holder.get_absolute_size_y(), 85.48)
    self.assertEqual(petri_dish_holder.get_absolute_size_z(), 14.5)

    self.assertEqual(len(petri_dish_holder.children), 1)
    dish = cast(PetriDish, petri_dish_holder.children[0])
    self.assertEqual(dish.name, "petri_dish")
    self.assertEqual(dish.diameter, 90.0)
    self.assertEqual(dish.height, 15.0)
