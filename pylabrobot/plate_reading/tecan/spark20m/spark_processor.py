import struct
import json
import math
import statistics
import logging
from .spark_packet_parser import SparkParser

logger = logging.getLogger(__name__)

class SparkProcessorBase:
    def _parse_raw_data(self, raw_results):

        parser = SparkParser(raw_results)
        return parser.process_all_sequences()

    def _identify_sequences(self, parsed_data):
        ref_seq_key = None
        meas_seq_keys = []

        for key, val in parsed_data.items():
            if isinstance(val, list) and len(val) > 0:
                item = val[0]
                if item.get("type") == "grouped":
                    ref_seq_key = key
                elif item.get("type") == "standalone":
                    meas_seq_keys.append(key)

        meas_seq_keys.sort()
        return ref_seq_key, meas_seq_keys

    def _safe_log10(self, x):
        try:
            return -math.log10(x)
        except ValueError:
            return "Error"
        except TypeError:
            return "Error"

    def _safe_div(self, n, d):
        if d == 0:
            return float('nan')
        return n / d

class AbsorbanceProcessor(SparkProcessorBase):
    def __init__(self):
        pass

    def process(self, raw_results):
        parsed_data = self._parse_raw_data(raw_results)
        if not parsed_data:
            logger.warning("No valid packets found in results.")
            return []

        ref_seq_key, meas_seq_keys = self._identify_sequences(parsed_data)

        if ref_seq_key is None or not meas_seq_keys:
            logger.error("Could not identify Reference (grouped) and Measurement (standalone) sequences.")
            logger.debug(f"Found sequences: {parsed_data.keys()}")
            return []

        try:
            # Calculate average dark values from reference sequence (Block 0)
            dark_block = parsed_data[ref_seq_key][0]["blocks"][0]
            avg_rd_dark = statistics.mean(p["U16RD_DARK_0"] for p in dark_block["rd_md_pairs"])
            avg_md_dark = statistics.mean(p["U16MD_DARK_1"] for p in dark_block["rd_md_pairs"])

            # Calculate average reference values from reference sequence (Block 1)
            ref_block = parsed_data[ref_seq_key][0]["blocks"][1]
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
                        ref_ratio = self._safe_div(ref_md_dark, ref_rd_dark)

                        for loop in loops:
                            sample_md = loop["U16MD_1"]
                            sample_rd = loop["U16RD_0"]
                            sample_md_dark = sample_md - avg_md_dark
                            sample_rd_dark = sample_rd - avg_rd_dark
                            sample_ratio = self._safe_div(sample_md_dark, sample_rd_dark)
                            ratios_md_rd.append(self._safe_div(sample_ratio, ref_ratio))

                        valid_ratios = [r for r in ratios_md_rd if not math.isnan(r)]
                        if valid_ratios:
                            avg_ratio = statistics.mean(valid_ratios)
                            log_ratio = self._safe_log10(avg_ratio)
                        else:
                            log_ratio = "Error"

                        log_ratios_row.append(log_ratio)
                    final_results_list.append(log_ratios_row)
                else:
                    logger.warning(f"Skipping non-standalone or malformed measurement block entry in sequence {seq_key}")

            return final_results_list

        except Exception as e:
            logger.error(f"Error during calculation: {e}", exc_info=True)
            return []

class FluorescenceProcessor(SparkProcessorBase):
    def process(self, raw_results):
        parsed_data = self._parse_raw_data(raw_results)
        if not parsed_data:
             logger.warning("No valid packets found in results.")
             return []

        ref_seq_key, meas_seq_keys = self._identify_sequences(parsed_data)

        if ref_seq_key is None:
            logger.error("Calibration sequence not found.")
            return []

        if not meas_seq_keys:
            logger.error("Measurement sequence not found.")
            return []

        logger.info(f"Using Sequence {ref_seq_key} for Calibration and Sequences {meas_seq_keys} for Measurements.")

        try:
            # Extract dark values
            # Assuming grouped sequence block 0 is dark
            cal_seq_data = parsed_data[ref_seq_key][0]
            block0 = cal_seq_data['blocks'][0]

            # Check if it has dark headers
            if any('DARK' in t for t in block0.get('header_types', [])):
                 dark_pairs = block0['rd_md_pairs']
                 signal_dark_values = []
                 ref_dark_values = []

                 for pair in dark_pairs:
                     md_key = next((k for k in pair.keys() if 'U16MD' in k), None)
                     rd_key = next((k for k in pair.keys() if 'U16RD' in k), None)
                     if md_key and rd_key:
                         signal_dark_values.append(pair[md_key])
                         ref_dark_values.append(pair[rd_key])

                 if not signal_dark_values or not ref_dark_values:
                     logger.error("Could not extract Dark values.")
                     return []

                 signalDark = sum(signal_dark_values) / len(signal_dark_values)
                 refDark = sum(ref_dark_values) / len(ref_dark_values)
            else:
                 logger.error("Block 0 does not look like Dark calibration.")
                 return []

            # Extract bright values
            block1 = cal_seq_data['blocks'][1]
            bright_pairs = block1['rd_md_pairs']
            ref_bright_values = []
            for pair in bright_pairs:
                 rd_key = next((k for k in pair.keys() if 'U16RD' in k), None)
                 if rd_key:
                     ref_bright_values.append(pair[rd_key])

            if not ref_bright_values:
                logger.error("Could not extract Bright Reference values.")
                return []

            refBright = sum(ref_bright_values) / len(ref_bright_values)

            # Calculate K
            k_val = (refBright - refDark) / (65536.0 - signalDark) * 65536.0 * 1.0

            logger.debug(f"signalDark: {signalDark}, refDark: {refDark}, refBright: {refBright}, K: {k_val}")

            # Calculate RFU
            final_results_list = []
            for seq_id in meas_seq_keys:
                meas_seq_data = parsed_data[seq_id][0]
                measurements = meas_seq_data['block']['measurements']
                rfu_row = []

                for measurement in measurements:
                    inner_loops = measurement['inner_loops']

                    raw_signal_values = []
                    raw_ref_signal_values = []
                    for loop in inner_loops:
                        md_key = next((k for k in loop.keys() if 'U16MD' in k), None)
                        rd_key = next((k for k in loop.keys() if 'U16RD' in k), None)
                        if md_key and rd_key:
                             raw_signal_values.append(loop[md_key])
                             raw_ref_signal_values.append(loop[rd_key])

                    if not raw_signal_values or not raw_ref_signal_values:
                        logger.warning(f"Skipping measurement due to missing data.")
                        rfu_row.append("Error")
                        continue

                    rawSignal = sum(raw_signal_values) / len(raw_signal_values)
                    rawRefSignal = sum(raw_ref_signal_values) / len(raw_ref_signal_values)

                    # RFU Calculation
                    rfu = (rawSignal - signalDark) / (rawRefSignal - refDark) * k_val
                    rfu_row.append(rfu)

                final_results_list.append(rfu_row)

            return final_results_list

        except Exception as e:
            logger.error(f"Error during calculation: {e}", exc_info=True)
            return []
