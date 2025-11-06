"""Hamilton Nimbus backend implementation.

This module provides the NimbusBackend class for controlling Hamilton Nimbus
instruments via TCP communication using the Hamilton protocol.
"""

from __future__ import annotations

import logging
from typing import List, Optional

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

logger = logging.getLogger(__name__)


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

    async def setup(self, unlock_door: bool = False):
        """Set up the Nimbus backend.

        This method:
        1. Establishes TCP connection and performs protocol initialization
        2. Detects if door lock exists
        3. Locks door if available
        4. Pre-initializes pipette
        5. Queries tip presence
        6. Queries channel configuration to get num_channels
        7. Optionally unlocks door after pre-initialization

        Args:
            unlock_door: If True, unlock door after pre-initialization (default: False)
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

        # Lock door if available (optional - no error if not found)
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

        # Pre-initialize pipette (use discovered address only)
        try:
            await self.send_command(PreInitializeSmart(self._pipette_address))
            logger.info("Pipette pre-initialized successfully")
        except Exception as e:
            logger.error(f"Failed to pre-initialize pipette: {e}")
            raise

        # Query tip presence (use discovered address only)
        try:
            tip_status = await self.send_command(IsTipPresent(self._pipette_address))
            tip_present = tip_status.get("tip_present", [])
            logger.info(f"Tip presence: {tip_present}")
        except Exception as e:
            logger.warning(f"Failed to query tip presence: {e}")

        # Query channel configuration to get num_channels (use discovered address only)
        try:
            config = await self.send_command(GetChannelConfiguration_1(self._nimbus_core_address))
            self._num_channels = config["channels"]
            logger.info(f"Channel configuration: {config['channels']} channels")
        except Exception as e:
            logger.error(f"Failed to query channel configuration: {e}")
            raise

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

    # ============== Abstract methods from LiquidHandlerBackend ==============

    async def pick_up_tips(self, ops: List[Pickup], use_channels: List[int]):
        """Pick up tips from the specified resource."""
        raise NotImplementedError("pick_up_tips not yet implemented")

    async def drop_tips(self, ops: List[Drop], use_channels: List[int]):
        """Drop tips from the specified resource."""
        raise NotImplementedError("drop_tips not yet implemented")

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
        """Check if the tip can be picked up by the specified channel."""
        raise NotImplementedError("can_pick_up_tip not yet implemented")

