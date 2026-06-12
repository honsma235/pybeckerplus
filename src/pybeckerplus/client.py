"""Client implementation for interacting with the Becker CentronicPlus USB stick."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from asyncio import StreamReader, StreamWriter
from typing import TYPE_CHECKING, Any, Self

import serialx

from pybeckerplus.constants import (
    ACK_TIMEOUT,
    COMMAND_GAP_TIME,
    ETX,
    STICK_ACK,
    STX,
    Action,
)
from pybeckerplus.device import CentronicPlusDevice
from pybeckerplus.exceptions import (
    BeckerConnectionError,
    BeckerError,
    BeckerTimeoutError,
)
from pybeckerplus.packet import (
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
    from types import TracebackType

_LOGGER = logging.getLogger(__name__)


class BeckerClient:
    """Main interface for the Becker CentronicPlus USB stick."""

    def __init__(
        self,
        port: str,
        device_callback: Callable[[CentronicPlusDevice], None] | None = None,
        on_disconnect: Callable[[Exception | None], None] | None = None,
        *,
        enable_polling: bool = False,
    ) -> None:
        """Initialize the BeckerClient with port and callbacks."""
        self.port = port
        self.devices: dict[str, CentronicPlusDevice] = {}
        self._device_callback = device_callback
        self._on_disconnect = on_disconnect
        self.stick_mac: str | None = None
        self.stick_fw: str | None = None
        self.stick_install_id: str | None = None
        self._reader: StreamReader | None = None
        self._writer: StreamWriter | None = None
        self._read_task: asyncio.Task[None] | None = None
        self._monitor_task: asyncio.Task[None] | None = None
        self._cnt = 0
        self._ack_waiter: asyncio.Future[bool] | None = None
        self._stick_info_waiter: asyncio.Future[bool] | None = None
        self._stick_fw_waiter: asyncio.Future[bool] | None = None
        self._lock = asyncio.Lock()
        self._last_send_time = 0.0
        self._connection_error: Exception | None = None
        self.enable_polling = enable_polling
        self._is_closing = False

    async def __aenter__(self) -> Self:
        """Establish serial connection and start background reader."""
        await self.connect()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        """Close the serial connection."""
        await self.close()

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
        # Idempotency guard: return immediately if already closed or closing
        if self._is_closing or (self._writer is None and self._read_task is None):
            return

        self._is_closing = True
        try:
            self._stop_background_tasks()

            # Prevent deadlock if close() is called from within the read loop callback
            if self._read_task and self._read_task != asyncio.current_task():
                self._read_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await self._read_task
            self._read_task = None

            # Ensure all pending command waiters are failed immediately upon closing
            self._fail_waiters(BeckerConnectionError("Client closed"))

            if self._writer:
                self._writer.close()
                try:
                    # wait_closed() often fails if hardware is unplugged
                    await asyncio.wait_for(self._writer.wait_closed(), timeout=1.0)
                except TimeoutError:
                    _LOGGER.debug(
                        "Timed out waiting for writer to close; hardware likely gone"
                    )
                except Exception as exc:  # noqa: BLE001
                    _LOGGER.debug(
                        "Ignored error during writer close (%s): %s",
                        exc.__class__.__name__,
                        exc,
                    )
            self._reader = None
            self._writer = None
        finally:
            self._is_closing = False

    async def initialize(self) -> None:
        """Initialize the stick and fetch its MAC/install ID and firmware info."""
        loop = asyncio.get_running_loop()
        max_attempts = 3

        try:
            for attempt in range(max_attempts):
                self._stick_info_waiter = loop.create_future()
                self._stick_fw_waiter = loop.create_future()

                try:
                    # The device may emit initial empty responses and free-form text
                    # before the first strictly framed packets arrive. Use a light
                    # startup handshake without requiring a strict ACK for every frame.
                    await self.send("", expect_ack=False)
                    await self.send("", expect_ack=False)
                    await self.send("", expect_ack=False)
                    await self.send(build_stick_fw_request())
                    await asyncio.wait_for(self._stick_fw_waiter, timeout=1.5)
                    await self.send(build_stick_info_request())
                    await asyncio.wait_for(self._stick_info_waiter, timeout=1.5)
                except (TimeoutError, BeckerTimeoutError) as exc:
                    if attempt == max_attempts - 1:
                        msg = "Timed out waiting for stick info/firmware response"
                        raise BeckerTimeoutError(msg) from exc

                    _LOGGER.debug(
                        "Stick initialization attempt %s/%s timed out; retrying",
                        attempt + 1,
                        max_attempts,
                    )
                    if self._stick_info_waiter and not self._stick_info_waiter.done():
                        self._stick_info_waiter.cancel()
                    if self._stick_fw_waiter and not self._stick_fw_waiter.done():
                        self._stick_fw_waiter.cancel()
                else:
                    return
            # wait fo stick to get ready in between retries
            await asyncio.sleep(3.0)
        finally:
            self._clear_waiters(self._stick_info_waiter, self._stick_fw_waiter)
            self._stick_info_waiter = None
            self._stick_fw_waiter = None

    def get_next_cnt(self) -> int:
        """Increment and return the next 16-bit command counter."""
        self._cnt = (self._cnt + 1) & 0xFFFF
        return self._cnt

    async def send(self, payload_hex: str, *, expect_ack: bool = True) -> None:
        """Send packet and optionally wait for stick acknowledgment."""
        if self._connection_error:
            msg = "Connection lost"
            raise BeckerConnectionError(msg) from self._connection_error

        if not self._writer:
            msg = "Not connected"
            raise BeckerError(msg)

        async with self._lock:
            if not expect_ack:
                await self._write_packet_logic(payload_hex)
                return

            self._ack_waiter = asyncio.get_running_loop().create_future()
            try:
                await self._write_packet_logic(payload_hex)
                await asyncio.wait_for(self._ack_waiter, timeout=ACK_TIMEOUT)
            except TimeoutError as exc:
                msg = "Stick did not acknowledge command"
                raise BeckerTimeoutError(msg) from exc
            finally:
                self._clear_waiters(self._ack_waiter)
                self._ack_waiter = None

    async def _write_packet_logic(self, payload_hex: str) -> None:
        """Write a framed packet and enforce the command gap."""
        # Simple rate limiting: 100ms gap between commands
        now = asyncio.get_running_loop().time()
        delay = self._last_send_time + COMMAND_GAP_TIME - now
        if delay > 0:
            await asyncio.sleep(delay)

        packet = wrap_packet(payload_hex)
        _LOGGER.debug(" --> USB %s", payload_hex or "(empty)")
        if self._writer:
            self._writer.write(packet)
            await self._writer.drain()
        self._last_send_time = asyncio.get_running_loop().time()

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
                                _LOGGER.debug(" <-- USB %s", packet_hex)
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

            except asyncio.CancelledError:
                break
            except Exception as exc:  # noqa: BLE001
                _LOGGER.debug(
                    "Unexpected serial read loop error (%s): %s",
                    exc.__class__.__name__,
                    exc,
                )
                self._handle_disconnect(exc)
                break

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
                        self.devices[mac_id] = CentronicPlusDevice(
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

                case _:
                    pass

        except Exception:
            _LOGGER.exception("Unexpected error processing packet: %s", packet_hex)

    def _wrapped_callback(self, device: CentronicPlusDevice) -> None:
        """Notify listener only if the device has finished initial discovery."""
        if device.is_ready and self._device_callback:
            self._device_callback(device)

    def _fail_waiters(self, exc: Exception) -> None:
        """Fail all pending waiters with the provided exception."""
        for waiter in [
            self._ack_waiter,
            self._stick_info_waiter,
            self._stick_fw_waiter,
        ]:
            if waiter and not waiter.done():
                waiter.set_exception(exc)

    def _clear_waiters(self, *waiters: asyncio.Future[Any] | None) -> None:
        """Consume exceptions to avoid 'Future exception was never retrieved'."""
        for waiter in waiters:
            if waiter and waiter.done() and not waiter.cancelled():
                with contextlib.suppress(Exception):
                    waiter.exception()

    def _stop_background_tasks(self) -> None:
        """Stop discovery, maintenance, and device-specific polling tasks."""
        self.stop_monitoring()
        for device in self.devices.values():
            if device._poll_task:  # noqa: SLF001
                device._poll_task.cancel()  # noqa: SLF001

    def _handle_disconnect(self, exc: Exception) -> None:
        """Handle cleanup when an unexpected connection error occurs."""
        if self._is_closing:
            return

        self._connection_error = exc
        self._fail_waiters(exc)
        self._stop_background_tasks()

        if self._on_disconnect:
            self._on_disconnect(exc)

    def _trigger_expectation(self, mac_id: str | None) -> None:
        """Inform devices that an immediate response is expected."""
        if mac_id is None:
            for device in self.devices.values():
                device._expect_response()  # noqa: SLF001
        elif device := self.get_device(mac_id):
            device._expect_response()  # noqa: SLF001

    @property
    def connected(self) -> bool:
        """Return True if the client is currently connected to the USB stick."""
        return self._writer is not None and self._connection_error is None

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

    async def start_monitoring(self, *, restart: bool) -> None:
        """Start discovery and the periodic maintenance loop."""
        if restart:
            self.stop_monitoring()
        if self._monitor_task and not self._monitor_task.done():
            return
        self._monitor_task = asyncio.create_task(
            self._run_monitoring(), name="pybeckerplus_monitor_loop"
        )

    def stop_monitoring(self) -> None:
        """Stop discovery and maintenance monitoring."""
        if self._monitor_task:
            self._monitor_task.cancel()
            self._monitor_task = None

    async def _run_monitoring(self) -> None:  # noqa: PLR0915
        """Perform discovery then poll globally with varying intervals."""
        try:

            async def sweep_device(
                device: CentronicPlusDevice, max_attempts: int
            ) -> None:
                """Sweep a single device up to max_attempts times."""
                for attempt in range(max_attempts):
                    if device.is_ready:
                        _LOGGER.debug("Device %s ready", device.mac_id)
                        break

                    _LOGGER.debug(
                        "Sweeping device %s (attempt %s/%s)",
                        device.mac_id,
                        attempt + 1,
                        max_attempts,
                    )

                    if not device._got_name:  # noqa: SLF001
                        await device.get_name()
                        await asyncio.sleep(1.0)
                    if not device._got_info:  # noqa: SLF001
                        await device.request_info()
                        await asyncio.sleep(1.0)
                    if not device._got_status:  # noqa: SLF001
                        await device.request_status()
                        await asyncio.sleep(1.0)

                    if attempt < max_attempts - 1:
                        await asyncio.sleep(5.0)

            async def sweep_all_not_ready() -> None:
                """Sweep all not-ready devices in parallel."""
                tasks = [
                    asyncio.create_task(sweep_device(device, 10))
                    for device in self.devices.values()
                    if not device.is_ready
                ]
                if tasks:
                    await asyncio.gather(*tasks)

            # Phase 1: Initial Global Burst
            await self.send(build_global_name_request())
            await asyncio.sleep(2.5)
            await self.send(build_global_info_request(self.get_next_cnt()))
            await asyncio.sleep(2.5)
            await self.global_request_status()
            await asyncio.sleep(5.0)
            await sweep_all_not_ready()

            # Phase 2: Global status every 5s (3x) with device sweeps
            for _ in range(3):
                await self.global_request_status()
                await asyncio.sleep(5.0)
                await sweep_all_not_ready()

            # Phase 3: Repeat global Burst
            await self.send(build_global_name_request())
            await asyncio.sleep(2.5)
            await self.send(build_global_info_request(self.get_next_cnt()))
            await asyncio.sleep(2.5)
            await self.global_request_status()
            await asyncio.sleep(5.0)

            # Phase 4: Global status every 5s (3x) with device sweeps
            for _ in range(3):
                await self.global_request_status()
                await asyncio.sleep(5.0)
                await sweep_all_not_ready()

            # Phase 5: Long-term maintenance - every 1800s
            while True:
                _LOGGER.debug("Performing 30-minute global maintenance poll")
                await self.global_request_status()
                await asyncio.sleep(5.0)
                await sweep_all_not_ready()
                await asyncio.sleep(1800)

        except asyncio.CancelledError:
            pass
        except Exception as exc:  # noqa: BLE001
            _LOGGER.debug(
                "Unexpected error in global monitoring loop (%s): %s",
                exc.__class__.__name__,
                exc,
            )
            self._handle_disconnect(exc)

    def get_device(self, mac_id: str) -> CentronicPlusDevice | None:
        """Get device object from registry."""
        return self.devices.get(format_mac(mac_id))
