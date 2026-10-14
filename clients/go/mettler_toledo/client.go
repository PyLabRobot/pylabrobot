// Package balance provides a typed client for PyLabRobot's Mettler Toledo OPC UA server.
// All weights and capacities are expressed in grams.
package balance

import (
	"context"
	"errors"
	"fmt"
	"sync"
	"time"

	"github.com/gopcua/opcua"
	"github.com/gopcua/opcua/ua"
)

// DefaultEndpoint is the server's anonymous, unencrypted loopback endpoint.
const DefaultEndpoint = "opc.tcp://127.0.0.1:4840/pylabrobot/mettler-toledo/"

// NamespaceURI identifies the balance interface regardless of its namespace index.
const NamespaceURI = "https://pylabrobot.org/opcua/mettler-toledo/scales"

// ErrClosed indicates that an operation was attempted after Close.
var ErrClosed = errors.New("balance client is closed")

// Info contains balance identity and capacity in grams.
type Info struct {
	DeviceType      string
	SerialNumber    string
	FirmwareVersion string
	Capacity        float64
	Unit            string
}

// transport contains the OPC UA services used by the balance client.
type transport interface {
	Call(context.Context, *ua.CallMethodRequest) (*ua.CallMethodResult, error)
	Read(context.Context, *ua.ReadRequest) (*ua.ReadResponse, error)
	Close(context.Context) error
}

// Client owns an OPC UA session and resolves all device node IDs internally.
// Concurrent calls are supported. Close waits for active calls to finish.
// Commands are never retried. After a disconnect, Close and Dial a new client
// to rediscover the namespace. A timed-out operation may still run on the server.
type Client struct {
	mu        sync.RWMutex
	transport transport
	namespace uint16
	closed    bool
}

// Dial connects and discovers the balance without issuing hardware commands.
// Options can override the default 70-second request timeout and configure security.
// Automatic reconnection is disabled so a server restart cannot reuse stale node IDs.
func Dial(ctx context.Context, endpoint string, options ...opcua.Option) (*Client, error) {
	opts := []opcua.Option{
		opcua.SecurityMode(ua.MessageSecurityModeNone),
		opcua.RequestTimeout(70 * time.Second),
	}
	opts = append(opts, options...)
	opts = append(opts, opcua.AutoReconnect(false))
	connection, err := opcua.NewClient(endpoint, opts...)
	if err != nil {
		return nil, fmt.Errorf("configure balance client: %w", err)
	}
	connected := false
	defer func() {
		if !connected {
			cleanup, cancel := context.WithTimeout(context.Background(), 5*time.Second)
			defer cancel()
			_ = connection.Close(cleanup)
		}
	}()
	if err := connection.Connect(ctx); err != nil {
		return nil, fmt.Errorf("connect to balance: %w", err)
	}
	namespace, err := connection.FindNamespace(ctx, NamespaceURI)
	if err != nil {
		return nil, fmt.Errorf("discover balance namespace: %w", err)
	}
	class, err := connection.Node(ua.NewStringNodeID(namespace, "Balance")).NodeClass(ctx)
	if err != nil {
		return nil, fmt.Errorf("discover balance object: %w", err)
	}
	if class != ua.NodeClassObject {
		return nil, errors.New("the server's Balance node is not an OPC UA object")
	}
	connected = true
	return &Client{transport: connection, namespace: namespace}, nil
}

// Close releases the client session without stopping or resetting the balance.
// Repeated calls have no effect.
func (c *Client) Close(ctx context.Context) error {
	c.mu.Lock()
	defer c.mu.Unlock()
	if c.closed {
		return nil
	}
	c.closed = true
	return c.transport.Close(ctx)
}

// call checks the method status and output count, preserving status codes as errors.
func (c *Client) call(ctx context.Context, method string, outputCount int) ([]*ua.Variant, error) {
	c.mu.RLock()
	defer c.mu.RUnlock()
	if c.closed {
		return nil, ErrClosed
	}
	result, err := c.transport.Call(ctx, &ua.CallMethodRequest{
		ObjectID: ua.NewStringNodeID(c.namespace, "Balance"),
		MethodID: ua.NewStringNodeID(c.namespace, "Balance."+method),
	})
	if err != nil {
		return nil, fmt.Errorf("%s: %w", method, err)
	}
	if result == nil {
		return nil, fmt.Errorf("%s: missing OPC UA method result", method)
	}
	if result.StatusCode != ua.StatusOK {
		return nil, fmt.Errorf("%s: %w", method, result.StatusCode)
	}
	if len(result.OutputArguments) != outputCount {
		return nil, fmt.Errorf("%s: expected %d output arguments, got %d", method, outputCount, len(result.OutputArguments))
	}
	return result.OutputArguments, nil
}

// readWeight validates the scalar Double returned by a weight method.
func (c *Client) readWeight(ctx context.Context, method string) (float64, error) {
	outputs, err := c.call(ctx, method, 1)
	if err != nil {
		return 0, err
	}
	if outputs[0] == nil || outputs[0].Type() != ua.TypeIDDouble {
		return 0, fmt.Errorf("%s: expected a scalar Double weight in grams", method)
	}
	weight, ok := outputs[0].Value().(float64)
	if !ok {
		return 0, fmt.Errorf("%s: expected a scalar Double weight in grams", method)
	}
	return weight, nil
}

// ReadWeight returns a stable net weight in grams.
func (c *Client) ReadWeight(ctx context.Context) (float64, error) {
	return c.readWeight(ctx, "ReadStableWeight")
}

// ReadWeightImmediately returns the current net weight without waiting for stability.
func (c *Client) ReadWeightImmediately(ctx context.Context) (float64, error) {
	return c.readWeight(ctx, "ReadWeightImmediately")
}

// ReadTareWeight returns the stored tare weight in grams.
func (c *Client) ReadTareWeight(ctx context.Context) (float64, error) {
	return c.readWeight(ctx, "ReadTareWeight")
}

// Zero zeros the balance once its reading is stable.
func (c *Client) Zero(ctx context.Context) error {
	_, err := c.call(ctx, "Zero", 0)
	return err
}

// Tare tares the current load once its reading is stable.
func (c *Client) Tare(ctx context.Context) error {
	_, err := c.call(ctx, "Tare", 0)
	return err
}

// ClearTare clears the stored tare without changing the zero reference.
func (c *Client) ClearTare(ctx context.Context) error {
	_, err := c.call(ctx, "ClearTare", 0)
	return err
}

// GetInfo reads identity and capacity, rejecting incompatible property values.
func (c *Client) GetInfo(ctx context.Context) (Info, error) {
	c.mu.RLock()
	defer c.mu.RUnlock()
	if c.closed {
		return Info{}, ErrClosed
	}
	names := []string{"DeviceType", "SerialNumber", "FirmwareVersion", "Capacity", "Unit"}
	request := &ua.ReadRequest{}
	for _, name := range names {
		request.NodesToRead = append(request.NodesToRead, &ua.ReadValueID{
			NodeID: ua.NewStringNodeID(c.namespace, "Balance."+name), AttributeID: ua.AttributeIDValue,
		})
	}
	response, err := c.transport.Read(ctx, request)
	if err != nil {
		return Info{}, fmt.Errorf("read balance properties: %w", err)
	}
	if response == nil || len(response.Results) != len(names) {
		return Info{}, errors.New("expected five balance properties")
	}
	for index, value := range response.Results {
		if value == nil {
			return Info{}, fmt.Errorf("%s: missing property result", names[index])
		}
		if value.Status != ua.StatusOK {
			return Info{}, fmt.Errorf("%s: %w", names[index], value.Status)
		}
		if value.Value == nil {
			return Info{}, fmt.Errorf("%s: missing property value", names[index])
		}
	}
	deviceType, typeOK := response.Results[0].Value.Value().(string)
	serial, serialOK := response.Results[1].Value.Value().(string)
	firmware, firmwareOK := response.Results[2].Value.Value().(string)
	capacity, capacityOK := response.Results[3].Value.Value().(float64)
	unit, unitOK := response.Results[4].Value.Value().(string)
	if !typeOK || !serialOK || !firmwareOK || !capacityOK || !unitOK || unit != "g" {
		return Info{}, errors.New("invalid balance properties: expected identity strings and capacity in grams")
	}
	return Info{deviceType, serial, firmware, capacity, unit}, nil
}
