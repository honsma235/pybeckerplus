import asyncio
import logging
import serial_asyncio
from typing import Dict, Optional, Callable

from .constants import Action, STX, ETX, STICK_ACK
from .packet import (
    wrap_packet, format_mac, build_action_packet, build_moveto_packet,
    build_status_request, build_global_status_request,
    build_global_info_request, build_global_name_request,
    build_get_name_packet, build_set_name_packet, parse_packet
)
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
        self._reader: Optional[asyncio.StreamReader] = None
        self._writer: Optional[asyncio.StreamWriter] = None
        self._read_task: Optional[asyncio.Task] = None
        self._cnt = 0
        self._ack_waiter: Optional[asyncio.Future] = None

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
        try:
            data = parse_packet(packet_hex)
            
            # Handle stick-level information
            if data["type"] == "stick_info":
                self.stick_mac = data["mac_id"]
                return
            if data["type"] == "stick_fw":
                self.stick_fw = data["fw"]
                _LOGGER.info("Stick Firmware: %s", self.stick_fw)
                return

            mac_id = data["mac_id"]
            if mac_id not in self.devices:
                self.devices[mac_id] = CentronicDevice(mac_id, self._device_callback)
            
            device = self.devices[mac_id]
            
            if "status" in data:
                device.update_from_payload(data["status"], data.get("pos"))
            if "sn" in data:
                device.update_info(data["sn"], data["fw"])
            if "name" in data:
                device.update_name(data["name"])
                
        except Exception as e:
            _LOGGER.warning("Failed to handle packet %s: %s", packet_hex, e)

    async def _send(self, payload_hex: str, timeout: float = 1.0):
        """Send packet and wait for stick acknowledgment."""
        if not self._writer:
            raise BeckerError("Not connected")

        self._ack_waiter = asyncio.get_running_loop().create_future()
        packet = wrap_packet(payload_hex)
        
        self._writer.write(packet)
        await self._writer.drain()

        try:
            await asyncio.wait_for(self._ack_waiter, timeout=timeout)
        except asyncio.TimeoutError:
            raise BeckerTimeoutError("Stick did not acknowledge command")
        finally:
            self._ack_waiter = None

    async def action(self, mac_id: str, action: Action):
        """Send a simple action (UP, DOWN, STOP, etc)."""
        payload = build_action_packet(mac_id, action)
        await self._send(payload)

    async def move_to(self, mac_id: str, percentage: float):
        """Move device to specific position (0-100)."""
        payload = build_moveto_packet(mac_id, percentage, self._get_next_cnt())
        await self._send(payload)

    async def request_status(self, mac_id: str):
        """Manually poll a device for its status."""
        payload = build_status_request(mac_id, self._get_next_cnt())
        await self._send(payload)

    async def get_device_name(self, mac_id: str):
        """Request the name of a specific device."""
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
        await self._send(build_global_status_request(self._get_next_cnt()))
        await asyncio.sleep(3)
        await self._send(build_global_info_request(self._get_next_cnt()))
        await asyncio.sleep(3)
        await self._send(build_global_name_request())

    def get_device(self, mac_id: str) -> Optional[CentronicDevice]:
        """Get device object from registry."""
        return self.devices.get(format_mac(mac_id))