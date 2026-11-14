"""Blocking Tuya LAN client for the Danfoss Ally gateway.

All methods block and must run in an executor. A single lock serialises access,
because the gateway accepts only one local connection and all sub-devices share
its socket.
"""

from __future__ import annotations

import logging
import threading
from typing import Any

import tinytuya

from .const import FORBIDDEN_WRITE_DPS

_LOGGER = logging.getLogger(__name__)

# tinytuya error codes that mean the key or protocol version is wrong.
_AUTH_ERRORS = {"914", "904"}


class GatewayError(Exception):
    """The gateway could not be reached or returned an error."""


class GatewayAuthError(GatewayError):
    """The local key or protocol version is wrong."""


def _check(result: Any, what: str) -> dict[str, Any]:
    if result is None:
        raise GatewayError(f"{what}: no response")
    if not isinstance(result, dict):
        raise GatewayError(f"{what}: unexpected response {result!r}")
    if "Err" in result:
        err = str(result["Err"])
        msg = f"{what}: {result.get('Error', 'error')} ({err})"
        if err in _AUTH_ERRORS:
            raise GatewayAuthError(msg)
        raise GatewayError(msg)
    return result


class DanfossGateway:
    """Connection to one gateway and its Zigbee sub-devices."""

    def __init__(self, host: str, device_id: str, local_key: str, version: str) -> None:
        self._host = host
        self._device_id = device_id
        self._local_key = local_key
        self._version = float(version)
        self._lock = threading.Lock()
        self._gw: tinytuya.Device | None = None
        self._children: dict[str, tinytuya.Device] = {}

    def _gateway(self) -> tinytuya.Device:
        if self._gw is None:
            gw = tinytuya.Device(
                self._device_id,
                self._host,
                self._local_key,
                version=self._version,
                persist=True,
                connection_retry_limit=2,
                connection_retry_delay=2,
            )
            gw.set_socketTimeout(5)
            self._gw = gw
            self._children = {}
        return self._gw

    def _child(self, cid: str) -> tinytuya.Device:
        child = self._children.get(cid)
        if child is None:
            # The sub-device id is only used locally to route replies; the gateway
            # addresses sub-devices by cid (Zigbee node id).
            child = tinytuya.Device(cid, cid=cid, parent=self._gateway())
            self._children[cid] = child
        return child

    def _reset(self) -> None:
        if self._gw is not None:
            try:
                self._gw.close()
            except Exception:  # noqa: BLE001
                pass
        self._gw = None
        self._children = {}

    def list_subdevices(self) -> dict[str, bool]:
        """Return ``{cid: online}`` for every sub-device paired to the gateway."""
        with self._lock:
            try:
                result = _check(self._gateway().subdev_query(), "subdev_query")
            except GatewayError:
                self._reset()
                raise
        data = result.get("data", result)
        if not isinstance(data, dict):
            raise GatewayError(f"subdev_query: unexpected payload {result!r}")
        found: dict[str, bool] = {}
        for cid in data.get("online", []) or []:
            found[str(cid)] = True
        for cid in data.get("offline", []) or []:
            found.setdefault(str(cid), False)
        return found

    def status(self, cid: str) -> dict[str, Any]:
        """Return the raw DP dict of one sub-device."""
        with self._lock:
            try:
                result = _check(self._child(cid).status(), f"status {cid}")
            except GatewayError:
                self._reset()
                raise
        dps = result.get("dps")
        if not isinstance(dps, dict):
            raise GatewayError(f"status {cid}: no dps in {result!r}")
        return {str(k): v for k, v in dps.items()}

    def set_dps(self, cid: str, values: dict[str, Any]) -> None:
        """Write raw DPs to one sub-device."""
        bad = FORBIDDEN_WRITE_DPS.intersection(values)
        if bad:
            raise GatewayError(f"refusing to write forbidden DP(s) {sorted(bad)}")
        with self._lock:
            try:
                _check(self._child(cid).set_multiple_values(values), f"set {cid}")
            except GatewayError:
                self._reset()
                raise

    def close(self) -> None:
        """Close the socket."""
        with self._lock:
            self._reset()
