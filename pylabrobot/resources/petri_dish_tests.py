from typing import cast
import unittest

from pylabrobot.resources import PetriDish, PetriDishHolder


class TestPetriDish(unittest.TestCase):
  """ Test the PetriDish and PetriDishHolder classes """

  def test_petri_dish_serialization(self):
    petri_dish = PetriDish("petri_dish", 90.0, 15.0)
    serialized = petri_dish.serialize()
    self.assertEqual(serialized, {
      "name": "petri_dish",
      "category": "petri_dish",
      "diameter": 90.0,
      "height": 15.0,
      "parent_name": None,
      "type": "PetriDish",
      "children": [],
      "location": None,
      "max_volume": 121500.0,
      "model": None,
    })

  def test_petri_dish_holder_serialization(self):
    petri_dish_holder = PetriDishHolder("petri_dish_holder")
    serialized = petri_dish_holder.serialize()
    self.assertEqual(serialized, {
      "name": "petri_dish_holder",
      "category": "petri_dish_holder",
      "size_x": 127.0,
      "size_y": 86.0,
      "size_z": 14.5,
      "parent_name": None,
      "type": "PetriDishHolder",
      "children": [],
      "location": None,
      "model": None,
    })

  def test_petri_dish_holder_deserialization_without_dish(self):
    petri_dish_holder = PetriDishHolder.deserialize({
      "name": "petri_dish_holder",
      "category": "petri_dish_holder",
      "size_x": 127.0,
      "size_y": 86.0,
      "size_z": 14.5,
      "children": [],
      "location": None,
      "model": None,
      "type": "PetriDishHolder",
      "parent_name": None,
    })

    self.assertEqual(petri_dish_holder.name, "petri_dish_holder")
    self.assertEqual(petri_dish_holder.get_size_x(), 127.0)
    self.assertEqual(petri_dish_holder.get_size_y(), 86.0)

  def test_petri_dish_holder_deserialization_with_dish(self):
    petri_dish_holder = PetriDishHolder.deserialize({
      "name": "petri_dish_holder",
      "category": "petri_dish_holder",
      "size_x": 127.0,
      "size_y": 86.0,
      "size_z": 14.5,
      "children": [
        {
          "name": "petri_dish",
          "category": "petri_dish",
          "diameter": 90.0,
          "height": 15.0,
          "parent_name": "petri_dish_holder",
          "type": "PetriDish",
          "children": [],
          "location": {
            "x": 0.0,
            "y": 0.0,
            "z": 0.0,
          },
          "model": None,
        }
      ],
      "location": None,
      "model": None,
      "type": "PetriDishHolder",
      "parent_name": None,
    })

    self.assertEqual(petri_dish_holder.name, "petri_dish_holder")
    self.assertEqual(petri_dish_holder.get_size_x(), 127.0)
    self.assertEqual(petri_dish_holder.get_size_y(), 86.0)
    self.assertEqual(petri_dish_holder.get_size_z(), 14.5)

    self.assertEqual(len(petri_dish_holder.children), 1)
    dish = cast(PetriDish, petri_dish_holder.children[0])
    self.assertEqual(dish.name, "petri_dish")
    self.assertEqual(dish.diameter, 90.0)
    self.assertEqual(dish.height, 15.0)
