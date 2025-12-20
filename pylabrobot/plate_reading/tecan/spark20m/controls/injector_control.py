from .base_control import baseControl
from enum import Enum
from typing import List
from .spark_enums import InjectorName, InjectionMode, InjectorState

class InjectorControl(baseControl):
    async def get_all_injectors(self):
        """Gets all available injectors."""
        response = await self.send_command("#INJECTOR PUMP")
        return response

    async def get_injector_volume_range(self, pump: InjectorName, mode: InjectionMode):
        """Gets the injector volume range."""
        return await self.send_command(f"#INJECTOR PUMP={pump.value} MODE={mode.value} VOLUME")

    async def get_injector_speed_range(self, pump: InjectorName, mode: InjectionMode):
        """Gets the injector speed range."""
        return await self.send_command(f"#INJECTOR PUMP={pump.value} MODE={mode.value} SPEED")

    async def get_injector_well_diameter_range(self, pump: InjectorName):
        """Gets the injector well diameter range."""
        return await self.send_command(f"#INJECTOR PUMP={pump.value} WELLDIAMETER")

    async def get_injector_position_x(self, pump: InjectorName):
        """Gets the injector X position."""
        return await self.send_command(f"?INJECTOR PUMP={pump.value} POSITIONX")

    async def get_injector_position_y(self, pump: InjectorName):
        """Gets the injector Y position."""
        return await self.send_command(f"?INJECTOR PUMP={pump.value} POSITIONY")

    async def get_defined_injector_volume(self, pump: InjectorName, mode: InjectionMode):
        """Gets the defined injector volume."""
        return await self.send_command(f"?INJECTOR PUMP={pump.value} MODE={mode.value} VOLUME")

    async def get_injector_default_volume(self, pump: InjectorName, mode: InjectionMode):
        """Gets the injector default volume."""
        return await self.send_command(f"?INJECTOR DEFAULT PUMP={pump.value} MODE={mode.value} VOLUME")

    async def set_injector_volume(self, pump: InjectorName, mode: InjectionMode, volume):
        """Sets the injector volume."""
        return await self.send_command(f"INJECTOR PUMP={pump.value} MODE={mode.value} VOLUME={volume}")

    async def set_injector_default_volume(self, pump: InjectorName, mode: InjectionMode, volume):
        """Sets the injector default volume."""
        return await self.send_command(f"INJECTOR DEFAULT PUMP={pump.value} MODE={mode.value} VOLUME={volume}")

    async def get_defined_injector_speed(self, pump: InjectorName, mode: InjectionMode):
        """Gets the defined injector speed."""
        return await self.send_command(f"?INJECTOR PUMP={pump.value} MODE={mode.value} SPEED")

    async def get_injector_default_speed(self, pump: InjectorName, mode: InjectionMode):
        """Gets the injector default speed."""
        return await self.send_command(f"?INJECTOR DEFAULT PUMP={pump.value} MODE={mode.value} SPEED")

    async def set_injector_speed(self, pump: InjectorName, mode: InjectionMode, speed):
        """Sets the injector speed."""
        return await self.send_command(f"INJECTOR PUMP={pump.value} MODE={mode.value} SPEED={speed}")

    async def set_injector_default_speed(self, pump: InjectorName, mode: InjectionMode, speed):
        """Sets the injector default speed."""
        return await self.send_command(f"INJECTOR DEFAULT PUMP={pump.value} MODE={mode.value} SPEED={speed}")

    async def get_injector_model(self, pump: InjectorName):
        """Gets the injector model."""
        return await self.send_command(f"?INJECTOR PUMP={pump.value} MODEL")

    async def set_injector_state(self, state: InjectorState, pumps: List[InjectorName]):
        """Sets the state of the specified injector(s)."""
        pumps_str = "|".join([p.value for p in pumps])
        return await self.send_command(f"INJECTOR STATE={state.value} PUMP={pumps_str}")

    async def set_injector_refill_mode(self, pump: InjectorName, mode):
        """Sets the injector refill mode."""
        return await self.send_command(f"INJECTOR REFILL TYPE={mode.upper()} PUMP={pump.value}")

    async def is_injector_primed(self, pump: InjectorName):
        """Checks if the injector is primed."""
        return await self.send_command(f"?INJECTOR PUMP={pump.value} PRIMED")

    async def injector_start_injecting(self, mode: InjectionMode):
        """Starts the injection process in the specified mode (DISPENSE, PRIME, RINSE, BACKFLUSH)."""
        return await self.send_command(f"INJECTOR {mode.value}", self.ep_interrupt_in, read_timeout=60000)

    async def injector_dispense(self):
        """Alias for injector_start_injecting('DISPENSE')."""
        return await self.injector_start_injecting(InjectionMode.DISPENSE)

    async def injector_prime(self):
        """Alias for injector_start_injecting('PRIME')."""
        return await self.injector_start_injecting(InjectionMode.PRIME)

    async def injector_rinse(self):
        """Alias for injector_start_injecting('RINSE')."""
        return await self.injector_start_injecting(InjectionMode.RINSE)

    async def injector_backflush(self):
        """Alias for injector_start_injecting('BACKFLUSH')."""
        return await self.injector_start_injecting(InjectionMode.BACKFLUSH)

    async def get_injector_syringe_volume(self, pump: InjectorName):
        """Gets the injector syringe volume."""
        return await self.send_command(f"?INJECTOR PUMP={pump.value} SYRINGEVOLUME")

    async def deactivate_all_injectors(self):
        """Deactivates all injectors."""
        all_injectors_str = await self.get_all_injectors()
        if all_injectors_str:
            parts = all_injectors_str.split('|')
            injectors = []
            for p in parts:
                try:
                   injectors.append(InjectorName(p))
                except ValueError:
                   pass # Ignore unknown pumps
            if injectors:
                return await self.set_injector_state(InjectorState.INACTIVE, injectors)
        return None

    async def activate_injectors(self, injectors: List[InjectorName]):
        """Activates the specified injectors."""
        return await self.set_injector_state(InjectorState.ACTIVE, injectors)

    async def fim_raise_trigger(self):
        """Raises the FIM camera trigger."""
        return await self.send_command("SCAN")
