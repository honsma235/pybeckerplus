import re
import struct
import logging
from .constants import Action, STX, ETX
from .exceptions import BeckerParseError

_LOGGER = logging.getLogger(__name__)


def hex_to_bytes(hex_str: str) -> bytes:
    try:
        return bytes.fromhex(hex_str)
    except ValueError as e:
        raise BeckerParseError(f"Invalid hexadecimal string: {hex_str}") from e

def bytes_to_hex(data: bytes) -> str:
    return data.hex().upper()

def wrap_packet(payload_hex: str) -> bytes:
    """Wrap hex string in STX/ETX envelope."""
    return STX + payload_hex.encode("ascii").upper() + ETX

def format_mac(mac: str) -> str:
    """Ensure MAC is 16 hex chars."""
    clean = mac.replace(":", "").replace("-", "").lower()
    if len(clean) != 16:
        raise ValueError(f"Invalid MAC ID length: {mac}")
    return clean

def format_pos(percentage: float) -> str:
    """Convert 0-100 float to 0000-FFFF little-endian hex string."""
    val = int(max(0, min(100, percentage)) * 655.35)
    # Little endian 16-bit unsigned
    return bytes_to_hex(struct.pack("<H", val))

def format_cnt(cnt: int) -> str:
    """Convert integer to 4-char hex string (2 bytes)."""
    return bytes_to_hex(struct.pack(">H", cnt & 0xFFFF))

def build_action_packet(mac: str, action: Action) -> str:
    """Section 2.1: Action Commands (Direct)."""
    mac = format_mac(mac)
    # 07010118 + MAC(16) + 01013400000000000000 + CMD(2) + 0000000501
    return f"07010118{mac}01013400000000000000{action.value}0000000501"

def build_global_action_packet(action: Action, cnt: int) -> str:
    """Section 2.1: Action Commands (Global)."""
    cnt_hex = format_cnt(cnt)
    # 0709011a + 0000000000000000 + 01013400000000002000 + CMD(2) + 000000 + CNT(4) + 0501
    return f"0709011A000000000000000001013400000000002000{action.value}000000{cnt_hex}0501"

def build_moveto_packet(mac: str, percentage: float, cnt: int) -> str:
    """Section 2.2: MoveTo Command (Direct)."""
    mac = format_mac(mac)
    pos_hex = format_pos(percentage)
    cnt_hex = format_cnt(cnt)
    # 0701011a + MAC(16) + 010134000000005340000000 + POS(4) + CNT(4) + 0501
    return f"0701011A{mac}010134000000005340000000{pos_hex}{cnt_hex}0501"

def build_global_moveto_packet(percentage: float, cnt: int) -> str:
    """Section 2.2: MoveTo Command (Global)."""
    pos_hex = format_pos(percentage)
    cnt_hex = format_cnt(cnt)
    # 0709011a + 0000000000000000 + 010134000000005340000000 + POS(4) + CNT(4) + 0501
    return f"0709011A0000000000000000010134000000005340000000{pos_hex}{cnt_hex}0501"

def build_status_request(mac: str, cnt: int) -> str:
    """Section 3.1: Status & Position Request (Direct)."""
    mac = format_mac(mac)
    cnt_hex = format_cnt(cnt)
    return f"0701011A{mac}0101340000000080A00000000000{cnt_hex}0501"

def build_global_status_request(cnt: int) -> str:
    """Section 3.1: Global Status & Position Request."""
    cnt_hex = format_cnt(cnt)
    return f"0709011A00000000000000000101340000000080A00000000000{cnt_hex}0501"

def build_global_info_request(cnt: int) -> str:
    """Section 3.2: Global SN & FW Request."""
    cnt_hex = format_cnt(cnt)
    return f"07090119000000000000000001013400000000510000000000{cnt_hex}0501"

def build_global_name_request() -> str:
    """Section 3.4.1: Global Name Request."""
    return f"0709013000000000000000008001340000000060{'0'*72}"

def build_get_name_packet(mac: str) -> str:
    """Section 3.4.1: Direct Name Request."""
    mac = format_mac(mac)
    return f"07010130{mac}8001340000000060{'0'*72}"

def build_set_name_packet(mac: str, name: str) -> str:
    """Section 3.4.2: Set Device Name (UTF-8, hex encoded, 32-byte padded)."""
    mac = format_mac(mac)
    name_bytes = name.encode("utf-8")
    if len(name_bytes) > 32:
        _LOGGER.warning(
            "Device name '%s' is too long (%d bytes after UTF-8 encoding) and will be truncated to 32 bytes.",
            name, len(name_bytes)
        )
        name_bytes = name_bytes[:32]
    # Pad to 32 bytes (64 hex chars)
    name_hex = name_bytes.hex().upper().ljust(64, '0')
    return f"07010130{mac}8001340000000061{name_hex}"


# Strict Protocol Patterns (Hex String Format)
PATTERNS = {
    "status": re.compile(r"^0700011A(?P<mac>.{16}).{14}80.{4}(?P<status>.{4})(?P<pos>.{4})(?P<cnt>.{4}).{4}$", re.IGNORECASE),
    "unsolicited": re.compile(r"^07000126(?P<mac>.{16}).{14}52.{4}(?P<status>.{4})(?P<pos>.{4}).{32}$", re.IGNORECASE),
    "info": re.compile(r"^0700012B(?P<mac>.{16}).{14}51.{18}(?P<sn>.{10}).{2}(?P<fw>.{6}).{10}(?P<cnt>.{4}).{4}$", re.IGNORECASE),
    "name": re.compile(r"^07000130(?P<mac>.{16}).{14}62(?P<name>.{64})$", re.IGNORECASE),
    "stick_info": re.compile(r"^07270111(?P<mac>.{16})(?P<install>.{8}).{10}$", re.IGNORECASE),
    "stick_fw": re.compile(r"^072E010C.{16}(?P<fw>.{6}).{2}$", re.IGNORECASE),}

def parse_packet(raw_hex: str):
    """
    Parses incoming hex packets and returns a dictionary of extracted data.
    Strictly enforces protocol format via Regex.
    """
    for ptype, pattern in PATTERNS.items():
        match = pattern.match(raw_hex)
        if not match:
            continue

        # Found a matching strictly enforced pattern
        if ptype in ["status", "unsolicited"]:
            pos_raw = struct.unpack("<H", hex_to_bytes(match.group("pos")))[0]
            return {
                "type": "device",
                "mac_id": match.group("mac").lower(),
                "status": hex_to_bytes(match.group("status")),
                "pos": (pos_raw / 65535.0) * 100.0
            }

        if ptype == "info":
            fw_bytes = hex_to_bytes(match.group("fw"))
            return {
                "type": "device",
                "mac_id": match.group("mac").lower(),
                "sn": match.group("sn"),
                "fw": ".".join([f"{b:02}" for b in fw_bytes])
            }

        if ptype == "name":
            name_bytes = hex_to_bytes(match.group("name"))
            return {
                "type": "device",
                "mac_id": match.group("mac").lower(),
                "name": name_bytes.decode("utf-8").rstrip("\x00")
            }

        if ptype == "stick_info":
            return {
                "type": "stick_info",
                "mac_id": match.group("mac").lower(),
                "install_id": match.group("install").lower()
            }

        if ptype == "stick_fw":
            fw_bytes = hex_to_bytes(match.group("fw"))
            return {
                "type": "stick_fw",
                "fw": ".".join([f"{b:02}" for b in fw_bytes])
            }
    
    _LOGGER.warning("Unknown packet structure or unparsable: %s", raw_hex)
    return None