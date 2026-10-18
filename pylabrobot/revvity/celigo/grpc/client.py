"""Typed backend client with explicit recovery and no automatic motion retries."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from typing import Any, Generic, cast

import grpc

from pylabrobot.revvity.celigo.grpc.model import CHANNEL_OPTIONS, M, SimulationSpec
from pylabrobot.revvity.celigo.grpc.security import Certificates
from pylabrobot.revvity.celigo.grpc.v1 import service_pb2 as pb
from pylabrobot.revvity.celigo.grpc.v1 import service_pb2_grpc as rpc


class Updates(Generic[M]):
  def __init__(self, call: Any):
    self.call = call

  async def get(self) -> M:
    value = await self.call.read()
    if value is grpc.aio.EOF:
      raise EOFError("update stream finished")
    return cast(M, value)


class Job:
  def __init__(self, owner: DeviceClient, ref: pb.JobRef):
    self.owner = owner
    self.identifier = ref.job_id
    self.device_id = ref.device_id

  @property
  def ref(self) -> pb.JobRef:
    return pb.JobRef(device_id=self.device_id, job_id=self.identifier)

  async def start(self) -> pb.JobStatus:
    return cast(pb.JobStatus, await self.owner.jobs.StartJob(self.ref, timeout=self.owner.timeout))

  async def cancel(self) -> pb.JobStatus:
    return cast(pb.JobStatus, await self.owner.jobs.CancelJob(self.ref, timeout=self.owner.timeout))

  async def release_images(self) -> pb.JobStatus:
    """Explicitly discard a finished job's pixels after archiving them elsewhere."""
    return cast(
      pb.JobStatus, await self.owner.jobs.ReleaseJobImages(self.ref, timeout=self.owner.timeout)
    )

  async def status(self) -> pb.JobStatus:
    return cast(pb.JobStatus, await self.owner.jobs.GetJob(self.ref, timeout=self.owner.timeout))

  async def list_frames(self, after_sequence=0, limit=64) -> list[pb.FrameInfo]:
    page = await self.owner.images.ListFrames(
      pb.ListFramesRequest(job=self.ref, after_sequence=after_sequence, limit=limit),
      timeout=self.owner.timeout,
    )
    return list(page.frames)

  async def get_frame(self, frame_id: str) -> pb.ImageFrame:
    return cast(
      pb.ImageFrame,
      await self.owner.images.GetFrame(
        pb.GetFrameRequest(job=self.ref, frame_id=frame_id),
        timeout=self.owner.timeout,
      ),
    )

  @asynccontextmanager
  async def updates(self):
    call = self.owner.jobs.WatchJob(self.ref)
    try:
      yield Updates[pb.JobStatus](call)
    finally:
      call.cancel()

  async def wait(self, timeout: float = 30) -> pb.JobStatus:
    async def finished():
      async with self.updates() as updates:
        while True:
          snapshot = await updates.get()
          if snapshot.state == pb.JOB_STATE_FINISHED:
            return snapshot

    return await asyncio.wait_for(finished(), timeout)


class DeviceClient:
  expected_device_type: str | None = None

  def __init__(
    self, endpoint: str, certs: Certificates, device_id="celigo-sim-01", timeout: float = 20
  ):
    self.endpoint = endpoint
    self.certs = certs
    self.device_id = device_id
    self.timeout = timeout
    self.configuration_id = ""
    self.channel: grpc.aio.Channel | None = None
    self.devices: rpc.DeviceServiceStub
    self.jobs: rpc.JobServiceStub
    self.images: rpc.ImageServiceStub

  @property
  def request(self) -> pb.DeviceRequest:
    return pb.DeviceRequest(device_id=self.device_id)

  async def connect(self):
    await self.disconnect()
    self.channel = grpc.aio.secure_channel(
      self.endpoint,
      self.certs.client_credentials(),
      options=CHANNEL_OPTIONS,
    )
    self.devices = rpc.DeviceServiceStub(self.channel)
    self.jobs = rpc.JobServiceStub(self.channel)
    self.images = rpc.ImageServiceStub(self.channel)
    try:
      info = await self.device_info()
      if info.device_id != self.device_id or info.api_version != "celigo.v1":
        raise ValueError("device identity or API version mismatch")
      if self.expected_device_type is not None and info.device_type != self.expected_device_type:
        raise ValueError(f"this client only accepts a {self.expected_device_type}")
      self.configuration_id = info.configuration_id
    except BaseException:
      await self.disconnect()
      raise
    return self

  async def disconnect(self):
    if self.channel is not None:
      await self.channel.close()
      self.channel = None

  async def __aenter__(self):
    return await self.connect()

  async def __aexit__(self, *_exc):
    await self.disconnect()

  async def device_info(self) -> pb.DeviceInfo:
    return cast(pb.DeviceInfo, await self.devices.GetDevice(self.request, timeout=self.timeout))

  async def status(self) -> pb.DeviceStatus:
    return cast(pb.DeviceStatus, await self.devices.GetStatus(self.request, timeout=self.timeout))

  @asynccontextmanager
  async def status_updates(self):
    call = self.devices.WatchStatus(self.request)
    try:
      yield Updates[pb.DeviceStatus](call)
    finally:
      call.cancel()

  @asynccontextmanager
  async def previews(self):
    call = self.images.WatchPreview(self.request)
    try:
      yield Updates[pb.ImageFrame](call)
    finally:
      call.cancel()

  async def recover(self, request_id: str) -> Job:
    ref = await self.jobs.GetJobByRequestId(
      pb.RequestLookup(device_id=self.device_id, request_id=request_id),
      timeout=self.timeout,
    )
    return Job(self, ref)


class PrototypeClient(DeviceClient):
  expected_device_type = "CeligoSimulator"

  async def connect(self):
    await super().connect()
    self.operations = rpc.SimulationServiceStub(self.channel)
    return self

  async def create(self, request_id: str, spec: SimulationSpec, *, preview=False) -> Job:
    method = self.operations.CreatePreview if preview else self.operations.CreateAcquisition
    ref = await method(
      pb.CreateRequest(
        device_id=self.device_id,
        request_id=request_id,
        specification=spec.to_proto(),
        configuration_id=self.configuration_id,
      ),
      timeout=self.timeout,
    )
    return Job(self, ref)

  async def create_initialization(self, request_id: str) -> Job:
    ref = await self.operations.CreateInitialization(
      pb.RequestLookup(device_id=self.device_id, request_id=request_id),
      timeout=self.timeout,
    )
    return Job(self, ref)


class CeligoClient(DeviceClient):
  expected_device_type = "Celigo"

  async def connect(self):
    await super().connect()
    self.operations = rpc.CeligoServiceStub(self.channel)
    return self

  async def configuration(self) -> pb.CeligoConfiguration:
    return cast(
      pb.CeligoConfiguration,
      await self.operations.GetConfiguration(self.request, timeout=self.timeout),
    )

  async def create_initialization(self, request_id: str) -> Job:
    ref = await self.operations.CreateInitialization(
      pb.InitializationRequest(
        device_id=self.device_id,
        request_id=request_id,
        configuration_id=self.configuration_id,
      ),
      timeout=self.timeout,
    )
    return Job(self, ref)

  async def create_brightfield_capture(
    self,
    request_id: str,
    *,
    sample_x_mm: float,
    sample_y_mm: float,
    z_mm: float,
    exposure_ms: float,
    gain: float,
  ) -> Job:
    ref = await self.operations.CreateBrightfieldCapture(
      pb.BrightfieldRequest(
        device_id=self.device_id,
        request_id=request_id,
        configuration_id=self.configuration_id,
        settings=pb.BrightfieldSettings(
          sample_x_mm=sample_x_mm,
          sample_y_mm=sample_y_mm,
          z_mm=z_mm,
          exposure_ms=exposure_ms,
          gain=gain,
        ),
      ),
      timeout=self.timeout,
    )
    return Job(self, ref)
