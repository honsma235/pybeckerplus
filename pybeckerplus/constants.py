from enum import Enum

class Action(Enum):
    """Action commands for CentronicPlus devices."""
    STOP = "10"
    UP = "20"
    DOWN = "40"
    HALT = "10"  # Alias
    PRESET_1 = "24"
    PRESET_2 = "44"
    SET_PRESET_1 = "31"
    SET_PRESET_2 = "51"
    DELETE_PRESETS = "17"
    TOGGLE_FLY_SCREEN = "D1"

class PairingAction(Enum):
    """Pairing/Teach-in commands."""
    ACTIVATE_CENTRONIC_PLUS = "9A"
    DEACTIVATE_CENTRONIC_PLUS = "9B"
    ACTIVATE_CENTRONIC_MASTER = "96"
    ACTIVATE_CENTRONIC = "97"
    ACTIVATE_CENTRONIC_TEACH_OUT = "98"
    DEACTIVATE_CENTRONIC = "99"
    DELETE_ALL_PAIRINGS = "9D"

class StatusBit(Enum):
    """Status bits from the first status byte."""
    MOVING = 0x02
    UPPER_LIMIT = 0x04
    LOWER_LIMIT = 0x08
    OVERHEATED = 0x40
    BLOCKED = 0x80

class StatusBitAux(Enum):
    """Status bits from the second status byte."""
    FLY_SCREEN = 0x20

STX = b"\x02"
ETX = b"\x03"
STICK_ACK = b"\r\n\r\n"