# pylint: skip-file

from pyhamilton.liquid_handling.resources.abstract import Tips


class FourmlTF_L(Tips):
  """ Tip Rack 24x 4ml Tip with Filter landscape oriented """

  def __init__(self, name: str):
    super().__init__(
      name=name,
      size_x=122.4,
      size_y=82.6,
      size_z=7.0,
      tip_type="MlStar4mlTipWithFilter",
      dx=16.3,
      dy=14.2,
      dz=-93.2,
    )


class LT_L(Tips):
  """ Rack with 96 10ul Low Volume Tip """

  def __init__(self, name: str):
    super().__init__(
      name=name,
      size_x=122.4,
      size_y=82.6,
      size_z=20.0,
      tip_type="MlStar10ulLowVolumeTip",
      dx=11.7,
      dy=9.8,
      dz=-22.5,
    )


class HTF_L(Tips):
  """ Rack with 96 1000ul High Volume Tip with filter """

  def __init__(self, name: str):
    super().__init__(
      name=name,
      size_x=122.4,
      size_y=82.6,
      size_z=20.0,
      tip_type="MlStar1000ulHighVolumeTipWithFilter",
      dx=11.7,
      dy=9.8,
      dz=-83.5,
    )


class HT_L(Tips):
  """ Rack with 96 1000ul High Volume Tip """

  def __init__(self, name: str):
    super().__init__(
      name=name,
      size_x=122.4,
      size_y=82.6,
      size_z=20.0,
      tip_type="MlStar1000ulHighVolumeTip",
      dx=11.7,
      dy=9.8,
      dz=-83.5,
    )


class LTF_L(Tips):
  """ Rack with 96 10ul Low Volume Tip with filter """

  def __init__(self, name: str):
    super().__init__(
      name=name,
      size_x=122.4,
      size_y=82.6,
      size_z=20.0,
      tip_type="MlStar10ulLowVolumeTipWithFilter",
      dx=11.7,
      dy=9.8,
      dz=-22.5,
    )


class FivemlT_L(Tips):
  """ Tip Rack 24x 5ml Tip landscape oriented """

  def __init__(self, name: str):
    super().__init__(
      name=name,
      size_x=122.4,
      size_y=82.6,
      size_z=7.0,
      tip_type="MlStar5mlTip",
      dx=16.3,
      dy=14.2,
      dz=-93.2,
    )


class STF_L(Tips):
  """ Rack with 96 300ul Standard Volume Tip with filter """

  def __init__(self, name: str):
    super().__init__(
      name=name,
      size_x=122.4,
      size_y=82.6,
      size_z=20.0,
      tip_type="MlStar300ulStandardVolumeTipWithFilter",
      dx=11.7,
      dy=9.8,
      dz=-50.5,
    )


class ST_L(Tips):
  """ Rack with 96 300ul Standard Volume Tip """

  def __init__(self, name: str):
    super().__init__(
      name=name,
      size_x=122.4,
      size_y=82.6,
      size_z=20.0,
      tip_type="MlStar300ulStandardVolumeTip",
      dx=11.7,
      dy=9.8,
      dz=-50.5,
    )
