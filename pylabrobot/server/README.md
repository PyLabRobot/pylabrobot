# PyLabRobot Server

PyLabRobot Server is a server for PyLabRobot: it provides a RESTful API that can be used to control
a lab.

## Installation

```
pip install pylabrobot[server]
```

## Usage

```sh
python server.py
```

## API Reference

### Liquid handling

#### Setting up the robot

`POST /api/v1/liquid_handler/setup`

**Response**

- `200 OK`

```json
{
  "status": "running"
}
```

`status` can be one of:

- `running`: the robot is running
- `stopped`: the robot is stopped
- `error`: the robot is stopped, see `500` response:

- `500 Internal Server Error`: an error occurred

```json
{
  "status": "error",
  "message": "An error occurred"
}
```

Check the logs for more details.

#### Stopping the robot

`POST /api/v1/liquid_handler/stop`

**Response**

- `200 OK`

```json
{
  "status": "stopped"
}
```

#### Requesting the robot status

`GET /api/v1/liquid_handler/status`

**Response**

- `200 OK`

```json
{
  "status": "running"
}
```

#### Defining labware

`POST /api/v1/liquid_handler/labware`

Post a JSON object that was generatated by calling `serialize()` on `lh`.

**Response**

- `201 Created`: the labware was created

#### Picking up tips

`POST /api/v1/liquid_handler/pick-up-tips`

```json
{
  "resource": "tips",
  "channels": ["A1", "B1", "C1"]
}
```

**Response**

- `200 OK`: the tips were picked up

```json
{
  "status": "ok"
}
```

#### Discarding tips

`POST /api/v1/liquid_handler/discard-tips`

```json
{
  "resource": "tips",
  "channels": ["A1", "B1", "C1"]
}
```

**Response**

- `200 OK`: the tips were discarded

```json
{
  "status": "ok"
}
```

#### Aspirating liquid

`POST /api/v1/liquid_handler/aspirate`

```json
{
  "resource": "plate",
  "channels": [
    {
      "well": "A1",
      "volume": "10"
    },
    {
      "well": "A2",
      "volume": "10"
    }
  ],
  "kwargs": [
    "speed": 10
  ]
}
```

**Response**

- `200 OK`: the liquid was aspirated

```json
{
  "status": "ok"
}
```

#### Dispensing liquid

`POST /api/v1/liquid_handler/dispense`

```json
{
  "resource": "other_plate",
  "channels": [
    {
      "well": "A1",
      "volume": "10"
    },
    {
      "channel": "A2",
      "volume": "20",
      "speed": "10"
    }
  ]
}
```

**Response**

- `200 OK`: the liquid was dispensed

```json
{
  "status": "ok"
}
```
