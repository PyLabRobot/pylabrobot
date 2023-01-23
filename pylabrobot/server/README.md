# PyLabRobot Server

PyLabRobot Server is a server for PyLabRobot: it provides HTTP APIs that can be used to control
a lab.

## Installation

```
pip install pylabrobot[server]
```

## Usage

Each component of the library (currently `liquid_handling` and `plate_reading`) has its own server. Each server will run on its own port. You can use a reverse proxy like [nginx](https://www.nginx.com/) if you wish to use a single port or configure routing.

Configuration using environment variables:

- `PORT`: the port to listen on (default: `5001`)
- `HOST`: the host to listen on (default: `0.0.0.0`)

## API Reference

All action endpoints return a JSON object with a `status` field. The value of this field can be one of:

- `queued`: the action is queued
- `running`: the action is running
- `succeeded`: the action succeeded
- `error`: the action failed, see the `message` field for more details

You can view all tasks using the `GET /tasks` endpoint. You can request the status of a specific task using the `GET /tasks/<task_id>` endpoint.

```json
{
  "id": "task_id",
  "status": "queued"
}
```

```json
{
  "id": "task_id",
  "status": "error",
  "error": "error message"
}
```

### Liquid handling

Run:

```sh
lh-server
```

The `backend.json` file must contain a serialized backend. See [`LiquidHandlerBackend.deserialize`](https://docs.pylabrobot.org/_autosummary/pylabrobot.liquid_handling.backends.backend.LiquidHandlerBackend.deserialize.html) and [`LiquidHandlerBackend.serialize`](https://docs.pylabrobot.org/_autosummary/pylabrobot.liquid_handling.backends.backend.LiquidHandlerBackend.serialize.html)

The `deck.json` file must contain a serialized deck. See [`Deck.deserialize`](https://docs.pylabrobot.org/_autosummary/pylabrobot.resources.Deck.deserialize.html) and [`Deck.serialize`](https://docs.pylabrobot.org/_autosummary/pylabrobot.resources.Deck.serialize.html).

Filenames and paths can be overridden with the `BACKEND_FILE` and `DECK_FILE` environment variables.

#### Setting up the robot

`POST /setup`

#### Stopping the robot

`POST /stop`

#### Requesting the robot status

`GET /status`

**Response**

- `200 OK`

```json
{
  "status": "running"
}
```

#### Defining labware

`POST /labware`

Post a JSON object that was generatated by calling `serialize()` on `lh`.

**Response**

- `201 Created`: the labware was created

#### Picking up tips

`POST /pick-up-tips`

```json
{
  "resource_name": "tiprack_tip_0_0",
  "offset": { "x": 0, "y": 0, "z": 0 }
}
```

#### Discarding tips

`POST /discard-tips`

```json
{
  "resource_name": "tiprack_tip_0_0",
  "offset": { "x": 0, "y": 0, "z": 0 }
}
```

#### Aspirating liquid

`POST /aspirate`

```json
{
  "resource_name": "plate_well_0_0",
  "offset": { "x": 0, "y": 0, "z": 0 },
  "volume": 100,
  "flow_rate": null
}
```

#### Dispensing liquid

`POST /dispense`

```json
{
  "resource_name": "plate_well_0_0",
  "offset": { "x": 0, "y": 0, "z": 0 },
  "volume": 100,
  "flow_rate": null
}
```
