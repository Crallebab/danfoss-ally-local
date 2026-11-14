"""State of one sub-device (no Home Assistant imports, so it is unit-testable)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .profiles import UNKNOWN, Profile


@dataclass
class SubDevice:
    """State of one sub-device."""

    cid: str
    # Reachable: the last poll worked and the gateway lists the device as online.
    online: bool = True
    dps: dict[str, Any] = field(default_factory=dict)
    profile: Profile = UNKNOWN
    # What the gateway's sub-device list says.
    gateway_online: bool = True
    # DPs written recently: {dp: (value, monotonic deadline)}.
    pending: dict[str, tuple[Any, float]] = field(default_factory=dict)

    def value(self, code: str) -> Any:
        """Return a decoded value by Danfoss code."""
        return self.profile.value(self.dps, code)

    def has(self, code: str) -> bool:
        """Return whether a code is mapped and reported."""
        return self.profile.has(self.dps, code)

    def merge(self, polled: dict[str, Any], now: float) -> None:
        """Merge polled DPs, keeping recently written values until confirmed."""
        for dp, value in polled.items():
            held = self.pending.get(dp)
            if held is not None:
                if value != held[0] and now < held[1]:
                    continue  # still the pre-write value from the gateway's cache
                del self.pending[dp]
            self.dps[dp] = value
