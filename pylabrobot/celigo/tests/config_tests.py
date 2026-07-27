"""Tests for the Celigo hardware-config XML loader."""

import os
import tempfile
import unittest
from unittest.mock import patch

from pylabrobot.celigo.config import (
  CeligoConfig,
  CeligoHardwareConfig,
  load_galvo_calibrations,
  load_galvo_optical_calibration,
  load_illumination_channels,
)
from pylabrobot.celigo.tests.helpers import make_linear_axis_config, require

# A trimmed but structurally faithful USBIOHardwareConfig.config.
COMMON_MOTOR_XML = """
        <ConfigVersion>1001</ConfigVersion>
        <MotorType>0</MotorType>
        <CommIndex>1</CommIndex>
        <ControllerIndex>0</ControllerIndex>
        <Enabled>true</Enabled>
        <MaxVelocity>45</MaxVelocity>
        <MaxAcceleration>45</MaxAcceleration>
        <MaxDeceleration>45</MaxDeceleration>
        <MaxSAcceleration>0</MaxSAcceleration>
        <ModerateAccleration>20</ModerateAccleration>
        <MinimumAcceleration>10</MinimumAcceleration>
        <ModerateSAcceleration>0</ModerateSAcceleration>
        <MinimumSAcceleration>0</MinimumSAcceleration>
        <SCurveSupport>false</SCurveSupport>
        <HomeType>Normal_Accurate</HomeType>
        <HomingVelocity>20</HomingVelocity>
        <IndexVelocity>10</IndexVelocity>
        <HomingShortMove>100</HomingShortMove>
        <HomeOffset>0</HomeOffset>
        <PositiveLimit>true</PositiveLimit>
        <NegativeLimit>true</NegativeLimit>
        <InvertAxisDirection>false</InvertAxisDirection>
        <DefaultPositiveDirection>true</DefaultPositiveDirection>
        <MovingCurrentPercentage>65</MovingCurrentPercentage>
        <HoldingCurrentPercentage>20</HoldingCurrentPercentage>
        <LoadingCurrentPercentage>55</LoadingCurrentPercentage>
        <MovingOverloadLimit>500</MovingOverloadLimit>
        <Mode_EnableLimits>true</Mode_EnableLimits>
        <Mode_EnableStepAndDirection>false</Mode_EnableStepAndDirection>
        <Mode_EnablePositionCorrection>true</Mode_EnablePositionCorrection>
        <Mode_EnableMotorSlaveToEncoder>false</Mode_EnableMotorSlaveToEncoder>
        <CoursePositionErrorWindow>100</CoursePositionErrorWindow>
        <FinePositionErrorWindow>10</FinePositionErrorWindow>
        <Gain>1</Gain>
        <EncoderToMotorTickRatio>256</EncoderToMotorTickRatio>
        <BacklashCompensation>0</BacklashCompensation>
        <MotorResponseTime>1</MotorResponseTime>
"""

SAMPLE_XML = f"""<?xml version="1.0" encoding="utf-8"?>
<USBIOHardwareConfig>
  <xmlSerializerSection type="Cyntellect.Hardware.Instrument.USBIOConfig.USBIOConfigurationFile, Instrument.USBIOConfig">
    <USBIOConfigurationFile xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">
      <XAxis xmlns="Cyntellect.com/USBIOConfig.xsd">
        {COMMON_MOTOR_XML}
        <MotionName>X Axis</MotionName>
        <AxisIndex>1</AxisIndex>
        <HomeOffset>-18</HomeOffset>
        <MinPosition>0</MinPosition>
        <MaxPosition>100</MaxPosition>
        <MMPerEncoderTick>0.0127</MMPerEncoderTick>
        <SomeUnknownField>123</SomeUnknownField>
      </XAxis>
      <YAxis xmlns="Cyntellect.com/USBIOConfig.xsd">
        {COMMON_MOTOR_XML}
        <MotionName>Y Axis</MotionName>
        <AxisIndex>2</AxisIndex>
        <MinPosition>0</MinPosition>
        <MaxPosition>100</MaxPosition>
        <MMPerEncoderTick>0.0127</MMPerEncoderTick>
      </YAxis>
      <ZSingleAxis xmlns="Cyntellect.com/USBIOConfig.xsd">
        {COMMON_MOTOR_XML}
        <MotionName>Z Axis</MotionName>
        <AxisIndex>3</AxisIndex>
        <MinPosition>0</MinPosition>
        <MaxPosition>14.5</MaxPosition>
        <MMPerEncoderTick>0.001</MMPerEncoderTick>
      </ZSingleAxis>
      <DichroicFilterWheel xmlns="Cyntellect.com/USBIOConfig.xsd">
        {COMMON_MOTOR_XML}
        <MotionName>Dichroic</MotionName>
        <AxisIndex>4</AxisIndex>
        <LimitPolarity>0</LimitPolarity>
        <NumberOfEncoderTickPerRev>6000</NumberOfEncoderTickPerRev>
        <NumberOfFilters>6</NumberOfFilters>
        <FilterMap><LogicalNumber>1</LogicalNumber><PhysicalNumber>3</PhysicalNumber></FilterMap>
        <FilterMap><LogicalNumber>2</LogicalNumber><PhysicalNumber>5</PhysicalNumber></FilterMap>
      </DichroicFilterWheel>
      <IOConfiguration xmlns="Cyntellect.com/USBIOConfig.xsd">
        <LightingIOs>
          <ConfigVersion>1000</ConfigVersion><ControllerIndex>0</ControllerIndex>
          <Channel>0</Channel><Enabled>true</Enabled><Invert>false</Invert>
          <IOName>Brightfield</IOName><MinVoltage>0</MinVoltage><MaxVoltage>5</MaxVoltage>
          <DelayMS>0</DelayMS>
        </LightingIOs>
        <LightingIOs>
          <ConfigVersion>1000</ConfigVersion><ControllerIndex>0</ControllerIndex>
          <Channel>1</Channel><Enabled>true</Enabled><Invert>false</Invert>
          <IOName>Green 483/536</IOName><MinVoltage>0</MinVoltage><MaxVoltage>5</MaxVoltage>
          <DelayMS>0</DelayMS>
        </LightingIOs>
        <AnalogIns>
          <ConfigVersion>1000</ConfigVersion><ControllerIndex>0</ControllerIndex>
          <Channel>2</Channel><Enabled>true</Enabled><Invert>false</Invert><IOName>HWAF</IOName>
        </AnalogIns>
      </IOConfiguration>
    </USBIOConfigurationFile>
  </xmlSerializerSection>
</USBIOHardwareConfig>
"""


class TestConfigFromXml(unittest.TestCase):
  def setUp(self):
    fd, self.path = tempfile.mkstemp(suffix=".config")
    with os.fdopen(fd, "w") as f:
      f.write(SAMPLE_XML)

  def tearDown(self):
    os.remove(self.path)

  def test_axes_parsed(self):
    cfg = CeligoHardwareConfig.from_xml(self.path)
    x_axis = require(cfg.x_axis)
    self.assertEqual(x_axis.motion_name, "X Axis")
    self.assertEqual(x_axis.axis_index, 1)
    self.assertEqual(x_axis.comm_index, 1)
    self.assertEqual(x_axis.home_offset, -18.0)
    self.assertEqual(x_axis.home_type, "Normal_Accurate")
    self.assertTrue(x_axis.enabled)
    self.assertTrue(x_axis.mode_enable_position_correction)
    self.assertEqual(x_axis.encoder_to_motor_tick_ratio, 256.0)

  def test_type_coercion(self):
    cfg = CeligoHardwareConfig.from_xml(self.path)
    x_axis = require(cfg.x_axis)
    self.assertIsInstance(x_axis.max_velocity, float)
    self.assertIsInstance(x_axis.axis_index, int)
    self.assertIsInstance(x_axis.enabled, bool)

  def test_unknown_field_kept_in_extra(self):
    cfg = CeligoHardwareConfig.from_xml(self.path)
    self.assertEqual(require(cfg.x_axis).unrecognized_fields.get("SomeUnknownField"), "123")

  def test_z_axis_positions(self):
    cfg = CeligoHardwareConfig.from_xml(self.path)
    self.assertEqual(require(cfg.z_axis).max_position, 14.5)

  def test_filter_wheel(self):
    cfg = CeligoHardwareConfig.from_xml(self.path)
    fw = require(cfg.dichroic_filter_wheel)
    self.assertEqual(fw.number_of_filters, 6)
    self.assertEqual(len(fw.filter_map), 2)
    self.assertEqual(fw.filter_map[0].logical_number, 1)
    self.assertEqual(fw.filter_map[0].physical_number, 3)
    self.assertEqual(fw.motion_name, "Dichroic")

  def test_io_config(self):
    cfg = CeligoHardwareConfig.from_xml(self.path)
    io = require(cfg.io)
    self.assertEqual(len(io.lighting_ios), 2)
    self.assertEqual(io.lighting_ios[1].io_name, "Green 483/536")
    self.assertEqual(len(io.analog_ins), 1)

  def test_source_path_recorded(self):
    cfg = CeligoHardwareConfig.from_xml(self.path)
    self.assertEqual(cfg.source_path, os.path.abspath(self.path))


class TestDirectConstruction(unittest.TestCase):
  def test_user_can_build_in_code(self):
    cfg = CeligoHardwareConfig(
      x_axis=make_linear_axis_config(motion_name="X", axis_index=1, max_velocity=50.0),
    )
    self.assertEqual(require(cfg.x_axis).max_velocity, 50.0)
    self.assertIsNone(cfg.y_axis)


GALVO_CAL_XML = """<?xml version="1.0"?>
<configuration>
  <section name="GalvoCubicCalibrationSection">
    <setting key="GalvoCalibrationConfig_1">
      <Calibrated2DCubicTranformation xmlns="ns">
        <Forward>
          <LinearXTerm xmlns:d="d"><d:X>1.3</d:X><d:Y>-0.004</d:Y></LinearXTerm>
          <LinearYTerm xmlns:d="d"><d:X>0.004</d:X><d:Y>1.31</d:Y></LinearYTerm>
          <OffsetTerm xmlns:d="d"><d:X>0.1</d:X><d:Y>0.2</d:Y></OffsetTerm>
        </Forward>
        <Reverse>
          <LinearXTerm xmlns:d="d"><d:X>0.77</d:X><d:Y>0</d:Y></LinearXTerm>
        </Reverse>
      </Calibrated2DCubicTranformation>
    </setting>
  </section>
</configuration>
"""

ILLUMINATION_XML = """<?xml version="1.0"?>
<LEAPHardwareCalibrationConfig>
  <XGalvo>
    <LaserCenterVoltage>1.6</LaserCenterVoltage>
    <UVLaserCenterVoltage>0.2</UVLaserCenterVoltage>
    <ImageCenter3X><CenterVoltage>5</CenterVoltage><FrameSizeVolts>6.5</FrameSizeVolts></ImageCenter3X>
    <LogicalFilterCenterVoltageOffset><LogicalNumber>2</LogicalNumber><CenterVoltageOffset>0.2</CenterVoltageOffset></LogicalFilterCenterVoltageOffset>
  </XGalvo>
  <YGalvo>
    <LaserCenterVoltage>1.5</LaserCenterVoltage>
    <UVLaserCenterVoltage>0.1</UVLaserCenterVoltage>
    <ImageCenter3X><CenterVoltage>4.9</CenterVoltage><FrameSizeVolts>6.4</FrameSizeVolts></ImageCenter3X>
  </YGalvo>
  <BFVoltageCal><LogicalFilter>1</LogicalFilter><VoltageMag3X>70</VoltageMag3X><VoltageMag10X>80</VoltageMag10X></BFVoltageCal>
  <MultiVariableFLVoltageCal>
    <FLLight>
      <LogicalFilter>2</LogicalFilter><Name>Green 483/536</Name><BitValue>1</BitValue>
      <Intensity><VoltageMag3X>70</VoltageMag3X><VoltageMag10X>80</VoltageMag10X></Intensity>
      <CalibratedZOffsetToBFMM>0.12</CalibratedZOffsetToBFMM>
      <CalibratedMMPerPixelXCorrectionToBF>1.01</CalibratedMMPerPixelXCorrectionToBF>
      <CalibratedMMPerPixelYCorrectionToBF>0.99</CalibratedMMPerPixelYCorrectionToBF>
    </FLLight>
    <FLLight>
      <LogicalFilter>4</LogicalFilter><Name>Blue 377/447</Name><BitValue>0</BitValue>
      <Intensity><VoltageMag3X>30</VoltageMag3X><VoltageMag10X>35</VoltageMag10X></Intensity>
    </FLLight>
  </MultiVariableFLVoltageCal>
</LEAPHardwareCalibrationConfig>
"""

CALIBRATION_XML = """<CalibrationConfig>
  <MicronsPerPixelX>1</MicronsPerPixelX>
  <MicronsPerPixelY>1</MicronsPerPixelY>
  <ImageWidthPixels>2048</ImageWidthPixels>
  <ImageHeightPixels>2048</ImageHeightPixels>
  <ImageToStageThetaRadians>0</ImageToStageThetaRadians>
  <GalvoToStageThetaRadians>0</GalvoToStageThetaRadians>
  <CalibratedPlateCornerX>0</CalibratedPlateCornerX>
  <CalibratedPlateCornerY>0</CalibratedPlateCornerY>
  <CalibratedPlateToStageThetaRadians>0</CalibratedPlateToStageThetaRadians>
  <StageXScale>1</StageXScale>
  <StageYScale>1</StageYScale>
  <StageShear>0</StageShear>
  <StageXShearOffset>0</StageXShearOffset>
  <StageYShearOffset>0</StageYShearOffset>
  <CalibratedZPosition>0</CalibratedZPosition>
  <CalibratedZGlassPlateDelta>0</CalibratedZGlassPlateDelta>
  <ZPlaneXCoeff>0</ZPlaneXCoeff>
  <ZPlaneYCoeff>0</ZPlaneYCoeff>
</CalibrationConfig>"""

HARDWARE_DEFAULT_XML = """<HardwareDefaultConfig>
  <DefaultCalibratedZ>0</DefaultCalibratedZ>
  <DefaultPlateXCornerStageCoordinate>0</DefaultPlateXCornerStageCoordinate>
  <DefaultPlateYCornerStageCoordinate>0</DefaultPlateYCornerStageCoordinate>
  <DefaultXFieldOfViewMM>0</DefaultXFieldOfViewMM>
  <DefaultYFieldOfViewMM>0</DefaultYFieldOfViewMM>
  <DefaultXGalvoMMPerVolt>0</DefaultXGalvoMMPerVolt>
  <DefaultYGalvoMMPerVolt>0</DefaultYGalvoMMPerVolt>
</HardwareDefaultConfig>"""

NAVIGATION_XML = """<NavigationConfig>
  <FrameOverlapXMM>0</FrameOverlapXMM>
  <FrameOverlapYMM>0</FrameOverlapYMM>
  <MaxGalvoDeflectionXMM>0</MaxGalvoDeflectionXMM>
  <MaxGalvoDeflectionYMM>0</MaxGalvoDeflectionYMM>
</NavigationConfig>"""


def _write(xml: str) -> str:
  fd, path = tempfile.mkstemp(suffix=".config")
  with os.fdopen(fd, "w") as f:
    f.write(xml)
  return path


class TestExtraLoaders(unittest.TestCase):
  def test_invalid_boolean_spelling_is_rejected(self):
    malformed = SAMPLE_XML.replace("<Enabled>true</Enabled>", "<Enabled>treu</Enabled>", 1)
    with self.assertRaisesRegex(ValueError, "Invalid boolean"):
      CeligoHardwareConfig.from_xml(_write(malformed))

  def test_fractional_integer_field_is_rejected(self):
    malformed = SAMPLE_XML.replace("<AxisIndex>1</AxisIndex>", "<AxisIndex>1.5</AxisIndex>", 1)
    with self.assertRaisesRegex(ValueError, "AxisIndex must be an integer"):
      CeligoHardwareConfig.from_xml(_write(malformed))

  def test_galvo_voltage_fields(self):
    xml = SAMPLE_XML.replace(
      "</USBIOConfigurationFile>",
      '<XGalvo xmlns="Cyntellect.com/USBIOConfig.xsd">'
      "<ConfigVersion>1000</ConfigVersion><ControllerIndex>0</ControllerIndex>"
      "<MaxVoltage>10</MaxVoltage><MinVoltage>0</MinVoltage>"
      "<InvertVoltage>true</InvertVoltage><PositionErrorWindow>20</PositionErrorWindow>"
      "<VelocityErrorWindow>20</VelocityErrorWindow><BigMoveDelayMS>0</BigMoveDelayMS>"
      "<Enabled>true</Enabled>"
      "</XGalvo></USBIOConfigurationFile>",
    )
    cfg = CeligoHardwareConfig.from_xml(_write(xml))
    x_galvo = require(cfg.x_galvo)
    self.assertEqual(x_galvo.max_voltage, 10.0)
    self.assertTrue(x_galvo.invert_voltage)
    self.assertEqual(x_galvo.position_error_window, 20)

  def test_galvo_calibration_terms(self):
    cal = load_galvo_calibrations(_write(GALVO_CAL_XML))[1]
    self.assertAlmostEqual(cal.forward["LinearXTerm"][0], 1.3)
    self.assertAlmostEqual(cal.forward["LinearXTerm"][1], -0.004)
    self.assertAlmostEqual(cal.forward["OffsetTerm"][1], 0.2)
    self.assertAlmostEqual(cal.reverse["LinearXTerm"][0], 0.77)

  def test_illumination_hardware_recipes(self):
    channels = load_illumination_channels(_write(ILLUMINATION_XML))
    self.assertEqual(set(channels), {"brightfield", "green", "blue"})
    self.assertEqual(channels["brightfield"].logical_filter, 1)
    self.assertEqual(channels["green"].bit_value, 1)
    self.assertEqual(channels["blue"].intensity_percent, 35.0)
    self.assertEqual(channels["green"].z_offset_to_brightfield_mm, 0.12)
    self.assertEqual(channels["green"].mm_per_pixel_x_correction_to_brightfield, 1.01)

  def test_galvo_optical_centers_and_filter_offsets(self):
    calibration = load_galvo_optical_calibration(_write(ILLUMINATION_XML))
    self.assertEqual(calibration.x.magnifications[3].center_voltage, 5.0)
    self.assertEqual(calibration.y.magnifications[3].frame_size_volts, 6.4)
    self.assertEqual(calibration.x.logical_filter_offsets[2], 0.2)

  def test_missing_galvo_center_is_rejected(self):
    malformed = ILLUMINATION_XML.replace("<CenterVoltage>5</CenterVoltage>", "", 1)
    with self.assertRaisesRegex(ValueError, "missing center/frame"):
      load_galvo_optical_calibration(_write(malformed))


class TestAggregateConfig(unittest.TestCase):
  def _write_complete_config(self, directory: str) -> str:
    files = {
      "USBIOHardwareConfig.config": SAMPLE_XML,
      "leaphardwarecalibration.config": ILLUMINATION_XML,
      "ChannelConfig.xml": "<configuration />",
      "CalibrationConfig.xml": CALIBRATION_XML,
      "HardwareDefaultConfig.xml": HARDWARE_DEFAULT_XML,
      "GalvoCalibrationConfig.xml": "<configuration />",
      "NavigationConfig.xml": NAVIGATION_XML,
    }
    for filename, content in files.items():
      with open(os.path.join(directory, filename), "w") as output:
        output.write(content)
    return os.path.join(directory, "USBIOHardwareConfig.config")

  def test_loads_complete_config_after_indexing_directory_once(self):
    with tempfile.TemporaryDirectory() as directory:
      hardware_path = self._write_complete_config(directory)
      with patch(
        "pylabrobot.celigo.config.os.listdir",
        wraps=os.listdir,
      ) as list_directory:
        config = CeligoConfig.from_install(hardware_path, magnification=10)

    list_directory.assert_called_once_with(directory)
    self.assertEqual(config.magnification, 10)
    self.assertEqual(config.hardware.source_path, hardware_path)
    self.assertEqual(set(config.channels), {"brightfield", "green", "blue"})
    self.assertEqual(set(config.channels_by_magnification), {3, 10})
    self.assertEqual(config.navigation.frame_overlap_x_mm, 0.0)

  def test_locates_complete_config_via_configfiles_subdirectory(self):
    with tempfile.TemporaryDirectory() as install_root:
      config_directory = os.path.join(install_root, "ConfigFiles")
      os.makedirs(config_directory)
      self._write_complete_config(config_directory)
      config = CeligoConfig.from_install(install_root)

    self.assertEqual(config.magnification, 3)
    self.assertEqual(require(config.hardware.x_axis).motion_name, "X Axis")

  def test_magnification_channels_are_memory_resident_after_load(self):
    with tempfile.TemporaryDirectory() as directory:
      hardware_path = self._write_complete_config(directory)
      config = CeligoConfig.from_install(hardware_path, magnification=10)

    config.magnification = 3
    self.assertEqual(config.channels["brightfield"].intensity_percent, 70)

  def test_missing_hardware_file_raises(self):
    with (
      tempfile.TemporaryDirectory() as install_root,
      self.assertRaises(FileNotFoundError),
    ):
      CeligoConfig.from_install(install_root)

  def test_missing_companion_file_fails_during_load(self):
    with tempfile.TemporaryDirectory() as directory:
      hardware_path = self._write_complete_config(directory)
      os.remove(os.path.join(directory, "NavigationConfig.xml"))
      with self.assertRaisesRegex(FileNotFoundError, "NavigationConfig.xml"):
        CeligoConfig.from_install(hardware_path)


if __name__ == "__main__":
  unittest.main()
