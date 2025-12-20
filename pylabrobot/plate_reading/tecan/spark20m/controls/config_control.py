from .base_control import baseControl
from .spark_enums import ModuleType, ConfigAxis

class ConfigControl(baseControl):
    async def get_config_expected_modules(self):
        """Gets the expected modules from configuration."""
        return await self.send_command("?CONFIG MODULE EXPECTED")

    async def set_config_expected_modules(self, module_details_list):
        """Sets the expected modules in configuration."""
        modules_str = "|".join([f"{m.Name}:{m.Number}" for m in module_details_list])
        return await self.send_command(f"CONFIG MODULE EXPECTED={modules_str}")

    async def get_config_expected_usb_modules(self):
        """Gets the expected USB modules from configuration."""
        return await self.send_command("?CONFIG MODULE EXPECTED_USB")

    async def set_config_expected_usb_modules(self, module_details_list):
        """Sets the expected USB modules in configuration."""
        modules_str = "|".join([f"{m.Name}:{m.Number}" for m in module_details_list])
        return await self.send_command(f"CONFIG MODULE EXPECTED_USB={modules_str}")

    async def get_config_sap_instrument_serial_number(self):
        """Gets the SAP instrument serial number from configuration."""
        return await self.send_command("?CONFIG IDENTIFICATION SAP_SERIAL_INSTR")

    async def set_config_sap_instrument_serial_number(self, serial_number, module: ModuleType=None, sub_module=None):
        """Sets the SAP instrument serial number in configuration."""
        command = f"CONFIG IDENTIFICATION SAP_SERIAL_INSTR={serial_number}"
        if module: command += f" MODULE={module.value}"
        if sub_module: command += f" SUB={sub_module}"
        return await self.send_command(command)

    async def set_config_module_serial_number(self, serial_number, module: ModuleType, module_number, sub_module=None):
        """Sets the module serial number in configuration."""
        command = f"CONFIG INFO SAP_NR_MODULE={serial_number} MODULE={module.value} NUMBER={module_number}"
        if sub_module: command += f" SUB={sub_module}"
        return await self.send_command(command)

    async def configure_home_direction(self, motor, value, hw_module: ModuleType=None, number=None, subcomponent=None):
        """Configures the home direction for a motor."""
        command = f"CONFIG INIT MOTOR={motor} HOMEDIRECTION={value}"
        if hw_module: command += f" MODULE={hw_module.value}"
        if number is not None: command += f" NUMBER={number}"
        if subcomponent: command += f" SUB={subcomponent}"
        return await self.send_command(command)

    async def configure_home_level_position(self, motor, value, hw_module: ModuleType=None, number=None, subcomponent=None):
        """Configures the home level position for a motor."""
        command = f"CONFIG INIT MOTOR={motor} HOMELEVEL={value}"
        if hw_module: command += f" MODULE={hw_module.value}"
        if number is not None: command += f" NUMBER={number}"
        if subcomponent: command += f" SUB={subcomponent}"
        return await self.send_command(command)

    async def configure_home_sensor_position(self, motor, value, hw_module: ModuleType=None, number=None, subcomponent=None):
        """Configures the home sensor position for a motor."""
        command = f"CONFIG INIT MOTOR={motor} HOMESENSOR={value}"
        if hw_module: command += f" MODULE={hw_module.value}"
        if number is not None: command += f" NUMBER={number}"
        if subcomponent: command += f" SUB={subcomponent}"
        return await self.send_command(command)

    async def configure_init_position(self, motor, value, hw_module: ModuleType=None, number=None, subcomponent=None):
        """Configures the initial position for a motor."""
        command = f"CONFIG INIT MOTOR={motor} INITPOS={value}"
        if hw_module: command += f" MODULE={hw_module.value}"
        if number is not None: command += f" NUMBER={number}"
        if subcomponent: command += f" SUB={subcomponent}"
        return await self.send_command(command)

    async def configure_max_home(self, motor, value, hw_module: ModuleType=None, number=None, subcomponent=None):
        """Configures the maximum home position for a motor."""
        command = f"CONFIG INIT MOTOR={motor} MAXHOME={value}"
        if hw_module: command += f" MODULE={hw_module.value}"
        if number is not None: command += f" NUMBER={number}"
        if subcomponent: command += f" SUB={subcomponent}"
        return await self.send_command(command)

    async def configure_max_out_of_home(self, motor, value, hw_module: ModuleType=None, number=None, subcomponent=None):
        """Configures the maximum out of home position for a motor."""
        command = f"CONFIG INIT MOTOR={motor} MAXOUTOFHOME={value}"
        if hw_module: command += f" MODULE={hw_module.value}"
        if number is not None: command += f" NUMBER={number}"
        if subcomponent: command += f" SUB={subcomponent}"
        return await self.send_command(command)

    async def get_home_direction(self, motor, hw_module: ModuleType=None, number=None, subcomponent=None):
        """Gets the home direction for a motor."""
        command = f"?CONFIG INIT MOTOR={motor} HOMEDIRECTION"
        if hw_module: command += f" MODULE={hw_module.value}"
        if number is not None: command += f" NUMBER={number}"
        if subcomponent: command += f" SUB={subcomponent}"
        return await self.send_command(command)

    async def get_home_level_position(self, motor, hw_module: ModuleType=None, number=None, subcomponent=None):
        """Gets the home level position for a motor."""
        command = f"?CONFIG INIT MOTOR={motor} HOMELEVEL"
        if hw_module: command += f" MODULE={hw_module.value}"
        if number is not None: command += f" NUMBER={number}"
        if subcomponent: command += f" SUB={subcomponent}"
        return await self.send_command(command)

    async def get_home_sensor_position(self, motor, hw_module: ModuleType=None, number=None, subcomponent=None):
        """Gets the home sensor position for a motor."""
        command = f"?CONFIG INIT MOTOR={motor} HOMESENSOR"
        if hw_module: command += f" MODULE={hw_module.value}"
        if number is not None: command += f" NUMBER={number}"
        if subcomponent: command += f" SUB={subcomponent}"
        return await self.send_command(command)

    async def get_init_position(self, motor, hw_module: ModuleType=None, number=None, subcomponent=None):
        """Gets the initial position for a motor."""
        command = f"?CONFIG INIT MOTOR={motor} INITPOS"
        if hw_module: command += f" MODULE={hw_module.value}"
        if number is not None: command += f" NUMBER={number}"
        if subcomponent: command += f" SUB={subcomponent}"
        return await self.send_command(command)

    async def get_max_home(self, motor, hw_module: ModuleType=None, number=None, subcomponent=None):
        """Gets the maximum home position for a motor."""
        command = f"?CONFIG INIT MOTOR={motor} MAXHOME"
        if hw_module: command += f" MODULE={hw_module.value}"
        if number is not None: command += f" NUMBER={number}"
        if subcomponent: command += f" SUB={subcomponent}"
        return await self.send_command(command)

    async def get_max_out_of_home(self, motor, hw_module: ModuleType=None, number=None, subcomponent=None):
        """Gets the maximum out of home position for a motor."""
        command = f"?CONFIG INIT MOTOR={motor} MAXOUTOFHOME"
        if hw_module: command += f" MODULE={hw_module.value}"
        if number is not None: command += f" NUMBER={number}"
        if subcomponent: command += f" SUB={subcomponent}"
        return await self.send_command(command)

    async def get_config_limit_value(self, name, hw_module: ModuleType=None, number=None, subcomponent=None):
        """Gets the configured limit value."""
        command = f"?CONFIG LIMIT"
        if hw_module: command += f" MODULE={hw_module.value}"
        if number is not None: command += f" NUMBER={number}"
        if subcomponent: command += f" SUB={subcomponent}"
        command += f" {name.upper()}"
        return await self.send_command(command)

    async def set_config_limit_value(self, value, name, hw_module: ModuleType=None, number=None, subcomponent=None):
        """Sets the configured limit value."""
        command = f"CONFIG LIMIT"
        if hw_module: command += f" MODULE={hw_module.value}"
        if number is not None: command += f" NUMBER={number}"
        if subcomponent: command += f" SUB={subcomponent}"
        command += f" {name.upper()}={value}"
        return await self.send_command(command)

    async def init_module(self, hw_module: ModuleType=None, number=None, subcomponent=None, excluded_modules=None):
        """Initializes the specified module or all modules if none specified.
        Can exclude modules from initialization.
        """
        command = "INIT"
        if excluded_modules:
            excluded_str = "|".join(excluded_modules)
            command += f" WITHOUT={excluded_str}"
        else:
            if hw_module: command += f" MODULE={hw_module.value}"
            if number is not None: command += f" NUMBER={number}"
            if subcomponent: command += f" SUB={subcomponent}"
        return await self.send_command(command)

    async def init_motor(self, motor, hw_module: ModuleType=None, number=None, subcomponent=None):
        """Initializes the specified motor."""
        command = f"INIT MOTOR={motor}"
        if hw_module: command += f" MODULE={hw_module.value}"
        if number is not None: command += f" NUMBER={number}"
        if subcomponent: command += f" SUB={subcomponent}"
        return await self.send_command(command)

    async def get_initializable_motors(self, hw_module: ModuleType=None, number=None, subcomponent=None):
        """Gets the list of initializable motors."""
        command = "#INIT"
        if hw_module: command += f" MODULE={hw_module.value}"
        if number is not None: command += f" NUMBER={number}"
        if subcomponent: command += f" SUB={subcomponent}"
        response = await self.send_command(command)
        return response

    async def set_offset(self, axis: ConfigAxis, offset):
        """Sets the offset for a given axis (X, Y, Z)."""
        return await self.send_command(f"CONFIG OFFSET {axis.value}={offset}")

    async def get_offset(self, axis: ConfigAxis):
        """Gets the offset for a given axis (X, Y, Z)."""
        return await self.send_command(f"?CONFIG OFFSET {axis.value}")

    async def set_x_offset(self, offset):
        return await self.set_offset(ConfigAxis.X, offset)

    async def get_x_offset(self):
        return await self.get_offset(ConfigAxis.X)

    async def set_y_offset(self, offset):
        return await self.set_offset(ConfigAxis.Y, offset)

    async def get_y_offset(self):
        return await self.get_offset(ConfigAxis.Y)

    async def set_z_offset(self, offset):
        return await self.set_offset(ConfigAxis.Z, offset)

    async def get_z_offset(self):
        return await self.get_offset(ConfigAxis.Z)

    async def set_mirror_offset(self, offset, module: ModuleType=ModuleType.FLUORESCENCE):
        """Sets the mirror offset for a given module."""
        return await self.send_command(f"CONFIG OFFSET MIRROR1={offset} MODULE={module.value}")

    async def get_mirror_offset(self, module: ModuleType=ModuleType.FLUORESCENCE):
        """Gets the mirror offset for a given module."""
        return await self.send_command(f"?CONFIG OFFSET MIRROR1 MODULE={module.value}")

    async def _get_objective_config(self, index, param, hw_module: ModuleType=ModuleType.FLUORESCENCE_IMAGING, number=1):
        command = f"?CONFIG OBJECTIVE INDEX={index} {param}"
        if hw_module: command += f" MODULE={hw_module.value}"
        if number is not None: command += f" NUMBER={number}"
        return await self.send_command(command)

    async def _set_objective_config(self, index, param, value, hw_module: ModuleType=ModuleType.FLUORESCENCE_IMAGING, number=1):
        command = f"CONFIG OBJECTIVE INDEX={index} {param}={value}"
        if hw_module: command += f" MODULE={hw_module.value}"
        if number is not None: command += f" NUMBER={number}"
        return await self.send_command(command)

    async def get_objective_magnification(self, index):
        return await self._get_objective_config(index, "MAGNIFICATION")

    async def set_objective_magnification(self, index, value):
        return await self._set_objective_config(index, "MAGNIFICATION", value)

    async def get_objective_autofocus_offset(self, index):
        return await self._get_objective_config(index, "AF_OFFSET")

    async def set_objective_autofocus_offset(self, index, value):
        return await self._set_objective_config(index, "AF_OFFSET", value)

    async def get_objective_z_offset(self, index):
        return await self._get_objective_config(index, "Z_OFFSET")

    async def set_objective_z_offset(self, index, value):
        return await self._set_objective_config(index, "Z_OFFSET", value)

    async def get_objective_brightfield_time(self, index):
        return await self._get_objective_config(index, "BF_TIME")

    async def set_objective_brightfield_time(self, index, value):
        return await self._set_objective_config(index, "BF_TIME", value)

    async def get_objective_roi_offset_x(self, hw_module: ModuleType=ModuleType.FLUORESCENCE_IMAGING, number=1):
        return await self.send_command(f"?CONFIG OBJECTIVE AF_ROI_OFFSET_X MODULE={hw_module.value} NUMBER={number}")

    async def set_objective_roi_offset_x(self, value, hw_module: ModuleType=ModuleType.FLUORESCENCE_IMAGING, number=1):
        return await self.send_command(f"CONFIG OBJECTIVE AF_ROI_OFFSET_X={value} MODULE={hw_module.value} NUMBER={number}")

    async def get_objective_roi_offset_y(self, hw_module: ModuleType=ModuleType.FLUORESCENCE_IMAGING, number=1):
        return await self.send_command(f"?CONFIG OBJECTIVE AF_ROI_OFFSET_Y MODULE={hw_module.value} NUMBER={number}")

    async def set_objective_roi_offset_y(self, value, hw_module: ModuleType=ModuleType.FLUORESCENCE_IMAGING, number=1):
        return await self.send_command(f"CONFIG OBJECTIVE AF_ROI_OFFSET_Y={value} MODULE={hw_module.value} NUMBER={number}")

    def _create_target_string(self, hwModule: ModuleType=None, number=None, subcomponent=None):
        target_string = ""
        if hwModule:
            target_string += f" MODULE={hwModule.value}"
        if number is not None:
            target_string += f" NUMBER={number}"
        if subcomponent:
            target_string += f" SUB={subcomponent}"
        return target_string

    async def get_dead_time_config(self, hwModule: ModuleType=None, number=None, subcomponent=None):
        """Gets the dead time configuration."""
        target_string = self._create_target_string(hwModule, number, subcomponent)
        return await self.send_command(f"?CONFIG{target_string} DEADTIME")

    async def set_dead_time_config(self, deadTime, hwModule: ModuleType=None, number=None, subcomponent=None):
        """Sets the dead time configuration."""
        target_string = self._create_target_string(hwModule, number, subcomponent)
        return await self.send_command(f"CONFIG{target_string} DEADTIME={deadTime}")
