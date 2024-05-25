from __future__ import annotations

import csv
import json
from typing import Union, Dict, List, Optional, Literal


class PumpCalibration:
  """ Calibration for a single pump or pump array

  Attributes:
    calibration: The calibration of the pump or pump array.
  """

  def __init__(
    self,
    calibration: List[Union[float, int]],
    calibration_mode: Literal["duration", "revolutions"] = "duration"
  ):
    """ Initialize a PumpCalibration object.

    Args:
      calibration: calibration of the pump in pump-specific volume per time/revolution units.
      calibration_mode: units of the calibration. "duration" for volume per time, "revolutions" for
        volume per revolution. Defaults to "duration".

    Raises:
      ValueError: if a value in the calibration is outside expected parameters.
    """

    if any(value <= 0 for value in calibration):
      raise ValueError("A value in the calibration is is outside expected parameters.")
    if calibration_mode not in ["duration", "revolutions"]:
      raise ValueError("calibration_mode must be 'duration' or 'revolutions'")
    self.calibration = calibration
    self.calibration_mode = calibration_mode

  def __getitem__(self, item: int) -> Union[float, int]:
    return self.calibration[item]  # type: ignore

  def __len__(self) -> int:
    """ Return the length of the calibration. """

    return len(self.calibration)

  @classmethod
  def load_calibration(
    cls,
    calibration: Optional[Union[dict, list, float, int, str]] = None,
    num_items: Optional[int] = None,
    calibration_mode: Literal["duration", "revolutions"] = "duration"
  ) -> PumpCalibration:
    """ Load a calibration from a file, dictionary, list, or value. :param calibration: pump
    calibration file, dictionary, list, or value. If None, returns an empty PumpCalibration
    object.

    Args:
      calibration: pump calibration file, dictionary, list, or value.
      calibration_mode: units of the calibration. "duration" for volume per time, "revolutions" for
        volume per revolution. Defaults to "duration".
      num_items: number of items in the calibration. Required if calibration is a value.

    Raises:
      NotImplementedError: if the calibration filetype or format is not supported.
      ValueError: if num_items is not specified when calibration is a value.
    """

    if isinstance(calibration, dict):
      return PumpCalibration.load_from_dict(calibration=calibration,
                                            calibration_mode=calibration_mode)
    if isinstance(calibration, list):
      return PumpCalibration.load_from_list(calibration=calibration,
                                            calibration_mode=calibration_mode)
    if isinstance(calibration, (float, int)):
      if num_items is None:
        raise ValueError("num_items must be specified if calibration is a value.")
      return PumpCalibration.load_from_value(value=calibration,
                                             num_items=num_items,
                                             calibration_mode=calibration_mode)
    if isinstance(calibration, str):
      if calibration.endswith(".json"):
        return PumpCalibration.load_from_json(file_path=calibration,
                                              calibration_mode=calibration_mode)
      if calibration.endswith(".csv"):
        return PumpCalibration.load_from_csv(file_path=calibration,
                                             calibration_mode=calibration_mode)
      raise NotImplementedError("Calibration filetype not supported.")
    raise NotImplementedError("Calibration format not supported.")

  def serialize(self) -> dict:
    return {
      "calibration": self.calibration,
      "calibration_mode": self.calibration_mode
    }

  @classmethod
  def deserialize(cls, data: dict) -> PumpCalibration:
    return cls(calibration=data["calibration"], calibration_mode=data["calibration_mode"])

  @classmethod
  def load_from_json(
    cls,
    file_path: str,
    calibration_mode: Literal["duration", "revolutions"] = "duration"
  ) -> PumpCalibration:
    """ Load a calibration from a json file.

    Args:
      file_path: json file to load calibration from.
      calibration_mode: units of the calibration. "duration" for volume per time, "revolutions" for
        volume per revolution. Defaults to "duration".

    Raises:
      TypeError: if the calibration pulled from the json is not a dictionary or list.
    """

    with open(file_path, "rb") as f:
      calibration = json.load(f)
    if isinstance(calibration, dict):
      calibration = {int(key): float(value) for key, value in calibration.items()}
      return PumpCalibration.load_from_dict(calibration=calibration,
                                            calibration_mode=calibration_mode)
    if isinstance(calibration, list):
      return PumpCalibration(calibration=calibration, calibration_mode=calibration_mode)
    raise TypeError(f"Calibration pulled from {file_path} is not a dictionary or list.")

  @classmethod
  def load_from_csv(
    cls,
    file_path: str,
    calibration_mode: Literal["duration", "revolutions"] = "duration"
  ) -> PumpCalibration:
    """ Load a calibration from a csv file.

    Args:
      file_path: csv file to load calibration from. 0-indexed. The first column is treated as the
        index, the second column as the value.
      calibration_mode: units of the calibration. "duration" for volume per time, "revolutions" for
        volume per revolution. Defaults to "duration".
    """

    with open(file_path, encoding="utf-8", newline="") as f:
      csv_file = list(csv.reader(f))
      num_columns = len(csv_file[0])
      if num_columns != 2:
        raise ValueError("CSV file must have two columns.")
      calibration = {int(row[0]): float(row[1]) for row in csv_file}
      return PumpCalibration.load_from_dict(calibration=calibration,
                                            calibration_mode=calibration_mode)

  @classmethod
  def load_from_dict(
    cls,
    calibration: Dict[int, Union[int, float]],
    calibration_mode: Literal["duration", "revolutions"] = "duration"
  ) -> PumpCalibration:
    """ Load a calibration from a dictionary.

    Args:
      calibration: dictionary to load calibration from. 0-indexed.
      calibration_mode: units of the calibration. "duration" for volume per time, "revolutions" for
        volume per revolution. Defaults to "duration".

    Raises:
      ValueError: if the calibration dictionary is not formatted correctly.
    """

    if sorted(calibration.keys()) != list(range(len(calibration))):
      raise ValueError("Keys must be a contiguous range of integers starting at 0.")
    calibration_list = [calibration[key] for key in sorted(calibration.keys())]
    return cls(calibration=calibration_list, calibration_mode=calibration_mode)

  @classmethod
  def load_from_list(
    cls,
    calibration: List[Union[int, float]],
    calibration_mode: Literal["duration", "revolutions"] = "duration"
  ) -> PumpCalibration:
    """ Load a calibration from a list. Equivalent to PumpCalibration(calibration).

    Args:
      calibration: list to load calibration from.
      calibration_mode: units of the calibration. "duration" for volume per time, "revolutions" for
        volume per revolution. Defaults to "duration".
    """

    return cls(calibration=calibration, calibration_mode=calibration_mode)

  @classmethod
  def load_from_value(
    cls,
    value: Union[float, int],
    num_items: int,
    calibration_mode: Literal["duration", "revolutions"] = "duration"
  ) -> PumpCalibration:
    """ Load a calibration from a value. Equivalent to PumpCalibration([value] * num_items).

    Args:
      value: value to load calibration from.
      num_items: number of items in the calibration.
      calibration_mode: units of the calibration. "duration" for volume per time, "revolutions" for
        volume per revolution. Defaults to "duration".
    """

    calibration = [value] * num_items
    return cls(calibration, calibration_mode)
