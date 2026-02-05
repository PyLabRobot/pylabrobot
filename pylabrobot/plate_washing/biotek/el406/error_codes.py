"""
BioTek EL406 Error Codes

This module contains error codes for the BioTek EL406 plate washer.

The error codes provide human-readable descriptions for errors that may
occur during communication with the EL406 plate washer.
"""


ERROR_CODES: dict[int, str] = {
  0x0175: "Error communicating with instrument software. didn't find find park opto sensor transition.",  # 373
  0x0C01: "Requested config/autocal data absent.",  # 3073
  0x0C02: "Calculated checksum didn't match checksum saved.",  # 3074
  0x0C03: "Config parameter out of range.",  # 3075
  0x1001: "Bootcode checksum error at powerup.",  # 4097
  0x1002: "Bootcode error unknown.",  # 4098
  0x1003: "Bootcode page program error.",  # 4099
  0x1004: "Bootcode block size error.",  # 4100
  0x1005: "Bootcode invalid processor signature.",  # 4101
  0x1006: "Bootcode memory exceeded.",  # 4102
  0x1007: "Bootcode invalid slave port.",  # 4103
  0x1008: "Bootcode invalid slave response.",  # 4104
  0x1009: "Bootcode invalid processor detected.",  # 4105
  0x100A: "Bootcode checksum error at powerup.",  # 4106
  0x100B: "Bootcode checksum error at powerup.",  # 4107
  0x100C: "Bootcode checksum error at powerup.",  # 4108
  0x100D: "Bootcode checksum error at powerup.",  # 4109
  0x100E: "Bootcode checksum error at powerup.",  # 4110
  0x100F: "Bootcode checksum error at powerup.",  # 4111
  0x1010: "Bootcode download checksum error.",  # 4112
  0x1250: "UI Processor internal RAM failure.",  # 4688
  0x1251: "MC Processor internal RAM failure.",  # 4689
  0x1300: "Invalid syringe",  # 4864
  0x1301: "Syringe is not connected",  # 4865
  0x1302: "Unable to initialize syringe",  # 4866
  0x1303: "Unable to initialize syringe sensor clear",  # 4867
  0x1304: "Syringe dispense volume out of calibration range",  # 4868
  0x1305: "Invalid syringe operation",  # 4869
  0x1306: "Syringe A FMEA check error",  # 4870
  0x1307: "Syringe B FMEA check error",  # 4871
  0x1355: "The Peri-pump module is not configured",  # 4949
  0x1356: "Invalid Peri-pump dispense position",  # 4950
  0x1357: "The second Peri-pump module is required",  # 4951
  0x1358: "This instrument does not support 0.5 µL Peri-pump dispense volume",  # 4952
  0x1400: "No vacuum pressure detected after turning on the vacuum pump",  # 5120
  0x1401: "The waste bottles must be emptied before continuing",  # 5121
  0x1402: "The valve to be cycle is invalid",  # 5122
  0x1403: "The magnet adapter height is out of range",  # 5123
  0x1404: "Use of the selected plate type is restricted",  # 5124
  0x1405: "Z Axis height error",  # 5125
  0x1406: "Invalid Plate type",  # 5126
  0x1407: "Invalid Step type",  # 5127
  0x1408: "Invalid plate geometry",  # 5128
  0x1409: "Invalid carrier type",  # 5129
  0x140A: "Invalid carrier specified",  # 5130
  0x140B: "Invalid carrier specified",  # 5131
  0x140C: "Invalid carrier specified",  # 5132
  0x140D: "Invalid carrier specified",  # 5133
  0x140E: "Invalid carrier specified",  # 5134
  0x140F: "Invalid carrier specified",  # 5135
  0x1410: "Incompatible hardware configuration",  # 5136
  0x1411: "Invalid carrier specified",  # 5137
  0x1412: "Plate clearance error",  # 5138
  0x1413: "AutoPrime in progress. Please wait until AutoPrime completes.",  # 5139
  0x1414: "AutoPrime is cleaning up. Please wait until AutoPrime cleanup completes.",  # 5140
  0x1415: "An AutoPrime value is out-of-range.",  # 5141
  0x1416: "Vacuum pressure incorrectly detected prior to starting the vacuum pump.",  # 5142
  0x1417: "The autocal sensor was not detected in the back of the instrument.",  # 5143
  0x1430: "Strip washer syringe FMEA check error.",  # 5168
  0x1431: "Strip washer aspirate head not installed.",  # 5169
  0x1432: "Strip washer syringe box not connected.",  # 5170
  0x1433: "Bad step type pointer passed in when finding plate heights.",  # 5171
  0x1500: "There was no buffer fluid present at the start of a manifold-based protocol or at the start of an individual step.",  # 5376
  0x1501: "There was no buffer fluid present immediately before the manifold dispense sequence.",  # 5377
  0x1502: "The buffer valve selection is invalid",  # 5378
  0x1503: "The requested volume to be dispensed through the manifold is smaller than the minimum volume that will be dispensed by the time the DC dispense pump turns on and the dispense valve is opened.",  # 5379
  0x1504: "There was no buffer fluid detected flowing through the manifold tubing during a manifold dispense/prime operation.",  # 5380
  0x1505: "There was no buffer fluid present at the end of a manifold-based protocol or at the end of an individual step.",  # 5381
  0x1506: "The requested carrier Y-axis position is out of range.",  # 5382
  0x1514: "The Ultrasonic Advantage hardware is not configured.",  # 5396
  0x1515: "The low-flow cell-wash hardware is not configured",  # 5397
  0x1516: "Vacuum pressure issue for vacuum filtration",  # 5398
  0x1517: "The software could not read the vacuum filter hardware consistently.",  # 5399
  0x1600: "Ran out of on-board storage space",  # 5632
  0x1601: "Ran out of on-board storage space for P-Dispense steps",  # 5633
  0x1602: "Ran out of on-board storage space for P-Prime steps",  # 5634
  0x1603: "Ran out of on-board storage space for P-Purge steps",  # 5635
  0x1604: "Ran out of on-board storage space for S-Dispense steps",  # 5636
  0x1605: "Ran out of on-board storage space for S-Prime steps",  # 5637
  0x1606: "Ran out of on-board storage space for W-Wash steps",  # 5638
  0x1607: "Ran out of on-board storage space for W-Aspirate steps",  # 5639
  0x1608: "Ran out of on-board storage space for W-Dispense steps",  # 5640
  0x1609: "Ran out of on-board storage space for W-Prime steps",  # 5641
  0x160A: "Ran out of on-board storage space for W-AutoClean steps",  # 5642
  0x160B: "Ran out of on-board storage space for Shake/Soak steps",  # 5643
  0x160C: "Ran out of on-board storage space for 1536 Wash steps",  # 5644
  0x160D: "Invalid Step Type encountered",  # 5645
  0x160E: "Ran out of on-board storage space for P-Purge steps",  # 5646
  0x160F: "Ran out of on-board storage space for P-Purge steps",  # 5647
  0x1610: "Protocol transfer failed.",  # 5648
  0x1700: "Level sensor not installed.",  # 5888
  0x1701: "Level sensor framing error.",  # 5889
  0x1702: "Level sensor timing error.",  # 5890
  0x1703: "Level sensor unknown command.",  # 5891
  0x1704: "Level sensor parameter error.",  # 5892
  0x1705: "Level sensor address error.",  # 5893
  0x1706: "Level sensor error detected but not classified.",  # 5894
  0x1707: "Level sensor response cmd char != request cmd char.",  # 5895
  0x1708: "Level sensor command response not long enough.",  # 5896
  0x1709: "Level sensor command response address not equal to '0'.",  # 5897
  0x170A: "Level sensor command response checksum error.",  # 5898
  0x170B: "Level sensor timeout while looking for SOF char.",  # 5899
  0x170C: "Level sensor RX error - framing error.",  # 5900
  0x170D: "Level sensor RX error in Mode parameter.",  # 5901
  0x170E: "Level sensor RX error in Format parameter.",  # 5902
  0x170F: "Level sensor RX error in Sensitivity parameter.",  # 5903
  0x1710: "Level sensor RX error in Average parameter.",  # 5904
  0x1711: "Level sensor RX error in Temp Comp parameter.",  # 5905
  0x1712: "Level sensor RX error in SDC parameter.",  # 5906
  0x1713: "Level sensor RX error in SDE parameter.",  # 5907
  0x1714: "Level sensor RX error in setting configuration.",  # 5908
  0x1715: "Level sensor error in converting a read to a level.",  # 5909
  0x1716: "7 reads did not come up with at least 3 good ones.",  # 5910
  0x1717: "Level sensor echo range error.",  # 5911
  0x1718: "Level sensor echo width error.",  # 5912
  0x1719: "7 reads did not come up with at least 3 good ones.",  # 5913
  0x171A: "Level sensor - motor axis incorrect in FindAxisCenter().",  # 5914
  0x171B: "7 reads did not come up with at least 3 good ones.",  # 5915
  0x171C: "In FindAxisCenter() initial read not > threshold.",  # 5916
  0x171D: "7 reads did not come up with at least 3 good ones.",  # 5917
  0x171E: "Level sensor - no well edge found - reached step limit.",  # 5918
  0x171F: "Level sensor - repeated FindAxisCenter() did not converge.",  # 5919
  0x1720: "Level sensor corner cal memory checksum error.",  # 5920
  0x1721: "Level sensor A1 cal memory checksum error.",  # 5921
  0x1722: "Level sensor - carrier height wrong - plate test > 30mm.",  # 5922
  0x1723: "A plate read was started but not finished successfully.",  # 5923
  0x1724: "7 reads did not come up with at least 3 good ones.",  # 5924
  0x1725: "The range of the smallest 3 reads (of 7) was > 0.5mm.",  # 5925
  0x1726: "Input to McReqLvlSnsZPosn() out of range.",  # 5926
  0x1727: "The correction factor is out of range.",  # 5927
  0x1728: "7 reads did not come up with at least 3 good ones.",  # 5928
  0x1729: "FindLsyParkPosn() could not find the park position.",  # 5929
  0x172A: "Read Plate or Read One command to MC - invalid Read Type.",  # 5930
  0x172B: "Row or column was 0 - must start at 1.",  # 5931
  0x172C: "Well test error - previous config not loaded.",  # 5932
  0x172D: "Well test error - wrong well.",  # 5933
  0x172E: "7 reads did not come up with at least 3 good ones.",  # 5934
  0x172F: "7 reads did not come up with at least 3 good ones.",  # 5935
  0x1730: "7 reads did not come up with at least 3 good ones.",  # 5936
  0x1731: "7 reads did not come up with at least 3 good ones.",  # 5937
  0x1732: "Level sensor - config memory checksum error.",  # 5938
  0x1733: "Well positions have not been calculated.",  # 5939
  0x1734: "Level sense correction factor not been calculated.",  # 5940
  0x1735: "Doing a Carrier Test - no previous Z-Axis cal data in EEPROM.",  # 5941
  0x1736: "Attempted a Z-axis wash head move with Sensor Y not at park posn.",  # 5942
  0x1737: "Plate test did not find a plate.",  # 5943
  0x1738: "Level sensor - config memory checksum error.",  # 5944
  0x1739: "MC Not all level sensor cal and config data has been loaded.",  # 5945
  0x173A: "Level sensor transmission buffer should be empty before sending a command.",  # 5946
  0x173B: "Level sensor - Z-Cal, Z=0, current to cal > +/-0.75mm.",  # 5947
  0x173C: "Level sensor - Z-Cal, Z=0, factory cal < 23mm or > 29mm.",  # 5948
  0x173D: "Level sensor - Z-Cal, Z=0, post to pre > +/-0.3mm.",  # 5949
  0x173E: "Level sensor - Z-Cal, Z=0, < 15.0mm.",  # 5950
  0x173F: "7 reads did not produce at least 6 good ones.",  # 5951
  0x6100: "The Mini-Tube plate must be used with the Mini-Tube Carrier.",  # 24832
  0x6101: "The 405 TS does not support downloading basecode from the LHC.",  # 24833
  0x6102: "The Mini-Tube plate must be used with the Mini-Tube Carrier.",  # 24834
  0x6110: "The Verify Manifold Test input parameters file was not found.",  # 24848
  0x6111: "The user data file for the Verify Manifold Test could not be read in.",  # 24849
  0x6112: "The Verify Manifold Test was stopped by user.",  # 24850
  0x6113: "The Verify Manifold Test is not supported.",  # 24851
  0x6114: "Invalid well specified.",  # 24852
  0x6115: "Invalid well specified.",  # 24853
  0x6116: "Invalid well specified.",  # 24854
  0x6117: "Invalid well specified.",  # 24855
  0x6118: "Invalid well specified.",  # 24856
  0x6119: "Invalid well specified.",  # 24857
  0x611A: "Invalid well specified.",  # 24858
  0x611B: "Invalid well specified.",  # 24859
  0x611C: "Invalid well specified.",  # 24860
  0x611D: "Invalid well specified.",  # 24861
  0x611E: "Invalid well specified.",  # 24862
  0x611F: "Invalid well specified.",  # 24863
  0x6120: "The carrier is not level.",  # 24864
  0x6121: "The test had an aspirate scan error.",  # 24865
  0x6122: "The test had an dispense scan error.",  # 24866
  0x6123: "Center of well not found where expected for Verify test plate.",  # 24867
  0x6124: "Incorrect plate installed for Verify test.",  # 24868
  0x6125: "The well volume following an aspirate indicates insufficient aspiration.",  # 24869
  0x6126: "The well volume following a dispense indicates insufficient dispense.",  # 24870
  0x6127: "Scan data could not be returned from the instrument.",  # 24871
  0x6128: "Invalid well specified.",  # 24872
  0x6129: "This Verify Manifold Test step was not performed.",  # 24873
  0x6150: "The mean Dispense Volume is out of range.",  # 24912
  0x6151: "The Dispense CV % exceeds the maximum threshold.",  # 24913
  0x6152: "The Aspirate Rate is below the minimum threshold.",  # 24914
  0x6160: "This step requires Washer components to be installed and connected.",  # 24928
  0x6161: "The Strip Washer Manifold and the Plate Type are incompatible.",  # 24929
  0x6162: "The Strip Washer does not support this Plate Type",  # 24930
  0x6165: "This Peri-pump does not support single well dispensing.",  # 24933
  0x6166: "The instrument does not support single well dispensing.",  # 24934
  0x6167: "The Syringe Manifold can only be used with 6-well plates",  # 24935
  0x6168: "The Syringe Manifold can only be used with 12-well plates",  # 24936
  0x6169: "The Syringe Manifold can only be used with 24-well plates",  # 24937
  0x6170: "The Syringe Manifold can only be used with 48-well plates",  # 24944
  0x6171: "The Cassette for single well dispensing does not support this plate type",  # 24945
  0x8100: "Error communicating with instrument software. Message not acknowledged (NAK).",  # 33024
  0x8101: "Error communicating with instrument software. Timeout while waiting for serial message data.",  # 33025
  0x8102: "Error communicating with instrument software. Instrument busy and unable to process message.",  # 33026
  0x8103: "Error communicating with instrument software. Receive buffer overflow error.",  # 33027
  0x8104: "Error communicating with instrument software. Communication checksum error.",  # 33028
  0x8105: "Error communicating with instrument software. Invalid structure type in byMsgStructure header field.",  # 33029
  0x8106: "Error communicating with instrument software. Invalid destination in byMsgDestination header field.",  # 33030
  0x8107: "Error communicating with instrument software. Message sent to instrument is not supported.",  # 33031
  0x8108: "Error communicating with instrument software. Message body size exceeds max limit.",  # 33032
  0x8109: "Error communicating with instrument software. Max number of requests currently running and cannot run the latest request.",  # 33033
  0x810A: "Error communicating with instrument software. No request running when response request issued.",  # 33034
  0x810B: "Error communicating with instrument software. Receive buffer overflow error.",  # 33035
  0x810C: "Error communicating with instrument software. Response for outstanding request not ready yet.",  # 33036
  0x810D: "Error communicating with instrument software. To communicate, the instrument must be at the Main Menu.",  # 33037
  0x810E: "Error communicating with instrument software. One or more request parameters are not valid.",  # 33038
  0x810F: "Error communicating with instrument software. Command not valid in current state.",  # 33039
  0xA100: "<device> not available.",  # 41216
  0xA101: "<device> not available.",  # 41217
  0xA102: "<device> not available.",  # 41218
  0xA103: "<device> not available.",  # 41219
  0xA104: "<device> not available.",  # 41220
  0xA300: "<test type> power supply level error.",  # 41728
  0xA301: "+5v logic power supply level error.",  # 41729
  0xA302: "+24v system/motor power supply level error.",  # 41730
  0xA303: "Internal +42v PeriPump power supply level error.",  # 41731
  0xA304: "Internal reference voltage error.",  # 41732
  0xA305: "External +42v PeriPump power supply level error.",  # 41733
}


def get_error_message(code: int) -> str:
  """
  Get the error message for a given error code.

  Args:
      code: The error code to look up.

  Returns:
      The error message, or a default message if not found.
  """
  return ERROR_CODES.get(code, f"Unknown error code: 0x{code:04X} ({code})")
