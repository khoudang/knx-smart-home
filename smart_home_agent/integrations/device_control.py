from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol


@dataclass(frozen=True)
class ControlCommand:
    device_id: str
    device_type: str
    action: str
    control_ga: str | None = None
    status_ga: str | None = None
    dim_control_ga: str | None = None
    dim_status_ga: str | None = None
    color_control_ga: str | None = None
    color_status_ga: str | None = None
    value: Any = None
    expected_state: dict[str, Any] | None = None
    request_id: str | None = None


@dataclass(frozen=True)
class ControlResult:
    status: str
    message: str | None = None
    state: Any = None


class DeviceControlClient(Protocol):
    def health_check(self) -> ControlResult:
        ...

    def execute(self, command: ControlCommand) -> ControlResult:
        ...

    def read_state(self, command: ControlCommand) -> ControlResult:
        ...
