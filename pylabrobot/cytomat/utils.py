import os

from .constants import BINARY_REPRESENTATION, HEX

current_dir = os.path.dirname(os.path.abspath(__file__))
DATA_PATH = os.path.join(current_dir, "..", "..", "..", "experiments", "cytomat", "data")


def hex_to_binary(hex_str: HEX) -> BINARY_REPRESENTATION:
    """
    >>> hex_to_binary('01')
    '00000001'
    """
    return bin(int(hex_str, 16))[2:].zfill(8)


def hex_to_base_twelve(hex_str: HEX) -> BINARY_REPRESENTATION:
    return bin(int(hex_str, 12))[2:].zfill(15)


def validate_storage_location_number(storage_location_number: str):
    try:
        int(storage_location_number)
    except ValueError as exc:
        raise ValueError("Storage location number must be an integer.") from exc
    if len(storage_location_number) != 3:
        raise ValueError("Storage location number must be a three-digit number.")