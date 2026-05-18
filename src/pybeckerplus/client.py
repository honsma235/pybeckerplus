"""Client implementation for interacting with the Becker CentronicPlus USB stick."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from asyncio import StreamReader, StreamWriter
from typing import TYPE_CHECKING

import serialx

from .constants import ACK_TIMEOUT, COMMAND_GAP_TIME, ETX, STICK_ACK, STX, Action
from .device import CentronicDevice
from .exceptions import BeckerConnectionError, BeckerError, BeckerTimeoutError
from .packet import (
    build_global_action_packet,
    build_global_info_request,
    build_global_moveto_packet,
    build_global_name_request,
    build_global_status_request,
    build_stick_fw_request,
    build_stick_info_request,
    format_mac,
    parse_packet,
    wrap_packet,
)

if TYPE_CHECKING:
    from collections.abc import Callable

_LOGGER = logging.getLogger(__name__)


class BeckerClient:
    """Main interface for the Becker CentronicPlus USB stick."""

    def __init__(
        self,
        port: str,
        device_callback: Callable[[CentronicDevice], None] | None = None,
        on_disconnect: Callable[[Exception | None], None] | None = None,
    ) -> None:
        """Initialize the BeckerClient with port and callbacks."""
        self.port = port
        self.devices: dict[str, CentronicDevice] = {}
        self._device_callback = device_callback
        self._on_disconnect = on_disconnect
        self.stick_mac: str | None = None
        self.stick_fw: str | None = None
        self.stick_install_id: str | None = None
        self._reader: StreamReader | None = None
        self._writer: StreamWriter | None = None
        self._read_task: asyncio.Task[None] | None = None
        self._cnt = 0
        self._ack_waiter: asyncio.Future[bool] | None = None
        self._stick_info_waiter: asyncio.Future[bool] | None = None
        self._stick_fw_waiter: asyncio.Future[bool] | None = None
        self._lock = asyncio.Lock()
        self._last_send_time = 0.0
        self._connection_error: Exception | None = None

    async def connect(self) -> None:
        """Establish serial connection and start background reader."""
        self._reader, self._writer = await serialx.open_serial_connection(
            url=self.port, baudrate=115200
        )
        self._read_task = asyncio.create_task(
            self._read_loop(), name="pybeckerplus_read_loop"
        )

        self._connection_error = None
        # Initialize with current loop time to enforce the command gap after connecting
        self._last_send_time = asyncio.get_running_loop().time()
        _LOGGER.debug("Connected to Becker USB stick on %s", self.port)

    async def close(self) -> None:
        """Close the serial connection."""
        if self._read_task:
            self._read_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._read_task

        # Ensure all pending command waiters are failed immediately upon closing
        self._handle_disconnect(None)

        if self._writer:
            self._writer.close()
            await self._writer.wait_closed()
        self._reader = None
        self._writer = None

    def get_next_cnt(self) -> int:
        """Increment and return the next 16-bit command counter."""
        self._cnt = (self._cnt + 1) & 0xFFFF
        return self._cnt

    async def send(self, payload_hex: str) -> None:
        """Send packet and wait for stick acknowledgment."""
        if not self._writer:
            if self._connection_error:
                msg = "Connection lost"
                raise BeckerConnectionError(msg) from self._connection_error
            msg = "Not connected"
            raise BeckerError(msg)

        async with self._lock:
            # Simple rate limiting: 100ms gap between commands
            now = asyncio.get_running_loop().time()
            delay = self._last_send_time + COMMAND_GAP_TIME - now
            if delay > 0:
                await asyncio.sleep(delay)

            self._ack_waiter = asyncio.get_running_loop().create_future()
            packet = wrap_packet(payload_hex)

            _LOGGER.debug(" --> USB %s", payload_hex)
            self._writer.write(packet)
            await self._writer.drain()

            try:
                await asyncio.wait_for(self._ack_waiter, timeout=ACK_TIMEOUT)
            except TimeoutError as exc:
                msg = "Stick did not acknowledge command"
                raise BeckerTimeoutError(msg) from exc
            finally:
                self._ack_waiter = None
                self._last_send_time = asyncio.get_running_loop().time()

    def _wrapped_callback(self, device: CentronicDevice) -> None:
        """Notify listener only if the device has finished initial discovery."""
        if device.is_ready and self._device_callback:
            self._device_callback(device)

    async def _read_loop(self) -> None:  # noqa: PLR0912, PLR0915
        """Continuously read from serial, parsing packets and watching for ACKs."""
        buffer = b""
        while True:
            try:
                if self._reader is None:
                    break

                # Read larger chunks to reduce event loop overhead
                data = await self._reader.read(1024)
                if not data:
                    # EOF reached - usually means the device was closed or disconnected
                    _LOGGER.debug("Serial connection closed (EOF)")
                    self._handle_disconnect(
                        BeckerConnectionError("Serial connection closed (EOF)")
                    )
                    break

                buffer += data

                while buffer:
                    ack_pos = buffer.find(STICK_ACK)
                    stx_pos = buffer.find(STX)

                    # 1. Handle Stick Acknowledgments
                    # (high priority, can be interleaved)
                    if ack_pos != -1:
                        if self._ack_waiter and not self._ack_waiter.done():
                            self._ack_waiter.set_result(True)
                        # Remove the ACK byte. We keep data before it in case it's
                        # embedded in a frame. Leading junk is handled by
                        # frame processing or the final buffer prune.
                        buffer = buffer[:ack_pos] + buffer[ack_pos + len(STICK_ACK) :]
                        continue

                    # 2. Handle Framed Packets (\x02 ... \x03)
                    if stx_pos != -1:
                        # Discard leading junk before the STX
                        if stx_pos > 0:
                            buffer = buffer[stx_pos:]
                            continue

                        etx_pos = buffer.find(ETX, stx_pos)
                        if etx_pos != -1:
                            # Resync: if there's a later STX before this ETX, skip to it
                            last_stx = buffer.rfind(STX, stx_pos, etx_pos)
                            if last_stx > stx_pos:
                                buffer = buffer[last_stx:]
                                continue

                            try:
                                packet_hex = buffer[stx_pos + 1 : etx_pos].decode(
                                    "ascii"
                                )
                                _LOGGER.debug(" <-- USB : %s", packet_hex)
                                self._handle_packet(packet_hex)
                            except (UnicodeDecodeError, ValueError):
                                _LOGGER.debug(
                                    "Received invalid data in framed packet; discarding"
                                )
                            except Exception:
                                _LOGGER.exception("Error processing serial packet")
                            buffer = buffer[etx_pos + 1 :]
                            continue

                        # STX but no ETX yet. Guard against orphaned STX: discard if
                        # buffer is excessively long or if another STX appears later.
                        if len(buffer) > 512 or buffer.find(STX, 1) != -1:  # noqa: PLR2004
                            _LOGGER.debug("Discarding orphaned or stale STX marker")
                            buffer = buffer[1:]
                            continue

                        break

                    # 3. No full ACK or packet found. Keep trailing bytes that could
                    # be the start of a STICK_ACK (\r\n\r\n)
                    keep_idx = len(buffer)
                    for i in range(len(STICK_ACK) - 1, 0, -1):
                        if buffer.endswith(STICK_ACK[:i]):
                            keep_idx = len(buffer) - i
                            break

                    buffer = buffer[keep_idx:]
                    break

            except Exception as exc:
                _LOGGER.exception("Fatal error in serial read loop")
                self._handle_disconnect(exc)
                break

    def _handle_disconnect(self, exc: Exception | None) -> None:
        """Handle cleanup when the connection is lost."""
        self._connection_error = exc
        # Fail any pending waiters immediately so they don't time out
        for waiter in [
            self._ack_waiter,
            self._stick_info_waiter,
            self._stick_fw_waiter,
        ]:
            if waiter and not waiter.done():
                waiter.set_exception(exc or BeckerConnectionError("Disconnected"))

        if self._on_disconnect:
            self._on_disconnect(exc)

    def _handle_packet(self, packet_hex: str) -> None:
        """Route parsed data to device objects."""
        if not (data := parse_packet(packet_hex)):
            return

        try:
            match data.get("type"):
                case "stick_info":
                    self.stick_mac = data["mac_id"]
                    self.stick_install_id = data["install_id"]
                    _LOGGER.debug(
                        "Stick MAC: %s, Install ID: %s",
                        self.stick_mac,
                        self.stick_install_id,
                    )
                    if self._stick_info_waiter and not self._stick_info_waiter.done():
                        self._stick_info_waiter.set_result(True)

                case "stick_fw":
                    self.stick_fw = data["fw"]
                    _LOGGER.debug("Stick Firmware: %s", self.stick_fw)
                    if self._stick_fw_waiter and not self._stick_fw_waiter.done():
                        self._stick_fw_waiter.set_result(True)

                case "device":
                    mac_id = data["mac_id"]
                    if mac_id not in self.devices:
                        self.devices[mac_id] = CentronicDevice(
                            mac_id, self, self._wrapped_callback
                        )
                    device = self.devices[mac_id]

                    # Update the specific attributes provided in this packet
                    if "status" in data:
                        device.update_from_payload(
                            data["status"], data.get("pos"), data.get("rssi")
                        )
                    if "sn" in data:
                        device.update_info(data["sn"], data["fw"])
                    if "name" in data:
                        device.update_name(data["name"])

        except Exception:
            _LOGGER.exception("Unexpected error processing packet: %s", packet_hex)

    def _trigger_expectation(self, mac_id: str | None) -> None:
        """Inform devices that an immediate response is expected."""
        if mac_id is None:
            for device in self.devices.values():
                device.expect_response()
        elif device := self.get_device(mac_id):
            device.expect_response()

    @property
    def connected(self) -> bool:
        """Return True if the client is currently connected to the USB stick."""
        return self._writer is not None

    @property
    def all_devices_ready(self) -> bool:
        """Return True if all discovered devices have finished initial discovery."""
        if not self.devices:
            return False
        return all(device.is_ready for device in self.devices.values())

    async def global_action(self, action: Action) -> None:
        """Send a global action command to all devices."""
        payload = build_global_action_packet(action, self.get_next_cnt())
        await self.send(payload)

    async def global_move_to(self, percentage: float) -> None:
        """Move all devices to a specific position."""
        payload = build_global_moveto_packet(percentage, self.get_next_cnt())
        await self.send(payload)

    async def global_request_status(self) -> None:
        """Manually poll status for all devices."""
        payload = build_global_status_request(self.get_next_cnt())
        await self.send(payload)
        self._trigger_expectation(None)

    async def global_get_device_names(self) -> None:
        """Request names for all devices."""
        payload = build_global_name_request()
        await self.send(payload)
        self._trigger_expectation(None)

    async def update_stick_info(self) -> None:
        """Fetch and wait for stick MAC and Firmware info."""
        loop = asyncio.get_running_loop()
        self._stick_info_waiter = loop.create_future()
        self._stick_fw_waiter = loop.create_future()

        try:
            # Send requests sequentially; each waits for a serial ACK
            await self.send(build_stick_fw_request())
            await self.send(build_stick_info_request())

            # Wait for the actual data packets to arrive from the read loop
            await asyncio.wait_for(
                asyncio.gather(self._stick_info_waiter, self._stick_fw_waiter),
                timeout=2.0,
            )
        except TimeoutError as e:
            msg = "Timed out waiting for stick info/firmware response"
            raise BeckerTimeoutError(msg) from e
        finally:
            self._stick_info_waiter = None
            self._stick_fw_waiter = None

    async def start_discovery(self) -> None:
        """Send global requests to find all devices and their states."""
        await self.update_stick_info()
        # Send discovery commands sequentially
        await self.send(build_global_name_request())
        await asyncio.sleep(2.5)
        await self.send(build_global_info_request(self.get_next_cnt()))
        await asyncio.sleep(2.5)
        await self.send(build_global_status_request(self.get_next_cnt()))

    def get_device(self, mac_id: str) -> CentronicDevice | None:
        """Get device object from registry."""
        return self.devices.get(format_mac(mac_id))
