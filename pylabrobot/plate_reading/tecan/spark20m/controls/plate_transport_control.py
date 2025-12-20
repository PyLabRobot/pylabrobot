import logging
from enum import Enum
from .base_control import baseControl
from .spark_enums import MovementSpeed, PlatePosition

class PlateColor(Enum):
    BLACK = "BLACK"
    WHITE = "WHITE"
    TRANSPARENT = "TRANSPARENT"
    NO = "NO"

class plateControl(baseControl):
    """
    This class provides methods for controlling the plate transport system.
    It includes functionalities to move the plate to absolute positions or named positions,
    retrieve current positions and coordinates, and manage movement speed.
    """

    async def move_to_position(self, position: PlatePosition, additional_option=None):
        """Moves the plate transport to a predefined position."""
        command = f"ABSOLUTE MODULE=MTP POSITION={position.value}"
        if additional_option:
            command += f" {additional_option}"
        return await self.send_command(command)

    async def move_to_coordinate(self, x=None, y=None, z=None):
        """Moves the plate transport to the specified coordinates."""
        command = "ABSOLUTE MODULE=MTP"
        if x is not None: command += f" X={x}"
        if y is not None: command += f" Y={y}"
        if z is not None: command += f" Z={z}"
        return await self.send_command(command)

    async def get_current_position(self):
        """Gets the current predefined position of the plate transport."""
        return await self.send_command("?ABSOLUTE MODULE=MTP POSITION")

    async def get_current_coordinates(self):
        """Gets the current X, Y, Z coordinates of the plate transport."""
        return await self.send_command("?ABSOLUTE MODULE=MTP")

    async def get_current_coordinate(self, motor):
        """Gets the current coordinate of a specific motor (X, Y, or Z)."""
        return await self.send_command(f"?ABSOLUTE MODULE=MTP {motor.upper()}")

    async def get_available_positions(self):
        """Gets the list of available predefined positions."""
        return await self.send_command("#ABSOLUTE MODULE=MTP POSITION")

    async def get_motor_range(self, motor):
        """Gets the movement range for a specific motor (X, Y, or Z)."""
        return await self.send_command(f"#ABSOLUTE MODULE=MTP {motor.upper()}")

    async def get_motor_x_range(self):
        return await self.get_motor_range("X")

    async def get_motor_y_range(self):
        return await self.get_motor_range("Y")

    async def get_motor_z_range(self):
        return await self.get_motor_range("Z")

    async def get_available_speed_modes(self):
        """Gets the available movement speed modes."""
        response = await self.send_command("#SPEED MOVEMENT MODULE=MTP")
        return response

    async def get_current_motor_speed(self):
        """Gets the current movement speed mode."""
        return await self.send_command("?SPEED MOVEMENT MODULE=MTP")

    async def set_motor_speed(self, speed_mode: MovementSpeed):
        """Sets the movement speed mode."""
        return await self.send_command(f"SPEED MOVEMENT={speed_mode.value} MODULE=MTP")

    async def get_stacker_sensor_column(self, column_type):
        """Gets the stacker sensor column state."""
        return await self.send_command(f"?STACKER SENSOR {column_type.upper()} COLUMN")

    async def get_stacker_sensor_plate(self, column_type):
        """Gets the stacker sensor plate state."""
        return await self.send_command(f"?STACKER SENSOR {column_type.upper()} PLATE")

    async def get_stacker_sensor_lift(self, column_type):
        """Gets the stacker sensor lift state."""
        return await self.send_command(f"?STACKER SENSOR {column_type.upper()} LIFT")

    async def stacker_stack_plate(self, column_type, plate_height, skirt_height):
        """Commands the stacker to stack a plate."""
        return await self.send_command(f"STACKER STACK COLUMN={column_type.upper()} PLATEHEIGHT={plate_height} SKIRTHEIGHT={skirt_height}")

    async def stacker_get_plate(self, column_type, plate_height, skirt_height, color: PlateColor=PlateColor.NO):
        """Commands the stacker to get a plate."""
        return await self.send_command(f"STACKER GET COLUMN={column_type.upper()} PLATEHEIGHT={plate_height} SKIRTHEIGHT={skirt_height} COLOUR={color.value}")

    async def stacker_finish(self):
        """Commands the stacker to finish its current operation."""
        return await self.send_command("STACKER FINISH")
