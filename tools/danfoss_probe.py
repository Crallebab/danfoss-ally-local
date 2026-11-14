"""Read-only probe for a Danfoss Ally gateway. Never writes to any device.

    pip install tinytuya pydanfossally

    python danfoss_probe.py scan
        Listen for Tuya broadcasts; prints gateway id, IP and protocol version.

    python danfoss_probe.py local --host IP --gw-id ID --key KEY [--version 3.5]
        List sub-devices and dump their raw DPs to snapshots/local.json.

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
        print(f"{ip}  gwId={info.get('gwId')}  version={info.get('version')}  "
              f"productKey={info.get('productKey')}")


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
        print(f"  {cid} -> {cloud[idx]['name'] + ' [' + cloud[idx]['type'] + ']' if idx is not None else 'UNMATCHED'}")

    # 2. Per device type, intersect candidate DPs for every code across all pairs.
    by_type: dict[str, dict[str, set[str] | None]] = {}
    for cid, idx in pairs.items():
        cdev, dps = cloud[idx], local[cid]
        codes = by_type.setdefault(cdev["type"], {})
        for code, value in cdev["status"].items():
            cands = {dp for dp, v in dps.items() if v == value}
            codes[code] = cands if codes.get(code) is None else codes[code] & cands

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
            if not cands:
                verdict = "NO MATCH (value changed between snapshots, or not exposed locally)"
            elif len(cands) == 1:
                dp = next(iter(cands))
                verdict = f"DP {dp}" + ("" if current_dp in (None, dp) else f"  <-- profiles.py says {current_dp}!")
            else:
                verdict = f"ambiguous: {sorted(cands, key=int)}"
                if current_dp in cands:
                    verdict += f"  (profiles.py guess {current_dp} fits)"
            print(f"  {code:24} {verdict}")
    print("\nAmbiguous codes: change that setting in the Danfoss app, re-run "
          "'local' and 'cloud', then 'map' again.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("scan").set_defaults(func=cmd_scan)
    p = sub.add_parser("local")
    p.add_argument("--host", required=True)
    p.add_argument("--gw-id", required=True)
    p.add_argument("--key", required=True)
    p.add_argument("--version", default="3.5")
    p.set_defaults(func=cmd_local)
    sub.add_parser("cloud").set_defaults(func=cmd_cloud)
    sub.add_parser("map").set_defaults(func=cmd_map)
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
