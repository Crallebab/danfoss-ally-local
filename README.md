# Danfoss Ally Local

A Home Assistant custom integration that controls a **Danfoss Ally Gateway**
over your LAN, with no cloud involved. The gateway is Tuya white-label hardware:
it speaks the Tuya LAN protocol (v3.5, TCP 6668), and its Zigbee devices are
Tuya sub-devices addressed by node id (`cid`). See
[Virki-Workspace/danfoss-ally-is-tuya](https://github.com/Virki-Workspace/danfoss-ally-is-tuya).

> **Status: scaffold.** The integration is not usable until you have the
> gateway's Tuya `local_key`, and writes stay disabled until the data point map
> is confirmed on real devices.

## Supported devices

| Device | Entities | DP map |
|---|---|---|
| Ally Radiator Thermostat | climate, battery, heating demand | numbers taken from the Icon RT schema (same code names), **unconfirmed** |
| Ally Room Sensor | temperature, humidity, battery | **unknown** |
| anything else | raw data points (diagnostic) | — |

Every sub-device also gets a **Raw data points** diagnostic sensor whose
attributes hold every raw DP. That is how unknown DPs get mapped.

## Safety

- Writes are refused for any profile with `confirmed=False` (`profiles.py`).
- DP 44 (`factory_reset` on Icon RT) can never be written.
- The gateway accepts **one local connection**: don't run LocalTuya, tuya-local
  or tinytuya scripts against it while this integration is loaded.

## Getting started

1. **Find the gateway**: `python tools/danfoss_probe.py scan`, which prints the
   gateway id and protocol version.
2. **Get the local key** for your own gateway. Options:
   - Tuya IoT Platform: create a cloud project and try to link your app account.
     This often fails for OEM apps such as Danfoss Ally.
   - The method from the reference above: run the Danfoss Ally app on a rooted
     Android emulator and read `DeviceBean.getLocalKey()` with Frida.
3. **Dump local DPs**: `python tools/danfoss_probe.py local --host IP --gw-id ID --key KEY`
4. **Dump the cloud view**: `DANFOSS_KEY=... DANFOSS_SECRET=... python tools/danfoss_probe.py cloud`
5. **Map**: `python tools/danfoss_probe.py map` shows, for each Danfoss code,
   which local DP matches. Fill in `profiles.py`, set `confirmed=True`, and commit.
6. Install (HACS custom repository or copy `custom_components/danfoss_ally_local`
   to `/config/custom_components/`), restart, then add the integration.

Snapshots go to `snapshots/`, which is git-ignored.

## Development

```
pip install pytest
pytest
```
