// Command read prints identity and one stable weight from the simulated balance.
package main

import (
	"context"
	"flag"
	"fmt"
	"log"
	"time"

	balance "github.com/PyLabRobot/pylabrobot/clients/go/mettler_toledo"
)

// run connects to the simulator and reads its weight without changing tare or zero.
func run(endpoint string) error {
	ctx, cancel := context.WithTimeout(context.Background(), 80*time.Second)
	defer cancel()
	client, err := balance.Dial(ctx, endpoint)
	if err != nil {
		return err
	}
	defer func() {
		cleanup, cancel := context.WithTimeout(context.Background(), 5*time.Second)
		defer cancel()
		if err := client.Close(cleanup); err != nil {
			log.Printf("disconnect: %v", err)
		}
	}()
	info, err := client.GetInfo(ctx)
	if err != nil {
		return err
	}
	if info.SerialNumber != "SIMULATED-001" {
		return fmt.Errorf("this example expects SIMULATED-001; found %q", info.SerialNumber)
	}
	weight, err := client.ReadWeight(ctx)
	if err != nil {
		return err
	}
	fmt.Printf("%s (%s): %.6f g\n", info.DeviceType, info.SerialNumber, weight)
	return nil
}

// main parses the simulator endpoint and runs the example.
func main() {
	endpoint := flag.String("endpoint", balance.DefaultEndpoint, "Simulated balance OPC UA endpoint")
	flag.Parse()
	if err := run(*endpoint); err != nil {
		log.Fatal(err)
	}
}
