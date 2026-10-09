import abc
import warnings
from enum import Enum
from typing import Dict, Literal, Optional

import anyio

from pylabrobot.concurrency import AsyncExitStackWithShielding, HasLifespan
from pylabrobot.io.usb import USB
from pylabrobot.legacy.heating_shaking.backend import HeaterShakerBackend

# Budget for one complete command exchange on the box: the command write and the reply read
# share it. It bounds the exchange as one operation, rather than giving each phase its own
# budget, so a lost connection fails the command instead of each step in turn. It is the sum
# of the two transport budgets it replaces (`USB.write_timeout` plus `USB.read_timeout`), so
# no command that used to succeed can newly fail under it.
_COMMAND_TIMEOUT = 60.0

# Bound for stopping a shake this operation started. The stop dispatches `SC` and then `SW`
# and may queue behind one in-flight exchange, so it covers three exchanges plus scheduling
# margin. It is a fixed budget for confirming those two commands and never derives from a
# speed, a retry count, or how long the device was asked to shake.
_START_CLEANUP_TIMEOUT = 3 * _COMMAND_TIMEOUT + 4.0


class PlateLockPosition(Enum):
  LOCKED = 1
  UNLOCKED = 0


class HamiltonHeaterShakerInterface(abc.ABC):
  """Either a control box or a STAR: the api is the same"""

  @abc.abstractmethod
  async def send_hhs_command(self, index: int, command: str, **kwargs) -> str:
    pass


class HamiltonHeaterShakerBox(HasLifespan, HamiltonHeaterShakerInterface):
  def __init__(
    self,
    id_vendor: int = 0x8AF,
    id_product: int = 0x8002,
    device_address: Optional[int] = None,
    serial_number: Optional[str] = None,
  ):
    super().__init__()
    self._transaction_lock = anyio.Lock()
    self.io = USB(
      human_readable_device_name="Hamilton Heater Shaker Box",
      id_vendor=id_vendor,
      id_product=id_product,
      device_address=device_address,
      serial_number=serial_number,
    )
    self._id = 0

  def _generate_id(self) -> int:
    """continuously generate unique ids 0 <= x < 10000."""
    self._id += 1
    return self._id % 10000

  async def _enter_lifespan(self, stack: AsyncExitStackWithShielding) -> None:
    """Own the USB transport for this lifespan.

    The box owns the transport and nothing else: no device command is issued on entry or on
    exit, exactly as the legacy `setup`/`stop` pair did. If USB entry fails, ensure that
    libusb drivers were installed for the HHS as per docs.
    """
    await super()._enter_lifespan(stack)

    await stack.enter_async_context(self.io.lifespan())

  async def send_hhs_command(self, index: int, command: str, **kwargs) -> str:
    """Run one complete command exchange as a single serialized transaction.

    Waiting for the transaction and the checkpoint before the write stay cancellable. Once
    the write may have committed, the write and the reply read are shielded under one finite
    budget, so cancellation cannot abandon a command the device has already received. The
    transaction is released before the final checkpoint, so a pending external cancellation
    wins over a stored timeout.
    """
    # Pure computation, so it happens before the transaction is taken.
    args = "".join([f"{key}{value}" for key, value in kwargs.items()])

    await anyio.lowlevel.checkpoint_if_cancelled()
    await self._transaction_lock.acquire()
    try:
      # Intentionally redundant with the cancellable acquire above on a contended lock, and
      # the only stop on an uncontended one: anyio's fast-path `acquire` ends in a shielded
      # checkpoint that swallows a cancellation arriving while the task waits there, so the
      # pre-lock checkpoint cannot catch it. Nothing pins this line alone, so a green suite
      # is not evidence it is dead; deleting it does cause a real post-cancellation write.
      # The pre-lock checkpoint above is unpinned alone for the mirror-image reason: on a
      # contended lock this one catches the same cancellation, so deleting either one by
      # itself leaves the suite green. The two are complementary across the contended and
      # uncontended paths, not duplicates; keep both.
      # Do not remove it, above all if the acquire is ever shielded.
      await anyio.lowlevel.checkpoint_if_cancelled()
      response = b""
      with anyio.CancelScope(shield=True):
        with anyio.move_on_after(_COMMAND_TIMEOUT) as exchange_scope:
          id_ = str(self._generate_id()).zfill(4)
          await self.io.write(f"T{index}{command}id{id_}{args}".encode())
          response = await self.io.read()
        exchange_timed_out = exchange_scope.cancelled_caught
    finally:
      self._transaction_lock.release()
    await anyio.lowlevel.checkpoint()

    if exchange_timed_out:
      raise TimeoutError(f"Timed out waiting for response to '{command}'")

    return response.decode("utf-8")


class HamiltonHeaterShakerBackend(HeaterShakerBackend):
  """Backend for Hamilton Heater Shaker devices connected through an Heater Shaker Box"""

  @property
  def supports_active_cooling(self) -> bool:
    return False

  def __init__(self, index: int, interface: HamiltonHeaterShakerInterface) -> None:
    """
    Multiple Hamilton Heater Shakers can be connected to the same Heat Shaker Box. Each has A
    unique 'shaker index'
    """
    assert index >= 0, "Shaker index must be non-negative"
    self.index = index

    super().__init__()
    self.interface = interface

  async def _enter_lifespan(self, stack: AsyncExitStackWithShielding) -> None:
    """Borrow an active interface and initialize the lock and the shaker drive.

    The interface is borrowed, never entered, stopped, or closed here: it owns the transport,
    and several devices share one box or one STAR, so a child that closed it would cut off
    its siblings.

    No cleanup is registered. The legacy `stop()` this replaces was literally `pass`, and
    every shutdown action already has an owner: `HeaterShaker._pre_backend_stop`
    unconditionally issues `deactivate` (`TO`) and `_force_stop_shaking` (`SC`, `SW`), and
    `Shaker._shaking()` registers `unlock_plate` (`LP lp0`) whenever locking is supported.
    Registering any of them here would issue it twice on every frontend exit.
    """
    await super()._enter_lifespan(stack)
    # The abstract interface declares only `send_hhs_command`, so an implementation can offer
    # no way to tell whether its transport is open. Both real implementers
    # (`HamiltonHeaterShakerBox` and `STARBackend`) are `HasLifespan`, so one that cannot
    # report activity is rejected rather than trusted.
    if not (isinstance(self.interface, HasLifespan) and self.interface.is_active):
      raise RuntimeError("Hamilton heater shaker backends require an active HasLifespan interface")

    await self._initialize_lock()
    await self._initialize_shaker_drive()

  def serialize(self) -> dict:
    warnings.warn("The interface is not serialized.")

    heater_shaker_serialized = HeaterShakerBackend.serialize(self)
    return {
      **heater_shaker_serialized,
      "index": self.index,
      "interface": None,  # TODO: implement serialization
    }

  async def start_shaking(
    self,
    speed: float = 800,
    direction: Literal[0, 1] = 0,
    acceleration: int = 1_000,
    timeout: Optional[float] = 30,
  ):
    """
    if the plate is not locked, it will be locked.

    speed: steps per second
    direction: 0 for positive, 1 for negative
    acceleration: increments per second
    """

    await self.lock_plate()

    int_speed = int(speed)
    assert 20 <= int_speed <= 2_000, "Speed must be between 20 and 2_000"
    assert direction in [0, 1], "Direction must be 0 or 1"
    assert 500 <= acceleration <= 10_000, "Acceleration must be between 500 and 10_000"

    # Once an `SB` may have reached the device, this operation owns stopping it again. That
    # ownership is operation-local and is distinct from lifespan cleanup, which this backend
    # deliberately does not register.
    committed = False
    try:
      now = anyio.current_time()
      while True:
        try:
          await self._start_shaking(direction=direction, speed=int_speed, acceleration=acceleration)
        except BaseException:
          # A write that fails or is cancelled is conservatively ambiguous: the device may
          # already have received it, so treat it as committed.
          committed = True
          raise
        # Published as soon as the write returns, before the reply below is interpreted, so
        # no failure between the two can lose the start.
        committed = True
        if await self.get_is_shaking():
          break
        # The elapsed time is checked after the attempt, so `timeout=0` still performs
        # exactly one attempt, and `timeout=None` remains unbounded.
        if timeout is not None and anyio.current_time() - now > timeout:
          raise TimeoutError("Failed to start shaking within timeout")
    except BaseException:
      # Deliberately not observable alone in this shape, so a green suite is not evidence it
      # is dead. `lock_plate()` above is the only pre-commit await and it sits outside this
      # `try`, and nothing between `try:` and the first `SB` can fail, so every failure that
      # reaches here already committed. Measured: replacing this with `if True:` survives all
      # 111 nodes, while the same deletion applied after moving `lock_plate()` inside the
      # `try` fails four nodes that no other mutation reaches. Keep it: it states the
      # ownership rule, and that refactor makes it load-bearing immediately.
      if committed:
        with anyio.CancelScope(shield=True):
          with anyio.move_on_after(_START_CLEANUP_TIMEOUT) as cleanup_scope:
            await self.stop_shaking()
        if cleanup_scope.cancelled_caught:
          raise TimeoutError("Could not confirm shaking started by this operation was stopped")
      raise

  async def shake(
    self,
    speed: float = 800,
    direction: Literal[0, 1] = 0,
    acceleration: int = 1_000,
    timeout: Optional[float] = 30,
  ):
    warnings.warn(
      "HamiltonHeaterShakerBackend.shake() is deprecated and will be removed in a future release. "
      "Use start_shaking() instead.",
      DeprecationWarning,
      stacklevel=2,
    )
    await self.start_shaking(
      speed=speed,
      direction=direction,
      acceleration=acceleration,
      timeout=timeout,
    )

  async def stop_shaking(self):
    await self._stop_shaking()
    await self._wait_for_stop()

  async def get_is_shaking(self) -> bool:
    response = await self.interface.send_hhs_command(index=self.index, command="RD")
    return response.endswith("1")  # type: ignore[no-any-return] # what

  async def _move_plate_lock(self, position: PlateLockPosition):
    return await self.interface.send_hhs_command(index=self.index, command="LP", lp=position.value)

  @property
  def supports_locking(self) -> bool:
    return True

  async def lock_plate(self):
    await self._move_plate_lock(PlateLockPosition.LOCKED)

  async def unlock_plate(self):
    await self._move_plate_lock(PlateLockPosition.UNLOCKED)

  async def _initialize_lock(self):
    """Firmware command initialize lock."""
    return await self.interface.send_hhs_command(index=self.index, command="LI")

  async def _initialize_shaker_drive(self):
    """Initialize the shaker drive, homing to absolute position 0"""
    return await self.interface.send_hhs_command(index=self.index, command="SI")

  async def _start_shaking(self, direction: int, speed: int, acceleration: int):
    """Firmware command for starting shaking."""
    speed_str = str(speed).zfill(4)
    acceleration_str = str(acceleration).zfill(5)
    return await self.interface.send_hhs_command(
      index=self.index, command="SB", st=direction, sv=speed_str, sr=acceleration_str
    )

  async def _stop_shaking(self):
    """Firmware command for stopping shaking."""
    return await self.interface.send_hhs_command(index=self.index, command="SC")

  async def _wait_for_stop(self):
    """Firmware command for waiting for shaking to stop."""
    return await self.interface.send_hhs_command(index=self.index, command="SW")

  async def set_temperature(self, temperature: float):
    """set temperature in Celsius"""
    assert 0 < temperature <= 105
    temp_str = f"{round(10 * temperature):04d}"
    return await self.interface.send_hhs_command(index=self.index, command="TA", ta=temp_str)

  async def _get_current_temperature(self) -> Dict[str, float]:
    """get temperature in Celsius"""
    response = await self.interface.send_hhs_command(index=self.index, command="RT")
    response = response.split("rt")[1]
    middle_temp = float(str(response).split(" ")[0].strip("+")) / 10
    edge_temp = float(str(response).split(" ")[1].strip("+")) / 10
    return {"middle": middle_temp, "edge": edge_temp}

  async def get_current_temperature(self) -> float:
    """get temperature in Celsius"""
    response = await self._get_current_temperature()
    return response["middle"]

  async def get_edge_temperature(self) -> float:
    """get temperature in Celsius"""
    response = await self._get_current_temperature()
    return response["edge"]

  async def deactivate(self):
    """turn off heating"""
    return await self.interface.send_hhs_command(index=self.index, command="TO")
