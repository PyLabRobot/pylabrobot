from __future__ import annotations

import json
import csv
from typing import Union, Dict, List, Optional, Literal


class PumpCalibration:
  """ Calibration for a single pump or pump array

  Attributes:
    calibration: The calibration of the pump or pump array.
  """

  def __init__(self,
               calibration: Union[List[Union[float, int]]],
               calibration_mode: Literal["duration", "revolutions"]="duration"):
    """ Initialize a PumpCalibration object.

    Args:
      calibration: calibration of the pump in pump-specific volume per time/revolution units.
      calibration_mode: units of the calibration.

    Raises:
      ValueError: if a value in the calibration is outside expected parameters.
    """

    if any(value <= 0 for value in calibration):
      raise ValueError("A value in the calibration is is outside expected parameters.")
    self.calibration = calibration
    self.calibration_mode = calibration_mode

  def __getitem__(self, item) -> Union[float, int]:
    return self.calibration[item]  # type: ignore

  def __len__(self) -> int:
    """ Return the length of the calibration. """

    return len(self.calibration)

  @classmethod
  def load_calibration(cls,
                       calibration: Optional[Union[dict, list, float, int, str]] = None,
                       num_items: Optional[int] = None) -> PumpCalibration:
    """ Load a calibration from a file, dictionary, list, or value. :param calibration: pump
    calibration file, dictionary, list, or value. If None, returns an empty PumpCalibration
    object.

    Args:
      calibration: pump calibration file, dictionary, list, or value.

    Returns:
      PumpCalibration

    Raises:
      NotImplementedError: if the calibration filetype or format is not supported.
      ValueError: if num_items is not specified when calibration is a value.
    """
    if isinstance(calibration, dict):
      return PumpCalibration.load_from_dict(calibration)
    if isinstance(calibration, list):
      return PumpCalibration.load_from_list(calibration)
    if isinstance(calibration, (float, int)):
      if num_items is None:
        raise ValueError("num_items must be specified if calibration is a value.")
      return PumpCalibration.load_from_value(calibration, num_items)
    if isinstance(calibration, str):
      if calibration.endswith(".json"):
        return PumpCalibration.load_from_json(calibration)
      if calibration.endswith(".csv"):
        return PumpCalibration.load_from_csv(calibration)
      raise NotImplementedError("Calibration filetype not supported.")
    return PumpCalibration()

  @classmethod
  def load_from_json(cls, file: str) -> PumpCalibration:
    """ Load a calibration from a json file.

    Args:
      file: json file to load calibration from.

    Returns:
      PumpCalibration

    Raises:
      TypeError: if the calibration pulled from the json is not a dictionary or list.
    """
    with open(file, "rb") as f:
      calibration = json.load(f)
    if isinstance(calibration, dict):
      calibration = {int(key): float(value) for key, value in calibration.items()}
      return PumpCalibration.load_from_dict(calibration)
    if isinstance(calibration, list):
      return PumpCalibration(calibration)
    raise TypeError(f"Calibration pulled from {file} is not a dictionary or list.")

  @classmethod
  def load_from_csv(cls, file: str) -> PumpCalibration:
    """ Load a calibration from a csv file.

    Args:
      file: csv file to load calibration from.

    Returns:
      PumpCalibration
    """

    with open(file, encoding="utf-8", newline="") as f:
      reader = csv.DictReader(f)
      calibration = {int(row["0"]): float(row["1"]) for row in reader}
    return PumpCalibration.load_from_dict(calibration)

  @classmethod
  def load_from_dict(cls, calibration: Dict[int, Union[int, float]]) -> PumpCalibration:
    """ Load a calibration from a dictionary.

    Args:
      calibration: dictionary to load calibration from. 0-indexed.

    Returns:
      PumpCalibration

    Raises:
      ValueError: if the calibration dictionary is not formatted correctly.
    """
    if sorted(calibration.keys()) != list(range(len(calibration))):
      raise ValueError("Keys must be a contiguous range of integers starting at 0.")
    calibration_list = [calibration[key] for key in sorted(calibration.keys())]
    return PumpCalibration(calibration_list)

  @classmethod
  def load_from_list(cls, calibration: List[Union[int, float]]) -> PumpCalibration:
    """ Load a calibration from a list. Equivalent to PumpCalibration(calibration).

    Args:
      calibration: list to load calibration from.

    Returns:
      PumpCalibration
    """
    return PumpCalibration(calibration)

  @classmethod
  def load_from_value(cls, value: Union[float, int], num_items: int) -> PumpCalibration:
    """ Load a calibration from a value. Equivalent to PumpCalibration([value] * num_items).

    Args:
      value: value to load calibration from.
      num_items: number of items in the calibration.

    Returns:
      PumpCalibration
    """
    calibration = [value] * num_items
    return PumpCalibration(calibration)
