"""Blocking Tuya LAN client for the Danfoss Ally gateway.

All methods block and must run in an executor. A single lock serialises access,
because the gateway accepts only one local connection and all sub-devices share
its socket.
"""

from __future__ import annotations

from collections.abc import Callable
import logging
import threading
import time
from typing import Any

import tinytuya

from .const import FORBIDDEN_WRITE_DPS

_LOGGER = logging.getLogger(__name__)

# tinytuya error code for "check device key or version". It is also returned when
# the gateway simply does not answer the session-key negotiation on a fresh
# socket (busy connection slot), so it is retried once like any other error and
# callers must not treat a single occurrence as proof of a wrong key.
# 904 ("Unexpected Payload") is a plain transient error.
_AUTH_ERRORS = {"914"}
# The TCP connection itself failed (host down or unreachable). tinytuya already
# retried the connect, so these are not retried again.
_CONNECTION_ERRORS = {"901", "905"}
_RETRY_DELAY = 2.0
_SOCKET_TIMEOUT = 5.0
# Reading buffered messages before a request: short timeout, bounded count.
_DRAIN_TIMEOUT = 0.2
_DRAIN_MAX_MESSAGES = 25


class GatewayError(Exception):
    """The gateway could not be reached or returned an error."""


class GatewayAuthError(GatewayError):
    """The gateway rejected the session twice in a row: probably a wrong key."""


class GatewayConnectionError(GatewayError):
    """The gateway cannot be reached on the network."""


def _check(result: Any, what: str, none_ok: bool = False) -> dict[str, Any]:
    if result is None:
        if none_ok:
            return {}
        raise GatewayError(f"{what}: no response")
    if not isinstance(result, dict):
        raise GatewayError(f"{what}: unexpected response {result!r}")
    if "Err" in result:
        err = str(result["Err"])
        msg = f"{what}: {result.get('Error', 'error')} ({err})"
        if err in _AUTH_ERRORS:
            raise GatewayAuthError(msg)
        if err in _CONNECTION_ERRORS:
            raise GatewayConnectionError(msg)
        raise GatewayError(msg)
    return result


class DanfossGateway:
    """Connection to one gateway and its Zigbee sub-devices."""

    def __init__(
        self,
        host: str,
        device_id: str,
        local_key: str,
        version: str,
        *,
        device_factory: Callable[..., Any] | None = None,
        retry_delay: float = _RETRY_DELAY,
    ) -> None:
        self._host = host
        self._device_id = device_id
        self._local_key = local_key
        self._version = float(version)
        self._factory = device_factory or tinytuya.Device
        self._retry_delay = retry_delay
        self._lock = threading.Lock()
        self._gw: Any = None
        self._children: dict[str, Any] = {}
        self._closed = False

    @property
    def closed(self) -> bool:
        """Return whether close() was called; a closed gateway never reconnects."""
        return self._closed

    def _gateway(self) -> Any:
        if self._gw is None:
            gw = self._factory(
                self._device_id,
                self._host,
                self._local_key,
                version=self._version,
                persist=True,
                connection_retry_limit=2,
                connection_retry_delay=2,
            )
            gw.set_socketTimeout(_SOCKET_TIMEOUT)
            self._gw = gw
            self._children = {}
        return self._gw

    def _child(self, cid: str) -> Any:
        child = self._children.get(cid)
        if child is None:
            # The sub-device id is only used locally to route replies; the gateway
            # addresses sub-devices by cid (Zigbee node id).
            child = self._factory(cid, cid=cid, parent=self._gateway())
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

    def _drain(self) -> None:
        """Read everything already buffered on the socket.

        The gateway pushes DP changes unasked. tinytuya takes the first message
        it reads as the reply to a request, so an unread push would be mistaken
        for the reply and the real reply left behind for the next request. Each
        message read here is merged into the cached status of its child.
        """
        gw = self._gateway()
        if getattr(gw, "socket", None) is None:
            return  # not connected yet: nothing can be buffered
        gw.set_socketTimeout(_DRAIN_TIMEOUT)
        try:
            for _ in range(_DRAIN_MAX_MESSAGES):
                message = gw.receive()
                if not isinstance(message, dict) or "Err" in message:
                    break
        finally:
            gw.set_socketTimeout(_SOCKET_TIMEOUT)
        # Updates parked for other children are in their caches by now; a stale
        # entry must not be replayed as a reply.
        queue = getattr(gw, "received_wrong_cid_queue", None)
        if queue:
            queue.clear()

    def _call(self, what: str, func: Callable[[], Any], none_ok: bool = False) -> dict[str, Any]:
        """Run one request; on a failure reconnect and retry once.

        The gateway sometimes ignores the first request on a fresh socket shortly
        after another connection closed, which surfaces as "no response", 904 or
        914. Only a failure on both attempts is reported to the caller. An
        unreachable gateway is reported at once: tinytuya already retried.
        """
        with self._lock:
            for attempt in (1, 2):
                if self._closed:
                    raise GatewayError(f"{what}: connection is closed")
                try:
                    try:
                        self._drain()
                        result = func()
                    except GatewayError:
                        raise
                    except Exception as err:
                        # tinytuya raises for malformed input (e.g. a key that is
                        # not latin-1). Only the type is reported: the message
                        # can contain the key.
                        raise GatewayError(f"{what}: {type(err).__name__}") from None
                    return _check(result, what, none_ok)
                except GatewayConnectionError:
                    self._reset()
                    raise
                except GatewayError as err:
                    self._reset()
                    if attempt == 2:
                        raise
                    _LOGGER.debug("%s failed (%s); reconnecting and retrying", what, err)
                    time.sleep(self._retry_delay)
        raise AssertionError("unreachable")

    def list_subdevices(self) -> dict[str, bool]:
        """Return ``{cid: online}`` for every sub-device paired to the gateway."""

        def query() -> Any:
            result = self._gateway().subdev_query()
            if isinstance(result, dict) and "Err" not in result:
                data = result.get("data", result)
                # A DP push can still slip in ahead of the reply; it has neither
                # list. Raising here makes _call reconnect and ask again.
                if not isinstance(data, dict) or not ("online" in data or "offline" in data):
                    raise GatewayError("subdev_query: reply carries no sub-device list")
            return result

        result = self._call("subdev_query", query)
        data = result.get("data", result)
        found: dict[str, bool] = {}
        for cid in data.get("online", []) or []:
            found[str(cid)] = True
        for cid in data.get("offline", []) or []:
            found.setdefault(str(cid), False)
        return found

    def status(self, cid: str) -> dict[str, Any]:
        """Return the raw DP dict of one sub-device."""

        def query() -> Any:
            child = self._child(cid)
            result = child.status()
            if isinstance(result, dict) and "Err" not in result:
                # The historic status of the child holds every message from it
                # in arrival order, including pushes read while draining or
                # while another child was being queried.
                cached = child.cached_status(historic=True)
                if isinstance(cached, dict) and isinstance(cached.get("dps"), dict):
                    return {**result, "dps": dict(cached["dps"])}
            return result

        result = self._call(f"status {cid}", query)
        dps = result.get("dps")
        if not isinstance(dps, dict):
            raise GatewayError(f"status {cid}: no dps in {result!r}")
        return {str(k): v for k, v in dps.items()}

    def set_dps(self, cid: str, values: dict[str, Any]) -> None:
        """Write raw DPs to one sub-device."""
        # Normalise keys first so an int 44 cannot slip past the guard.
        raw = {str(dp): value for dp, value in values.items()}
        bad = FORBIDDEN_WRITE_DPS.intersection(raw)
        if bad:
            raise GatewayError(f"refusing to write forbidden DP(s) {sorted(bad)}")
        # tinytuya returns None when the gateway acknowledged the command but sent
        # no DP report in time (unchanged value, sleeping valve): that is success.
        # DP values are absolute, so a retry after a real failure is safe.
        self._call(f"set {cid}", lambda: self._child(cid).set_multiple_values(raw), none_ok=True)

    def close(self) -> None:
        """Close the socket for good; later calls fail instead of reconnecting."""
        # Set before taking the lock so a request in flight does not retry.
        self._closed = True
        with self._lock:
            self._reset()
