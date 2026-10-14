package balance

import (
	"context"
	"errors"
	"testing"

	"github.com/gopcua/opcua/ua"
)

// fakeTransport records requests and supplies protocol results without hardware.
type fakeTransport struct {
	request *ua.CallMethodRequest
	result  *ua.CallMethodResult
	read    *ua.ReadResponse
	err     error
	calls   int
	closes  int
}

// Call records a method request and honors an already-cancelled context.
func (f *fakeTransport) Call(ctx context.Context, request *ua.CallMethodRequest) (*ua.CallMethodResult, error) {
	f.request = request
	f.calls++
	if err := ctx.Err(); err != nil {
		return nil, err
	}
	return f.result, f.err
}

// Read returns configured property results.
func (f *fakeTransport) Read(context.Context, *ua.ReadRequest) (*ua.ReadResponse, error) {
	return f.read, f.err
}

// Close records the release of the session.
func (f *fakeTransport) Close(context.Context) error {
	f.closes++
	return nil
}

// TestOperations checks all method mappings using an arbitrary namespace index.
func TestOperations(t *testing.T) {
	f := &fakeTransport{}
	c := &Client{transport: f, namespace: 7}
	ctx := context.Background()
	for _, test := range []struct {
		name string
		call func(context.Context) (float64, error)
	}{
		{"ReadStableWeight", c.ReadWeight},
		{"ReadWeightImmediately", c.ReadWeightImmediately},
		{"ReadTareWeight", c.ReadTareWeight},
	} {
		t.Run(test.name, func(t *testing.T) {
			f.result = &ua.CallMethodResult{OutputArguments: []*ua.Variant{ua.MustVariant(-1.25)}}
			value, err := test.call(ctx)
			if err != nil || value != -1.25 {
				t.Fatalf("weight = %v, error = %v", value, err)
			}
			if f.request.ObjectID.String() != "ns=7;s=Balance" || f.request.MethodID.String() != "ns=7;s=Balance."+test.name || len(f.request.InputArguments) != 0 {
				t.Fatalf("unexpected request: %+v", f.request)
			}
		})
	}
	for _, test := range []struct {
		name string
		call func(context.Context) error
	}{
		{"Zero", c.Zero}, {"Tare", c.Tare}, {"ClearTare", c.ClearTare},
	} {
		t.Run(test.name, func(t *testing.T) {
			f.result = &ua.CallMethodResult{}
			if err := test.call(ctx); err != nil {
				t.Fatal(err)
			}
			if f.request.MethodID.String() != "ns=7;s=Balance."+test.name {
				t.Fatalf("unexpected method: %s", f.request.MethodID)
			}
		})
	}
}

// TestFailures checks status preservation, malformed results, and absence of retries.
func TestFailures(t *testing.T) {
	for _, test := range []struct {
		name   string
		result *ua.CallMethodResult
		err    error
		want   error
	}{
		{"device", &ua.CallMethodResult{StatusCode: ua.StatusBadDeviceFailure}, nil, ua.StatusBadDeviceFailure},
		{"transport", nil, context.DeadlineExceeded, context.DeadlineExceeded},
		{"missing result", nil, nil, nil},
		{"missing output", &ua.CallMethodResult{}, nil, nil},
		{"nil output", &ua.CallMethodResult{OutputArguments: []*ua.Variant{nil}}, nil, nil},
		{"wrong type", &ua.CallMethodResult{OutputArguments: []*ua.Variant{ua.MustVariant("1.25")}}, nil, nil},
		{"array", &ua.CallMethodResult{OutputArguments: []*ua.Variant{ua.MustVariant([]float64{1.25})}}, nil, nil},
		{"extra output", &ua.CallMethodResult{OutputArguments: []*ua.Variant{ua.MustVariant(1.25), ua.MustVariant(2.0)}}, nil, nil},
	} {
		t.Run(test.name, func(t *testing.T) {
			f := &fakeTransport{result: test.result, err: test.err}
			c := &Client{transport: f, namespace: 7}
			_, err := c.ReadWeight(context.Background())
			if err == nil || (test.want != nil && !errors.Is(err, test.want)) {
				t.Fatalf("unexpected error: %v", err)
			}
			if f.calls != 1 {
				t.Fatalf("made %d calls; expected one", f.calls)
			}
		})
	}
}

// TestControlOutput rejects unexpected results from methods with no outputs.
func TestControlOutput(t *testing.T) {
	f := &fakeTransport{result: &ua.CallMethodResult{OutputArguments: []*ua.Variant{ua.MustVariant(1.0)}}}
	c := &Client{transport: f}
	if err := c.Tare(context.Background()); err == nil {
		t.Fatal("accepted unexpected output")
	}
}

// TestCloseAndCancellation checks session ownership and context propagation.
func TestCloseAndCancellation(t *testing.T) {
	f := &fakeTransport{}
	c := &Client{transport: f}
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	if _, err := c.ReadWeight(ctx); !errors.Is(err, context.Canceled) {
		t.Fatalf("expected context cancellation, got %v", err)
	}
	for range 2 {
		if err := c.Close(context.Background()); err != nil {
			t.Fatal(err)
		}
	}
	if f.closes != 1 {
		t.Fatalf("closed %d times", f.closes)
	}
	if _, err := c.ReadWeight(context.Background()); !errors.Is(err, ErrClosed) {
		t.Fatalf("expected ErrClosed, got %v", err)
	}
	if _, err := c.GetInfo(context.Background()); !errors.Is(err, ErrClosed) {
		t.Fatalf("expected ErrClosed, got %v", err)
	}
}

// TestInfo validates identity types, property statuses, and units.
func TestInfo(t *testing.T) {
	f := &fakeTransport{read: &ua.ReadResponse{}}
	for _, value := range []any{"WXS205SDU", "SIMULATED-001", "simulation", 220.009, "g"} {
		f.read.Results = append(f.read.Results, &ua.DataValue{Value: ua.MustVariant(value)})
	}
	c := &Client{transport: f, namespace: 7}
	info, err := c.GetInfo(context.Background())
	if err != nil || info.SerialNumber != "SIMULATED-001" || info.Capacity != 220.009 || info.Unit != "g" {
		t.Fatalf("info = %+v, error = %v", info, err)
	}
	f.read.Results[4].Value = ua.MustVariant("kg")
	if _, err := c.GetInfo(context.Background()); err == nil {
		t.Fatal("accepted incompatible units")
	}
	f.read.Results[0].Status = ua.StatusBadNotConnected
	if _, err := c.GetInfo(context.Background()); !errors.Is(err, ua.StatusBadNotConnected) {
		t.Fatalf("lost property status: %v", err)
	}
	f.read.Results = nil
	if _, err := c.GetInfo(context.Background()); err == nil {
		t.Fatal("accepted missing properties")
	}
}
