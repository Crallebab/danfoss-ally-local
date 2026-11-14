"""Tests for the gateway client with a fake tinytuya device (no network, no HA)."""

from __future__ import annotations

import pytest

from conftest import load

load("const")
gateway = load("gateway")

ERR_904 = {"Error": "Unexpected Payload from Device", "Err": "904", "Payload": None}
ERR_914 = {"Error": "Check device key or version", "Err": "914", "Payload": None}


class FakeDevice:
    """Stands in for tinytuya.Device; replies are popped from a shared script."""

    def __init__(self, dev_id, address=None, local_key="", *, cid=None, parent=None, **kwargs):
        self.id = dev_id
        self.cid = cid
        self.parent = parent
        self.closed = False
        self.received_wrong_cid_queue = []
        self.cache = None

    def cached_status(self, nowait=False):
        return self.cache

    def set_socketTimeout(self, seconds):  # noqa: N802 - tinytuya's name
        pass

    def close(self):
        self.closed = True


class Script:
    """Factory plus a queue of replies and a log of calls."""

    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = []
        self.devices = []

    def factory(self, *args, **kwargs):
        script = self

        class Device(FakeDevice):
            def subdev_query(self):
                script.calls.append(("subdev_query",))
                return script.replies.pop(0)

            def status(self):
                script.calls.append(("status", self.cid))
                return script.replies.pop(0)

            def set_multiple_values(self, values):
                script.calls.append(("set", self.cid, dict(values)))
                return script.replies.pop(0)

        dev = Device(*args, **kwargs)
        self.devices.append(dev)
        return dev


def make(replies):
    script = Script(replies)
    gw = gateway.DanfossGateway(
        "192.0.2.1", "gwid", "0123456789abcdef", "3.5", device_factory=script.factory, retry_delay=0
    )
    return gw, script


def test_status_stringifies_dp_keys():
    gw, _ = make([{"dps": {1: True, "16": 205}}])
    assert gw.status("cid1") == {"1": True, "16": 205}


def test_904_is_retried_once_then_succeeds():
    gw, script = make([ERR_904, {"dps": {"16": 205}}])
    assert gw.status("cid1") == {"16": 205}
    assert script.calls == [("status", "cid1"), ("status", "cid1")]
    # The first connection was dropped and a new one opened for the retry.
    assert script.devices[0].closed


def test_914_is_retried_once_then_succeeds():
    gw, script = make([ERR_914, {"dps": {"16": 205}}])
    assert gw.status("cid1") == {"16": 205}
    assert len(script.calls) == 2


def test_914_twice_raises_auth_error():
    gw, _ = make([ERR_914, ERR_914])
    with pytest.raises(gateway.GatewayAuthError):
        gw.status("cid1")


def test_failure_twice_raises_gateway_error_not_auth():
    gw, _ = make([None, ERR_904])
    with pytest.raises(gateway.GatewayError) as err:
        gw.status("cid1")
    assert not isinstance(err.value, gateway.GatewayAuthError)


def test_non_dict_response_raises():
    gw, _ = make(["garbage", "garbage"])
    with pytest.raises(gateway.GatewayError):
        gw.status("cid1")


def test_list_subdevices_merges_online_and_offline():
    gw, _ = make([{"data": {"online": ["a", "b"], "offline": ["b", "c"]}}])
    assert gw.list_subdevices() == {"a": True, "b": True, "c": False}


def test_list_subdevices_accepts_flat_payload():
    gw, _ = make([{"online": ["a"], "offline": []}])
    assert gw.list_subdevices() == {"a": True}


@pytest.mark.parametrize("key", ["44", 44])
def test_factory_reset_dp_is_never_written(key):
    gw, script = make([{}])
    with pytest.raises(gateway.GatewayError, match="forbidden"):
        gw.set_dps("cid1", {key: True, "30": False})
    assert script.calls == []


def test_set_dps_sends_string_keys():
    gw, script = make([{}])
    gw.set_dps("cid1", {30: False})
    assert script.calls == [("set", "cid1", {"30": False})]


def test_closed_gateway_never_reconnects():
    gw, script = make([{"dps": {"16": 205}}, {"dps": {"16": 205}}])
    gw.status("cid1")
    gw.close()
    assert gw.closed
    with pytest.raises(gateway.GatewayError, match="closed"):
        gw.status("cid1")
    assert len(script.devices) == 2  # gateway + one child, nothing new after close


def test_none_reply_to_a_write_is_success():
    # tinytuya returns None when the gateway ACKs a command without a DP report.
    gw, script = make([None])
    gw.set_dps("cid1", {"30": True})
    assert len(script.calls) == 1  # not re-sent


def test_none_reply_to_a_status_is_retried():
    gw, script = make([None, {"dps": {"16": 205}}])
    assert gw.status("cid1") == {"16": 205}
    assert len(script.calls) == 2


def test_status_prefers_the_in_order_cache_over_a_replayed_reply():
    # The reply handed back is an old queued update; the child's cache has the
    # messages in arrival order and is the truth.
    gw, script = make([{"dps": {"24": 193}}])
    child = gw._child("cid1")
    child.cache = {"dps": {"24": 189, "170": 20}}
    assert gw.status("cid1") == {"24": 189, "170": 20}


def test_status_clears_the_parked_update_queue():
    gw, script = make([{"dps": {"24": 189}}])
    parent = gw._gateway()
    parent.received_wrong_cid_queue.append(("child", {"dps": {"24": 193}}))
    gw.status("cid1")
    assert parent.received_wrong_cid_queue == []


def test_a_push_in_place_of_the_subdevice_list_is_an_error():
    push = {"dps": {"24": 189}, "cid": "cid1"}
    gw, _ = make([push, push])
    with pytest.raises(gateway.GatewayError, match="no sub-device list"):
        gw.list_subdevices()
