# Serve a balance over OPC UA

`MettlerToledoOPCUAServer` exposes the existing MT-SICS driver through OPC UA. The
selected device is the Mettler Toledo WXS205SDU; other MT-SICS balances can use the
same interface when they support the corresponding commands.

The OPC UA interface has been tested with a simulated serial transport, but has
not been verified on real hardware. See [the balance setup guide](hello-world.ipynb)
for cabling and serial configuration.

Install the optional server and serial dependencies:

```shell
pip install 'PyLabRobot[opcua,serial]'
```

## Simulator

For the interactive example, start a server that never opens a hardware connection:

```shell
python -m pylabrobot.mettler_toledo.scales.opcua_simulator
```

It samples an in-memory balance every 0.5 seconds and exposes a separate
`Simulation` object. `SetLoad(load: Double)` changes the gross load in grams;
`SetFault(active: Boolean)` injects or clears an overload. These methods advertise
their input names, types, and descriptions. They exist only on the simulator.
Use `--endpoint` and `--poll-interval` to change its settings.

## Server

Run this on the computer connected to the balance, replacing the serial port:

```python
import asyncio

from pylabrobot.mettler_toledo import MettlerToledoOPCUAServer, MettlerToledoWXS205SDU


async def main():
  balance = MettlerToledoWXS205SDU(port="/dev/ttyUSB0")
  await balance.setup()
  server = MettlerToledoOPCUAServer(balance, poll_interval=1.0)
  try:
    await server.setup()
    await asyncio.Event().wait()  # Serve until interrupted.
  finally:
    await server.stop()
    await balance.stop()


asyncio.run(main())
```

The caller owns the balance connection. Server setup and shutdown do not reset,
connect, or disconnect the balance. While serving, access the balance exclusively
through OPC UA: the server serializes client commands, but cannot serialize direct
calls made elsewhere to the driver. Shutdown waits for an active command to finish.
Polling is opt-in: `poll_interval=None` (the default) updates `Weight` only when a
client requests a net measurement. A positive interval starts immediate-weight
polling through the same command lock as method calls. The interval is a delay
after each completed poll, not a guaranteed hardware sampling frequency.

The default endpoint is
`opc.tcp://127.0.0.1:4840/pylabrobot/mettler-toledo/`, with anonymous access and no
encryption. Before binding to a network interface, configure the exposed
`server.server` asyncua instance with certificates, a security policy, and access
control appropriate for that network. The
[asyncua server API](https://opcua-asyncio.readthedocs.io/en/latest/api/asyncua.server.html)
documents these settings.

## Client

To learn namespace discovery, browsing, and method signatures against the simulator,
open the [raw OPC UA client notebook](opcua-client.ipynb). The separate
[typed-client notebook](opcua-client-typed.ipynb) uses the Python wrapper below.

The Python `BalanceClient` handles namespace discovery, NodeIds, and result
validation. It requires only `PyLabRobot[opcua]` on the client computer:

```python
import asyncio

from pylabrobot.mettler_toledo import BalanceClient


async def main():
  endpoint = "opc.tcp://127.0.0.1:4840/pylabrobot/mettler-toledo/"
  async with BalanceClient(endpoint) as balance:
    info = await balance.get_info()
    weight = await balance.read_weight()
    print(f"Balance: {info.device_type} ({info.serial_number})")
    print(f"Weight: {weight} g")


asyncio.run(main())
```

Use `connect()` and `disconnect()` for a session spanning notebook cells. Other
operations are `read_weight_immediately()`, `read_tare_weight()`, `zero()`, `tare()`,
and `clear_tare()`. Weight methods return `float` values in grams, and `get_info()`
returns a frozen `BalanceInfo` dataclass. Disconnecting leaves the remote server
and device running. Configure `balance.client` before connecting when certificates
or authentication are needed.

The client preserves `asyncua.ua.UaStatusCodeError`, including its `.code`, and
raises `ValueError` for incompatible server results. It does not retry commands
or reconnect automatically; disconnect and connect explicitly after a server
restart to resolve its namespace again.

A Go client with matching operations is in `clients/go/mettler_toledo` in the
repository. It exposes `Dial`, `ReadWeight`, `ReadWeightImmediately`,
`ReadTareWeight`, `Zero`, `Tare`, `ClearTare`, `GetInfo`, and `Close`. From that
directory, run `go run ./cmd/read` to read the simulator. See the module's README
for imports, context handling, and security options.

## OPC UA address space

The balance object has string NodeId `Balance`. Its children have string NodeIds
`Balance.<name>` in the same namespace. This is a PyLabRobot-specific address space,
not an implementation of an OPC UA companion specification.

| Method | Arguments | Result | MT-SICS command |
| --- | --- | --- | --- |
| `ReadStableWeight` | None | Double, grams | `S` |
| `ReadWeightImmediately` | None | Double, grams; may be unstable | `SI` |
| `ReadTareWeight` | None | Double, grams | `TA` |
| `Zero` | None | None; waits for stability | `Z` |
| `Tare` | None | None; waits for stability | `T` |
| `ClearTare` | None | None | `TAC` |

`DeviceType`, `SerialNumber`, `FirmwareVersion`, `Capacity` (Double, grams), and
`Unit` (`g`) are read-only properties captured at server setup. All other properties
are Strings. With polling disabled, measurements are requested explicitly through
methods; serving cached variables does not issue additional hardware commands.

The following read-only Variables can be browsed and subscribed to by a generic
OPC UA client:

| Variable | Type | Meaning |
| --- | --- | --- |
| `Weight` | Double, `AnalogItemType` | Latest net weight, grams; immediate samples may be unstable |
| `DeviceState` | String | Observed adapter state: `idle`, `busy`, `fault`, or `stopped` |
| `Fault` | String | Error text from the latest failed operation; empty after success |
| `FaultCode` | StatusCode | Corresponding OPC UA error code, or Good after success |

`Weight` has standard `0:EngineeringUnits` (`EUInformation`, gram, UnitId 4674125)
and `0:EURange` (nominal net range from minus capacity to plus capacity) properties.
Its `DataValue` carries a source timestamp recording when the adapter received the
sample. This is not a timestamp reported by the scale firmware. Good quality means
the operation succeeded; it does not imply a stable reading.

Before the first net sample, or after zero/tare changes until another sample is
taken, `Weight` has `BadWaitingForInitialData`. A failed operation replaces it with
a null value and the failure status, so an old sample is not left marked Good.
It becomes Good again after a successful net measurement. Reading tare does not
replace the net weight. During shutdown it is marked `BadNotConnected`.

Subscriptions deliver value and quality changes. Their publishing interval is
independent of the server's polling interval. Default data-change monitoring does
not report each new timestamp when value and status are unchanged. Monitor source
timestamps to assess sample age, especially while a long method blocks polling.

`DeviceState` reflects adapter operations, not a device firmware state machine.
Faults clear on the next successful operation and are not latched alarms. The
example does not implement OPC UA Alarms & Conditions, acknowledgment, or history.

## Companion specifications

A companion specification adds an agreed information model to the core OPC UA
services. Relevant examples are
[OPC UA for Weighing Technology (OPC 40200)](https://reference.opcfoundation.org/Scales/v200/docs/6.2),
which defines scale-system and scale-device types, and
[LADS (OPC 30500-1)](https://reference.opcfoundation.org/specs/OPC-30500-1/6),
which models laboratory devices and functional units, including states and health.

This server uses the core
[AnalogItemType](https://reference.opcfoundation.org/Core/Part8/v105/docs/5.3.2)
for a generic client to discover measurement metadata. Its application-specific
`Balance` object and state strings do not implement either companion specification.
Conformance would require the chosen specification's types, mandatory members,
state semantics, and applicable profiles; names alone do not establish conformance.

The raw notebook demonstrates the distinction by browsing type definitions and
engineering units, subscribing to data changes, and injecting simulator faults.

## Timing and errors

Stable operations use the driver's 60-second response timeout. Set client timeouts
accordingly, allowing additional time if other clients have queued commands.
Disconnecting a client or timing out a client request does not cancel an operation
already sent to the balance. A stable measurement may operate the balance doors
if its automatic door function is enabled.

Timeouts return `BadTimeout`, communication failures return `BadCommunicationError`,
and device rejections (including unsupported commands and overload) return
`BadDeviceFailure`. Detailed device errors are logged on the server. Failed calls
return no measurement. Extra arguments return `BadTooManyArguments` without issuing
a device command.
