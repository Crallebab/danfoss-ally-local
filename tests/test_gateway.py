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
        self.historic = {}
        self.socket = None
        self.buffered = []
        self.timeouts = []

    def cached_status(self, historic=False, nowait=False):
        # Like tinytuya: the live cache is unavailable for a sub-device.
        return self.historic if historic else None

    def receive(self):
        return self.buffered.pop(0) if self.buffered else None

    def set_socketTimeout(self, seconds):  # noqa: N802 - tinytuya's name
        self.timeouts.append(seconds)

    def close(self):
        self.closed = True


class FakeSocket:
    """Stands in for the connected socket; ``data`` is what a peek returns."""

    def __init__(self, data=b"x"):
        self.data = data
        self.timeouts = []

    def recv(self, size, flags=0):
        if isinstance(self.data, Exception):
            raise self.data
        return self.data

    def settimeout(self, seconds):
        self.timeouts.append(seconds)


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


def test_status_returns_the_in_order_record_of_the_child():
    # The reply handed back can be an old message; the historic status of the
    # child has every message in arrival order and is the truth.
    gw, script = make([{"dps": {"24": 193}}])
    child = gw._child("cid1")
    child.historic = {"dps": {"24": 189, "170": 20}}
    assert gw.status("cid1") == {"24": 189, "170": 20}


def test_buffered_pushes_are_read_before_a_request(monkeypatch):
    gw, script = make([{"dps": {"24": 189}}])
    parent = gw._gateway()
    parent.socket = sock = FakeSocket()  # connected
    parent.buffered = [{"dps": {"24": 193}, "cid": "cid2"}, {"dps": {"1": 5}, "cid": "cid3"}]
    parent.received_wrong_cid_queue.append(("child", {"dps": {"24": 193}}))
    monkeypatch.setattr(gateway, "_readable", lambda sock: bool(parent.buffered))
    gw.status("cid1")
    assert parent.buffered == []
    assert parent.received_wrong_cid_queue == []
    # Short while reading what is buffered, then back to normal for the request.
    assert sock.timeouts == [gateway._DRAIN_TIMEOUT, gateway._SOCKET_TIMEOUT]


def test_an_empty_buffer_is_not_read(monkeypatch):
    gw, script = make([{"dps": {"24": 189}}])
    parent = gw._gateway()
    parent.socket = sock = FakeSocket()
    parent.buffered = [{"dps": {"24": 193}}]
    monkeypatch.setattr(gateway, "_readable", lambda sock: False)
    gw.status("cid1")
    assert parent.buffered  # receive() was never called
    assert sock.timeouts == []  # and the timeout never touched


def test_parked_updates_are_dropped_even_with_an_empty_buffer(monkeypatch):
    # tinytuya's receive() hands out a parked update instead of reading the socket.
    gw, script = make([{"dps": {"24": 189}}])
    parent = gw._gateway()
    parent.socket = FakeSocket()
    parent.received_wrong_cid_queue.append(("child", {"dps": {"24": 193}}))
    monkeypatch.setattr(gateway, "_readable", lambda sock: False)
    gw.status("cid1")
    assert parent.received_wrong_cid_queue == []


@pytest.mark.parametrize("data", [b"", ConnectionResetError()])
def test_a_connection_closed_by_the_gateway_is_dropped_before_reading(monkeypatch, data):
    gw, script = make([{"dps": {"24": 189}}])
    parent = gw._gateway()
    parent.socket = FakeSocket(data)
    parent.buffered = [{"dps": {"24": 193}}]
    monkeypatch.setattr(gateway, "_readable", lambda sock: True)
    assert gw.status("cid1") == {"24": 189}
    assert parent.closed
    assert parent.buffered  # receive() was never called on the dead connection
    assert script.calls == [("status", "cid1")]  # asked once, on a new connection
    assert gw._gateway() is not parent


def test_readable_works_on_a_real_socket():
    import socket

    a, b = socket.socketpair()
    try:
        assert gateway._waiting(a) is False
        b.sendall(b"x")
        assert gateway._waiting(a) is True
        assert a.recv(1) == b"x"  # the peek consumed nothing
        b.close()
        assert gateway._waiting(a) is None
    finally:
        a.close()
        b.close()


def test_nothing_is_drained_before_the_first_connection():
    gw, script = make([{"dps": {"24": 189}}])
    parent = gw._gateway()
    parent.buffered = [{"dps": {"24": 193}}]
    gw.status("cid1")
    assert parent.buffered  # untouched: no socket yet


def test_readable_is_false_for_something_that_is_not_a_socket():
    assert gateway._readable(object()) is False


def test_unreachable_gateway_is_retried_once():
    down = {"Error": "Network Error: Unable to Connect", "Err": "901", "Payload": None}
    gw, script = make([down, {"dps": {"16": 205}}])
    assert gw.status("cid1") == {"16": 205}
    gw, script = make([down, down])
    with pytest.raises(gateway.GatewayConnectionError):
        gw.status("cid1")
    assert len(script.calls) == 2


def test_other_exceptions_propagate_with_their_type():
    def factory(*args, **kwargs):
        raise TypeError("a bug, not a gateway problem")

    gw = gateway.DanfossGateway(
        "192.0.2.1", "gwid", "k", "3.5", device_factory=factory, retry_delay=0
    )
    with pytest.raises(TypeError):
        gw.list_subdevices()


def test_an_exception_from_tinytuya_becomes_a_gateway_error_without_its_message():
    def factory(*args, **kwargs):
        raise UnicodeEncodeError("latin-1", "secret-key", 0, 1, "bad")

    gw = gateway.DanfossGateway(
        "192.0.2.1", "gwid", "k", "3.5", device_factory=factory, retry_delay=0
    )
    with pytest.raises(gateway.GatewayError) as err:
        gw.list_subdevices()
    assert "secret-key" not in str(err.value)
    assert "UnicodeEncodeError" in str(err.value)


def test_a_push_in_place_of_the_subdevice_list_is_retried():
    push = {"dps": {"24": 189}, "cid": "cid1"}
    gw, script = make([push, {"data": {"online": ["a"], "offline": []}}])
    assert gw.list_subdevices() == {"a": True}
    assert len(script.calls) == 2


def test_the_subdevice_list_is_read_past_a_push_without_reconnecting():
    push = {"dps": {"24": 189}, "cid": "cid1"}
    gw, script = make([push])
    parent = gw._gateway()
    parent.buffered = [push, {"data": {"online": ["a"], "offline": ["b"]}}]
    assert gw.list_subdevices() == {"a": True, "b": False}
    assert script.calls == [("subdev_query",)]
    assert not parent.closed


def test_nothing_but_pushes_after_a_subdevice_query_is_retried():
    push = {"dps": {"24": 189}, "cid": "cid1"}
    gw, script = make([push, {"data": {"online": ["a"], "offline": []}}])
    parent = gw._gateway()
    parent.buffered = [push] * gateway._PUSHES_BEFORE_REPLY
    assert gw.list_subdevices() == {"a": True}
    assert len(script.calls) == 2
    assert parent.closed


def test_error_messages_carry_no_full_ids():
    cid = "0123456789abcdef"

    class Holder:
        def __repr__(self):
            return f"Device(gwid, cid={cid})"

    reply = {"cid": cid, "t": 1, "device": Holder()}
    gw, _ = make([reply, reply])
    with pytest.raises(gateway.GatewayError) as err:
        gw.status(cid)
    assert cid not in str(err.value) and "gwid" not in str(err.value)
    assert "cdef" in str(err.value)

    gw, _ = make([None, None])
    with pytest.raises(gateway.GatewayError) as err:
        gw.status(cid)
    assert cid not in str(err.value)

    gw, _ = make([ERR_904, ERR_904])
    with pytest.raises(gateway.GatewayError) as err:
        gw.set_dps(cid, {"30": True})
    assert cid not in str(err.value)
