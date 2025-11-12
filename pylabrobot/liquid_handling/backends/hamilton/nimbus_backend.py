"""Hamilton Nimbus backend implementation.

This module provides the NimbusBackend class for controlling Hamilton Nimbus
instruments via TCP communication using the Hamilton protocol.
"""

from __future__ import annotations

import enum
import logging
from typing import List, Optional, Union

from pylabrobot.resources.coordinate import Coordinate

from pylabrobot.liquid_handling.backends.backend import LiquidHandlerBackend
from pylabrobot.liquid_handling.backends.hamilton.commands import HamiltonCommand
from pylabrobot.liquid_handling.backends.hamilton.messages import (
    HoiParams,
    HoiParamsParser,
)
from pylabrobot.liquid_handling.backends.hamilton.packets import Address
from pylabrobot.liquid_handling.backends.hamilton.protocol import (
    HamiltonProtocol,
)
from pylabrobot.liquid_handling.backends.hamilton.tcp_backend import TCPBackend
from pylabrobot.liquid_handling.backends.hamilton.tcp_introspection import (
    HamiltonIntrospection,
)
from pylabrobot.liquid_handling.standard import (
    Drop,
    DropTipRack,
    MultiHeadAspirationContainer,
    MultiHeadAspirationPlate,
    MultiHeadDispenseContainer,
    MultiHeadDispensePlate,
    Pickup,
    PickupTipRack,
    ResourceDrop,
    ResourceMove,
    ResourcePickup,
    SingleChannelAspiration,
    SingleChannelDispense,
)
from pylabrobot.resources import Tip
from pylabrobot.resources.hamilton import HamiltonTip, TipSize
from pylabrobot.resources.hamilton.nimbus_decks import NimbusDeck
from pylabrobot.resources.trash import Trash

logger = logging.getLogger(__name__)


# ============================================================================
# TIP TYPE ENUM
# ============================================================================


class NimbusTipType(enum.IntEnum):
    """Hamilton Nimbus tip type enumeration.

    Maps tip type names to their integer values used in Hamilton protocol commands.
    """

    STANDARD_300UL = 0  # "300ul Standard Volume Tip"
    STANDARD_300UL_FILTER = 1  # "300ul Standard Volume Tip with filter"
    LOW_VOLUME_10UL = 2  # "10ul Low Volume Tip"
    LOW_VOLUME_10UL_FILTER = 3  # "10ul Low Volume Tip with filter"
    HIGH_VOLUME_1000UL = 4  # "1000ul High Volume Tip"
    HIGH_VOLUME_1000UL_FILTER = 5  # "1000ul High Volume Tip with filter"
    TIP_50UL = 22  # "50ul Tip"
    TIP_50UL_FILTER = 23  # "50ul Tip with filter"
    SLIM_CORE_300UL = 36  # "SLIM CO-RE Tip 300ul"


def _get_tip_type_from_tip(tip: Tip) -> int: # TODO: Map these to Hamilton Tip Rack Resources rather than inferring from tip characteristics
    """Map Tip object characteristics to Hamilton tip type integer.

    Args:
        tip: Tip object with volume and filter information.

    Returns:
        Hamilton tip type integer value.

    Raises:
        ValueError: If tip characteristics don't match any known tip type.
    """
    # Match based on volume and filter
    if tip.maximal_volume <= 15:  # 10ul tip
        if tip.has_filter:
            return NimbusTipType.LOW_VOLUME_10UL_FILTER
        else:
            return NimbusTipType.LOW_VOLUME_10UL
    elif tip.maximal_volume <= 60:  # 50ul tip
        if tip.has_filter:
            return NimbusTipType.TIP_50UL_FILTER
        else:
            return NimbusTipType.TIP_50UL
    elif tip.maximal_volume <= 500:  # 300ul tip (increased threshold to catch 360µL filtered tips)
        if tip.has_filter:
            return NimbusTipType.STANDARD_300UL_FILTER
        else:
            return NimbusTipType.STANDARD_300UL
    elif tip.maximal_volume <= 1100:  # 1000ul tip
        if tip.has_filter:
            return NimbusTipType.HIGH_VOLUME_1000UL_FILTER
        else:
            return NimbusTipType.HIGH_VOLUME_1000UL
    else:
        raise ValueError(
            f"Cannot determine tip type for tip with volume {tip.maximal_volume}µL "
            f"and filter={tip.has_filter}. No matching Hamilton tip type found."
        )


# ============================================================================
# COMMAND CLASSES
# ============================================================================


class LockDoor(HamiltonCommand):
    """Lock door command (DoorLock at 1:1:268, interface_id=1, command_id=1)."""

    protocol = HamiltonProtocol.OBJECT_DISCOVERY
    interface_id = 1
    command_id = 1

    def build_parameters(self) -> HoiParams:
        """Build parameters for LockDoor command."""
        return HoiParams()

    @classmethod
    def parse_response_parameters(cls, data: bytes) -> dict:
        """Parse LockDoor response."""
        return {"success": True}


class UnlockDoor(HamiltonCommand):
    """Unlock door command (DoorLock at 1:1:268, interface_id=1, command_id=2)."""

    protocol = HamiltonProtocol.OBJECT_DISCOVERY
    interface_id = 1
    command_id = 2

    def build_parameters(self) -> HoiParams:
        """Build parameters for UnlockDoor command."""
        return HoiParams()

    @classmethod
    def parse_response_parameters(cls, data: bytes) -> dict:
        """Parse UnlockDoor response."""
        return {"success": True}


class IsDoorLocked(HamiltonCommand):
    """Check if door is locked (DoorLock at 1:1:268, interface_id=1, command_id=3)."""

    protocol = HamiltonProtocol.OBJECT_DISCOVERY
    interface_id = 1
    command_id = 3
    action_code = 0  # Must be 0 (STATUS_REQUEST), default is 3 (COMMAND_REQUEST)

    def build_parameters(self) -> HoiParams:
        """Build parameters for IsDoorLocked command."""
        return HoiParams()

    @classmethod
    def parse_response_parameters(cls, data: bytes) -> dict:
        """Parse IsDoorLocked response."""
        parser = HoiParamsParser(data)
        _, locked = parser.parse_next()
        return {"locked": bool(locked)}


class PreInitializeSmart(HamiltonCommand):
    """Pre-initialize smart command (Pipette at 1:1:257, interface_id=1, command_id=32)."""

    protocol = HamiltonProtocol.OBJECT_DISCOVERY
    interface_id = 1
    command_id = 32

    def build_parameters(self) -> HoiParams:
        """Build parameters for PreInitializeSmart command."""
        return HoiParams()

    @classmethod
    def parse_response_parameters(cls, data: bytes) -> dict:
        """Parse PreInitializeSmart response."""
        return {"success": True}


class InitializeSmartRoll(HamiltonCommand):
    """Initialize smart roll command (NimbusCore at 1:1:48896, interface_id=1, command_id=29)."""

    protocol = HamiltonProtocol.OBJECT_DISCOVERY
    interface_id = 1
    command_id = 29

    def __init__(
        self,
        dest: Address,
        x_positions: List[int],
        y_positions: List[int],
        z_start_positions: List[int],
        z_stop_positions: List[int],
        z_final_positions: List[int],
        roll_distances: List[int],
    ):
        """Initialize InitializeSmartRoll command.

        Args:
            dest: Destination address (NimbusCore)
            x_positions: X positions in 0.01mm units
            y_positions: Y positions in 0.01mm units
            z_start_positions: Z start positions in 0.01mm units
            z_stop_positions: Z stop positions in 0.01mm units
            z_final_positions: Z final positions in 0.01mm units
            roll_distances: Roll distances in 0.01mm units
        """
        super().__init__(dest)
        self.x_positions = x_positions
        self.y_positions = y_positions
        self.z_start_positions = z_start_positions
        self.z_stop_positions = z_stop_positions
        self.z_final_positions = z_final_positions
        self.roll_distances = roll_distances

    def build_parameters(self) -> HoiParams:
        """Build parameters for InitializeSmartRoll command."""
        return (
            HoiParams()
            .i32_array(self.x_positions)
            .i32_array(self.y_positions)
            .i32_array(self.z_start_positions)
            .i32_array(self.z_stop_positions)
            .i32_array(self.z_final_positions)
            .i32_array(self.roll_distances)
        )

    @classmethod
    def parse_response_parameters(cls, data: bytes) -> dict:
        """Parse InitializeSmartRoll response (void return)."""
        return {"success": True}


class IsInitialized(HamiltonCommand):
    """Check if instrument is initialized (NimbusCore at 1:1:48896, interface_id=1, command_id=14)."""

    protocol = HamiltonProtocol.OBJECT_DISCOVERY
    interface_id = 1
    command_id = 14
    action_code = 0  # Must be 0 (STATUS_REQUEST), default is 3 (COMMAND_REQUEST)

    def build_parameters(self) -> HoiParams:
        """Build parameters for IsInitialized command."""
        return HoiParams()

    @classmethod
    def parse_response_parameters(cls, data: bytes) -> dict:
        """Parse IsInitialized response."""
        parser = HoiParamsParser(data)
        _, initialized = parser.parse_next()
        return {"initialized": bool(initialized)}


class IsTipPresent(HamiltonCommand):
    """Check tip presence (Pipette at 1:1:257, interface_id=1, command_id=16)."""

    protocol = HamiltonProtocol.OBJECT_DISCOVERY
    interface_id = 1
    command_id = 16
    action_code = 0

    def build_parameters(self) -> HoiParams:
        """Build parameters for IsTipPresent command."""
        return HoiParams()

    @classmethod
    def parse_response_parameters(cls, data: bytes) -> dict:
        """Parse IsTipPresent response - returns List[i16]."""
        parser = HoiParamsParser(data)
        # Parse array of i16 values representing tip presence per channel
        _, tip_presence = parser.parse_next()
        return {"tip_present": tip_presence}


class GetChannelConfiguration_1(HamiltonCommand):
    """Get channel configuration (NimbusCore root, interface_id=1, command_id=15)."""

    protocol = HamiltonProtocol.OBJECT_DISCOVERY
    interface_id = 1
    command_id = 15
    action_code = 0

    def build_parameters(self) -> HoiParams:
        """Build parameters for GetChannelConfiguration_1 command."""
        return HoiParams()

    @classmethod
    def parse_response_parameters(cls, data: bytes) -> dict:
        """Parse GetChannelConfiguration_1 response.

        Returns: (channels: u16, channel_types: List[i16])
        """
        parser = HoiParamsParser(data)
        _, channels = parser.parse_next()
        _, channel_types = parser.parse_next()
        return {"channels": channels, "channel_types": channel_types}


class SetChannelConfiguration(HamiltonCommand):
    """Set channel configuration (Pipette at 1:1:257, interface_id=1, command_id=67)."""

    protocol = HamiltonProtocol.OBJECT_DISCOVERY
    interface_id = 1
    command_id = 67

    def __init__(
        self,
        dest: Address,
        channel: int,
        indexes: List[int],
        enables: List[bool],
    ):
        """Initialize SetChannelConfiguration command.

        Args:
            dest: Destination address (Pipette)
            channel: Channel number (1-based)
            indexes: List of configuration indexes (e.g., [1, 3, 4])
            1: Tip Recognition, 2: Aspirate and clot monitoring pLLD,
            3: Aspirate monitoring with cLLD, 4: Clot monitoring with cLLD
            enables: List of enable flags (e.g., [True, False, False, False])
        """
        super().__init__(dest)
        self.channel = channel
        self.indexes = indexes
        self.enables = enables

    def build_parameters(self) -> HoiParams:
        """Build parameters for SetChannelConfiguration command."""
        return (
            HoiParams()
            .u16(self.channel)
            .i16_array(self.indexes)
            .bool_array(self.enables)
        )

    @classmethod
    def parse_response_parameters(cls, data: bytes) -> dict:
        """Parse SetChannelConfiguration response (void return)."""
        return {"success": True}


class Park(HamiltonCommand):
    """Park command (Pipette at 1:1:257, interface_id=1, command_id=21)."""

    protocol = HamiltonProtocol.OBJECT_DISCOVERY
    interface_id = 1
    command_id = 21

    def build_parameters(self) -> HoiParams:
        """Build parameters for Park command."""
        return HoiParams()

    @classmethod
    def parse_response_parameters(cls, data: bytes) -> dict:
        """Parse Park response."""
        return {"success": True}


class PickupTips(HamiltonCommand):
    """Pick up tips command (Pipette at 1:1:257, interface_id=1, command_id=4)."""

    protocol = HamiltonProtocol.OBJECT_DISCOVERY
    interface_id = 1
    command_id = 4

    def __init__(
        self,
        dest: Address,
        tips_used: List[int],
        x_positions: List[int],
        y_positions: List[int],
        traverse_height: int,
        z_start_positions: List[int],
        z_stop_positions: List[int],
        tip_types: List[int],
    ):
        """Initialize PickupTips command.

        Args:
            dest: Destination address (Pipette)
            tips_used: Tip pattern (1 for active channels, 0 for inactive)
            x_positions: X positions in 0.01mm units
            y_positions: Y positions in 0.01mm units
            traverse_height: Traverse height in 0.01mm units
            z_start_positions: Z start positions in 0.01mm units
            z_stop_positions: Z stop positions in 0.01mm units
            tip_types: Tip type integers for each channel
        """
        super().__init__(dest)
        self.tips_used = tips_used
        self.x_positions = x_positions
        self.y_positions = y_positions
        self.traverse_height = traverse_height
        self.z_start_positions = z_start_positions
        self.z_stop_positions = z_stop_positions
        self.tip_types = tip_types

    def build_parameters(self) -> HoiParams:
        """Build parameters for PickupTips command."""
        return (
            HoiParams()
            .u16_array(self.tips_used)
            .i32_array(self.x_positions)
            .i32_array(self.y_positions)
            .i32(self.traverse_height)
            .i32_array(self.z_start_positions)
            .i32_array(self.z_stop_positions)
            .u16_array(self.tip_types)
        )

    @classmethod
    def parse_response_parameters(cls, data: bytes) -> dict:
        """Parse PickupTips response (void return)."""
        return {"success": True}


class DropTips(HamiltonCommand):
    """Drop tips command (Pipette at 1:1:257, interface_id=1, command_id=5)."""

    protocol = HamiltonProtocol.OBJECT_DISCOVERY
    interface_id = 1
    command_id = 5

    def __init__(
        self,
        dest: Address,
        tips_used: List[int],
        x_positions: List[int],
        y_positions: List[int],
        traverse_height: int,
        z_start_positions: List[int],
        z_stop_positions: List[int],
        z_final_positions: List[int],
        default_waste: bool,
    ):
        """Initialize DropTips command.

        Args:
            dest: Destination address (Pipette)
            tips_used: Tip pattern (1 for active channels, 0 for inactive)
            x_positions: X positions in 0.01mm units
            y_positions: Y positions in 0.01mm units
            traverse_height: Traverse height in 0.01mm units
            z_start_positions: Z start positions in 0.01mm units
            z_stop_positions: Z stop positions in 0.01mm units
            z_final_positions: Z final positions in 0.01mm units
            default_waste: If True, drop to default waste (positions may be ignored)
        """
        super().__init__(dest)
        self.tips_used = tips_used
        self.x_positions = x_positions
        self.y_positions = y_positions
        self.traverse_height = traverse_height
        self.z_start_positions = z_start_positions
        self.z_stop_positions = z_stop_positions
        self.z_final_positions = z_final_positions
        self.default_waste = default_waste

    def build_parameters(self) -> HoiParams:
        """Build parameters for DropTips command."""
        return (
            HoiParams()
            .u16_array(self.tips_used)
            .i32_array(self.x_positions)
            .i32_array(self.y_positions)
            .i32(self.traverse_height)
            .i32_array(self.z_start_positions)
            .i32_array(self.z_stop_positions)
            .i32_array(self.z_final_positions)
            .bool_value(self.default_waste)
        )

    @classmethod
    def parse_response_parameters(cls, data: bytes) -> dict:
        """Parse DropTips response (void return)."""
        return {"success": True}


class DropTipsRoll(HamiltonCommand):
    """Drop tips with roll command (Pipette at 1:1:257, interface_id=1, command_id=82)."""

    protocol = HamiltonProtocol.OBJECT_DISCOVERY
    interface_id = 1
    command_id = 82

    def __init__(
        self,
        dest: Address,
        tips_used: List[int],
        x_positions: List[int],
        y_positions: List[int],
        traverse_height: int,
        z_start_positions: List[int],
        z_stop_positions: List[int],
        z_final_positions: List[int],
        roll_distances: List[int],
    ):
        """Initialize DropTipsRoll command.

        Args:
            dest: Destination address (Pipette)
            tips_used: Tip pattern (1 for active channels, 0 for inactive)
            x_positions: X positions in 0.01mm units
            y_positions: Y positions in 0.01mm units
            traverse_height: Traverse height in 0.01mm units
            z_start_positions: Z start positions in 0.01mm units
            z_stop_positions: Z stop positions in 0.01mm units
            z_final_positions: Z final positions in 0.01mm units
            roll_distances: Roll distance for each channel in 0.01mm units
        """
        super().__init__(dest)
        self.tips_used = tips_used
        self.x_positions = x_positions
        self.y_positions = y_positions
        self.traverse_height = traverse_height
        self.z_start_positions = z_start_positions
        self.z_stop_positions = z_stop_positions
        self.z_final_positions = z_final_positions
        self.roll_distances = roll_distances

    def build_parameters(self) -> HoiParams:
        """Build parameters for DropTipsRoll command."""
        return (
            HoiParams()
            .u16_array(self.tips_used)
            .i32_array(self.x_positions)
            .i32_array(self.y_positions)
            .i32(self.traverse_height)
            .i32_array(self.z_start_positions)
            .i32_array(self.z_stop_positions)
            .i32_array(self.z_final_positions)
            .i32_array(self.roll_distances)
        )

    @classmethod
    def parse_response_parameters(cls, data: bytes) -> dict:
        """Parse DropTipsRoll response (void return)."""
        return {"success": True}


# ============================================================================
# MAIN BACKEND CLASS
# ============================================================================


class NimbusBackend(TCPBackend, LiquidHandlerBackend):
    """Backend for Hamilton Nimbus liquid handling instruments.

    This backend uses TCP communication with the Hamilton protocol to control
    Nimbus instruments. It inherits from both TCPBackend (for communication)
    and LiquidHandlerBackend (for liquid handling interface).

    Attributes:
        setup_finished: Whether the backend has been set up.
        _num_channels: Cached number of channels (queried from instrument).
        _door_lock_available: Whether door lock is available on this instrument.
    """

    def __init__(
        self,
        host: str,
        port: int = 2000,
        read_timeout: int = 30,
        write_timeout: int = 30,
        buffer_size: int = 1024,
        auto_reconnect: bool = True,
        max_reconnect_attempts: int = 3,
    ):
        """Initialize Nimbus backend.

        Args:
            host: Hamilton instrument IP address
            port: Hamilton instrument port (default: 2000)
            read_timeout: Read timeout in seconds
            write_timeout: Write timeout in seconds
            buffer_size: TCP buffer size
            auto_reconnect: Enable automatic reconnection
            max_reconnect_attempts: Maximum reconnection attempts
        """
        TCPBackend.__init__(
            self,
            host=host,
            port=port,
            read_timeout=read_timeout,
            write_timeout=write_timeout,
            buffer_size=buffer_size,
            auto_reconnect=auto_reconnect,
            max_reconnect_attempts=max_reconnect_attempts,
        )
        LiquidHandlerBackend.__init__(self)

        self._num_channels: Optional[int] = None
        self._pipette_address: Optional[Address] = None
        self._door_lock_address: Optional[Address] = None
        self._nimbus_core_address: Optional[Address] = None
        self._is_initialized: Optional[bool] = None
        self._tips_present: Optional[List[int]] = None

    async def setup(self, unlock_door: bool = False, force_initialize: bool = False):
        """Set up the Nimbus backend.

        This method:
        1. Establishes TCP connection and performs protocol initialization
        2. Discovers instrument objects
        3. Queries channel configuration to get num_channels
        4. Queries tip presence
        5. Queries initialization status
        6. Locks door if available
        7. Conditionally initializes NimbusCore with InitializeSmartRoll (only if not initialized)
        8. Optionally unlocks door after initialization

        Args:
            unlock_door: If True, unlock door after initialization (default: False)
        """
        # Call parent setup (TCP connection, Protocol 7 init, Protocol 3 registration)
        await TCPBackend.setup(self)

        # Ensure deck is set
        assert self._deck is not None, "Deck must be set before setup"

        # Discover instrument objects
        await self._discover_instrument_objects()

        # Ensure required objects are discovered
        if self._pipette_address is None:
            raise RuntimeError(
                "Pipette object not discovered. Cannot proceed with setup."
            )
        if self._nimbus_core_address is None:
            raise RuntimeError(
                "NimbusCore root object not discovered. Cannot proceed with setup."
            )

        # Query channel configuration to get num_channels (use discovered address only)
        try:
            config = await self.send_command(GetChannelConfiguration_1(self._nimbus_core_address))
            self._num_channels = config["channels"]
            logger.info(f"Channel configuration: {config['channels']} channels")
        except Exception as e:
            logger.error(f"Failed to query channel configuration: {e}")
            raise

        # Query tip presence (use discovered address only)
        try:
            tip_status = await self.send_command(IsTipPresent(self._pipette_address))
            tip_present = tip_status.get("tip_present", [])
            self._tips_present = tip_present
            logger.info(f"Tip presence: {tip_present}")
        except Exception as e:
            logger.warning(f"Failed to query tip presence: {e}")

        # Query initialization status (use discovered address only)
        try:
            init_status = await self.send_command(IsInitialized(self._nimbus_core_address))
            self._is_initialized = init_status.get("initialized", False)
            logger.info(f"Instrument initialized: {self._is_initialized}")
        except Exception as e:
            logger.error(f"Failed to query initialization status: {e}")
            raise

        # Lock door if available (optional - no error if not found)
        # This happens before initialization
        if self._door_lock_address is not None:
            try:
                if not await self.is_door_locked():
                    await self.lock_door()
                else:
                    logger.info("Door already locked")
            except RuntimeError:
                # Door lock not available or not set up - this is okay
                logger.warning("Door lock operations skipped (not available or not set up)")
            except Exception as e:
                logger.warning(f"Failed to lock door: {e}")

        # Conditional initialization - only if not already initialized
        if not self._is_initialized or force_initialize:
            # Set channel configuration for each channel (required before InitializeSmartRoll)
            try:
                # Configure all channels (1 to num_channels) - one SetChannelConfiguration call per channel
                # Parameters: channel (1-based), indexes=[1, 3, 4], enables=[True, False, False, False]
                for channel in range(1, self._num_channels + 1):
                    await self.send_command(
                        SetChannelConfiguration(
                            dest=self._pipette_address,
                            channel=channel,
                            indexes=[1, 3, 4],
                            enables=[True, False, False, False],
                        )
                    )
                logger.info(f"Channel configuration set for {self._num_channels} channels")
            except Exception as e:
                logger.error(f"Failed to set channel configuration: {e}")
                raise

            # Initialize NimbusCore with InitializeSmartRoll using waste positions
            try:
                # Build waste position parameters using helper method
                # Use all channels (0 to num_channels-1) for setup
                all_channels = list(range(self._num_channels))
                traverse_height = 146.0  # TODO: Access deck z_max property properly instead of hardcoded literal

                # Use same logic as DropTipsRoll: z_start = waste_z + 4.0mm, z_stop = waste_z, z_final = traverse_height
                waste_params = self._build_waste_position_params(
                    use_channels=all_channels,
                    traverse_height=traverse_height,
                    z_start_offset=None,  # Will be calculated as waste_z + 4.0mm
                    z_stop_offset=None,   # Will be calculated as waste_z
                    z_final_offset=None,  # Will default to traverse_height
                    roll_distance=None,   # Will default to 9.0mm
                )

                await self.send_command(
                    InitializeSmartRoll(
                        dest=self._nimbus_core_address,
                        x_positions=waste_params["x_positions"],
                        y_positions=waste_params["y_positions"],
                        z_start_positions=waste_params["z_start_positions"],
                        z_stop_positions=waste_params["z_stop_positions"],
                        z_final_positions=waste_params["z_final_positions"],
                        roll_distances=waste_params["roll_distances"],
                    )
                )
                logger.info("NimbusCore initialized with InitializeSmartRoll successfully")
                self._is_initialized = True
            except Exception as e:
                logger.error(f"Failed to initialize NimbusCore with InitializeSmartRoll: {e}")
                raise
        else:
            logger.info("Instrument already initialized, skipping initialization")

        # Unlock door if requested (optional - no error if not found)
        if unlock_door and self._door_lock_address is not None:
            try:
                await self.unlock_door()
            except RuntimeError:
                # Door lock not available or not set up - this is okay
                logger.warning("Door unlock requested but not available or not set up")
            except Exception as e:
                logger.warning(f"Failed to unlock door: {e}")

        self.setup_finished = True

    async def _discover_instrument_objects(self):
        """Discover instrument-specific objects using introspection."""
        introspection = HamiltonIntrospection(self)

        # Get root objects (already discovered in setup)
        root_objects = self._discovered_objects.get('root', [])
        if not root_objects:
            logger.warning("No root objects discovered")
            return

        # Use first root object as NimbusCore
        nimbus_core_addr = root_objects[0]
        self._nimbus_core_address = nimbus_core_addr

        try:
            # Get NimbusCore object info
            core_info = await introspection.get_object(nimbus_core_addr)

            # Discover subobjects to find Pipette and DoorLock
            for i in range(core_info.subobject_count):
                try:
                    sub_addr = await introspection.get_subobject_address(nimbus_core_addr, i)
                    sub_info = await introspection.get_object(sub_addr)

                    # Check if this is the Pipette by interface name
                    if sub_info.name == "Pipette":
                        self._pipette_address = sub_addr
                        logger.info(f"Found Pipette at {sub_addr}")

                    # Check if this is the DoorLock by interface name
                    if sub_info.name == "DoorLock":
                        self._door_lock_address = sub_addr
                        logger.info(f"Found DoorLock at {sub_addr}")

                except Exception as e:
                    logger.debug(f"Failed to get subobject {i}: {e}")

        except Exception as e:
            logger.warning(f"Failed to discover instrument objects: {e}")

        # If door lock not found via introspection, it's not available
        if self._door_lock_address is None:
            logger.info("DoorLock not available on this instrument")

    @property
    def num_channels(self) -> int:
        """The number of channels that the robot has."""
        if self._num_channels is None:
            raise RuntimeError(
                "num_channels not set. Call setup() first to query from instrument."
            )
        return self._num_channels

    async def park(self):
        """Park the pipette channels.

        This command moves the pipette channels to their parked position.

        Raises:
            RuntimeError: If pipette address was not discovered during setup.
        """
        if self._pipette_address is None:
            raise RuntimeError(
                "Pipette address not discovered. Call setup() first."
            )

        try:
            await self.send_command(Park(self._pipette_address))
            logger.info("Pipette parked successfully")
        except Exception as e:
            logger.error(f"Failed to park pipette: {e}")
            raise

    async def is_door_locked(self) -> bool:
        """Check if the door is locked.

        Returns:
            True if door is locked, False if unlocked.

        Raises:
            RuntimeError: If door lock is not available on this instrument,
                or if setup() has not been called yet.
        """
        if self._door_lock_address is None:
            raise RuntimeError(
                "Door lock is not available on this instrument or setup() has not been called."
            )

        try:
            status = await self.send_command(IsDoorLocked(self._door_lock_address))
            return bool(status["locked"])
        except Exception as e:
            logger.error(f"Failed to check door lock status: {e}")
            raise

    async def lock_door(self) -> None:
        """Lock the door.

        Raises:
            RuntimeError: If door lock is not available on this instrument,
                or if setup() has not been called yet.
        """
        if self._door_lock_address is None:
            raise RuntimeError(
                "Door lock is not available on this instrument or setup() has not been called."
            )

        try:
            await self.send_command(LockDoor(self._door_lock_address))
            logger.info("Door locked successfully")
        except Exception as e:
            logger.error(f"Failed to lock door: {e}")
            raise

    async def unlock_door(self) -> None:
        """Unlock the door.

        Raises:
            RuntimeError: If door lock is not available on this instrument,
                or if setup() has not been called yet.
        """
        if self._door_lock_address is None:
            raise RuntimeError(
                "Door lock is not available on this instrument or setup() has not been called."
            )

        try:
            await self.send_command(UnlockDoor(self._door_lock_address))
            logger.info("Door unlocked successfully")
        except Exception as e:
            logger.error(f"Failed to unlock door: {e}")
            raise

    async def stop(self):
        """Stop the backend and close connection."""
        await TCPBackend.stop(self)
        self.setup_finished = False

    def _build_waste_position_params(
        self,
        use_channels: List[int],
        traverse_height: float,
        z_start_offset: Optional[float] = None,
        z_stop_offset: Optional[float] = None,
        z_final_offset: Optional[float] = None,
        roll_distance: Optional[float] = None,
    ) -> dict:
        """Build waste position parameters for InitializeSmartRoll or DropTipsRoll.

        Args:
            use_channels: List of channel indices to use
            traverse_height: Traverse height in mm
            z_start_offset: Z start position in mm (absolute, optional, calculated from waste position)
            z_stop_offset: Z stop position in mm (absolute, optional, calculated from waste position)
            z_final_offset: Z final position in mm (absolute, optional, defaults to traverse_height)
            roll_distance: Roll distance in mm (optional, defaults to 9.0 mm)

        Returns:
            Dictionary with x_positions, y_positions, z_start_positions, z_stop_positions,
            z_final_positions, roll_distances (all in 0.01mm units as lists matching num_channels)

        Raises:
            RuntimeError: If deck is not set or waste position not found
        """
        if self._deck is None:
            raise RuntimeError("Deck must be set before building waste position parameters")

        # Validate we have a NimbusDeck for coordinate conversion
        if not isinstance(self._deck, NimbusDeck):
            raise RuntimeError(
                "Deck must be a NimbusDeck for coordinate conversion"
            )

        # Extract coordinates for each channel
        x_positions_mm: List[float] = []
        y_positions_mm: List[float] = []
        z_positions_mm: List[float] = []

        for channel_idx in use_channels:
            # Get waste position from deck based on channel index
            # Use waste_type attribute from deck to construct waste position name
            if not hasattr(self._deck, 'waste_type') or self._deck.waste_type is None:
                raise RuntimeError(
                    f"Deck does not have waste_type attribute or waste_type is None. "
                    f"Cannot determine waste position name for channel {channel_idx}."
                )
            waste_pos_name = f"{self._deck.waste_type}_{channel_idx + 1}"
            try:
                waste_pos = self._deck.get_resource(waste_pos_name)
                abs_location = waste_pos.get_absolute_location()
            except Exception as e:
                raise RuntimeError(
                    f"Failed to get waste position {waste_pos_name} for channel {channel_idx}: {e}"
                )

            # Convert to Hamilton coordinates (returns in mm)
            hamilton_coord = self._deck.to_hamilton_coordinate(abs_location)

            x_positions_mm.append(hamilton_coord.x)
            y_positions_mm.append(hamilton_coord.y)
            z_positions_mm.append(hamilton_coord.z)

        # Convert positions to 0.01mm units (multiply by 100)
        x_positions = [int(round(x * 100)) for x in x_positions_mm]
        y_positions = [int(round(y * 100)) for y in y_positions_mm]

        # Calculate Z positions from waste position coordinates
        max_z_hamilton = max(z_positions_mm)  # Highest waste position Z in Hamilton coordinates
        waste_z_hamilton = max_z_hamilton

        if z_start_offset is None:
            # Calculate from waste position: start above waste position
            z_start_absolute_mm = waste_z_hamilton + 4.0  # Start 4mm above waste position
        else:
            z_start_absolute_mm = z_start_offset

        if z_stop_offset is None:
            # Calculate from waste position: stop at waste position
            z_stop_absolute_mm = waste_z_hamilton  # Stop at waste position
        else:
            z_stop_absolute_mm = z_stop_offset

        if z_final_offset is None:
            z_final_offset_mm = traverse_height  # Use traverse height as final position
        else:
            z_final_offset_mm = z_final_offset

        if roll_distance is None:
            roll_distance_mm = 9.0  # Default roll distance from log
        else:
            roll_distance_mm = roll_distance

        # Use absolute Z positions (same for all channels)
        z_start_positions = [
            int(round(z_start_absolute_mm * 100))
        ] * len(use_channels)  # Absolute Z start position
        z_stop_positions = [
            int(round(z_stop_absolute_mm * 100))
        ] * len(use_channels)  # Absolute Z stop position
        z_final_positions = [
            int(round(z_final_offset_mm * 100))
        ] * len(use_channels)  # Absolute Z final position
        roll_distances = [int(round(roll_distance_mm * 100))] * len(use_channels)

        # Ensure arrays match num_channels length (with zeros for inactive channels)
        x_positions_full = [0] * self.num_channels
        y_positions_full = [0] * self.num_channels
        z_start_positions_full = [0] * self.num_channels
        z_stop_positions_full = [0] * self.num_channels
        z_final_positions_full = [0] * self.num_channels
        roll_distances_full = [0] * self.num_channels

        for i, channel_idx in enumerate(use_channels):
            x_positions_full[channel_idx] = x_positions[i]
            y_positions_full[channel_idx] = y_positions[i]
            z_start_positions_full[channel_idx] = z_start_positions[i]
            z_stop_positions_full[channel_idx] = z_stop_positions[i]
            z_final_positions_full[channel_idx] = z_final_positions[i]
            roll_distances_full[channel_idx] = roll_distances[i]

        return {
            "x_positions": x_positions_full,
            "y_positions": y_positions_full,
            "z_start_positions": z_start_positions_full,
            "z_stop_positions": z_stop_positions_full,
            "z_final_positions": z_final_positions_full,
            "roll_distances": roll_distances_full,
        }

    # ============== Abstract methods from LiquidHandlerBackend ==============

    async def pick_up_tips(
        self,
        ops: List[Pickup],
        use_channels: List[int],
        traverse_height: float = 146.0,  # TODO: Access deck z_max property properly instead of hardcoded literal
        z_start_offset: Optional[float] = None,
        z_stop_offset: Optional[float] = None,
    ):
        """Pick up tips from the specified resource.

        Z positions and traverse height are calculated from the resource locations and tip
        properties if not explicitly provided:
        - traverse_height: Uses deck z_max if not provided
        - z_start_offset: Calculated as max(resource Z) + max(tip total_tip_length)
        - z_stop_offset: Calculated as max(resource Z) + max(tip total_tip_length - tip fitting_depth)

        Args:
            ops: List of Pickup operations, one per channel
            use_channels: List of channel indices to use
            traverse_height: Traverse height in mm (optional, defaults to deck z_max)
            z_start_offset: Z start position in mm (absolute, optional, calculated from resources)
            z_stop_offset: Z stop position in mm (absolute, optional, calculated from resources)

        Raises:
            RuntimeError: If pipette address or deck is not set
            ValueError: If deck is not a NimbusDeck and traverse_height is not provided
        """
        if self._pipette_address is None:
            raise RuntimeError(
                "Pipette address not discovered. Call setup() first."
            )
        if self._deck is None:
            raise RuntimeError("Deck must be set before pick_up_tips")

        # Validate we have a NimbusDeck for coordinate conversion
        if not isinstance(self._deck, NimbusDeck):
            raise RuntimeError(
                "Deck must be a NimbusDeck for coordinate conversion"
            )

        # Extract coordinates and tip types for each operation
        x_positions_mm: List[float] = []
        y_positions_mm: List[float] = []
        z_positions_mm: List[float] = []
        tip_types: List[int] = []

        for op in ops:
            # Get absolute location from resource
            abs_location = op.resource.get_absolute_location()
            # Add offset
            final_location = Coordinate(
                x=abs_location.x + op.offset.x,
                y=abs_location.y + op.offset.y,
                z=abs_location.z + op.offset.z,
            )
            # Convert to Hamilton coordinates (returns in mm)
            hamilton_coord = self._deck.to_hamilton_coordinate(final_location)

            x_positions_mm.append(hamilton_coord.x)
            y_positions_mm.append(hamilton_coord.y)
            z_positions_mm.append(hamilton_coord.z)

            # Get tip type from tip object
            tip_type = _get_tip_type_from_tip(op.tip)
            tip_types.append(tip_type)

        # Build tip pattern array (1 for active channels, 0 for inactive)
        # Array length should match num_channels
        tips_used = [0] * self.num_channels
        for channel_idx in use_channels:
            if channel_idx >= self.num_channels:
                raise ValueError(
                    f"Channel index {channel_idx} exceeds num_channels {self.num_channels}"
                )
            tips_used[channel_idx] = 1

        # Convert positions to 0.01mm units (multiply by 100)
        x_positions = [int(round(x * 100)) for x in x_positions_mm]
        y_positions = [int(round(y * 100)) for y in y_positions_mm]

        # Calculate Z positions from resource locations and tip properties
        # Similar to STAR backend: z_start = max_z + max_total_tip_length, z_stop = max_z + max_tip_length
        max_z_hamilton = max(z_positions_mm)  # Highest resource Z in Hamilton coordinates
        max_total_tip_length = max(op.tip.total_tip_length for op in ops)
        max_tip_length = max((op.tip.total_tip_length - op.tip.fitting_depth) for op in ops)

        # Calculate absolute Z positions in Hamilton coordinates
        # z_start: resource Z + total tip length (where tip pickup starts)
        # z_stop: resource Z + (tip length - fitting depth) (where tip pickup stops)
        z_start_absolute_mm = max_z_hamilton + max_total_tip_length
        z_stop_absolute_mm = max_z_hamilton + max_tip_length

        # Traverse height: use provided value (defaults to 146.0 mm from function signature)
        traverse_height_mm = traverse_height

        # Allow override of Z positions if explicitly provided
        if z_start_offset is not None:
            z_start_absolute_mm = z_start_offset
        if z_stop_offset is not None:
            z_stop_absolute_mm = z_stop_offset

        # Convert to 0.01mm units
        traverse_height_units = int(round(traverse_height_mm * 100))

        # For Z positions, use absolute positions (same for all channels)
        z_start_positions = [
            int(round(z_start_absolute_mm * 100))
        ] * len(ops)  # Absolute Z start position
        z_stop_positions = [
            int(round(z_stop_absolute_mm * 100))
        ] * len(ops)  # Absolute Z stop position

        # Ensure arrays match num_channels length (pad with 0s for inactive channels)
        # We need to map use_channels to the correct positions
        x_positions_full = [0] * self.num_channels
        y_positions_full = [0] * self.num_channels
        z_start_positions_full = [0] * self.num_channels
        z_stop_positions_full = [0] * self.num_channels
        tip_types_full = [0] * self.num_channels

        for i, channel_idx in enumerate(use_channels):
            x_positions_full[channel_idx] = x_positions[i]
            y_positions_full[channel_idx] = y_positions[i]
            z_start_positions_full[channel_idx] = z_start_positions[i]
            z_stop_positions_full[channel_idx] = z_stop_positions[i]
            tip_types_full[channel_idx] = tip_types[i]

        # Create and send command
        command = PickupTips(
            dest=self._pipette_address,
            tips_used=tips_used,
            x_positions=x_positions_full,
            y_positions=y_positions_full,
            traverse_height=traverse_height_units,
            z_start_positions=z_start_positions_full,
            z_stop_positions=z_stop_positions_full,
            tip_types=tip_types_full,
        )

        # Check tip presence before picking up tips
        try:
            tip_status = await self.send_command(IsTipPresent(self._pipette_address))
            tip_present = tip_status.get("tip_present", [])
            # Check if any channels we're trying to use already have tips
            channels_with_tips = [
                i for i, present in enumerate(tip_present)
                if i in use_channels and present != 0
            ]
            if channels_with_tips:
                raise RuntimeError(
                    f"Cannot pick up tips: channels {channels_with_tips} already have tips mounted. "
                    f"Drop existing tips first."
                )
        except Exception as e:
            # If tip presence check fails, log warning but continue
            logger.warning(f"Could not check tip presence before pickup: {e}")

        # Log parameters for debugging
        logger.info("PickupTips parameters:")
        logger.info(f"  tips_used: {tips_used}")
        logger.info(f"  x_positions: {x_positions_full}")
        logger.info(f"  y_positions: {y_positions_full}")
        logger.info(f"  traverse_height: {traverse_height_units}")
        logger.info(f"  z_start_positions: {z_start_positions_full}")
        logger.info(f"  z_stop_positions: {z_stop_positions_full}")
        logger.info(f"  tip_types: {tip_types_full}")
        logger.info(f"  num_channels: {self.num_channels}")

        try:
            await self.send_command(command)
            logger.info(f"Picked up tips on channels {use_channels}")
        except Exception as e:
            logger.error(f"Failed to pick up tips: {e}")
            logger.error(f"Parameters sent: tips_used={tips_used}, "
                        f"x_positions={x_positions_full}, y_positions={y_positions_full}, "
                        f"traverse_height={traverse_height_units}, "
                        f"z_start_positions={z_start_positions_full}, "
                        f"z_stop_positions={z_stop_positions_full}, tip_types={tip_types_full}")
            raise

    async def drop_tips(
        self,
        ops: List[Drop],
        use_channels: List[int],
        default_waste: bool = False,
        traverse_height: float = 146.0,  # TODO: Access deck z_max property properly instead of hardcoded literal
        z_start_offset: Optional[float] = None,
        z_stop_offset: Optional[float] = None,
        z_final_offset: Optional[float] = None,
        roll_distance: Optional[float] = None,
    ):
        """Drop tips to the specified resource.

        Auto-detects waste positions and uses appropriate command:
        - If resource is a waste position (Trash with category="waste_position"), uses DropTipsRoll
        - Otherwise, uses DropTips command

        Z positions are calculated from resource locations if not explicitly provided:
        - traverse_height: Defaults to 146.0 mm (deck z_max)
        - z_start_offset: Calculated from resources (for waste: 135.39 mm, for regular: resource Z + offset)
        - z_stop_offset: Calculated from resources (for waste: 131.39 mm, for regular: resource Z + offset)
        - z_final_offset: Calculated from resources (defaults to traverse_height)
        - roll_distance: Defaults to 9.0 mm for waste positions

        Args:
            ops: List of Drop operations, one per channel
            use_channels: List of channel indices to use
            default_waste: For DropTips command, if True, drop to default waste (positions may be ignored)
            traverse_height: Traverse height in mm (optional, defaults to 146.0 mm)
            z_start_offset: Z start position in mm (absolute, optional, calculated from resources)
            z_stop_offset: Z stop position in mm (absolute, optional, calculated from resources)
            z_final_offset: Z final position in mm (absolute, optional, calculated from resources)
            roll_distance: Roll distance in mm (optional, defaults to 9.0 mm for waste positions)

        Raises:
            RuntimeError: If pipette address or deck is not set
            ValueError: If operations mix waste and regular resources
        """
        if self._pipette_address is None:
            raise RuntimeError(
                "Pipette address not discovered. Call setup() first."
            )
        if self._deck is None:
            raise RuntimeError("Deck must be set before drop_tips")

        # Validate we have a NimbusDeck for coordinate conversion
        if not isinstance(self._deck, NimbusDeck):
            raise RuntimeError(
                "Deck must be a NimbusDeck for coordinate conversion"
            )

        # Check if resources are waste positions (Trash objects with category="waste_position")
        is_waste_positions = [
            isinstance(op.resource, Trash) and getattr(op.resource, "category", None) == "waste_position"
            for op in ops
        ]

        # Check if all operations are waste positions or all are regular
        all_waste = all(is_waste_positions)
        all_regular = not any(is_waste_positions)

        if not (all_waste or all_regular):
            raise ValueError(
                "Cannot mix waste positions and regular resources in a single drop_tips call. "
                "All operations must be either waste positions or regular resources."
            )

        # Build tip pattern array (1 for active channels, 0 for inactive)
        tips_used = [0] * self.num_channels
        for channel_idx in use_channels:
            if channel_idx >= self.num_channels:
                raise ValueError(
                    f"Channel index {channel_idx} exceeds num_channels {self.num_channels}"
                )
            tips_used[channel_idx] = 1

        # Traverse height: use provided value (defaults to 146.0 mm from function signature)
        traverse_height_mm = traverse_height

        # Convert to 0.01mm units
        traverse_height_units = int(round(traverse_height_mm * 100))

        # Type annotation for command variable (can be either DropTips or DropTipsRoll)
        command: Union[DropTips, DropTipsRoll]

        if all_waste:
            # Use DropTipsRoll for waste positions
            # Build waste position parameters using helper method
            waste_params = self._build_waste_position_params(
                use_channels=use_channels,
                traverse_height=traverse_height_mm,
                z_start_offset=z_start_offset,
                z_stop_offset=z_stop_offset,
                z_final_offset=z_final_offset,
                roll_distance=roll_distance,
            )

            x_positions_full = waste_params["x_positions"]
            y_positions_full = waste_params["y_positions"]
            z_start_positions_full = waste_params["z_start_positions"]
            z_stop_positions_full = waste_params["z_stop_positions"]
            z_final_positions_full = waste_params["z_final_positions"]
            roll_distances_full = waste_params["roll_distances"]

            # Create and send DropTipsRoll command
            command = DropTipsRoll(
                dest=self._pipette_address,
                tips_used=tips_used,
                x_positions=x_positions_full,
                y_positions=y_positions_full,
                traverse_height=traverse_height_units,
                z_start_positions=z_start_positions_full,
                z_stop_positions=z_stop_positions_full,
                z_final_positions=z_final_positions_full,
                roll_distances=roll_distances_full,
            )
        else:
            # Use DropTips for regular resources
            # Extract coordinates for each operation
            x_positions_mm: List[float] = []
            y_positions_mm: List[float] = []
            z_positions_mm: List[float] = []

            for i, op in enumerate(ops):
                # Get absolute location from resource
                abs_location = op.resource.get_absolute_location()

                # Add offset
                final_location = Coordinate(
                    x=abs_location.x + op.offset.x,
                    y=abs_location.y + op.offset.y,
                    z=abs_location.z + op.offset.z,
                )
                # Convert to Hamilton coordinates (returns in mm)
                hamilton_coord = self._deck.to_hamilton_coordinate(final_location)

                x_positions_mm.append(hamilton_coord.x)
                y_positions_mm.append(hamilton_coord.y)
                z_positions_mm.append(hamilton_coord.z)

            # Convert positions to 0.01mm units (multiply by 100)
            x_positions = [int(round(x * 100)) for x in x_positions_mm]
            y_positions = [int(round(y * 100)) for y in y_positions_mm]

            # Calculate Z positions from resource locations
            max_z_hamilton = max(z_positions_mm)  # Highest resource Z in Hamilton coordinates

            # Z positions are absolute, not relative to resource position
            # Calculate from resource locations if not provided
            if z_start_offset is None:
                # TODO: Calculate from resources properly (resource Z + offset)
                z_start_absolute_mm = max_z_hamilton + 10.0  # Placeholder: resource Z + safety margin
            else:
                z_start_absolute_mm = z_start_offset

            if z_stop_offset is None:
                # TODO: Calculate from resources properly (resource Z + offset)
                z_stop_absolute_mm = max_z_hamilton  # Placeholder: resource Z
            else:
                z_stop_absolute_mm = z_stop_offset

            if z_final_offset is None:
                z_final_offset_mm = traverse_height_mm  # Use traverse height as final position
            else:
                z_final_offset_mm = z_final_offset

            # Use absolute Z positions (same for all channels)
            z_start_positions = [
                int(round(z_start_absolute_mm * 100))
            ] * len(ops)  # Absolute Z start position
            z_stop_positions = [
                int(round(z_stop_absolute_mm * 100))
            ] * len(ops)  # Absolute Z stop position
            z_final_positions = [
                int(round(z_final_offset_mm * 100))
            ] * len(ops)  # Absolute Z final position

            # Ensure arrays match num_channels length
            x_positions_full = [0] * self.num_channels
            y_positions_full = [0] * self.num_channels
            z_start_positions_full = [0] * self.num_channels
            z_stop_positions_full = [0] * self.num_channels
            z_final_positions_full = [0] * self.num_channels

            for i, channel_idx in enumerate(use_channels):
                x_positions_full[channel_idx] = x_positions[i]
                y_positions_full[channel_idx] = y_positions[i]
                z_start_positions_full[channel_idx] = z_start_positions[i]
                z_stop_positions_full[channel_idx] = z_stop_positions[i]
                z_final_positions_full[channel_idx] = z_final_positions[i]

            # Create and send DropTips command
            command = DropTips(
                dest=self._pipette_address,
                tips_used=tips_used,
                x_positions=x_positions_full,
                y_positions=y_positions_full,
                traverse_height=traverse_height_units,
                z_start_positions=z_start_positions_full,
                z_stop_positions=z_stop_positions_full,
                z_final_positions=z_final_positions_full,
                default_waste=default_waste,
            )

        try:
            await self.send_command(command)
            logger.info(f"Dropped tips on channels {use_channels}")
        except Exception as e:
            logger.error(f"Failed to drop tips: {e}")
            raise

    async def aspirate(
        self, ops: List[SingleChannelAspiration], use_channels: List[int]
    ):
        """Aspirate liquid from the specified resource using pip."""
        raise NotImplementedError("aspirate not yet implemented")

    async def dispense(
        self, ops: List[SingleChannelDispense], use_channels: List[int]
    ):
        """Dispense liquid from the specified resource using pip."""
        raise NotImplementedError("dispense not yet implemented")

    async def pick_up_tips96(self, pickup: PickupTipRack):
        """Pick up tips from the specified resource using CoRe 96."""
        raise NotImplementedError("pick_up_tips96 not yet implemented")

    async def drop_tips96(self, drop: DropTipRack):
        """Drop tips to the specified resource using CoRe 96."""
        raise NotImplementedError("drop_tips96 not yet implemented")

    async def aspirate96(
        self, aspiration: MultiHeadAspirationPlate | MultiHeadAspirationContainer
    ):
        """Aspirate from all wells in 96 well plate."""
        raise NotImplementedError("aspirate96 not yet implemented")

    async def dispense96(
        self, dispense: MultiHeadDispensePlate | MultiHeadDispenseContainer
    ):
        """Dispense to all wells in 96 well plate."""
        raise NotImplementedError("dispense96 not yet implemented")

    async def pick_up_resource(self, pickup: ResourcePickup):
        """Pick up a resource like a plate or a lid using the integrated robotic arm."""
        raise NotImplementedError("pick_up_resource not yet implemented")

    async def move_picked_up_resource(self, move: ResourceMove):
        """Move a picked up resource like a plate or a lid using the integrated robotic arm."""
        raise NotImplementedError("move_picked_up_resource not yet implemented")

    async def drop_resource(self, drop: ResourceDrop):
        """Drop a resource like a plate or a lid using the integrated robotic arm."""
        raise NotImplementedError("drop_resource not yet implemented")

    def can_pick_up_tip(self, channel_idx: int, tip: Tip) -> bool:
        """Check if the tip can be picked up by the specified channel.

        Args:
            channel_idx: Channel index (0-based)
            tip: Tip object to check

        Returns:
            True if the tip can be picked up, False otherwise
        """
        # Only Hamilton tips are supported
        if not isinstance(tip, HamiltonTip):
            return False

        # XL tips are not supported on Nimbus
        if tip.tip_size in {TipSize.XL}:
            return False

        # Check if channel index is valid
        if self._num_channels is not None and channel_idx >= self._num_channels:
            return False

        return True

