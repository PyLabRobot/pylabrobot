# Mettler Toledo balance client for Go

A typed client for PyLabRobot's OPC UA balance server. It resolves namespace
indices and NodeIds internally and returns weights as `float64` values in grams.
Requires Go 1.23 or later.

From this directory, try the running simulator:

```shell
go run ./cmd/read
```

The example checks for serial number `SIMULATED-001` before taking a measurement.
Use `-endpoint opc.tcp://...` to select another simulated server.

Import this package as:

```go
import balance "github.com/PyLabRobot/pylabrobot/clients/go/mettler_toledo"
```

This module lives in this checkout. Until it is published, an external Go project
can use a local replacement (substitute your checkout's absolute path):

```shell
go mod edit -replace github.com/PyLabRobot/pylabrobot/clients/go/mettler_toledo=/path/to/pylabrobot/clients/go/mettler_toledo
go get github.com/PyLabRobot/pylabrobot/clients/go/mettler_toledo
```

## Usage

```go
ctx, cancel := context.WithTimeout(context.Background(), 80*time.Second)
defer cancel()

client, err := balance.Dial(ctx, balance.DefaultEndpoint)
if err != nil {
    return err
}
defer func() {
    cleanup, cancel := context.WithTimeout(context.Background(), 5*time.Second)
    defer cancel()
    _ = client.Close(cleanup)
}()

info, err := client.GetInfo(ctx)
if err != nil {
    return err
}
weight, err := client.ReadWeight(ctx)
if err != nil {
    return err
}
fmt.Printf("%s: %.6f g\n", info.SerialNumber, weight)
```

The complete executable example is in [`cmd/read/main.go`](cmd/read/main.go).

| Method | Result |
| --- | --- |
| `ReadWeight(ctx)` | Stable net weight, grams |
| `ReadWeightImmediately(ctx)` | Current net weight, grams |
| `ReadTareWeight(ctx)` | Stored tare, grams |
| `Zero(ctx)` | Wait for stability and zero |
| `Tare(ctx)` | Wait for stability and tare |
| `ClearTare(ctx)` | Clear stored tare |
| `GetInfo(ctx)` | Typed model, serial, firmware, capacity, and unit |
| `Close(ctx)` | Disconnect this client; leave the server running |

OPC UA status errors and context errors are wrapped with `%w`; use `errors.Is`,
for example `errors.Is(err, ua.StatusBadDeviceFailure)` or
`errors.Is(err, context.DeadlineExceeded)`. Malformed results return descriptive
errors instead of silently producing zero weights.

The default request timeout is 70 seconds. Pass `opcua.RequestTimeout(...)` to
`Dial` to change it. Other `opcua.Option` values configure certificates and
credentials; the default loopback endpoint uses anonymous, unencrypted access.
`Close` waits for active calls; use request contexts to bound their duration.

Commands are not retried, and automatic reconnection is disabled. Close and dial
again after a lost connection so the namespace is rediscovered. A client timeout
does not guarantee cancellation of the operation on the server.

## Verification

```shell
go test ./...
go vet ./...
```

To also run the read-only interoperability test against the simulated Python server:

```shell
PYLABROBOT_OPCUA_TEST_ENDPOINT=opc.tcp://127.0.0.1:4840/pylabrobot/mettler-toledo/ go test -run TestSimulator -v
```

The integration test verifies the simulated serial number before any measurements.
Unit tests use a fake transport and require neither a server nor hardware.
