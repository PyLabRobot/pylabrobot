import unittest
from pylabrobot.resources.coordinate import Coordinate
from pylabrobot.resources.rotation import Rotation

class TestRotation(unittest.TestCase):
    def test_get_rotation_and_translation(self):
        rotation = Rotation(x=0, y=0, z=90)
        origin = Coordinate(x=1, y=0, z=1)

        rotation_matrix, translation = rotation.get_rotation_and_translation(origin)
        
        expected_rotation_matrix = [
            [0, -1, 0],
            [1, 0, 0],
            [0, 0, 1]
        ]
        expected_translation = [-1, 1, 0]

        for i in range(3):
            for j in range(3):
                self.assertAlmostEqual(rotation_matrix[i][j], expected_rotation_matrix[i][j], places=4)
        for i in range(3):
            self.assertAlmostEqual(translation[i], expected_translation[i], places=4)

if __name__ == '__main__': 
    unittest.main()
