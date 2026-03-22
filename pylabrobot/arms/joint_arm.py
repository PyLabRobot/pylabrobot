from typing import Dict, Optional

from pylabrobot.arms.backend import JointGripperArmBackend
from pylabrobot.arms.orientable_arm import OrientableArm
from pylabrobot.arms.standard import ArmPosition
from pylabrobot.resources import Resource
from pylabrobot.serializer import SerializableMixin


class JointArm(OrientableArm):
  """An arm with joint-space control and rotation capability. E.g. PreciseFlex, KX2."""

  def __init__(self, backend: JointGripperArmBackend, reference_resource: Resource):
    super().__init__(backend=backend, reference_resource=reference_resource)
    self.backend: JointGripperArmBackend = backend  # type: ignore[assignment]

  async def pick_up_at_joint_position(
    self,
    position: Dict[int, float],
    resource_width: float,
    backend_params: Optional[SerializableMixin] = None,
  ) -> None:
    await self.backend.pick_up_at_joint_position(
      position=position, resource_width=resource_width, backend_params=backend_params
    )

  async def drop_at_joint_position(
    self,
    position: Dict[int, float],
    resource_width: float,
    backend_params: Optional[SerializableMixin] = None,
  ) -> None:
    await self.backend.drop_at_joint_position(
      position=position, resource_width=resource_width, backend_params=backend_params
    )

  async def move_to_joint_position(
    self, position: Dict[int, float], backend_params: Optional[SerializableMixin] = None
  ) -> None:
    await self.backend.move_to_joint_position(position=position, backend_params=backend_params)

  async def get_joint_position(
    self, backend_params: Optional[SerializableMixin] = None
  ) -> Dict[int, float]:
    return await self.backend.get_joint_position(backend_params=backend_params)

  async def get_cartesian_position(
    self, backend_params: Optional[SerializableMixin] = None
  ) -> ArmPosition:
    return await self.backend.get_cartesian_position(backend_params=backend_params)
