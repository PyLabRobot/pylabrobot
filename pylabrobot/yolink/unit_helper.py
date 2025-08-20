"""YoLink Unit convert helper."""

from __future__ import annotations

from collections.abc import Callable
from enum import IntEnum
from functools import lru_cache

from .const import UNIT_NOT_RECOGNIZED_TEMPLATE
from .exception import YoLinkError


class UnitOfVolume(IntEnum):
  """Unit of meter."""

  GALLONS = 0
  CENTUM_CUBIC_FEET = 1
  CUBIC_METERS = 2
  LITERS = 3


_IN_TO_M = 0.0254  # 1 inch = 0.0254 m
_FOOT_TO_M = _IN_TO_M * 12  # 12 inches = 1 foot (0.3048 m)
_L_TO_CUBIC_METER = 0.001  # 1 L = 0.001 m³
_GALLON_TO_CUBIC_METER = 231 * pow(_IN_TO_M, 3)  # US gallon is 231 cubic inches
_CUBIC_FOOT_TO_CUBIC_METER = pow(_FOOT_TO_M, 3)


# source code from homeassistant.util.unit_conversion.py
class BaseUnitConverter:
  """Define the format of a conversion utility."""

  UNIT_CLASS: str
  NORMALIZED_UNIT: str | None
  VALID_UNITS: set[str | None]

  _UNIT_CONVERSION: dict[str | None, float]

  @classmethod
  def convert(cls, value: float, from_unit: str | None, to_unit: str | None) -> float:
    """Convert one unit of measurement to another."""
    return cls.converter_factory(from_unit, to_unit)(value)

  @classmethod
  @lru_cache
  def converter_factory(
    cls, from_unit: str | None, to_unit: str | None
  ) -> Callable[[float], float]:
    """Return a function to convert one unit of measurement to another."""
    if from_unit == to_unit:
      return lambda value: value
    from_ratio, to_ratio = cls._get_from_to_ratio(from_unit, to_unit)
    return lambda val: (val / from_ratio) * to_ratio

  @classmethod
  def _get_from_to_ratio(cls, from_unit: str | None, to_unit: str | None) -> tuple[float, float]:
    """Get unit ratio between units of measurement."""
    unit_conversion = cls._UNIT_CONVERSION
    try:
      return unit_conversion[from_unit], unit_conversion[to_unit]
    except KeyError as err:
      raise YoLinkError(UNIT_NOT_RECOGNIZED_TEMPLATE.format(err.args[0], cls.UNIT_CLASS)) from err

  @classmethod
  @lru_cache
  def converter_factory_allow_none(
    cls, from_unit: str | None, to_unit: str | None
  ) -> Callable[[float | None], float | None]:
    """Return a function to convert one unit of measurement to another which allows None."""
    if from_unit == to_unit:
      return lambda value: value
    from_ratio, to_ratio = cls._get_from_to_ratio(from_unit, to_unit)
    return lambda val: None if val is None else (val / from_ratio) * to_ratio

  @classmethod
  @lru_cache
  def get_unit_ratio(cls, from_unit: str | None, to_unit: str | None) -> float:
    """Get unit ratio between units of measurement."""
    from_ratio, to_ratio = cls._get_from_to_ratio(from_unit, to_unit)
    return from_ratio / to_ratio


class VolumeConverter(BaseUnitConverter):
  """Utility to convert volume values."""

  UNIT_CLASS = "volume"
  NORMALIZED_UNIT = UnitOfVolume.CUBIC_METERS
  # Units in terms of m³
  _UNIT_CONVERSION: dict[str | None, float] = {
    UnitOfVolume.LITERS: 1 / _L_TO_CUBIC_METER,
    UnitOfVolume.GALLONS: 1 / _GALLON_TO_CUBIC_METER,
    UnitOfVolume.CUBIC_METERS: 1,
    UnitOfVolume.CENTUM_CUBIC_FEET: 1 / (100 * _CUBIC_FOOT_TO_CUBIC_METER),
  }
  VALID_UNITS = {
    UnitOfVolume.LITERS,
    UnitOfVolume.GALLONS,
    UnitOfVolume.CUBIC_METERS,
    UnitOfVolume.CENTUM_CUBIC_FEET,
  }
