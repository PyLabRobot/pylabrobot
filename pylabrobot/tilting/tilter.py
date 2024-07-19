import math
from typing import List

from pylabrobot.resources import Coordinate, Plate
from pylabrobot.resources.ml_star.mfx_modules import MFXModule
from pylabrobot.tilting.hamilton_backend import HamiltonTiltModuleBackend

class HamiltonTiltModule(MFXModule):
    """ A class representing the Hamilton Tilt Module. """

    def __init__(
        self,
        name: str,
        size_x: int,
        size_y: int,
        size_z: int,
        child_resource_location: Coordinate,
        pedestal_size_z: float,
        category: str,
        hinge_coordinate: Coordinate,
        initial_offset: int,
        com_port: str,
        write_timeout: float = 3,
        timeout: float = 3,
    ):
        """
        Initialize the Hamilton Tilt Module.

        Args:
            name (str): The name of the module.
            size_x (int): The size of the module in the x dimension.
            size_y (int): The size of the module in the y dimension.
            size_z (int): The size of the module in the z dimension.
            child_resource_location (Coordinate): The location of the child resource.
            pedestal_size_z (float): The size of the pedestal in the z dimension.
            category (str): The category of the module.
            hinge_coordinate (Coordinate): The coordinate of the hinge.
            com_port (str): The communication port.
            write_timeout (float, optional): The write timeout. Defaults to 3.
            timeout (float, optional): The timeout. Defaults to 3.
        """
        super().__init__(
            name=name,
            size_x=size_x,
            size_y=size_y,
            size_z=size_z,
            child_resource_location=child_resource_location,
            pedestal_size_z=pedestal_size_z,
            category=category,
        )

        self.backend = HamiltonTiltModuleBackend(com_port=com_port, write_timeout=write_timeout, timeout=timeout)
        self._hinge_coordinate = hinge_coordinate
        self._absolute_angle = 0
        self._initial_offset = initial_offset
        self.setup_finished = False

    async def setup(self):
        await self.backend.setup(initial_offset=self._initial_offset)
        self.setup_finished = True

    @property
    def absolute_angle(self) -> int:
        return self._absolute_angle

    async def set_angle(self, absolute_angle: int):
        """
        Set the tilt module to rotate to a given angle.

        Args:
            absolute_angle (int): The absolute (unsigned) angle to set rotation to, in degrees, measured from horizontal as zero.
        """
        # if the hinge is on the left side of the tilter, the angle is kept positive
        # else, the angle is converted to negative. this follows Euler angle conventions.

        angle = absolute_angle if self._hinge_coordinate.x < self._size_x / 2 else -absolute_angle
        await self.backend.set_angle(angle=abs(angle))
        self._absolute_angle = absolute_angle

    def rotate_coordinate_around_hinge(self, absolute_coordinate: Coordinate, angle: int) -> Coordinate:
        """
        Rotate an absolute coordinate around the hinge of the tilter by a given angle.

        Args:
            absolute_coordinate (Coordinate): The coordinate to rotate.
            angle (int): The angle to rotate by, in degrees. Negative is clockwise according to Euler conventions.

        Returns:
            Coordinate: The new coordinate after rotation.
        """
        theta = math.radians(angle)

        rotation_arm_x = absolute_coordinate.x - (
            self._hinge_coordinate.x + self.get_absolute_location("l", "f", "b").x
        )
        rotation_arm_z = absolute_coordinate.z - (
            self._hinge_coordinate.z + self.get_absolute_location("l", "f", "b").z
        )

        x_prime = rotation_arm_x * math.cos(theta) - rotation_arm_z * math.sin(theta)
        z_prime = rotation_arm_x * math.sin(theta) + rotation_arm_z * math.cos(theta)

        new_x = x_prime + (self._hinge_coordinate.x + self.get_absolute_location("l", "f", "b").x)
        new_z = z_prime + (self._hinge_coordinate.z + self.get_absolute_location("l", "f", "b").z)

        return Coordinate(new_x, absolute_coordinate.y, new_z)

    def return_drain_offsets_of_plate(self, plate: Plate, absolute_angle: int = None) -> List[Coordinate]:
        """
        Return the drain edge offsets for all wells in the given plate, tilted around the hinge at a given absolute angle.

        Args:
            plate (Plate): The plate to calculate the offsets for.
            absolute_angle (int, optional): The absolute angle to rotate the plate. Defaults to current tilt angle.

        Returns:
            List[Coordinate]: A list of offsets for the wells in the plate.
        """

        # if absolute_angle is not provided, use the current tilt angle
        if absolute_angle is None:
            angle = self._absolute_angle if self._hinge_coordinate.x < self._size_x / 2 else -self._absolute_angle
        else:
            angle = absolute_angle if self._hinge_coordinate.x < self._size_x / 2 else -absolute_angle

        _hinge_side = "l" if self._hinge_coordinate.x < self._size_x / 2 else "r"

        well_drain_offsets = []
        for well in plate.children:
            level_absolute_well_drain_coordinate = well.get_absolute_location(_hinge_side, "c", "b")
            rotated_absolute_well_drain_coordinate = self.rotate_coordinate_around_hinge(
                level_absolute_well_drain_coordinate, angle
            )
            well_drain_offset = rotated_absolute_well_drain_coordinate - well.get_absolute_location("c", "c", "b")
            well_drain_offsets.append(well_drain_offset)

        return well_drain_offsets

    async def tilt(self, angle: int):
        """
        Tilt the plate contained in the tilt module by a given angle relative to the current angle.

        Args:
            angle (int): The angle to rotate by, in degrees. Clockwise. 0 is horizontal.
        """
        await self.set_angle(self._absolute_angle + angle)


def hamiltonTiltModule(
    name: str,
    com_port: str,
) -> HamiltonTiltModule:
    return HamiltonTiltModule(
        name=name,
        size_x=132,
        size_y=92.57,
        size_z=85.81,
        child_resource_location=Coordinate(1.0, 3.0, 83.55),
        com_port=com_port,
        hinge_coordinate=Coordinate(6.18, 0, 72.85),
        initial_offset=-20,
        category="tilt_module",
        pedestal_size_z=3.47,
    )


### Example use
# tilter = hamiltonTiltModule(name="tilter", com_port="/dev/tty.usbserial-FTDINDTT")
# await tilter.setup()
# tilter.assign_child_resource(plate)
# await tilter.set_angle(10)
# offsets = tilter.return_drain_offsets_of_plate(plate)