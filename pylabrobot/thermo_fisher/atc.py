from .thermocycler import ThermoFisherThermocycler


def ATC(name: str, ip: str, **kwargs) -> ThermoFisherThermocycler:
  """Create an ATC thermocycler with a single 3-zone block and lid control (BID 31)."""
  return ThermoFisherThermocycler(
    name=name, ip=ip, num_blocks=1, num_temp_zones=3, supports_lid_control=True, **kwargs
  )
