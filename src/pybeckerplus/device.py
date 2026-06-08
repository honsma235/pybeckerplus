"""Representation of individual Becker CentronicPlus devices."""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

from pybeckerplus.constants import (
    DEVICE_RESPONSE_TIMEOUT,
    Action,
    StatusBit,
    StatusBitAux,
)
from pybeckerplus.packet import (
    build_action_packet,
    build_get_name_packet,
    build_identify_packet,
    build_info_request,
    build_moveto_packet,
    build_set_name_packet,
    build_status_request,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from pybeckerplus.client import BeckerClient

_LOGGER = logging.getLogger(__name__)


class CentronicPlusDevice:
    """Representation of a Becker CentronicPlus Motor."""

    def __init__(
        self,
        mac_id: str,
        client: BeckerClient,
        callback: Callable[[CentronicPlusDevice], None] | None = None,
    ) -> None:
        """Initialize a new CentronicDevice instance."""
        self.mac_id = mac_id
        self._client = client
        self.position: float = 0.0
        self.moving: bool = False
        self.upper_limit: bool = False
        self.lower_limit: bool = False
        self.blocked: bool = False
        self.overheated: bool = False
        self.anti_freeze: bool = False
        self.fly_screen: bool = False
        self.rssi: int | None = None
        self.serial_number: str | None = None
        self.firmware_version: str | None = None
        self.name: str | None = None
        self.available: bool = True

        self._availability_timer: asyncio.TimerHandle | None = None
        self._poll_task: asyncio.Task[None] | None = None

        # Discovery flags
        self._got_status = False
        self._got_info = False
        self._got_name = False

        self._callback = callback
        _LOGGER.debug("Device: %s created", self.mac_id)

    async def up(self) -> None:
        """Move device up."""
        await self.action(Action.UP)

    async def down(self) -> None:
        """Move device down."""
        await self.action(Action.DOWN)

    async def stop(self) -> None:
        """Stop device movement."""
        await self.action(Action.STOP)

    async def action(self, action: Action) -> None:
        """Send an action command."""
        payload = build_action_packet(self.mac_id, action)
        await self._client.send(payload)
        self._start_polling()

    async def move_to(self, percentage: float) -> None:
        """Move to a specific position (0-100)."""
        payload = build_moveto_packet(
            self.mac_id, percentage, self._client.get_next_cnt()
        )
        await self._client.send(payload)
        self._start_polling()

    async def identify(self) -> None:
        """Identify the device (jog)."""
        payload = build_identify_packet(self.mac_id)
        await self._client.send(payload)

    async def request_status(self) -> None:
        """Poll current status/position."""
        payload = build_status_request(self.mac_id, self._client.get_next_cnt())
        await self._client.send(payload)
        self._expect_response()

    async def request_info(self) -> None:
        """Poll device info (SN and FW)."""
        payload = build_info_request(self.mac_id, self._client.get_next_cnt())
        await self._client.send(payload)
        self._expect_response()

    async def get_name(self) -> None:
        """Fetch the device name."""
        payload = build_get_name_packet(self.mac_id)
        await self._client.send(payload)
        self._expect_response()

    async def set_name(self, name: str) -> None:
        """Set a new device name."""
        payload = build_set_name_packet(self.mac_id, name)
        await self._client.send(payload)
        self._expect_response()

    def _mark_available(self) -> None:
        """Stop timeout timer and ensure device is marked available."""
        if self._availability_timer:
            self._availability_timer.cancel()
            self._availability_timer = None
        self.available = True

    def _expect_response(self) -> None:
        """Start a timer waiting for a response. Mark unavailable if it expires."""
        if self._availability_timer:
            self._availability_timer.cancel()
        self._availability_timer = asyncio.get_running_loop().call_later(
            DEVICE_RESPONSE_TIMEOUT, self._handle_timeout
        )

    def _handle_timeout(self) -> None:
        """Handle response timeout."""
        self._availability_timer = None
        if self.available:
            self.available = False
            _LOGGER.debug(
                "Device: %s availability changed to False (Timeout)", self.mac_id
            )
            if self._callback:
                self._callback(self)

        # Start recovery polling if the client has it enabled
        self._start_polling(force=False)

    def _start_polling(self, *, force: bool = True) -> None:
        """Start or ensure the activity polling task is running."""
        if not self._client.enable_polling:
            return

        # If not forcing, don't restart an already running poll
        if not force and self._poll_task and not self._poll_task.done():
            return

        if self._poll_task:
            self._poll_task.cancel()
        self._poll_task = asyncio.create_task(
            self._run_activity_poll(), name=f"pybeckerplus_device_poll_{self.mac_id}"
        )

    async def _run_activity_poll(self) -> None:
        """Poll frequently while moving or unavailable, with backoff."""
        start_time = asyncio.get_running_loop().time()
        interval = 3.5
        timeout = 600
        count = 0
        try:
            while True:
                # 1. Action: Request Status
                await self.request_status()
                count += 1

                # 2. Wait
                await asyncio.sleep(interval)

                # 3. Check Stop Conditions
                # Condition A: Total timeout (10 minutes)
                if (asyncio.get_running_loop().time() - start_time) > timeout:
                    _LOGGER.debug("Polling timeout reached for %s", self.mac_id)
                    break

                # Condition B: Healthy state reached (min 2 polls)
                if not self.moving and self.available and count >= 2:  # noqa: PLR2004
                    break

                # 4. Backoff: increase interval slightly (max 60s)
                interval = min(interval * 1.3, 60.0)

        except asyncio.CancelledError:
            pass
        except Exception:
            _LOGGER.exception("Error in device poll loop for %s", self.mac_id)
        finally:
            if self._poll_task is asyncio.current_task():
                self._poll_task = None

    @property
    def is_ready(self) -> bool:
        """Return True if all initial discovery data has been received."""
        return self._got_status and self._got_info and self._got_name

    def update_from_payload(
        self, status_bytes: bytes, position: float | None, rssi: int | None = None
    ) -> None:
        """Update internal state from raw packet data."""
        self._mark_available()
        self._got_status = True
        if status_bytes and len(status_bytes) >= 2:  # noqa: PLR2004
            b1 = status_bytes[0]
            b2 = status_bytes[1]

            self.moving = bool(b1 & StatusBit.MOVING.value)
            self.upper_limit = bool(b1 & StatusBit.UPPER_LIMIT.value)
            self.lower_limit = bool(b1 & StatusBit.LOWER_LIMIT.value)
            self.blocked = bool(b1 & StatusBit.BLOCKED.value)
            self.overheated = bool(b1 & StatusBit.OVERHEATED.value)
            self.anti_freeze = bool(b2 & StatusBitAux.ANTI_FREEZE.value)
            self.fly_screen = bool(b2 & StatusBitAux.FLY_SCREEN.value)

        if position is not None:
            self.position = round(position, 1)

        if rssi is not None:
            self.rssi = rssi

        _LOGGER.debug("Device: %s updated", self.mac_id)

        if self._callback:
            self._callback(self)

    def update_info(self, sn: str, fw: str) -> None:
        """Update Serial Number and Firmware version."""
        self._mark_available()
        self._got_info = True
        self.serial_number = sn
        self.firmware_version = fw
        _LOGGER.debug("Device: %s updated", self.mac_id)
        if self._callback:
            self._callback(self)

    def update_name(self, name: str) -> None:
        """Update the human-readable name."""
        self._mark_available()
        self._got_name = True
        # Strip null padding if present
        self.name = name.rstrip("\x00")
        _LOGGER.debug("Device: %s new Name: %s", self.mac_id, self.name)
        if self._callback:
            self._callback(self)
