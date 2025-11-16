"""Hamilton Nimbus backend implementation.

This module provides the NimbusBackend class for controlling Hamilton Nimbus
instruments via TCP communication using the Hamilton protocol.
"""

from __future__ import annotations

import enum
import logging
from typing import Dict, List, Optional, TypeVar, Union

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
from pylabrobot.resources.container import Container
from pylabrobot.resources.hamilton import HamiltonTip, TipSize
from pylabrobot.resources.hamilton.nimbus_decks import NimbusDeck
from pylabrobot.resources.trash import Trash
from pylabrobot.resources.well import Well

logger = logging.getLogger(__name__)

T = TypeVar("T")


# ============================================================================
# HELPER FUNCTIONS
# ============================================================================


def _fill_in_defaults(val: Optional[List[T]], default: List[T]) -> List[T]:
    """Util for converting an argument to the appropriate format for low level methods.

    Args:
        val: Optional list of values (None means use default)
        default: Default list of values

    Returns:
        List of values with defaults filled in

    Raises:
        ValueError: If val is provided but length doesn't match default length
    """
    # if the val is None, use the default.
    if val is None:
        return default
    # if the val is a list, it must be of the correct length.
    if len(val) != len(default):
        raise ValueError(
            f"Value length must equal num operations ({len(default)}), but is {len(val)}"
        )
    # replace None values in list with default values.
    val = [v if v is not None else d for v, d in zip(val, default)]
    # the value is ready to be used.
    return val


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
        self._assign_params()

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
        self._assign_params()

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
    """Park command (NimbusCore at 1:1:48896, interface_id=1, command_id=3)."""

    protocol = HamiltonProtocol.OBJECT_DISCOVERY
    interface_id = 1
    command_id = 3

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
        self._assign_params()

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
        self._assign_params()

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
        self._assign_params()

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


class EnableADC(HamiltonCommand):
    """Enable ADC command (Pipette at 1:1:257, interface_id=1, command_id=43)."""

    protocol = HamiltonProtocol.OBJECT_DISCOVERY
    interface_id = 1
    command_id = 43

    def __init__(
        self,
        dest: Address,
        tips_used: List[int],
    ):
        """Initialize EnableADC command.

        Args:
            dest: Destination address (Pipette)
            tips_used: Tip pattern (1 for active channels, 0 for inactive)
        """
        super().__init__(dest)
        self._assign_params()

    def build_parameters(self) -> HoiParams:
        """Build parameters for EnableADC command."""
        return HoiParams().u16_array(self.tips_used)

    @classmethod
    def parse_response_parameters(cls, data: bytes) -> dict:
        """Parse EnableADC response (void return)."""
        return {"success": True}


class DisableADC(HamiltonCommand):
    """Disable ADC command (Pipette at 1:1:257, interface_id=1, command_id=44)."""

    protocol = HamiltonProtocol.OBJECT_DISCOVERY
    interface_id = 1
    command_id = 44

    def __init__(
        self,
        dest: Address,
        tips_used: List[int],
    ):
        """Initialize DisableADC command.

        Args:
            dest: Destination address (Pipette)
            tips_used: Tip pattern (1 for active channels, 0 for inactive)
        """
        super().__init__(dest)
        self._assign_params()

    def build_parameters(self) -> HoiParams:
        """Build parameters for DisableADC command."""
        return HoiParams().u16_array(self.tips_used)

    @classmethod
    def parse_response_parameters(cls, data: bytes) -> dict:
        """Parse DisableADC response (void return)."""
        return {"success": True}


class GetChannelConfiguration(HamiltonCommand):
    """Get channel configuration command (Pipette at 1:1:257, interface_id=1, command_id=66)."""

    protocol = HamiltonProtocol.OBJECT_DISCOVERY
    interface_id = 1
    command_id = 66
    action_code = 0  # Must be 0 (STATUS_REQUEST), default is 3 (COMMAND_REQUEST)

    def __init__(
        self,
        dest: Address,
        channel: int,
        indexes: List[int],
    ):
        """Initialize GetChannelConfiguration command.

        Args:
            dest: Destination address (Pipette)
            channel: Channel number (1-based)
            indexes: List of configuration indexes (e.g., [2] for "Aspirate monitoring with cLLD")
        """
        super().__init__(dest)
        self._assign_params()

    def build_parameters(self) -> HoiParams:
        """Build parameters for GetChannelConfiguration command."""
        return (
            HoiParams()
            .u16(self.channel)
            .i16_array(self.indexes)
        )

    @classmethod
    def parse_response_parameters(cls, data: bytes) -> dict:
        """Parse GetChannelConfiguration response.

        Returns: { enabled: List[bool] }
        """
        parser = HoiParamsParser(data)
        _, enabled = parser.parse_next()
        return {"enabled": enabled}


class Aspirate(HamiltonCommand):
    """Aspirate command (Pipette at 1:1:257, interface_id=1, command_id=6)."""

    protocol = HamiltonProtocol.OBJECT_DISCOVERY
    interface_id = 1
    command_id = 6

    def __init__(
        self,
        dest: Address,
        aspirate_type: List[int],
        tips_used: List[int],
        x_positions: List[int],
        y_positions: List[int],
        traverse_height: int,
        liquid_seek_height: List[int],
        liquid_surface_height: List[int],
        submerge_depth: List[int],
        follow_depth: List[int],
        z_min_position: List[int],
        clot_check_height: List[int],
        z_final: int,
        liquid_exit_speed: List[int],
        blowout_volume: List[int],
        prewet_volume: List[int],
        aspirate_volume: List[int],
        transport_air_volume: List[int],
        aspirate_speed: List[int],
        settling_time: List[int],
        mix_volume: List[int],
        mix_cycles: List[int],
        mix_position: List[int],
        mix_follow_distance: List[int],
        mix_speed: List[int],
        tube_section_height: List[int],
        tube_section_ratio: List[int],
        lld_mode: List[int],
        capacitive_lld_sensitivity: List[int],
        pressure_lld_sensitivity: List[int],
        lld_height_difference: List[int],
        tadm_enabled: bool,
        limit_curve_index: List[int],
        recording_mode: int,
    ):
        """Initialize Aspirate command.

        Args:
            dest: Destination address (Pipette)
            aspirate_type: Aspirate type for each channel (List[i16])
            tips_used: Tip pattern (1 for active channels, 0 for inactive)
            x_positions: X positions in 0.01mm units
            y_positions: Y positions in 0.01mm units
            traverse_height: Traverse height in 0.01mm units
            liquid_seek_height: Liquid seek height for each channel in 0.01mm units
            liquid_surface_height: Liquid surface height for each channel in 0.01mm units
            submerge_depth: Submerge depth for each channel in 0.01mm units
            follow_depth: Follow depth for each channel in 0.01mm units
            z_min_position: Z minimum position for each channel in 0.01mm units
            clot_check_height: Clot check height for each channel in 0.01mm units
            z_final: Z final position in 0.01mm units
            liquid_exit_speed: Liquid exit speed for each channel in 0.1µL/s units
            blowout_volume: Blowout volume for each channel in 0.1µL units
            prewet_volume: Prewet volume for each channel in 0.1µL units
            aspirate_volume: Aspirate volume for each channel in 0.1µL units
            transport_air_volume: Transport air volume for each channel in 0.1µL units
            aspirate_speed: Aspirate speed for each channel in 0.1µL/s units
            settling_time: Settling time for each channel in 0.1s units
            mix_volume: Mix volume for each channel in 0.1µL units
            mix_cycles: Mix cycles for each channel
            mix_position: Mix position for each channel in 0.01mm units
            mix_follow_distance: Mix follow distance for each channel in 0.01mm units
            mix_speed: Mix speed for each channel in 0.1µL/s units
            tube_section_height: Tube section height for each channel in 0.01mm units
            tube_section_ratio: Tube section ratio for each channel
            lld_mode: LLD mode for each channel (List[i16])
            capacitive_lld_sensitivity: Capacitive LLD sensitivity for each channel (List[i16])
            pressure_lld_sensitivity: Pressure LLD sensitivity for each channel (List[i16])
            lld_height_difference: LLD height difference for each channel in 0.01mm units
            tadm_enabled: TADM enabled flag
            limit_curve_index: Limit curve index for each channel
            recording_mode: Recording mode (u16)
        """
        super().__init__(dest)
        self._assign_params()

    def build_parameters(self) -> HoiParams:
        """Build parameters for Aspirate command."""
        return (
            HoiParams()
            .i16_array(self.aspirate_type)
            .u16_array(self.tips_used)
            .i32_array(self.x_positions)
            .i32_array(self.y_positions)
            .i32(self.traverse_height)
            .i32_array(self.liquid_seek_height)
            .i32_array(self.liquid_surface_height)
            .i32_array(self.submerge_depth)
            .i32_array(self.follow_depth)
            .i32_array(self.z_min_position)
            .i32_array(self.clot_check_height)
            .i32(self.z_final)
            .u32_array(self.liquid_exit_speed)
            .u32_array(self.blowout_volume)
            .u32_array(self.prewet_volume)
            .u32_array(self.aspirate_volume)
            .u32_array(self.transport_air_volume)
            .u32_array(self.aspirate_speed)
            .u32_array(self.settling_time)
            .u32_array(self.mix_volume)
            .u32_array(self.mix_cycles)
            .i32_array(self.mix_position)
            .i32_array(self.mix_follow_distance)
            .u32_array(self.mix_speed)
            .i32_array(self.tube_section_height)
            .i32_array(self.tube_section_ratio)
            .i16_array(self.lld_mode)
            .i16_array(self.capacitive_lld_sensitivity)
            .i16_array(self.pressure_lld_sensitivity)
            .i32_array(self.lld_height_difference)
            .bool_value(self.tadm_enabled)
            .u32_array(self.limit_curve_index)
            .u16(self.recording_mode)
        )

    @classmethod
    def parse_response_parameters(cls, data: bytes) -> dict:
        """Parse Aspirate response (void return)."""
        return {"success": True}


class Dispense(HamiltonCommand):
    """Dispense command (Pipette at 1:1:257, interface_id=1, command_id=7)."""

    protocol = HamiltonProtocol.OBJECT_DISCOVERY
    interface_id = 1
    command_id = 7

    def __init__(
        self,
        dest: Address,
        dispense_type: List[int],
        tips_used: List[int],
        x_positions: List[int],
        y_positions: List[int],
        traverse_height: int,
        liquid_seek_height: List[int],
        dispense_height: List[int],
        submerge_depth: List[int],
        follow_depth: List[int],
        z_min_position: List[int],
        z_final: int,
        liquid_exit_speed: List[int],
        transport_air_volume: List[int],
        dispense_volume: List[int],
        stop_back_volume: List[int],
        blowout_volume: List[int],
        dispense_speed: List[int],
        cutoff_speed: List[int],
        settling_time: List[int],
        mix_volume: List[int],
        mix_cycles: List[int],
        mix_position: List[int],
        mix_follow_distance: List[int],
        mix_speed: List[int],
        touch_off_distance: int,
        dispense_offset: List[int],
        tube_section_height: List[int],
        tube_section_ratio: List[int],
        lld_mode: List[int],
        capacitive_lld_sensitivity: List[int],
        tadm_enabled: bool,
        limit_curve_index: List[int],
        recording_mode: int,
    ):
        """Initialize Dispense command.

        Args:
            dest: Destination address (Pipette)
            dispense_type: Dispense type for each channel (List[i16])
            tips_used: Tip pattern (1 for active channels, 0 for inactive)
            x_positions: X positions in 0.01mm units
            y_positions: Y positions in 0.01mm units
            traverse_height: Traverse height in 0.01mm units
            liquid_seek_height: Liquid seek height for each channel in 0.01mm units
            dispense_height: Dispense height for each channel in 0.01mm units
            submerge_depth: Submerge depth for each channel in 0.01mm units
            follow_depth: Follow depth for each channel in 0.01mm units
            z_min_position: Z minimum position for each channel in 0.01mm units
            z_final: Z final position in 0.01mm units
            liquid_exit_speed: Liquid exit speed for each channel in 0.1µL/s units
            transport_air_volume: Transport air volume for each channel in 0.1µL units
            dispense_volume: Dispense volume for each channel in 0.1µL units
            stop_back_volume: Stop back volume for each channel in 0.1µL units
            blowout_volume: Blowout volume for each channel in 0.1µL units
            dispense_speed: Dispense speed for each channel in 0.1µL/s units
            cutoff_speed: Cutoff speed for each channel in 0.1µL/s units
            settling_time: Settling time for each channel in 0.1s units
            mix_volume: Mix volume for each channel in 0.1µL units
            mix_cycles: Mix cycles for each channel
            mix_position: Mix position for each channel in 0.01mm units
            mix_follow_distance: Mix follow distance for each channel in 0.01mm units
            mix_speed: Mix speed for each channel in 0.1µL/s units
            touch_off_distance: Touch off distance in 0.01mm units
            dispense_offset: Dispense offset for each channel in 0.01mm units
            tube_section_height: Tube section height for each channel in 0.01mm units
            tube_section_ratio: Tube section ratio for each channel
            lld_mode: LLD mode for each channel (List[i16])
            capacitive_lld_sensitivity: Capacitive LLD sensitivity for each channel (List[i16])
            tadm_enabled: TADM enabled flag
            limit_curve_index: Limit curve index for each channel
            recording_mode: Recording mode (u16)
        """
        super().__init__(dest)
        self._assign_params()

    def build_parameters(self) -> HoiParams:
        """Build parameters for Dispense command."""
        return (
            HoiParams()
            .i16_array(self.dispense_type)
            .u16_array(self.tips_used)
            .i32_array(self.x_positions)
            .i32_array(self.y_positions)
            .i32(self.traverse_height)
            .i32_array(self.liquid_seek_height)
            .i32_array(self.dispense_height)
            .i32_array(self.submerge_depth)
            .i32_array(self.follow_depth)
            .i32_array(self.z_min_position)
            .i32(self.z_final)
            .u32_array(self.liquid_exit_speed)
            .u32_array(self.transport_air_volume)
            .u32_array(self.dispense_volume)
            .u32_array(self.stop_back_volume)
            .u32_array(self.blowout_volume)
            .u32_array(self.dispense_speed)
            .u32_array(self.cutoff_speed)
            .u32_array(self.settling_time)
            .u32_array(self.mix_volume)
            .u32_array(self.mix_cycles)
            .i32_array(self.mix_position)
            .i32_array(self.mix_follow_distance)
            .u32_array(self.mix_speed)
            .i32(self.touch_off_distance)
            .i32_array(self.dispense_offset)
            .i32_array(self.tube_section_height)
            .i32_array(self.tube_section_ratio)
            .i16_array(self.lld_mode)
            .i16_array(self.capacitive_lld_sensitivity)
            .bool_value(self.tadm_enabled)
            .u32_array(self.limit_curve_index)
            .u16(self.recording_mode)
        )

    @classmethod
    def parse_response_parameters(cls, data: bytes) -> dict:
        """Parse Dispense response (void return)."""
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
        self._channel_configurations: Optional[Dict[int, Dict[int, bool]]] = None

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
        """Park the instrument.

        This command moves the instrument to its parked position.

        Raises:
            RuntimeError: If NimbusCore address was not discovered during setup.
        """
        if self._nimbus_core_address is None:
            raise RuntimeError(
                "NimbusCore address not discovered. Call setup() first."
            )

        try:
            await self.send_command(Park(self._nimbus_core_address))
            logger.info("Instrument parked successfully")
        except Exception as e:
            logger.error(f"Failed to park instrument: {e}")
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
        self,
        ops: List[SingleChannelAspiration],
        use_channels: List[int],
        adc_enabled: bool = False,
        # Advanced kwargs (Optional, default to zeros/nulls)
        lld_mode: Optional[List[int]] = None,
        liquid_seek_height: Optional[List[float]] = None,
        immersion_depth: Optional[List[float]] = None,
        surface_following_distance: Optional[List[float]] = None,
        capacitive_lld_sensitivity: Optional[List[int]] = None,
        pressure_lld_sensitivity: Optional[List[int]] = None,
        settling_time: Optional[List[float]] = None,
        transport_air_volume: Optional[List[float]] = None,
        prewet_volume: Optional[List[float]] = None,
        liquid_exit_speed: Optional[List[float]] = None,
        mix_volume: Optional[List[float]] = None,
        mix_cycles: Optional[List[int]] = None,
        mix_speed: Optional[List[float]] = None,
        mix_position: Optional[List[float]] = None,
        limit_curve_index: Optional[List[int]] = None,
        tadm_enabled: Optional[bool] = None,
    ):
        """Aspirate liquid from the specified resource using pip.

        Args:
            ops: List of SingleChannelAspiration operations, one per channel
            use_channels: List of channel indices to use
            adc_enabled: If True, enable ADC (Automatic Drip Control), else disable (default: False)
            lld_mode: LLD mode (0=OFF, 1=cLLD, 2=pLLD, 3=DUAL), default: [0] * n
            liquid_seek_height: Relative offset from well bottom for LLD search start position (mm).
                This is a RELATIVE OFFSET, not an absolute coordinate. The instrument adds this to
                z_min_position (well bottom) to determine where to start the LLD search.
                If None, defaults to the well's size_z (depth), meaning "start search at top of well".
                When provided, should be a list of offsets in mm, one per channel.
            immersion_depth: Depth to submerge into liquid (mm), default: [0.0] * n
            surface_following_distance: Distance to follow liquid surface (mm), default: [0.0] * n
            capacitive_lld_sensitivity: cLLD sensitivity (1-4), default: [0] * n
            pressure_lld_sensitivity: pLLD sensitivity (1-4), default: [0] * n
            settling_time: Settling time (s), default: [1.0] * n
            transport_air_volume: Transport air volume (µL), default: [5.0] * n
            prewet_volume: Prewet volume (µL), default: [0.0] * n
            liquid_exit_speed: Liquid exit speed (µL/s), default: [20.0] * n
            mix_volume: Mix volume (µL). Extracted from op.mix if available, else default: [0.0] * n
            mix_cycles: Mix cycles. Extracted from op.mix if available, else default: [0] * n
            mix_speed: Mix speed (µL/s). Extracted from op.mix if available, else default: [0.0] * n
            mix_position: Mix position relative to liquid (mm), default: [0.0] * n
            limit_curve_index: Limit curve index, default: [0] * n
            tadm_enabled: TADM enabled flag, default: False

        Raises:
            RuntimeError: If pipette address or deck is not set
        """
        if self._pipette_address is None:
            raise RuntimeError(
                "Pipette address not discovered. Call setup() first."
            )
        if self._deck is None:
            raise RuntimeError("Deck must be set before aspirate")

        # Validate we have a NimbusDeck for coordinate conversion
        if not isinstance(self._deck, NimbusDeck):
            raise RuntimeError(
                "Deck must be a NimbusDeck for coordinate conversion"
            )

        n = len(ops)

        # Build tip pattern array (1 for active channels, 0 for inactive)
        tips_used = [0] * self.num_channels
        for channel_idx in use_channels:
            if channel_idx >= self.num_channels:
                raise ValueError(
                    f"Channel index {channel_idx} exceeds num_channels {self.num_channels}"
                )
            tips_used[channel_idx] = 1

        # Call ADC command (EnableADC or DisableADC)
        if adc_enabled:
            await self.send_command(EnableADC(self._pipette_address, tips_used))
            logger.info("Enabled ADC before aspirate")
        else:
            await self.send_command(DisableADC(self._pipette_address, tips_used))
            logger.info("Disabled ADC before aspirate")

        # Call GetChannelConfiguration for each active channel (index 2 = "Aspirate monitoring with cLLD")
        if self._channel_configurations is None:
            self._channel_configurations = {}
        for channel_idx in use_channels:
            channel_num = channel_idx + 1  # Convert to 1-based
            try:
                config = await self.send_command(
                    GetChannelConfiguration(
                        self._pipette_address,
                        channel=channel_num,
                        indexes=[2],  # Index 2 = "Aspirate monitoring with cLLD"
                    )
                )
                enabled = config["enabled"][0] if config["enabled"] else False
                if channel_num not in self._channel_configurations:
                    self._channel_configurations[channel_num] = {}
                self._channel_configurations[channel_num][2] = enabled
                logger.debug(f"Channel {channel_num} configuration (index 2): enabled={enabled}")
            except Exception as e:
                logger.warning(f"Failed to get channel configuration for channel {channel_num}: {e}")

        # ========================================================================
        # MINIMAL SET: Calculate from resources (NOT kwargs)
        # ========================================================================

        # Extract coordinates and convert to Hamilton coordinates
        x_positions_mm: List[float] = []
        y_positions_mm: List[float] = []
        z_positions_mm: List[float] = []

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

        # Convert positions to 0.01mm units (multiply by 100)
        x_positions = [int(round(x * 100)) for x in x_positions_mm]
        y_positions = [int(round(y * 100)) for y in y_positions_mm]

        # Traverse height: use deck z_max or default 146.0 mm
        traverse_height_mm = 146.0  # TODO: Access deck z_max property properly
        traverse_height_units = int(round(traverse_height_mm * 100))

        # Calculate well_bottoms: resource Z + offset Z + material_z_thickness
        well_bottoms: List[float] = []
        for op in ops:
            abs_location = op.resource.get_absolute_location()
            well_bottom = abs_location.z + op.offset.z
            if isinstance(op.resource, Container):
                well_bottom += op.resource.material_z_thickness
            well_bottoms.append(well_bottom)

        # Convert well_bottoms to Hamilton coordinates
        well_bottoms_hamilton: List[float] = []
        for i, op in enumerate(ops):
            abs_location = op.resource.get_absolute_location()
            well_bottom_location = Coordinate(
                x=abs_location.x + op.offset.x,
                y=abs_location.y + op.offset.y,
                z=well_bottoms[i],
            )
            hamilton_coord = self._deck.to_hamilton_coordinate(well_bottom_location)
            well_bottoms_hamilton.append(hamilton_coord.z)

        # Calculate liquid_surface_height: well_bottom + (op.liquid_height or 0)
        # This is the fixed Z-height when LLD is OFF
        liquid_surface_heights_mm: List[float] = []
        for i, op in enumerate(ops):
            liquid_height = getattr(op, "liquid_height", None) or 0.0
            liquid_surface_height = well_bottoms_hamilton[i] + liquid_height
            liquid_surface_heights_mm.append(liquid_surface_height)

        # Calculate liquid_seek_height if not provided as kwarg
        #
        # IMPORTANT: liquid_seek_height is a RELATIVE OFFSET (in mm), not an absolute coordinate.
        # It represents the height offset from the well bottom where the LLD (Liquid Level Detection)
        # search should start. The Hamilton instrument will add this offset to z_min_position
        # (well bottom) to determine the absolute Z position where the search begins.
        #
        # Default behavior: Use the well's size_z (depth) as the offset, which means
        # "start the LLD search at the top of the well" (well_bottom + well_size).
        # This is a reasonable default since we want to search from the top downward.
        #
        # When provided as a kwarg, it should be a list of relative offsets in mm.
        # The instrument will internally add these to z_min_position to get absolute coordinates.
        if liquid_seek_height is None:
            # Default: use well size_z as the offset (start search at top of well)
            liquid_seek_height = []
            for op in ops:
                well_size_z = op.resource.get_absolute_size_z()
                liquid_seek_height.append(well_size_z)
        else:
            # If provided, it's already a relative offset in mm, use as-is
            # The instrument will add this to z_min_position internally
            pass

        # Calculate z_min_position: default to well_bottom
        z_min_positions_mm = well_bottoms_hamilton.copy()

        # Extract volumes and speeds from operations
        volumes = [op.volume for op in ops]  # in µL
        # flow_rate should not be None - if it is, it's an error (no hardcoded fallback)
        flow_rates = [op.flow_rate for op in ops]  # in µL/s
        blowout_volumes = [op.blow_out_air_volume if op.blow_out_air_volume is not None else 40.0 for op in ops]  # in µL, default 40

        # Extract mix parameters from op.mix if available
        mix_volumes_from_op: List[float] = []
        mix_cycles_from_op: List[int] = []
        mix_speeds_from_op: List[float] = []
        for op in ops:
            if hasattr(op, "mix") and op.mix is not None:
                mix_volumes_from_op.append(op.mix.volume if hasattr(op.mix, "volume") else 0.0)
                mix_cycles_from_op.append(op.mix.repetitions if hasattr(op.mix, "repetitions") else 0)
                # If mix has flow_rate, use it; otherwise default to aspirate speed
                if hasattr(op.mix, "flow_rate") and op.mix.flow_rate is not None:
                    mix_speeds_from_op.append(op.mix.flow_rate)
                else:
                    # Default to aspirate speed (flow_rate) when mix speed not specified
                    mix_speeds_from_op.append(op.flow_rate)
            else:
                mix_volumes_from_op.append(0.0)
                mix_cycles_from_op.append(0)
                # Default to aspirate speed (flow_rate) when no mix operation
                mix_speeds_from_op.append(op.flow_rate)

        # ========================================================================
        # ADVANCED PARAMETERS: Fill in defaults using _fill_in_defaults()
        # ========================================================================

        # LLD mode: default to [0] * n (OFF)
        lld_mode = _fill_in_defaults(lld_mode, [0] * n)

        # Immersion depth: default to [0.0] * n
        immersion_depth = _fill_in_defaults(immersion_depth, [0.0] * n)

        # Surface following distance: default to [0.0] * n
        surface_following_distance = _fill_in_defaults(surface_following_distance, [0.0] * n)

        # LLD sensitivities: default to [0] * n
        capacitive_lld_sensitivity = _fill_in_defaults(capacitive_lld_sensitivity, [0] * n)
        pressure_lld_sensitivity = _fill_in_defaults(pressure_lld_sensitivity, [0] * n)

        # Settling time: default to [1.0] * n (from log: 10 in 0.1s units = 1.0s)
        settling_time = _fill_in_defaults(settling_time, [1.0] * n)

        # Transport air volume: default to [5.0] * n (from log: 50 in 0.1µL units = 5.0 µL)
        transport_air_volume = _fill_in_defaults(transport_air_volume, [5.0] * n)

        # Prewet volume: default to [0.0] * n
        prewet_volume = _fill_in_defaults(prewet_volume, [0.0] * n)

        # Liquid exit speed: default to [20.0] * n (from log: 200 in 0.1µL/s units = 20.0 µL/s)
        liquid_exit_speed = _fill_in_defaults(liquid_exit_speed, [20.0] * n)

        # Mix parameters: use op.mix if available, else use kwargs/defaults
        mix_volume = _fill_in_defaults(mix_volume, mix_volumes_from_op)
        mix_cycles = _fill_in_defaults(mix_cycles, mix_cycles_from_op)
        # mix_speed defaults to aspirate_speed (flow_rates) if not specified
        # This matches the log file behavior where mix_speed = aspirate_speed even when mix_volume = 0
        if mix_speed is None:
            mix_speed = flow_rates.copy()  # Default to aspirate speed
        else:
            mix_speed = _fill_in_defaults(mix_speed, mix_speeds_from_op)
        mix_position = _fill_in_defaults(mix_position, [0.0] * n)

        # Limit curve index: default to [0] * n
        limit_curve_index = _fill_in_defaults(limit_curve_index, [0] * n)

        # TADM enabled: default to False
        if tadm_enabled is None:
            tadm_enabled = False

        # ========================================================================
        # CONVERT UNITS AND BUILD FULL ARRAYS
        # ========================================================================

        # Convert volumes: µL → 0.1µL units (multiply by 10)
        aspirate_volumes = [int(round(vol * 10)) for vol in volumes]
        blowout_volumes_units = [int(round(vol * 10)) for vol in blowout_volumes]

        # Convert speeds: µL/s → 0.1µL/s units (multiply by 10)
        aspirate_speeds = [int(round(fr * 10)) for fr in flow_rates]

        # Convert heights: mm → 0.01mm units (multiply by 100)
        liquid_seek_height_units = [int(round(h * 100)) for h in liquid_seek_height]
        liquid_surface_height_units = [int(round(h * 100)) for h in liquid_surface_heights_mm]
        immersion_depth_units = [int(round(d * 100)) for d in immersion_depth]
        surface_following_distance_units = [int(round(d * 100)) for d in surface_following_distance]
        z_min_position_units = [int(round(z * 100)) for z in z_min_positions_mm]

        # Convert settling time: s → 0.1s units (multiply by 10)
        settling_time_units = [int(round(t * 10)) for t in settling_time]

        # Convert transport air volume: µL → 0.1µL units (multiply by 10)
        transport_air_volume_units = [int(round(v * 10)) for v in transport_air_volume]

        # Convert prewet volume: µL → 0.1µL units (multiply by 10)
        prewet_volume_units = [int(round(v * 10)) for v in prewet_volume]

        # Convert liquid exit speed: µL/s → 0.1µL/s units (multiply by 10)
        liquid_exit_speed_units = [int(round(s * 10)) for s in liquid_exit_speed]

        # Convert mix volume: µL → 0.1µL units (multiply by 10)
        mix_volume_units = [int(round(v * 10)) for v in mix_volume]

        # Convert mix speed: µL/s → 0.1µL/s units (multiply by 10)
        mix_speed_units = [int(round(s * 10)) for s in mix_speed]

        # Convert mix position: mm → 0.01mm units (multiply by 100)
        mix_position_units = [int(round(p * 100)) for p in mix_position]

        # Build arrays for all channels (pad with 0s for inactive channels)
        x_positions_full = [0] * self.num_channels
        y_positions_full = [0] * self.num_channels
        aspirate_volumes_full = [0] * self.num_channels
        blowout_volumes_full = [0] * self.num_channels
        aspirate_speeds_full = [0] * self.num_channels
        liquid_seek_height_full = [0] * self.num_channels
        liquid_surface_height_full = [0] * self.num_channels
        immersion_depth_full = [0] * self.num_channels
        surface_following_distance_full = [0] * self.num_channels
        z_min_position_full = [0] * self.num_channels
        settling_time_full = [0] * self.num_channels
        transport_air_volume_full = [0] * self.num_channels
        prewet_volume_full = [0] * self.num_channels
        liquid_exit_speed_full = [0] * self.num_channels
        mix_volume_full = [0] * self.num_channels
        mix_cycles_full = [0] * self.num_channels
        mix_speed_full = [0] * self.num_channels
        mix_position_full = [0] * self.num_channels
        capacitive_lld_sensitivity_full = [0] * self.num_channels
        pressure_lld_sensitivity_full = [0] * self.num_channels
        limit_curve_index_full = [0] * self.num_channels
        lld_mode_full = [0] * self.num_channels

        for i, channel_idx in enumerate(use_channels):
            x_positions_full[channel_idx] = x_positions[i]
            y_positions_full[channel_idx] = y_positions[i]
            aspirate_volumes_full[channel_idx] = aspirate_volumes[i]
            blowout_volumes_full[channel_idx] = blowout_volumes_units[i]
            aspirate_speeds_full[channel_idx] = aspirate_speeds[i]
            liquid_seek_height_full[channel_idx] = liquid_seek_height_units[i]
            liquid_surface_height_full[channel_idx] = liquid_surface_height_units[i]
            immersion_depth_full[channel_idx] = immersion_depth_units[i]
            surface_following_distance_full[channel_idx] = surface_following_distance_units[i]
            z_min_position_full[channel_idx] = z_min_position_units[i]
            settling_time_full[channel_idx] = settling_time_units[i]
            transport_air_volume_full[channel_idx] = transport_air_volume_units[i]
            prewet_volume_full[channel_idx] = prewet_volume_units[i]
            liquid_exit_speed_full[channel_idx] = liquid_exit_speed_units[i]
            mix_volume_full[channel_idx] = mix_volume_units[i]
            mix_cycles_full[channel_idx] = mix_cycles[i]
            mix_speed_full[channel_idx] = mix_speed_units[i]
            mix_position_full[channel_idx] = mix_position_units[i]
            capacitive_lld_sensitivity_full[channel_idx] = capacitive_lld_sensitivity[i]
            pressure_lld_sensitivity_full[channel_idx] = pressure_lld_sensitivity[i]
            limit_curve_index_full[channel_idx] = limit_curve_index[i]
            lld_mode_full[channel_idx] = lld_mode[i]

        # Default values for remaining parameters
        aspirate_type = [0] * self.num_channels
        clot_check_height = [0] * self.num_channels
        z_final = traverse_height_units
        mix_follow_distance = [0] * self.num_channels
        tube_section_height = [0] * self.num_channels
        tube_section_ratio = [0] * self.num_channels
        lld_height_difference = [0] * self.num_channels
        recording_mode = 0

        # Create and send Aspirate command
        command = Aspirate(
            dest=self._pipette_address,
            aspirate_type=aspirate_type,
            tips_used=tips_used,
            x_positions=x_positions_full,
            y_positions=y_positions_full,
            traverse_height=traverse_height_units,
            liquid_seek_height=liquid_seek_height_full,
            liquid_surface_height=liquid_surface_height_full,
            submerge_depth=immersion_depth_full,
            follow_depth=surface_following_distance_full,
            z_min_position=z_min_position_full,
            clot_check_height=clot_check_height,
            z_final=z_final,
            liquid_exit_speed=liquid_exit_speed_full,
            blowout_volume=blowout_volumes_full,
            prewet_volume=prewet_volume_full,
            aspirate_volume=aspirate_volumes_full,
            transport_air_volume=transport_air_volume_full,
            aspirate_speed=aspirate_speeds_full,
            settling_time=settling_time_full,
            mix_volume=mix_volume_full,
            mix_cycles=mix_cycles_full,
            mix_position=mix_position_full,
            mix_follow_distance=mix_follow_distance,
            mix_speed=mix_speed_full,
            tube_section_height=tube_section_height,
            tube_section_ratio=tube_section_ratio,
            lld_mode=lld_mode_full,
            capacitive_lld_sensitivity=capacitive_lld_sensitivity_full,
            pressure_lld_sensitivity=pressure_lld_sensitivity_full,
            lld_height_difference=lld_height_difference,
            tadm_enabled=tadm_enabled,
            limit_curve_index=limit_curve_index_full,
            recording_mode=recording_mode,
        )

        try:
            await self.send_command(command)
            logger.info(f"Aspirated on channels {use_channels}")
        except Exception as e:
            logger.error(f"Failed to aspirate: {e}")
            raise

    async def dispense(
        self,
        ops: List[SingleChannelDispense],
        use_channels: List[int],
        adc_enabled: bool = False,
        # Advanced kwargs (Optional, default to zeros/nulls)
        lld_mode: Optional[List[int]] = None,
        liquid_seek_height: Optional[List[float]] = None,
        immersion_depth: Optional[List[float]] = None,
        surface_following_distance: Optional[List[float]] = None,
        capacitive_lld_sensitivity: Optional[List[int]] = None,
        settling_time: Optional[List[float]] = None,
        transport_air_volume: Optional[List[float]] = None,
        prewet_volume: Optional[List[float]] = None,
        liquid_exit_speed: Optional[List[float]] = None,
        mix_volume: Optional[List[float]] = None,
        mix_cycles: Optional[List[int]] = None,
        mix_speed: Optional[List[float]] = None,
        mix_position: Optional[List[float]] = None,
        limit_curve_index: Optional[List[int]] = None,
        tadm_enabled: Optional[bool] = None,
        cutoff_speed: Optional[List[float]] = None,
        stop_back_volume: Optional[List[float]] = None,
        touch_off_distance: Optional[float] = None,
        dispense_offset: Optional[List[float]] = None,
    ):
        """Dispense liquid from the specified resource using pip.

        Args:
            ops: List of SingleChannelDispense operations, one per channel
            use_channels: List of channel indices to use
            adc_enabled: If True, enable ADC (Automatic Drip Control), else disable (default: False)
            lld_mode: LLD mode (0=OFF, 1=cLLD, 2=pLLD, 3=DUAL), default: [0] * n
            liquid_seek_height: Override calculated LLD search height (mm). If None, calculated from well_bottom + resource size
            immersion_depth: Depth to submerge into liquid (mm), default: [0.0] * n
            surface_following_distance: Distance to follow liquid surface (mm), default: [0.0] * n
            capacitive_lld_sensitivity: cLLD sensitivity (1-4), default: [0] * n
            settling_time: Settling time (s), default: [1.0] * n
            transport_air_volume: Transport air volume (µL), default: [5.0] * n
            prewet_volume: Prewet volume (µL), default: [0.0] * n
            liquid_exit_speed: Liquid exit speed (µL/s), default: [20.0] * n
            mix_volume: Mix volume (µL). Extracted from op.mix if available, else default: [0.0] * n
            mix_cycles: Mix cycles. Extracted from op.mix if available, else default: [0] * n
            mix_speed: Mix speed (µL/s). Extracted from op.mix if available, else default: [0.0] * n
            mix_position: Mix position relative to liquid (mm), default: [0.0] * n
            limit_curve_index: Limit curve index, default: [0] * n
            tadm_enabled: TADM enabled flag, default: False
            cutoff_speed: Cutoff speed (µL/s), default: [25.0] * n
            stop_back_volume: Stop back volume (µL), default: [0.0] * n
            touch_off_distance: Touch off distance (mm), default: 0.0
            dispense_offset: Dispense offset (mm), default: [0.0] * n

        Raises:
            RuntimeError: If pipette address or deck is not set
        """
        if self._pipette_address is None:
            raise RuntimeError(
                "Pipette address not discovered. Call setup() first."
            )
        if self._deck is None:
            raise RuntimeError("Deck must be set before dispense")

        # Validate we have a NimbusDeck for coordinate conversion
        if not isinstance(self._deck, NimbusDeck):
            raise RuntimeError(
                "Deck must be a NimbusDeck for coordinate conversion"
            )

        n = len(ops)

        # Build tip pattern array (1 for active channels, 0 for inactive)
        tips_used = [0] * self.num_channels
        for channel_idx in use_channels:
            if channel_idx >= self.num_channels:
                raise ValueError(
                    f"Channel index {channel_idx} exceeds num_channels {self.num_channels}"
                )
            tips_used[channel_idx] = 1

        # Call ADC command (EnableADC or DisableADC)
        if adc_enabled:
            await self.send_command(EnableADC(self._pipette_address, tips_used))
            logger.info("Enabled ADC before dispense")
        else:
            await self.send_command(DisableADC(self._pipette_address, tips_used))
            logger.info("Disabled ADC before dispense")

        # Call GetChannelConfiguration for each active channel (index 2 = "Aspirate monitoring with cLLD")
        if self._channel_configurations is None:
            self._channel_configurations = {}
        for channel_idx in use_channels:
            channel_num = channel_idx + 1  # Convert to 1-based
            try:
                config = await self.send_command(
                    GetChannelConfiguration(
                        self._pipette_address,
                        channel=channel_num,
                        indexes=[2],  # Index 2 = "Aspirate monitoring with cLLD"
                    )
                )
                enabled = config["enabled"][0] if config["enabled"] else False
                if channel_num not in self._channel_configurations:
                    self._channel_configurations[channel_num] = {}
                self._channel_configurations[channel_num][2] = enabled
                logger.debug(f"Channel {channel_num} configuration (index 2): enabled={enabled}")
            except Exception as e:
                logger.warning(f"Failed to get channel configuration for channel {channel_num}: {e}")

        # ========================================================================
        # MINIMAL SET: Calculate from resources (NOT kwargs)
        # ========================================================================

        # Extract coordinates and convert to Hamilton coordinates
        x_positions_mm: List[float] = []
        y_positions_mm: List[float] = []
        z_positions_mm: List[float] = []

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

        # Convert positions to 0.01mm units (multiply by 100)
        x_positions = [int(round(x * 100)) for x in x_positions_mm]
        y_positions = [int(round(y * 100)) for y in y_positions_mm]

        # Traverse height: use deck z_max or default 146.0 mm
        traverse_height_mm = 146.0  # TODO: Access deck z_max property properly
        traverse_height_units = int(round(traverse_height_mm * 100))

        # Calculate well_bottoms: resource Z + offset Z + material_z_thickness
        well_bottoms: List[float] = []
        for op in ops:
            abs_location = op.resource.get_absolute_location()
            well_bottom = abs_location.z + op.offset.z
            if isinstance(op.resource, Container):
                well_bottom += op.resource.material_z_thickness
            well_bottoms.append(well_bottom)

        # Convert well_bottoms to Hamilton coordinates
        well_bottoms_hamilton: List[float] = []
        for i, op in enumerate(ops):
            abs_location = op.resource.get_absolute_location()
            well_bottom_location = Coordinate(
                x=abs_location.x + op.offset.x,
                y=abs_location.y + op.offset.y,
                z=well_bottoms[i],
            )
            hamilton_coord = self._deck.to_hamilton_coordinate(well_bottom_location)
            well_bottoms_hamilton.append(hamilton_coord.z)

        # Calculate dispense_height: well_bottom + (op.liquid_height or 0)
        # This is the fixed Z-height when LLD is OFF
        dispense_heights_mm: List[float] = []
        for i, op in enumerate(ops):
            liquid_height = getattr(op, "liquid_height", None) or 0.0
            dispense_height = well_bottoms_hamilton[i] + liquid_height
            dispense_heights_mm.append(dispense_height)

        # Calculate liquid_seek_height if not provided as kwarg
        #
        # IMPORTANT: liquid_seek_height is a RELATIVE OFFSET (in mm), not an absolute coordinate.
        # It represents the height offset from the well bottom where the LLD (Liquid Level Detection)
        # search should start. The Hamilton instrument will add this offset to z_min_position
        # (well bottom) to determine the absolute Z position where the search begins.
        #
        # Default behavior: Use the well's size_z (depth) as the offset, which means
        # "start the LLD search at the top of the well" (well_bottom + well_size).
        # This is a reasonable default since we want to search from the top downward.
        #
        # When provided as a kwarg, it should be a list of relative offsets in mm.
        # The instrument will internally add these to z_min_position to get absolute coordinates.
        if liquid_seek_height is None:
            # Default: use well size_z as the offset (start search at top of well)
            liquid_seek_height = []
            for op in ops:
                well_size_z = op.resource.get_absolute_size_z()
                liquid_seek_height.append(well_size_z)
        else:
            # If provided, it's already a relative offset in mm, use as-is
            # The instrument will add this to z_min_position internally
            pass

        # Calculate z_min_position: default to well_bottom
        z_min_positions_mm = well_bottoms_hamilton.copy()

        # Extract volumes and speeds from operations
        volumes = [op.volume for op in ops]  # in µL
        # flow_rate should not be None - if it is, it's an error (no hardcoded fallback)
        flow_rates = [op.flow_rate for op in ops]  # in µL/s
        blowout_volumes = [op.blow_out_air_volume if op.blow_out_air_volume is not None else 40.0 for op in ops]  # in µL, default 40

        # Extract mix parameters from op.mix if available
        mix_volumes_from_op: List[float] = []
        mix_cycles_from_op: List[int] = []
        mix_speeds_from_op: List[float] = []
        for op in ops:
            if hasattr(op, "mix") and op.mix is not None:
                mix_volumes_from_op.append(op.mix.volume if hasattr(op.mix, "volume") else 0.0)
                mix_cycles_from_op.append(op.mix.repetitions if hasattr(op.mix, "repetitions") else 0)
                # If mix has flow_rate, use it; otherwise default to dispense speed
                if hasattr(op.mix, "flow_rate") and op.mix.flow_rate is not None:
                    mix_speeds_from_op.append(op.mix.flow_rate)
                else:
                    # Default to dispense speed (flow_rate) when mix speed not specified
                    mix_speeds_from_op.append(op.flow_rate)
            else:
                mix_volumes_from_op.append(0.0)
                mix_cycles_from_op.append(0)
                # Default to dispense speed (flow_rate) when no mix operation
                mix_speeds_from_op.append(op.flow_rate)

        # ========================================================================
        # ADVANCED PARAMETERS: Fill in defaults using _fill_in_defaults()
        # ========================================================================

        # LLD mode: default to [0] * n (OFF)
        lld_mode = _fill_in_defaults(lld_mode, [0] * n)

        # Immersion depth: default to [0.0] * n
        immersion_depth = _fill_in_defaults(immersion_depth, [0.0] * n)

        # Surface following distance: default to [0.0] * n
        surface_following_distance = _fill_in_defaults(surface_following_distance, [0.0] * n)

        # LLD sensitivities: default to [0] * n
        capacitive_lld_sensitivity = _fill_in_defaults(capacitive_lld_sensitivity, [0] * n)

        # Settling time: default to [1.0] * n (from log: 10 in 0.1s units = 1.0s)
        settling_time = _fill_in_defaults(settling_time, [1.0] * n)

        # Transport air volume: default to [5.0] * n (from log: 50 in 0.1µL units = 5.0 µL)
        transport_air_volume = _fill_in_defaults(transport_air_volume, [5.0] * n)

        # Prewet volume: default to [0.0] * n
        prewet_volume = _fill_in_defaults(prewet_volume, [0.0] * n)

        # Liquid exit speed: default to [20.0] * n (from log: 200 in 0.1µL/s units = 20.0 µL/s)
        liquid_exit_speed = _fill_in_defaults(liquid_exit_speed, [20.0] * n)

        # Mix parameters: use op.mix if available, else use kwargs/defaults
        mix_volume = _fill_in_defaults(mix_volume, mix_volumes_from_op)
        mix_cycles = _fill_in_defaults(mix_cycles, mix_cycles_from_op)
        # mix_speed defaults to dispense_speed (flow_rates) if not specified
        # This matches the log file behavior where mix_speed = dispense_speed even when mix_volume = 0
        if mix_speed is None:
            mix_speed = flow_rates.copy()  # Default to dispense speed
        else:
            mix_speed = _fill_in_defaults(mix_speed, mix_speeds_from_op)
        mix_position = _fill_in_defaults(mix_position, [0.0] * n)

        # Limit curve index: default to [0] * n
        limit_curve_index = _fill_in_defaults(limit_curve_index, [0] * n)

        # TADM enabled: default to False
        if tadm_enabled is None:
            tadm_enabled = False

        # Dispense-specific parameters
        cutoff_speed = _fill_in_defaults(cutoff_speed, [25.0] * n)
        stop_back_volume = _fill_in_defaults(stop_back_volume, [0.0] * n)
        dispense_offset = _fill_in_defaults(dispense_offset, [0.0] * n)

        # Touch off distance: default to 0.0 (not a list)
        if touch_off_distance is None:
            touch_off_distance = 0.0

        # ========================================================================
        # CONVERT UNITS AND BUILD FULL ARRAYS
        # ========================================================================

        # Convert volumes: µL → 0.1µL units (multiply by 10)
        dispense_volumes = [int(round(vol * 10)) for vol in volumes]
        blowout_volumes_units = [int(round(vol * 10)) for vol in blowout_volumes]

        # Convert speeds: µL/s → 0.1µL/s units (multiply by 10)
        dispense_speeds = [int(round(fr * 10)) for fr in flow_rates]

        # Convert heights: mm → 0.01mm units (multiply by 100)
        liquid_seek_height_units = [int(round(h * 100)) for h in liquid_seek_height]
        dispense_height_units = [int(round(h * 100)) for h in dispense_heights_mm]
        immersion_depth_units = [int(round(d * 100)) for d in immersion_depth]
        surface_following_distance_units = [int(round(d * 100)) for d in surface_following_distance]
        z_min_position_units = [int(round(z * 100)) for z in z_min_positions_mm]

        # Convert settling time: s → 0.1s units (multiply by 10)
        settling_time_units = [int(round(t * 10)) for t in settling_time]

        # Convert transport air volume: µL → 0.1µL units (multiply by 10)
        transport_air_volume_units = [int(round(v * 10)) for v in transport_air_volume]

        # Convert prewet volume: µL → 0.1µL units (multiply by 10)
        prewet_volume_units = [int(round(v * 10)) for v in prewet_volume]

        # Convert liquid exit speed: µL/s → 0.1µL/s units (multiply by 10)
        liquid_exit_speed_units = [int(round(s * 10)) for s in liquid_exit_speed]

        # Convert mix volume: µL → 0.1µL units (multiply by 10)
        mix_volume_units = [int(round(v * 10)) for v in mix_volume]

        # Convert mix speed: µL/s → 0.1µL/s units (multiply by 10)
        mix_speed_units = [int(round(s * 10)) for s in mix_speed]

        # Convert mix position: mm → 0.01mm units (multiply by 100)
        mix_position_units = [int(round(p * 100)) for p in mix_position]

        # Convert cutoff speed: µL/s → 0.1µL/s units (multiply by 10)
        cutoff_speed_units = [int(round(s * 10)) for s in cutoff_speed]

        # Convert stop back volume: µL → 0.1µL units (multiply by 10)
        stop_back_volume_units = [int(round(v * 10)) for v in stop_back_volume]

        # Convert dispense offset: mm → 0.01mm units (multiply by 100)
        dispense_offset_units = [int(round(o * 100)) for o in dispense_offset]

        # Convert touch off distance: mm → 0.01mm units (multiply by 100)
        touch_off_distance_units = int(round(touch_off_distance * 100))

        # Build arrays for all channels (pad with 0s for inactive channels)
        x_positions_full = [0] * self.num_channels
        y_positions_full = [0] * self.num_channels
        dispense_volumes_full = [0] * self.num_channels
        blowout_volumes_full = [0] * self.num_channels
        dispense_speeds_full = [0] * self.num_channels
        liquid_seek_height_full = [0] * self.num_channels
        dispense_height_full = [0] * self.num_channels
        immersion_depth_full = [0] * self.num_channels
        surface_following_distance_full = [0] * self.num_channels
        z_min_position_full = [0] * self.num_channels
        settling_time_full = [0] * self.num_channels
        transport_air_volume_full = [0] * self.num_channels
        prewet_volume_full = [0] * self.num_channels
        liquid_exit_speed_full = [0] * self.num_channels
        mix_volume_full = [0] * self.num_channels
        mix_cycles_full = [0] * self.num_channels
        mix_speed_full = [0] * self.num_channels
        mix_position_full = [0] * self.num_channels
        capacitive_lld_sensitivity_full = [0] * self.num_channels
        limit_curve_index_full = [0] * self.num_channels
        lld_mode_full = [0] * self.num_channels
        cutoff_speed_full = [0] * self.num_channels
        stop_back_volume_full = [0] * self.num_channels
        dispense_offset_full = [0] * self.num_channels

        for i, channel_idx in enumerate(use_channels):
            x_positions_full[channel_idx] = x_positions[i]
            y_positions_full[channel_idx] = y_positions[i]
            dispense_volumes_full[channel_idx] = dispense_volumes[i]
            blowout_volumes_full[channel_idx] = blowout_volumes_units[i]
            dispense_speeds_full[channel_idx] = dispense_speeds[i]
            liquid_seek_height_full[channel_idx] = liquid_seek_height_units[i]
            dispense_height_full[channel_idx] = dispense_height_units[i]
            immersion_depth_full[channel_idx] = immersion_depth_units[i]
            surface_following_distance_full[channel_idx] = surface_following_distance_units[i]
            z_min_position_full[channel_idx] = z_min_position_units[i]
            settling_time_full[channel_idx] = settling_time_units[i]
            transport_air_volume_full[channel_idx] = transport_air_volume_units[i]
            prewet_volume_full[channel_idx] = prewet_volume_units[i]
            liquid_exit_speed_full[channel_idx] = liquid_exit_speed_units[i]
            mix_volume_full[channel_idx] = mix_volume_units[i]
            mix_cycles_full[channel_idx] = mix_cycles[i]
            mix_speed_full[channel_idx] = mix_speed_units[i]
            mix_position_full[channel_idx] = mix_position_units[i]
            capacitive_lld_sensitivity_full[channel_idx] = capacitive_lld_sensitivity[i]
            limit_curve_index_full[channel_idx] = limit_curve_index[i]
            lld_mode_full[channel_idx] = lld_mode[i]
            cutoff_speed_full[channel_idx] = cutoff_speed_units[i]
            stop_back_volume_full[channel_idx] = stop_back_volume_units[i]
            dispense_offset_full[channel_idx] = dispense_offset_units[i]

        # Default values for remaining parameters
        dispense_type = [0] * self.num_channels
        z_final = traverse_height_units
        mix_follow_distance = [0] * self.num_channels
        tube_section_height = [0] * self.num_channels
        tube_section_ratio = [0] * self.num_channels
        recording_mode = 0

        # Create and send Dispense command
        command = Dispense(
            dest=self._pipette_address,
            dispense_type=dispense_type,
            tips_used=tips_used,
            x_positions=x_positions_full,
            y_positions=y_positions_full,
            traverse_height=traverse_height_units,
            liquid_seek_height=liquid_seek_height_full,
            dispense_height=dispense_height_full,
            submerge_depth=immersion_depth_full,
            follow_depth=surface_following_distance_full,
            z_min_position=z_min_position_full,
            z_final=z_final,
            liquid_exit_speed=liquid_exit_speed_full,
            transport_air_volume=transport_air_volume_full,
            dispense_volume=dispense_volumes_full,
            stop_back_volume=stop_back_volume_full,
            blowout_volume=blowout_volumes_full,
            dispense_speed=dispense_speeds_full,
            cutoff_speed=cutoff_speed_full,
            settling_time=settling_time_full,
            mix_volume=mix_volume_full,
            mix_cycles=mix_cycles_full,
            mix_position=mix_position_full,
            mix_follow_distance=mix_follow_distance,
            mix_speed=mix_speed_full,
            touch_off_distance=touch_off_distance_units,
            dispense_offset=dispense_offset_full,
            tube_section_height=tube_section_height,
            tube_section_ratio=tube_section_ratio,
            lld_mode=lld_mode_full,
            capacitive_lld_sensitivity=capacitive_lld_sensitivity_full,
            tadm_enabled=tadm_enabled,
            limit_curve_index=limit_curve_index_full,
            recording_mode=recording_mode,
        )

        try:
            await self.send_command(command)
            logger.info(f"Dispensed on channels {use_channels}")
        except Exception as e:
            logger.error(f"Failed to dispense: {e}")
            raise

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

