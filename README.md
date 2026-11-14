# Danfoss Ally Local

A Home Assistant custom integration that controls a **Danfoss Ally Gateway**
over your local network, with no cloud involved.

The gateway is Tuya white-label hardware: it speaks the Tuya LAN protocol
(v3.5, TCP 6668), and its Zigbee devices are Tuya sub-devices addressed by their
Zigbee node id. Background:
[Virki-Workspace/danfoss-ally-is-tuya](https://github.com/Virki-Workspace/danfoss-ally-is-tuya).

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
gateway. Two routes:

- **Tuya IoT Platform**: create a cloud project and link your app account. This
  often does not work for vendor-branded apps such as Danfoss Ally.
- **From your own signed-in app session**: described in the background link
  above.

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
pip install pytest tinytuya
pytest
```

The unit tests need neither Home Assistant nor a gateway.

### Mapping more data points

`tools/` holds read-only helpers for working out which local data point carries
which setting:

- `danfoss_probe.py scan`: find the gateway on the network.
- `danfoss_probe.py local`: dump each sub-device's raw data points (set
  `DANFOSS_LOCAL_KEY`; the integration must be disabled meanwhile).
- `danfoss_probe.py cloud` and `map`: compare with the Danfoss cloud view.
- `watch_dps.py`: print data point changes through the Home Assistant API while
  you change one setting at a time in the Danfoss app.

Snapshots go to `snapshots/`, which is git-ignored. `deploy.sh` copies the
integration to a Home Assistant host over SSH.
<!-- ha-marketplace:exclude-end -->
