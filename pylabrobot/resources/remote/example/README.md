# Remote Deck Example

Serve a deck over HTTP so a `LiquidHandler` on another machine (or process) can use it as a drop-in replacement.

## Setup

```
pip install connect-python uvicorn
```

## Run

Terminal 1 — start the server:

```
python server.py
```

Terminal 2 — run the client:

```
python client.py
```

The client connects to the server, fetches the full resource tree, and runs a
pick-up / aspirate / dispense / drop cycle through `STARBackend` exactly as if
the deck were local.
