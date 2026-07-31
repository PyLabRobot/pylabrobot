import logging
import math
import statistics
from typing import Any, Dict, List, Optional, Tuple

from .spark_packet_parser import SparkParser

logger = logging.getLogger(__name__)

# Block indices in the calibration sequence
DARK_BLOCK_INDEX = 0
REFERENCE_BLOCK_INDEX = 1

# 16-bit ADC maximum value (2^16)
UINT16_MAX = 65536.0


def _parse_raw_data(raw_results: List[bytes]) -> Dict[int, Any]:
  parser = SparkParser(raw_results)
  return parser.process_all_sequences()


def _identify_sequences(parsed_data: Dict[Any, Any]) -> Tuple[Optional[Any], List[Any]]:
  ref_seq_key: Optional[Any] = None
  meas_seq_keys: List[Any] = []

  for key, val in parsed_data.items():
    if isinstance(val, list) and len(val) > 0:
      item = val[0]
      if item.get("type") == "grouped":
        ref_seq_key = key
      elif item.get("type") == "standalone":
        meas_seq_keys.append(key)

  meas_seq_keys.sort()
  return ref_seq_key, meas_seq_keys


def _safe_div(n: float, d: float) -> float:
  if d == 0:
    return float("nan")
  return n / d


def _get_dark_for_wl(
  dark_by_wl: Dict[float, Tuple[float, float]], wl: float
) -> Tuple[float, float]:
  """Get dark (rd, md) values for a given wavelength, falling back to closest."""
  if wl in dark_by_wl:
    return dark_by_wl[wl]
  if dark_by_wl:
    closest = min(dark_by_wl.keys(), key=lambda x: abs(x - wl))
    return dark_by_wl[closest]
  return (0.0, 0.0)


def _find_key(
  pair: Dict[str, Any], *contains: str, exclude: Optional[List[str]] = None
) -> Optional[str]:
  """Find a key in a dict that contains all the given substrings and excludes others."""
  exclude = exclude or []
  for k in pair:
    if all(c in k for c in contains) and not any(e in k for e in exclude):
      return k
  return None


def _extract_measurement_pairs(block: Dict[str, Any]) -> List[Tuple[float, List[Dict[str, Any]]]]:
  """Extract (wavelength, pairs) from a measurement block.

  Handles both nested_mult (measurements → inner_loops) and flat (rd_md_pairs) structures.
  Returns a list of (wavelength, pairs) tuples.
  """
  result: List[Tuple[float, List[Dict[str, Any]]]] = []

  if "measurements" in block and block["measurements"]:
    for measurement in block["measurements"]:
      # In nested_mult, wavelength tag is at the measurement level
      wl_key = _find_key(measurement, "x10U16RWL") or _find_key(measurement, "x10U16MWL")
      wl = measurement[wl_key] if wl_key is not None else 0.0
      inner_loops = measurement.get("inner_loops", [])
      result.append((wl, inner_loops))
  elif "rd_md_pairs" in block:
    # Flat structure: group pairs by wavelength
    by_wl: Dict[float, List[Dict[str, Any]]] = {}
    for pair in block["rd_md_pairs"]:
      wl_key = _find_key(pair, "x10U16RWL") or _find_key(pair, "x10U16MWL")
      wl = pair[wl_key] if wl_key is not None else 0.0
      by_wl.setdefault(wl, []).append(pair)
    result.extend(by_wl.items())

  return result


def _extract_fluo_calibration(
  parsed_data: Dict[Any, Any], ref_seq_key: Any
) -> Optional[Tuple[float, float, float]]:
  """Extract fluorescence calibration values (signal_dark, ref_dark, k_val).

  Returns None on failure.
  """
  cal_seq_data = parsed_data[ref_seq_key][0]
  dark_block = cal_seq_data["blocks"][DARK_BLOCK_INDEX]

  if not any("DARK" in t for t in dark_block.get("header_types", [])):
    logger.error("Dark block does not look like Dark calibration.")
    return None

  dark_pairs = dark_block["rd_md_pairs"]
  signal_dark_values = []
  ref_dark_values = []

  for pair in dark_pairs:
    md_key = next((k for k in pair if "U16MD" in k), None)
    rd_key = next((k for k in pair if "U16RD" in k), None)
    if md_key and rd_key:
      signal_dark_values.append(pair[md_key])
      ref_dark_values.append(pair[rd_key])

  if not signal_dark_values or not ref_dark_values:
    logger.error("Could not extract Dark values.")
    return None

  signal_dark = statistics.mean(signal_dark_values)
  ref_dark = statistics.mean(ref_dark_values)

  # Extract bright reference values
  ref_block = cal_seq_data["blocks"][REFERENCE_BLOCK_INDEX]
  ref_bright_values: List[float] = []
  for pair in ref_block["rd_md_pairs"]:
    rd_key = next((k for k in pair if "U16RD" in k), None)
    if rd_key:
      ref_bright_values.append(pair[rd_key])

  if not ref_bright_values:
    logger.error("Could not extract Bright Reference values.")
    return None

  ref_bright = statistics.mean(ref_bright_values)

  # Calculate K
  denominator = UINT16_MAX - signal_dark
  if denominator == 0:
    logger.error("Division by zero: signal_dark equals UINT16_MAX")
    return None
  k_val = (ref_bright - ref_dark) / denominator * UINT16_MAX

  return signal_dark, ref_dark, k_val


def _reshape_to_rows(
  wavelength_data: Dict[float, List[float]], num_rows: int
) -> Dict[float, List[List[float]]]:
  """Reshape flat wavelength→values into wavelength→rows×cols."""
  result: Dict[float, List[List[float]]] = {}
  for wl, values in sorted(wavelength_data.items()):
    if num_rows > 0:
      cols_per_row = len(values) // num_rows
      if cols_per_row > 0:
        result[wl] = [values[i * cols_per_row : (i + 1) * cols_per_row] for i in range(num_rows)]
      else:
        result[wl] = [values]
    else:
      result[wl] = [values]
  return result


def process_absorbance(raw_results: List[bytes]) -> List[List[float]]:
  empty_result: List[List[float]] = []

  parsed_data = _parse_raw_data(raw_results)
  if not parsed_data:
    logger.warning("No valid packets found in results.")
    return empty_result

  ref_seq_key, meas_seq_keys = _identify_sequences(parsed_data)

  if ref_seq_key is None or not meas_seq_keys:
    logger.error("Could not identify Reference (grouped) and Measurement (standalone) sequences.")
    logger.debug(f"Found sequences: {list(parsed_data)}")
    return empty_result

  try:
    # Calculate average dark values from reference sequence
    dark_block = parsed_data[ref_seq_key][0]["blocks"][DARK_BLOCK_INDEX]
    avg_rd_dark = statistics.mean(p["U16RD_DARK_0"] for p in dark_block["rd_md_pairs"])
    avg_md_dark = statistics.mean(p["U16MD_DARK_1"] for p in dark_block["rd_md_pairs"])

    # Calculate average reference values from reference sequence
    ref_block = parsed_data[ref_seq_key][0]["blocks"][REFERENCE_BLOCK_INDEX]
    avg_rd_ref = statistics.mean(p["U16RD_0"] for p in ref_block["rd_md_pairs"])
    avg_md_ref = statistics.mean(p["U16MD_1"] for p in ref_block["rd_md_pairs"])

    # Analyze each measurement sequence
    final_results_list = []
    for seq_key in meas_seq_keys:
      meas_block_entry = parsed_data[seq_key][0]
      if meas_block_entry.get("type") == "standalone" and "block" in meas_block_entry:
        measurements = meas_block_entry["block"]["measurements"]
        log_ratios_row = []

        for m in measurements:
          loops = m["inner_loops"]

          ratios_md_rd = []
          ref_md_dark = avg_md_ref - avg_md_dark
          ref_rd_dark = avg_rd_ref - avg_rd_dark
          ref_ratio = _safe_div(ref_md_dark, ref_rd_dark)

          for loop in loops:
            sample_md = loop["U16MD_1"]
            sample_rd = loop["U16RD_0"]
            sample_md_dark = sample_md - avg_md_dark
            sample_rd_dark = sample_rd - avg_rd_dark
            sample_ratio = _safe_div(sample_md_dark, sample_rd_dark)
            ratios_md_rd.append(_safe_div(sample_ratio, ref_ratio))

          valid_ratios = [r for r in ratios_md_rd if not math.isnan(r)]
          if valid_ratios:
            avg_ratio = statistics.mean(valid_ratios)
            if avg_ratio > 0:
              log_ratio = -math.log10(avg_ratio)
            else:
              logger.warning(f"Non-positive avg_ratio ({avg_ratio}) in sequence {seq_key}")
              log_ratio = float("nan")
          else:
            logger.warning(f"All ratios are NaN in sequence {seq_key}")
            log_ratio = float("nan")

          log_ratios_row.append(log_ratio)
        final_results_list.append(log_ratios_row)
      else:
        logger.warning(
          f"Skipping non-standalone or malformed measurement block entry in sequence {seq_key}"
        )

    return final_results_list

  except Exception as e:
    logger.error(f"Error during calculation: {e}", exc_info=True)
    return empty_result


def process_fluorescence(raw_results: List[bytes]) -> List[List[float]]:
  empty_result: List[List[float]] = []

  parsed_data = _parse_raw_data(raw_results)
  if not parsed_data:
    logger.warning("No valid packets found in results.")
    return empty_result

  ref_seq_key, meas_seq_keys = _identify_sequences(parsed_data)

  if ref_seq_key is None:
    logger.error("Calibration sequence not found.")
    return empty_result

  if not meas_seq_keys:
    logger.error("Measurement sequence not found.")
    return empty_result

  logger.info(
    f"Using Sequence {ref_seq_key} for Calibration and Sequences {meas_seq_keys} for Measurements."
  )

  try:
    cal = _extract_fluo_calibration(parsed_data, ref_seq_key)
    if cal is None:
      return empty_result
    signal_dark, ref_dark, k_val = cal

    logger.debug(f"signal_dark: {signal_dark}, ref_dark: {ref_dark}, K: {k_val}")

    # Calculate RFU
    final_results_list: List[List[float]] = []
    for seq_id in meas_seq_keys:
      meas_seq_data = parsed_data[seq_id][0]
      measurements = meas_seq_data["block"]["measurements"]
      rfu_row: List[float] = []

      for measurement in measurements:
        inner_loops = measurement["inner_loops"]

        raw_signal_values = []
        raw_ref_signal_values = []
        for loop in inner_loops:
          md_key = next((k for k in loop if "U16MD" in k), None)
          rd_key = next((k for k in loop if "U16RD" in k), None)
          if md_key and rd_key:
            raw_signal_values.append(loop[md_key])
            raw_ref_signal_values.append(loop[rd_key])

        if not raw_signal_values or not raw_ref_signal_values:
          logger.warning("Skipping measurement due to missing data.")
          rfu_row.append(float("nan"))
          continue

        raw_signal = statistics.mean(raw_signal_values)
        raw_ref_signal = statistics.mean(raw_ref_signal_values)

        # RFU Calculation
        rfu = _safe_div(raw_signal - signal_dark, raw_ref_signal - ref_dark) * k_val
        rfu_row.append(rfu)

      final_results_list.append(rfu_row)

    return final_results_list

  except Exception as e:
    logger.error(f"Error during calculation: {e}", exc_info=True)
    return empty_result


def process_absorbance_spectrum(raw_results: List[bytes]) -> Dict[float, List[List[float]]]:
  """Process raw data from an absorbance spectrum scan.

  In spectrum mode, each standalone measurement block contains data for multiple
  wavelengths. The TDCL header includes ``x10U16RWL`` which tags each measurement
  point with the reference wavelength (in tenths of nm).

  Returns:
    A dict mapping wavelength (nm) to a 2D list of OD values (rows × cols).
    Returns an empty dict on failure.
  """
  empty_result: Dict[float, List[List[float]]] = {}

  parsed_data = _parse_raw_data(raw_results)
  if not parsed_data:
    logger.warning("No valid packets found in results.")
    return empty_result

  ref_seq_key, meas_seq_keys = _identify_sequences(parsed_data)

  if ref_seq_key is None or not meas_seq_keys:
    logger.error("Could not identify Reference (grouped) and Measurement (standalone) sequences.")
    return empty_result

  try:
    # Extract dark values from reference sequence, grouped by wavelength
    dark_block = parsed_data[ref_seq_key][0]["blocks"][DARK_BLOCK_INDEX]
    dark_by_wl: Dict[float, Tuple[float, float]] = {}  # wl -> (rd_dark, md_dark)

    for pair in dark_block["rd_md_pairs"]:
      wl_key = _find_key(pair, "x10U16RWL")
      wl = pair[wl_key] if wl_key is not None else 0.0
      rd_dark_key = _find_key(pair, "U16RD_DARK")
      md_dark_key = _find_key(pair, "U16MD_DARK")
      if rd_dark_key and md_dark_key:
        dark_by_wl[wl] = (pair[rd_dark_key], pair[md_dark_key])

    # Fallback: global average dark if wavelength grouping fails
    if not dark_by_wl:
      rd_dark_key = _find_key(dark_block["rd_md_pairs"][0], "U16RD_DARK")
      md_dark_key = _find_key(dark_block["rd_md_pairs"][0], "U16MD_DARK")
      if rd_dark_key and md_dark_key:
        avg_rd = statistics.mean(p[rd_dark_key] for p in dark_block["rd_md_pairs"])
        avg_md = statistics.mean(p[md_dark_key] for p in dark_block["rd_md_pairs"])
        dark_by_wl[0.0] = (avg_rd, avg_md)

    # Calculate per-wavelength reference ratios
    ref_block = parsed_data[ref_seq_key][0]["blocks"][REFERENCE_BLOCK_INDEX]
    ref_ratios: Dict[float, float] = {}

    ref_pairs_by_wl: Dict[float, List[Dict[str, Any]]] = {}
    for pair in ref_block["rd_md_pairs"]:
      wl_key = _find_key(pair, "x10U16RWL")
      wl = pair[wl_key] if wl_key is not None else 0.0
      ref_pairs_by_wl.setdefault(wl, []).append(pair)

    for wl, pairs in ref_pairs_by_wl.items():
      rd_key = _find_key(pairs[0], "U16RD", exclude=["DARK", "GAIN"])
      md_key = _find_key(pairs[0], "U16MD", exclude=["DARK", "GAIN"])
      if rd_key and md_key:
        avg_rd_ref = statistics.mean(p[rd_key] for p in pairs)
        avg_md_ref = statistics.mean(p[md_key] for p in pairs)
        rd_dark, md_dark = _get_dark_for_wl(dark_by_wl, wl)
        ref_ratios[wl] = _safe_div(avg_md_ref - md_dark, avg_rd_ref - rd_dark)

    # Process each measurement sequence
    wavelength_data: Dict[float, List[float]] = {}

    for seq_key in meas_seq_keys:
      meas_block_entry = parsed_data[seq_key][0]
      if meas_block_entry.get("type") != "standalone":
        continue

      block = meas_block_entry.get("block", meas_block_entry)
      for wl, pairs in _extract_measurement_pairs(block):
        ref_ratio = ref_ratios.get(wl)
        if ref_ratio is None and ref_ratios:
          closest_wl = min(ref_ratios.keys(), key=lambda x: abs(x - wl))
          ref_ratio = ref_ratios[closest_wl]
        if ref_ratio is None:
          wavelength_data.setdefault(wl, []).append(float("nan"))
          continue

        rd_dark, md_dark = _get_dark_for_wl(dark_by_wl, wl)

        ratios = []
        for pair in pairs:
          md_key = _find_key(pair, "U16MD", exclude=["DARK", "GAIN"])
          rd_key = _find_key(pair, "U16RD", exclude=["DARK", "GAIN"])
          if md_key and rd_key:
            sample_ratio = _safe_div(pair[md_key] - md_dark, pair[rd_key] - rd_dark)
            ratios.append(_safe_div(sample_ratio, ref_ratio))

        valid_ratios = [r for r in ratios if not math.isnan(r)]
        if valid_ratios:
          avg_ratio = statistics.mean(valid_ratios)
          od = -math.log10(avg_ratio) if avg_ratio > 0 else float("nan")
        else:
          od = float("nan")

        wavelength_data.setdefault(wl, []).append(od)

    return _reshape_to_rows(wavelength_data, len(meas_seq_keys))

  except Exception as e:
    logger.error(f"Error during absorbance spectrum calculation: {e}", exc_info=True)
    return empty_result


def process_fluorescence_spectrum(raw_results: List[bytes]) -> Dict[float, List[List[float]]]:
  """Process raw data from a fluorescence excitation spectrum scan.

  In spectrum mode, each standalone measurement block has a nested MULT structure
  where the outer loop iterates over wavelength steps and the inner loop iterates
  over reads at each wavelength. The TDCL header includes both ``x10U16MWL``
  (measurement/excitation wavelength) and ``x10U16RWL`` (reference wavelength).

  Returns:
    A dict mapping excitation wavelength (nm) to a 2D list of RFU values (rows × cols).
    Returns an empty dict on failure.
  """
  empty_result: Dict[float, List[List[float]]] = {}

  parsed_data = _parse_raw_data(raw_results)
  if not parsed_data:
    logger.warning("No valid packets found in results.")
    return empty_result

  ref_seq_key, meas_seq_keys = _identify_sequences(parsed_data)

  if ref_seq_key is None:
    logger.error("Calibration sequence not found.")
    return empty_result

  if not meas_seq_keys:
    logger.error("Measurement sequence not found.")
    return empty_result

  try:
    cal = _extract_fluo_calibration(parsed_data, ref_seq_key)
    if cal is None:
      return empty_result
    signal_dark, ref_dark, k_val = cal

    # Process each measurement sequence
    wavelength_data: Dict[float, List[float]] = {}

    for seq_id in meas_seq_keys:
      meas_seq_data = parsed_data[seq_id][0]
      block = meas_seq_data.get("block", meas_seq_data)

      for wl, pairs in _extract_measurement_pairs(block):
        raw_signal_values = []
        raw_ref_signal_values = []
        for pair in pairs:
          md_key = _find_key(pair, "U16MD", exclude=["DARK"])
          rd_key = _find_key(pair, "U16RD", exclude=["DARK"])
          if md_key and rd_key:
            raw_signal_values.append(pair[md_key])
            raw_ref_signal_values.append(pair[rd_key])

        if not raw_signal_values or not raw_ref_signal_values:
          wavelength_data.setdefault(wl, []).append(float("nan"))
          continue

        raw_signal = statistics.mean(raw_signal_values)
        raw_ref_signal = statistics.mean(raw_ref_signal_values)

        rfu = _safe_div(raw_signal - signal_dark, raw_ref_signal - ref_dark) * k_val
        wavelength_data.setdefault(wl, []).append(rfu)

    return _reshape_to_rows(wavelength_data, len(meas_seq_keys))

  except Exception as e:
    logger.error(f"Error during fluorescence spectrum calculation: {e}", exc_info=True)
    return empty_result
