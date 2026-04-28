from typing import Callable, Optional
from .constants import StatusBit, StatusBitAux

class CentronicDevice:
    """Representation of a Becker CentronicPlus Motor."""
    
    def __init__(self, mac_id: str, callback: Optional[Callable] = None):
        self.mac_id = mac_id
        self.position: float = 0.0
        self.moving: bool = False
        self.upper_limit: bool = False
        self.lower_limit: bool = False
        self.blocked: bool = False
        self.overheated: bool = False
        self.fly_screen: bool = False
        self.rssi: Optional[int] = None
        self.serial_number: Optional[str] = None
        self.firmware_version: Optional[str] = None
        self.name: Optional[str] = None
        
        self._callback = callback

    def update_from_payload(self, status_bytes: bytes, position: float, rssi: Optional[int] = None):
        """Update internal state from raw packet data."""
        if status_bytes and len(status_bytes) >= 2:
            b1 = status_bytes[0]
            b2 = status_bytes[1]
            
            self.moving = bool(b1 & StatusBit.MOVING.value)
            self.upper_limit = bool(b1 & StatusBit.UPPER_LIMIT.value)
            self.lower_limit = bool(b1 & StatusBit.LOWER_LIMIT.value)
            self.blocked = bool(b1 & StatusBit.BLOCKED.value)
            self.overheated = bool(b1 & StatusBit.OVERHEATED.value)
            
            self.fly_screen = bool(b2 & StatusBitAux.FLY_SCREEN.value)
        
        if position is not None:
            self.position = round(position, 1)

        if rssi is not None:
            self.rssi = rssi

        if self._callback:
            self._callback(self)

    def update_info(self, sn: str, fw: str):
        """Update Serial Number and Firmware version."""
        self.serial_number = sn
        self.firmware_version = fw
        if self._callback:
            self._callback(self)

    def update_name(self, name: str):
        """Update the human-readable name."""
        # Strip null padding if present
        self.name = name.rstrip('\x00')
        if self._callback:
            self._callback(self)

    def __repr__(self):
        return f"<CentronicDevice {self.mac_id} pos={self.position}% moving={self.moving}>"
