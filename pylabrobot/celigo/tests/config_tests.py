"""Tests for the Celigo hardware-config XML loader."""

import os
import tempfile
import unittest

from pylabrobot.celigo.config import (
  AxisConfig,
  CeligoHardwareConfig,
  load_galvo_calibration,
)

# A trimmed but structurally faithful USBIOHardwareConfig.config.
SAMPLE_XML = """<?xml version="1.0" encoding="utf-8"?>
<USBIOHardwareConfig>
  <xmlSerializerSection type="Cyntellect.Hardware.Instrument.USBIOConfig.USBIOConfigurationFile, Instrument.USBIOConfig">
    <USBIOConfigurationFile xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">
      <XAxis xmlns="Cyntellect.com/USBIOConfig.xsd">
        <ConfigVersion>1001</ConfigVersion>
        <ControllerIndex>0</ControllerIndex>
        <MaxVelocity>45</MaxVelocity>
        <MotionName>X Axis</MotionName>
        <MotorType>0</MotorType>
        <CommIndex>1</CommIndex>
        <Enabled>true</Enabled>
        <AxisIndex>1</AxisIndex>
        <HomeType>Normal_Accurate</HomeType>
        <HomeOffset>-18</HomeOffset>
        <MovingCurrentPercentage>65</MovingCurrentPercentage>
        <Mode_EnablePositionCorrection>true</Mode_EnablePositionCorrection>
        <EncoderToMotorTickRatio>256</EncoderToMotorTickRatio>
        <SomeUnknownField>123</SomeUnknownField>
      </XAxis>
      <YAxis xmlns="Cyntellect.com/USBIOConfig.xsd">
        <MotionName>Y Axis</MotionName>
        <AxisIndex>2</AxisIndex>
        <Enabled>true</Enabled>
      </YAxis>
      <ZSingleAxis xmlns="Cyntellect.com/USBIOConfig.xsd">
        <MotionName>Z Axis</MotionName>
        <MinPosition>0</MinPosition>
        <MaxPosition>14.5</MaxPosition>
      </ZSingleAxis>
      <DichroicFilterWheel xmlns="Cyntellect.com/USBIOConfig.xsd">
        <MotionName>Dichroic</MotionName>
        <NumberOfFilters>6</NumberOfFilters>
        <FilterMap><LogicalNumber>1</LogicalNumber><PhysicalNumber>3</PhysicalNumber></FilterMap>
        <FilterMap><LogicalNumber>2</LogicalNumber><PhysicalNumber>5</PhysicalNumber></FilterMap>
      </DichroicFilterWheel>
      <IOConfiguration xmlns="Cyntellect.com/USBIOConfig.xsd">
        <LightingIOs><IOName>Brightfield</IOName><Channel>0</Channel><MaxVoltage>5</MaxVoltage></LightingIOs>
        <LightingIOs><IOName>Green 483/536</IOName><Channel>1</Channel><MaxVoltage>5</MaxVoltage></LightingIOs>
        <AnalogIns><IOName>HWAF</IOName><Channel>2</Channel></AnalogIns>
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
    self.assertIsNotNone(cfg.x_axis)
    self.assertEqual(cfg.x_axis.motion_name, "X Axis")
    self.assertEqual(cfg.x_axis.axis_index, 1)
    self.assertEqual(cfg.x_axis.comm_index, 1)
    self.assertEqual(cfg.x_axis.home_offset, -18.0)
    self.assertEqual(cfg.x_axis.home_type, "Normal_Accurate")
    self.assertTrue(cfg.x_axis.enabled)
    self.assertTrue(cfg.x_axis.mode_enable_position_correction)
    self.assertEqual(cfg.x_axis.encoder_to_motor_tick_ratio, 256.0)

  def test_type_coercion(self):
    cfg = CeligoHardwareConfig.from_xml(self.path)
    self.assertIsInstance(cfg.x_axis.max_velocity, float)
    self.assertIsInstance(cfg.x_axis.axis_index, int)
    self.assertIsInstance(cfg.x_axis.enabled, bool)

  def test_unknown_field_kept_in_extra(self):
    cfg = CeligoHardwareConfig.from_xml(self.path)
    self.assertEqual(cfg.x_axis.extra.get("SomeUnknownField"), "123")

  def test_z_axis_positions(self):
    cfg = CeligoHardwareConfig.from_xml(self.path)
    self.assertEqual(cfg.z_axis.max_position, 14.5)

  def test_filter_wheel(self):
    cfg = CeligoHardwareConfig.from_xml(self.path)
    fw = cfg.dichroic_filter_wheel
    self.assertEqual(fw.number_of_filters, 6)
    self.assertEqual(len(fw.filter_map), 2)
    self.assertEqual(fw.filter_map[0].logical_number, 1)
    self.assertEqual(fw.filter_map[0].physical_number, 3)

  def test_io_config(self):
    cfg = CeligoHardwareConfig.from_xml(self.path)
    self.assertEqual(len(cfg.io.lighting_ios), 2)
    self.assertEqual(cfg.io.lighting_ios[1].io_name, "Green 483/536")
    self.assertEqual(len(cfg.io.analog_ins), 1)

  def test_source_path_recorded(self):
    cfg = CeligoHardwareConfig.from_xml(self.path)
    self.assertEqual(cfg.source_path, os.path.abspath(self.path))


class TestFromInstall(unittest.TestCase):
  def test_locates_via_configfiles_subdir(self):
    with tempfile.TemporaryDirectory() as root:
      cfgdir = os.path.join(root, "ConfigFiles")
      os.makedirs(cfgdir)
      with open(os.path.join(cfgdir, "USBIOHardwareConfig.config"), "w") as f:
        f.write(SAMPLE_XML)
      cfg = CeligoHardwareConfig.from_install(install_dir=root)
      self.assertEqual(cfg.x_axis.motion_name, "X Axis")

  def test_missing_raises(self):
    with tempfile.TemporaryDirectory() as root:
      with self.assertRaises(FileNotFoundError):
        CeligoHardwareConfig.from_install(install_dir=root)


class TestDirectConstruction(unittest.TestCase):
  def test_user_can_build_in_code(self):
    cfg = CeligoHardwareConfig(
      x_axis=AxisConfig(motion_name="X", axis_index=1, max_velocity=50.0),
    )
    self.assertEqual(cfg.x_axis.max_velocity, 50.0)
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


def _write(xml: str) -> str:
  fd, path = tempfile.mkstemp(suffix=".config")
  with os.fdopen(fd, "w") as f:
    f.write(xml)
  return path


class TestExtraLoaders(unittest.TestCase):
  def test_galvo_voltage_fields(self):
    xml = SAMPLE_XML.replace(
      "</USBIOConfigurationFile>",
      '<XGalvo xmlns="Cyntellect.com/USBIOConfig.xsd">'
      "<MaxVoltage>10</MaxVoltage><MinVoltage>0</MinVoltage>"
      "<InvertVoltage>true</InvertVoltage><PositionErrorWindow>20</PositionErrorWindow>"
      "</XGalvo></USBIOConfigurationFile>",
    )
    cfg = CeligoHardwareConfig.from_xml(_write(xml))
    self.assertEqual(cfg.x_galvo.max_voltage, 10.0)
    self.assertTrue(cfg.x_galvo.invert_voltage)
    self.assertEqual(cfg.x_galvo.position_error_window, 20)

  def test_galvo_calibration_terms(self):
    cal = load_galvo_calibration(_write(GALVO_CAL_XML))
    self.assertAlmostEqual(cal.forward["LinearXTerm"][0], 1.3)
    self.assertAlmostEqual(cal.forward["LinearXTerm"][1], -0.004)
    self.assertAlmostEqual(cal.forward["OffsetTerm"][1], 0.2)
    self.assertAlmostEqual(cal.reverse["LinearXTerm"][0], 0.77)


if __name__ == "__main__":
  unittest.main()
