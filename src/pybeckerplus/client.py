import asyncio
import logging
from typing import Dict, Optional, Callable
import serialx

from .constants import Action, STX, ETX, STICK_ACK, COMMAND_GAP_TIME
from .packet import *
from .device import CentronicDevice
from .exceptions import BeckerTimeoutError, BeckerError, BeckerConnectionError

_LOGGER = logging.getLogger(__name__)


class BeckerClient:
    """Main interface for the Becker CentronicPlus USB stick."""

    def __init__(
        self,
        port: str,
        device_callback: Optional[Callable] = None,
        on_disconnect: Optional[Callable[[Optional[Exception]], None]] = None,
    ):
        self.port = port
        self.devices: Dict[str, CentronicDevice] = {}
        self._device_callback = device_callback
        self._on_disconnect = on_disconnect
        self.stick_mac: Optional[str] = None
        self.stick_fw: Optional[str] = None
        self.stick_install_id: Optional[str] = None
        self._reader: Optional[serialx.SerialReader] = None
        self._writer: Optional[serialx.SerialWriter] = None
        self._read_task: Optional[asyncio.Task] = None
        self._cnt = 0
        self._ack_waiter: Optional[asyncio.Future] = None
        self._stick_info_waiter: Optional[asyncio.Future] = None
        self._stick_fw_waiter: Optional[asyncio.Future] = None
        self._lock = asyncio.Lock()
        self._last_send_time = 0.0
        self._connection_error: Optional[Exception] = None

    async def connect(self):
        """Establish serial connection and start background reader."""
        self._reader, self._writer = await serialx.open_serial_connection(
            url=self.port, baudrate=115200
        )
        self._read_task = asyncio.create_task(
            self._read_loop(), name=f"pybeckerplus_read_loop"
        )

        self._connection_error = None
        # Initialize with current loop time to enforce the command gap after connecting
        self._last_send_time = asyncio.get_running_loop().time()
        _LOGGER.debug("Connected to Becker USB stick on %s", self.port)

    async def close(self):
        """Close the serial connection."""
        if self._read_task:
            self._read_task.cancel()
            try:
                await self._read_task
            except asyncio.CancelledError:
                pass
        if self._writer:
            self._writer.close()
            await self._writer.wait_closed()
        self._reader = None
        self._writer = None

    def _get_next_cnt(self) -> int:
        self._cnt = (self._cnt + 1) & 0xFFFF
        return self._cnt

    def _wrapped_callback(self, device: CentronicDevice):
        """Notify listener only if the device has finished initial discovery."""
        if device.is_ready and self._device_callback:
            self._device_callback(device)

    async def _read_loop(self):
        """Continuously read from serial, parsing packets and watching for ACKs."""
        buffer = b""
        while True:
            try:
                # Read larger chunks to reduce event loop overhead
                data = await self._reader.read(1024)
                if not data:
                    # EOF reached - usually means the device was closed or disconnected
                    raise BeckerConnectionError("Serial connection closed (EOF)")

                buffer += data

                while buffer:
                    ack_pos = buffer.find(STICK_ACK)
                    stx_pos = buffer.find(STX)

                    # 1. Handle Stick Acknowledgments appearing before or without packets
                    if ack_pos != -1 and (stx_pos == -1 or ack_pos < stx_pos):
                        if self._ack_waiter and not self._ack_waiter.done():
                            self._ack_waiter.set_result(True)
                        buffer = buffer[ack_pos + len(STICK_ACK) :]
                        continue

                    # 2. Handle Framed Packets (\x02 ... \x03)
                    if stx_pos != -1:
                        etx_pos = buffer.find(ETX, stx_pos)
                        if etx_pos != -1:
                            try:
                                packet_hex = buffer[stx_pos + 1 : etx_pos].decode(
                                    "ascii"
                                )
                                _LOGGER.debug(" <-- USB : %s", packet_hex)
                                self._handle_packet(packet_hex)
                            except UnicodeDecodeError, ValueError:
                                _LOGGER.debug(
                                    "Received invalid data in framed packet; discarding"
                                )
                            except Exception:
                                _LOGGER.exception("Error processing serial packet")
                            buffer = buffer[etx_pos + 1 :]
                            continue
                        else:
                            # Found STX but no ETX yet.
                            if stx_pos > 0:
                                # Discard leading junk and re-process immediately
                                buffer = buffer[stx_pos:]
                                continue

                            # Guard against orphaned STX: discard if buffer is excessively long
                            # or if another STX appears later in the buffer (resync).
                            if len(buffer) > 512 or buffer.find(STX, 1) != -1:
                                _LOGGER.debug("Discarding orphaned or stale STX marker")
                                buffer = buffer[1:]
                                continue

                            buffer = buffer[stx_pos:]
                            break

                    # 3. No full ACK or packet found.
                    # Keep trailing bytes that could be the start of a STICK_ACK (\r\n\r\n)
                    keep_idx = len(buffer)
                    for i in range(len(STICK_ACK) - 1, 0, -1):
                        if buffer.endswith(STICK_ACK[:i]):
                            keep_idx = len(buffer) - i
                            break

                    buffer = buffer[keep_idx:]
                    break

            except Exception as exc:
                if isinstance(exc, asyncio.CancelledError):
                    break
                _LOGGER.error("Fatal error in serial read loop: %s", exc)
                self._handle_disconnect(exc)
                break

    def _handle_disconnect(self, exc: Optional[Exception]):
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

    def _handle_packet(self, packet_hex: str):
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
                        self.stick_fw,
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

    async def _send(self, payload_hex: str, timeout: float = 1.0):
        """Send packet and wait for stick acknowledgment."""
        if not self._writer:
            if self._connection_error:
                raise BeckerConnectionError(
                    "Connection lost"
                ) from self._connection_error
            raise BeckerError("Not connected")

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
                await asyncio.wait_for(self._ack_waiter, timeout=timeout)
            except asyncio.TimeoutError:
                raise BeckerTimeoutError("Stick did not acknowledge command")
            finally:
                self._ack_waiter = None
                self._last_send_time = asyncio.get_running_loop().time()

    def _trigger_expectation(self, mac_id: Optional[str]):
        """Inform devices that an immediate response is expected."""
        if mac_id is None:
            for device in self.devices.values():
                device.expect_response()
        else:
            if device := self.get_device(mac_id):
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

    async def global_action(self, action: Action):
        """Send a global action command to all devices."""
        payload = build_global_action_packet(action, self._get_next_cnt())
        await self._send(payload)

    async def global_move_to(self, percentage: float):
        """Move all devices to a specific position."""
        payload = build_global_moveto_packet(percentage, self._get_next_cnt())
        await self._send(payload)

    async def global_request_status(self):
        """Manually poll status for all devices."""
        payload = build_global_status_request(self._get_next_cnt())
        await self._send(payload)
        self._trigger_expectation(None)

    async def global_get_device_names(self):
        """Request names for all devices."""
        payload = build_global_name_request()
        await self._send(payload)
        self._trigger_expectation(None)

    async def update_stick_info(self):
        """Fetch and wait for stick MAC and Firmware info."""
        loop = asyncio.get_running_loop()
        self._stick_info_waiter = loop.create_future()
        self._stick_fw_waiter = loop.create_future()

        try:
            # Send requests sequentially; each waits for a serial ACK
            await self._send(build_stick_fw_request())
            await self._send(build_stick_info_request())

            # Wait for the actual data packets to arrive from the read loop
            await asyncio.wait_for(
                asyncio.gather(self._stick_info_waiter, self._stick_fw_waiter),
                timeout=2.0,
            )
        except asyncio.TimeoutError as e:
            raise BeckerTimeoutError(
                "Timed out waiting for stick info/firmware response"
            ) from e
        finally:
            self._stick_info_waiter = None
            self._stick_fw_waiter = None

    async def start_discovery(self):
        """
        Send global requests to find all devices and their states.
        This will populate self.devices via the read loop.
        """
        await self.update_stick_info()
        # Send discovery commands sequentially
        await self._send(build_global_name_request())
        await asyncio.sleep(2.5)
        await self._send(build_global_info_request(self._get_next_cnt()))
        await asyncio.sleep(2.5)
        await self._send(build_global_status_request(self._get_next_cnt()))

    def get_device(self, mac_id: str) -> Optional[CentronicDevice]:
        """Get device object from registry."""
        return self.devices.get(format_mac(mac_id))
