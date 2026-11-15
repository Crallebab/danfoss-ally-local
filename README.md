# Danfoss Ally Local

A Home Assistant custom integration that controls a **Danfoss Ally Gateway**
over your local network, with no cloud involved.

The gateway is Tuya white-label hardware: it speaks the Tuya LAN protocol
(v3.5, TCP 6668), and its Zigbee devices are Tuya sub-devices addressed by their
Zigbee node id. Background:
[Virki-Workspace/danfoss-ally-is-tuya](https://github.com/Virki-Workspace/danfoss-ally-is-tuya).

> ## ⚠️ Read this before you install
>
> **This integration needs your gateway's Tuya local key, and that key is hard
> to get.** The Danfoss Ally app does not show it, and the usual way of getting
> a Tuya key (a Tuya developer account) does not apply: the Danfoss Ally app
> never creates a Tuya account to link. Expect real technical work before
> anything connects; see [Getting the local key](#getting-the-local-key).
>
> - Without the key the integration cannot connect at all.
> - The key changes whenever the gateway is reset or paired again, and you then
>   have to get it once more.
> - Nobody else can look it up for you, and you should never post it anywhere.
>
> If that is more than you want to take on, the cloud-based Danfoss Ally
> integration is the easier choice.

> Not affiliated with or endorsed by Danfoss or Tuya. Danfoss, Danfoss Ally and
> Tuya are trademarks of their respective owners and are used here only to
> identify the hardware this integration works with.

## Supported devices

| Device | What you get |
|---|---|
| Ally Radiator Thermostat | Thermostat (schedule / manual / pause, presets, target temperature), temperature, battery, heating demand, open-window sensor, switches for pre-heating, child lock and open-window detection, at home / away / pause temperatures, mounting orientation |
| Ally Room Sensor | Temperature, humidity, battery |
| Anything else behind the gateway | A disabled-by-default "Raw data points" diagnostic sensor only |

The data point map was worked out on real Ally radiator thermostats and room
sensors. Icon floor heating and other devices are not mapped, and the
integration never writes to a device type it does not recognise.

## Requirements

- Home Assistant 2026.11 or newer.
- The gateway's **Tuya device ID** and **local key** (see below).
- A fixed IP address for the gateway.

## Installation

1. Add this repository to the Home Assistant Marketplace (HACS) as a custom
   repository of type *Integration*, or copy
   `custom_components/danfoss_ally_local` into `/config/custom_components/`.
2. Restart Home Assistant.
3. Go to **Settings → Devices & services → Add integration** and pick
   **Danfoss Ally Local**.

### Setup fields

| Field | Meaning |
|---|---|
| Host | IP address of the gateway |
| Gateway device ID | The gateway's Tuya device ID (22 characters). `python tools/danfoss_probe.py scan` prints it. |
| Local key | The gateway's Tuya local key (16 characters). It changes if the gateway is reset or paired again. |
| Protocol version | 3.5 for Ally gateways |

### Getting the local key

Only the gateway has a local key, and each owner has to obtain it for their own
gateway. The one known route is **from your own signed-in Danfoss Ally app
session**, described in the background link above.

The usual Tuya route (a cloud project on the Tuya IoT Platform with your app
account linked to it) is not available: the Danfoss Ally app uses a Danfoss
account, and no Tuya account exists to link.

Treat the key like a password and never post it in an issue.

### Device names

The local protocol carries no device names, so devices start out as e.g.
"Danfoss radiator thermostat 2b83" (the last four characters of the Zigbee node
id). Name them under **Settings → Devices & services → Danfoss Ally Local →
Configure**.

## Good to know

- **One local connection.** The gateway accepts a single local connection. Do
  not run LocalTuya, tuya-local or tinytuya scripts against it while this
  integration is loaded.
- **Valves report slowly.** Battery-powered valves send updates every few
  minutes, so a change can take a while to show.
- **Settings start out unknown.** The gateway only hands out what a valve has
  reported to it, and valves report most settings only when they change. Until
  then pre-heating, open-window detection, pause temperature and mounting show
  as unknown; they still work, and setting one from Home Assistant or the
  Danfoss app makes it known. The open-window sensor appears once the valve has
  reported a window state.
- **Rooms with several thermostats.** The Danfoss app writes a room's settings
  to every thermostat in the room. Home Assistant writes to the one thermostat
  you change, so change a setting on each of them to keep the room in step.
- **Setting a temperature switches the valve to manual**, like the Danfoss app.
  Choose the *Auto* mode to return to the schedule.
- **Safety.** Writes are only sent to recognised, confirmed device types, and
  data point 44 (factory reset) can never be written.
- **Debug logs.** Do not enable debug logging for `tinytuya` when sharing logs:
  that library can print key material.

## Removal

Delete the integration under **Settings → Devices & services**, then remove the
repository from the Marketplace or delete
`custom_components/danfoss_ally_local`, and restart. The gateway and the Danfoss
app are not affected.

<!-- ha-marketplace:exclude-start -->
## Development

```
pip install -r requirements_test.txt
pytest
ruff check . && ruff format --check .
```

No test needs a gateway. The gateway, device and profile tests run with just
`pip install pytest tinytuya`; the coordinator and config flow tests also need
the Home Assistant test harness (`pytest-homeassistant-custom-component`, Linux
or macOS only) and are skipped without it.

### Mapping more data points

`tools/` holds read-only helpers for working out which local data point carries
which setting:

- `danfoss_probe.py scan`: find the gateway on the network.
- `danfoss_probe.py local`: dump each sub-device's raw data points (set
  `DANFOSS_LOCAL_KEY`; the integration must be disabled meanwhile).
- `danfoss_probe.py listen`: print every message the gateway sends while you
  change settings in the Danfoss app, including messages from ids it does not
  list as sub-devices (same key and disabled integration as `local`).
- `danfoss_probe.py cloud` and `map`: compare with the Danfoss cloud view.
- `watch_dps.py`: print data point changes through the Home Assistant API while
  you change one setting at a time in the Danfoss app.

Snapshots go to `snapshots/`, which is git-ignored. `deploy.sh` copies the
integration to a Home Assistant host over SSH.
<!-- ha-marketplace:exclude-end -->

## License

[MIT](LICENSE)
