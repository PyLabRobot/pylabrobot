package balance

import (
	"context"
	"os"
	"testing"
	"time"
)

// TestSimulator connects only when an explicit simulated-server endpoint is supplied.
// It verifies identity before any measurement and never changes tare or zero.
func TestSimulator(t *testing.T) {
	endpoint := os.Getenv("PYLABROBOT_OPCUA_TEST_ENDPOINT")
	if endpoint == "" {
		t.Skip("set PYLABROBOT_OPCUA_TEST_ENDPOINT to a running simulated balance")
	}
	ctx, cancel := context.WithTimeout(context.Background(), 80*time.Second)
	defer cancel()
	c, err := Dial(ctx, endpoint)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() {
		cleanup, cancel := context.WithTimeout(context.Background(), 5*time.Second)
		defer cancel()
		if err := c.Close(cleanup); err != nil {
			t.Error(err)
		}
	})
	info, err := c.GetInfo(ctx)
	if err != nil {
		t.Fatal(err)
	}
	if info.SerialNumber != "SIMULATED-001" {
		t.Fatalf("expected simulator, found %q; refusing to measure", info.SerialNumber)
	}
	stable, err := c.ReadWeight(ctx)
	if err != nil {
		t.Fatal(err)
	}
	immediate, err := c.ReadWeightImmediately(ctx)
	if err != nil {
		t.Fatal(err)
	}
	if stable != immediate {
		t.Fatalf("fixed simulator load changed: %v != %v", stable, immediate)
	}
	if _, err := c.ReadTareWeight(ctx); err != nil {
		t.Fatal(err)
	}
	t.Logf("%s (%s): %.6f g", info.DeviceType, info.SerialNumber, stable)
}
