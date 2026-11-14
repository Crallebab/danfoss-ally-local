"""Print every raw DP change of Danfoss Ally Local sub-devices, read via the HA API.

Use while changing one setting at a time in the Danfoss app, to see which local
data point it lands in. Read-only: it never talks to the gateway itself.

    python tools/watch_dps.py https://ha.example.com [--token-file ~/.ha_token] [--names names.json]

names.json (optional; keep it in snapshots/, which is git-ignored): {"<last 4 of cid>": "Room name", ...}
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import ssl
import sys
import time
import urllib.request

SUFFIX = "_raw_data_points"


def fetch(base: str, token: str) -> dict[str, dict]:
    req = urllib.request.Request(f"{base}/api/states", headers={"Authorization": f"Bearer {token}"})
    with urllib.request.urlopen(req, timeout=10, context=ssl.create_default_context()) as resp:
        states = json.load(resp)
    return {
        s["attributes"]["cid"]: s["attributes"].get("dps", {})
        for s in states
        if s["entity_id"].startswith("sensor.") and s["entity_id"].endswith(SUFFIX) and "cid" in s["attributes"]
    }


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("base")
    p.add_argument("--token-file", default=str(Path.home() / ".ha_token"))
    p.add_argument("--names")
    p.add_argument("--interval", type=float, default=5)
    a = p.parse_args()
    if not a.base.startswith("https://"):
        sys.exit("Refusing to send the access token over plain http; use an https:// URL")
    token = Path(a.token_file).read_text(encoding="utf-8").strip()
    names = json.loads(Path(a.names).read_text(encoding="utf-8")) if a.names else {}

    def label(cid: str) -> str:
        return names.get(cid[-4:], cid[-4:])

    prev = fetch(a.base, token)
    print(time.strftime("%H:%M:%S"), "watching", ", ".join(sorted(label(c) for c in prev)), flush=True)
    while True:
        time.sleep(a.interval)
        try:
            cur = fetch(a.base, token)
        except Exception as err:  # noqa: BLE001
            print(time.strftime("%H:%M:%S"), "fetch failed:", err, file=sys.stderr, flush=True)
            continue
        for cid, dps in cur.items():
            old = prev.get(cid, {})
            for dp in sorted(set(dps) | set(old), key=int):
                if dps.get(dp) != old.get(dp):
                    stamp = time.strftime("%H:%M:%S")
                    print(f"{stamp}  {label(cid):22} DP {dp:>4}: {old.get(dp)!r} -> {dps.get(dp)!r}", flush=True)
        prev = cur


if __name__ == "__main__":
    main()
