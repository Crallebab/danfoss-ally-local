"""Read-only probe for a Danfoss Ally gateway. Never writes to any device.

pip install tinytuya pydanfossally

python danfoss_probe.py scan
    Listen for Tuya broadcasts; prints gateway id, IP and protocol version.

python danfoss_probe.py local --host IP --gw-id ID --key KEY [--version 3.5]
    List sub-devices and dump their raw DPs to snapshots/local.json.

python danfoss_probe.py listen --host IP --gw-id ID [--minutes 10]
    Print every message the gateway sends while you change settings in the
    Danfoss app, including messages from ids it does not list as sub-devices
    (e.g. a room that groups a room sensor with its thermostats).

DANFOSS_KEY=... DANFOSS_SECRET=... python danfoss_probe.py cloud
    Dump the Danfoss cloud view (code -> value) to snapshots/cloud.json.
    Uses the same API key/secret as the Danfoss Ally cloud integration.

python danfoss_probe.py map
    Match local DP numbers to Danfoss codes by comparing the two snapshots.
    Take both snapshots within a minute of each other for best results.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path
import sys
import time
from typing import Any

SNAPSHOTS = Path(__file__).resolve().parent.parent / "snapshots"
REDACT = {"local_key", "localKey", "uid", "owner_id", "ip", "lat", "lon"}


def _save(name: str, data: Any) -> Path:
    SNAPSHOTS.mkdir(exist_ok=True)
    path = SNAPSHOTS / name
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    return path


def cmd_scan(_: argparse.Namespace) -> None:
    import tinytuya

    found = tinytuya.deviceScan(verbose=False, maxretry=8)
    if not found:
        print("No Tuya broadcasts seen. Same subnet? Firewall allowing UDP 6666/6667/7000?")
    for ip, info in found.items():
        print(
            f"{ip}  gwId={info.get('gwId')}  version={info.get('version')}  "
            f"productKey={info.get('productKey')}"
        )


def _load_gateway_class() -> type:
    """Import gateway.py without the package __init__ (which needs Home Assistant)."""
    import importlib.util
    import types

    pkg_dir = Path(__file__).resolve().parent.parent / "custom_components" / "danfoss_ally_local"
    pkg = types.ModuleType("dal")
    pkg.__path__ = [str(pkg_dir)]
    sys.modules["dal"] = pkg
    for name in ("const", "gateway"):
        spec = importlib.util.spec_from_file_location(f"dal.{name}", pkg_dir / f"{name}.py")
        mod = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = mod
        spec.loader.exec_module(mod)
    return sys.modules["dal.gateway"].DanfossGateway


def cmd_local(args: argparse.Namespace) -> None:
    if not args.key:
        sys.exit("Set DANFOSS_LOCAL_KEY or pass --key")
    DanfossGateway = _load_gateway_class()  # noqa: N806

    gw = DanfossGateway(args.host, args.gw_id, args.key, args.version)
    try:
        try:
            subs = gw.list_subdevices()
        except Exception as err:  # noqa: BLE001
            sys.exit(f"Gateway error: {err}")
        print(f"{len(subs)} sub-devices")
        out: dict[str, Any] = {}
        for cid, online in subs.items():
            try:
                dps = gw.status(cid)
            except Exception as err:  # noqa: BLE001
                print(f"  {cid}  online={online}  ERROR {err}")
                out[cid] = {"online": online, "error": str(err)}
                continue
            print(f"  {cid}  online={online}  {len(dps)} DPs: {dps}")
            out[cid] = {"online": online, "dps": dps}
    finally:
        gw.close()
    print(f"saved {_save('local.json', out)}")


def cmd_listen(args: argparse.Namespace) -> None:
    """Print every message the gateway sends, including ones from unlisted ids."""
    if not args.key:
        sys.exit("Set DANFOSS_LOCAL_KEY or pass --key")
    DanfossGateway = _load_gateway_class()  # noqa: N806

    gw = DanfossGateway(args.host, args.gw_id, args.key, args.version)
    seen: dict[str, dict[str, Any]] = {}
    try:
        try:
            listed = gw.list_subdevices()
        except Exception as err:  # noqa: BLE001
            sys.exit(f"Gateway error: {err}")
        print(f"{len(listed)} sub-devices listed: {', '.join(cid[-4:] for cid in listed)}")
        print(f"Listening for {args.minutes:g} min. Change one setting at a time in the app.")
        deadline = time.monotonic() + args.minutes * 60
        next_beat = 0.0
        while time.monotonic() < deadline:
            # No children are registered, so tinytuya hands every message over
            # as it is, whatever id it comes from.
            dev = gw._gateway()  # noqa: SLF001 - raw messages wanted
            if time.monotonic() >= next_beat:
                dev.heartbeat(nowait=True)  # keeps the gateway from closing the idle socket
                next_beat = time.monotonic() + 10
            msg = dev.receive()  # None after the socket timeout
            if not isinstance(msg, dict):
                continue
            stamp = time.strftime("%H:%M:%S")
            if "Err" in msg:
                print(f"{stamp}  connection lost ({msg.get('Error')}); reconnecting", flush=True)
                try:
                    gw.list_subdevices()
                except Exception as err:  # noqa: BLE001
                    print(f"{stamp}  {err}", flush=True)
                    time.sleep(5)
                next_beat = 0.0
                continue
            data = msg.get("data") if isinstance(msg.get("data"), dict) else {}
            cid = msg.get("cid") or data.get("cid")
            dps = msg.get("dps") or data.get("dps")
            if cid is None:
                who = "gateway itself"
            elif cid in listed:
                who = str(cid)[-4:]
            else:
                who = f"UNLISTED id {cid}"  # in full: this is what we are looking for
            if not isinstance(dps, dict):
                print(f"{stamp}  {who:22} {msg}", flush=True)
                continue
            old = seen.setdefault(str(cid), {})
            for dp, value in sorted(dps.items(), key=lambda kv: str(kv[0]).zfill(4)):
                if old.get(dp, value) != value:
                    print(f"{stamp}  {who:22} DP {dp:>4}: {old[dp]!r} -> {value!r}", flush=True)
                elif dp not in old:
                    print(f"{stamp}  {who:22} DP {dp:>4}: {value!r}", flush=True)
                old[dp] = value
    except KeyboardInterrupt:
        pass
    finally:
        gw.close()


def _redact(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {k: ("<redacted>" if k in REDACT else _redact(v)) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_redact(v) for v in obj]
    return obj


def cmd_cloud(_: argparse.Namespace) -> None:
    from pydanfossally import DanfossAlly

    key, secret = os.environ.get("DANFOSS_KEY"), os.environ.get("DANFOSS_SECRET")
    if not key or not secret:
        sys.exit("Set DANFOSS_KEY and DANFOSS_SECRET")

    async def run() -> list[dict[str, Any]]:
        async with DanfossAlly() as ally:
            if not await ally.initialize(key, secret):
                sys.exit("Danfoss cloud authentication failed")
            raw = await ally._api.get_devices()  # noqa: SLF001 - raw codes wanted
            return _redact(raw.get("result", []))

    devices = asyncio.run(run())
    out = [
        {
            "name": d.get("name", "").strip(),
            "type": d.get("device_type"),
            "status": {s["code"]: s["value"] for s in d.get("status", [])},
        }
        for d in devices
        if d.get("status")
    ]
    for d in out:
        print(f"  {d['type']}: {d['name']} ({len(d['status'])} codes)")
    print(f"saved {_save('cloud.json', out)}")


def _distinctive(value: Any) -> bool:
    """Values unlikely to match by coincidence."""
    if isinstance(value, bool) or value is None:
        return False
    if isinstance(value, (int, float)):
        return value not in (0, 1, 100)
    return isinstance(value, str) and len(value) > 3


def cmd_map(_: argparse.Namespace) -> None:
    for name in ("local.json", "cloud.json"):
        if not (SNAPSHOTS / name).exists():
            sys.exit(f"snapshots/{name} is missing; run the 'local' and 'cloud' commands first")
    local = json.loads((SNAPSHOTS / "local.json").read_text(encoding="utf-8"))
    cloud = json.loads((SNAPSHOTS / "cloud.json").read_text(encoding="utf-8"))
    local = {cid: v["dps"] for cid, v in local.items() if "dps" in v}

    # 1. Pair local sub-devices with cloud devices by shared distinctive values.
    scores = []
    for cid, dps in local.items():
        lvals = [v for v in dps.values() if _distinctive(v)]
        for idx, cdev in enumerate(cloud):
            cvals = [v for v in cdev["status"].values() if _distinctive(v)]
            shared = sum(min(lvals.count(v), cvals.count(v)) for v in set(cvals))
            size_penalty = abs(len(dps) - len(cdev["status"])) * 0.1
            scores.append((shared - size_penalty, cid, idx))
    pairs: dict[str, int] = {}
    for score, cid, idx in sorted(scores, reverse=True):
        if score <= 0 or cid in pairs or idx in pairs.values():
            continue
        pairs[cid] = idx

    print("Pairing (local cid -> cloud device):")
    for cid in local:
        idx = pairs.get(cid)
        print(
            f"  {cid} -> {cloud[idx]['name'] + ' [' + cloud[idx]['type'] + ']' if idx is not None else 'UNMATCHED'}"
        )

    # 2. Per device type and code, score every DP number. The gateway only returns
    #    the DPs it has cached for each sub-device, so a DP missing on one device is
    #    not evidence against it; a DP present with a different value is.
    #    A candidate is kept if it was never contradicted, ranked by how many
    #    devices confirm it.
    def same(a: Any, b: Any) -> bool:
        return type(a) is type(b) and a == b  # True must not equal 1

    present: dict[str, dict[str, int]] = {}  # dtype -> dp -> devices reporting it
    match: dict[str, dict[str, dict[str, int]]] = {}  # dtype -> code -> dp -> matches
    for cid, idx in pairs.items():
        cdev, dps = cloud[idx], local[cid]
        dtype = cdev["type"]
        for dp in dps:
            present.setdefault(dtype, {})[dp] = present.get(dtype, {}).get(dp, 0) + 1
        codes = match.setdefault(dtype, {})
        for code, value in cdev["status"].items():
            hits = codes.setdefault(code, {})
            for dp, v in dps.items():
                if same(v, value):
                    hits[dp] = hits.get(dp, 0) + 1

    by_type: dict[str, dict[str, dict[str, int]]] = {}
    for dtype, codes in match.items():
        by_type[dtype] = {
            code: {dp: n for dp, n in hits.items() if n == present[dtype][dp]}
            for code, hits in codes.items()
        }

    _load_gateway_class()  # registers the 'dal' package
    import importlib.util

    pkg_dir = Path(__file__).resolve().parent.parent / "custom_components" / "danfoss_ally_local"
    spec = importlib.util.spec_from_file_location("dal.profiles", pkg_dir / "profiles.py")
    prof_mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = prof_mod
    spec.loader.exec_module(prof_mod)
    hints = {"Radiator": prof_mod.RADIATOR_THERMOSTAT, "Room Sensor": prof_mod.ROOM_SENSOR}

    for dtype, codes in by_type.items():
        profile = next((p for k, p in hints.items() if k in dtype), None)
        print(f"\n{dtype}")
        for code, cands in codes.items():
            current = profile.codes.get(code) if profile else None
            current_dp = current.dp if current else None
            ranked = sorted(cands.items(), key=lambda kv: (-kv[1], int(kv[0])))
            if not ranked:
                verdict = "NO MATCH (not reported locally, or value changed between snapshots)"
            elif len(ranked) == 1 or ranked[0][1] > ranked[1][1]:
                dp, n = ranked[0]
                verdict = f"DP {dp}  ({n} device(s) agree)"
                if len(ranked) > 1:
                    verdict += f"  also fits: {[d for d, _ in ranked[1:]]}"
                if current_dp not in (None, dp):
                    verdict += f"  <-- profiles.py says {current_dp}!"
            else:
                verdict = "ambiguous: " + ", ".join(f"{d}({n})" for d, n in ranked)
                if current_dp in cands:
                    verdict += f"  (profiles.py guess {current_dp} fits)"
            print(f"  {code:24} {verdict}")
    print(
        "\nAmbiguous codes: change that setting in the Danfoss app, re-run "
        "'local' and 'cloud', then 'map' again."
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("scan").set_defaults(func=cmd_scan)
    p = sub.add_parser("local")
    p.add_argument("--host", required=True)
    p.add_argument("--gw-id", required=True)
    p.add_argument(
        "--key",
        default=os.environ.get("DANFOSS_LOCAL_KEY"),
        help="local key; prefer the DANFOSS_LOCAL_KEY env var (keeps it out of shell history)",
    )
    p.add_argument("--version", default="3.5")
    p.set_defaults(func=cmd_local)
    p = sub.add_parser("listen")
    p.add_argument("--host", required=True)
    p.add_argument("--gw-id", required=True)
    p.add_argument(
        "--key",
        default=os.environ.get("DANFOSS_LOCAL_KEY"),
        help="local key; prefer the DANFOSS_LOCAL_KEY env var (keeps it out of shell history)",
    )
    p.add_argument("--version", default="3.5")
    p.add_argument("--minutes", type=float, default=10)
    p.set_defaults(func=cmd_listen)
    sub.add_parser("cloud").set_defaults(func=cmd_cloud)
    sub.add_parser("map").set_defaults(func=cmd_map)
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
