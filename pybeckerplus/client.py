import asyncio
import logging
import serial_asyncio
from typing import Dict, Optional, Callable

from .constants import Action, STX, ETX, STICK_ACK
from .packet import *
from .device import CentronicDevice
from .exceptions import BeckerTimeoutError, BeckerError

_LOGGER = logging.getLogger(__name__)

class BeckerClient:
    """Main interface for the Becker CentronicPlus USB stick."""

    def __init__(self, port: str, device_callback: Optional[Callable] = None):
        self.port = port
        self.devices: Dict[str, CentronicDevice] = {}
        self._device_callback = device_callback
        self.stick_mac: Optional[str] = None
        self.stick_fw: Optional[str] = None
        self.stick_install_id: Optional[str] = None
        self._reader: Optional[asyncio.StreamReader] = None
        self._writer: Optional[asyncio.StreamWriter] = None
        self._read_task: Optional[asyncio.Task] = None
        self._cnt = 0
        self._ack_waiter: Optional[asyncio.Future] = None
        self._suppress_callbacks = False

    async def connect(self):
        """Establish serial connection and start background reader."""
        self._reader, self._writer = await serial_asyncio.open_serial_connection(
            url=self.port, baudrate=115200
        )
        self._read_task = asyncio.create_task(self._read_loop())
        _LOGGER.info("Connected to Becker USB stick on %s", self.port)

    async def close(self):
        """Close the serial connection."""
        if self._read_task:
            self._read_task.cancel()
        if self._writer:
            self._writer.close()
            await self._writer.wait_closed()

    def _get_next_cnt(self) -> int:
        self._cnt = (self._cnt + 1) & 0xFFFF
        return self._cnt

    def _wrapped_callback(self, device: CentronicDevice):
        """Wrapper to suppress callbacks during discovery."""
        if not self._suppress_callbacks and self._device_callback:
            self._device_callback(device)

    async def _read_loop(self):
        """Continuously read from serial, parsing packets and watching for ACKs."""
        buffer = b""
        while True:
            try:
                data = await self._reader.read(1)
                if not data:
                    continue
                
                buffer += data

                # Check for Stick Acknowledgment
                if STICK_ACK in buffer:
                    if self._ack_waiter and not self._ack_waiter.done():
                        self._ack_waiter.set_result(True)
                    buffer = buffer.replace(STICK_ACK, b"")

                # Check for STX/ETX Framed Packets
                if STX in buffer and ETX in buffer:
                    start = buffer.find(STX)
                    end = buffer.find(ETX, start)
                    if end > start:
                        packet_hex = buffer[start+1 : end].decode("ascii")
                        _LOGGER.debug(" <-- USB : %s", packet_hex)
                        self._handle_packet(packet_hex)
                        buffer = buffer[end+1:]
                
                # Prevent buffer bloat
                if len(buffer) > 1024:
                    buffer = buffer[-512:]

            except asyncio.CancelledError:
                break
            except Exception as e:
                _LOGGER.error("Error in read loop: %s", e)

    def _handle_packet(self, packet_hex: str):
        """Route parsed data to device objects."""
        if not (data := parse_packet(packet_hex)):
            return

        try:
            match data.get("type"):
                case "stick_info":
                    self.stick_mac = data["mac_id"]
                    self.stick_install_id = data["install_id"]

                case "stick_fw":
                    self.stick_fw = data["fw"]
                    _LOGGER.info("Stick Firmware: %s", self.stick_fw)

                case "device":
                    mac_id = data["mac_id"]
                    # setdefault ensures the device exists without an explicit check-and-insert
                    device = self.devices.setdefault(
                        mac_id, CentronicDevice(mac_id, self._wrapped_callback)
                    )

                    # Update the specific attributes provided in this packet
                    if "status" in data:
                        device.update_from_payload(data["status"], data.get("pos"), data.get("rssi"))
                    if "sn" in data:
                        device.update_info(data["sn"], data["fw"])
                    if "name" in data:
                        device.update_name(data["name"])

        except Exception:
            _LOGGER.exception("Unexpected error processing packet: %s", packet_hex)

    async def _send(self, payload_hex: str, timeout: float = 1.0):
        """Send packet and wait for stick acknowledgment."""
        if not self._writer:
            raise BeckerError("Not connected")

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

    async def action(self, mac_id: Optional[str], action: Action):
        """Send a simple action. Pass mac_id=None for a global command."""
        if mac_id is None:
            payload = build_global_action_packet(action, self._get_next_cnt())
        else:
            payload = build_action_packet(mac_id, action)
        await self._send(payload)

    async def move_to(self, mac_id: Optional[str], percentage: float):
        """Move device to specific position. Pass mac_id=None for a global command."""
        if mac_id is None:
            payload = build_global_moveto_packet(percentage, self._get_next_cnt())
        else:
            payload = build_moveto_packet(mac_id, percentage, self._get_next_cnt())
        await self._send(payload)

    async def request_status(self, mac_id: Optional[str] = None):
        """Manually poll status. Pass mac_id=None for a global request."""
        if mac_id is None:
            payload = build_global_status_request(self._get_next_cnt())
        else:
            payload = build_status_request(mac_id, self._get_next_cnt())
        await self._send(payload)

    async def get_device_name(self, mac_id: Optional[str] = None):
        """Request the name of a device. Pass mac_id=None for a global request."""
        if mac_id is None:
            payload = build_global_name_request()
        else:
            payload = build_get_name_packet(mac_id)
        await self._send(payload)

    async def set_device_name(self, mac_id: str, name: str):
        """Set the human-readable name for a device."""
        payload = build_set_name_packet(mac_id, name)
        await self._send(payload)

    async def start_discovery(self):
        """
        Send global requests to find all devices and their states.
        This will populate self.devices via the read loop.
        """
        self._suppress_callbacks = True
        try:
            await self._send(build_stick_fw_request())
            await asyncio.sleep(0.2)
            await self._send(build_stick_info_request())
            await asyncio.sleep(0.2)
            await self._send(build_global_name_request())
            await asyncio.sleep(2.5)
            await self._send(build_global_info_request(self._get_next_cnt()))
            await asyncio.sleep(2.5)
            # Last query round: re-enable callbacks so we notify about the final state
            self._suppress_callbacks = False
            await self._send(build_global_status_request(self._get_next_cnt()))
            await asyncio.sleep(2.5)
        finally:
            self._suppress_callbacks = False

    def get_device(self, mac_id: str) -> Optional[CentronicDevice]:
        """Get device object from registry."""
        return self.devices.get(format_mac(mac_id))