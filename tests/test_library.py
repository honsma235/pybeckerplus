import pytest
from pybeckerplus.packet import parse_packet, build_action_packet, build_set_name_packet, build_moveto_packet
from pybeckerplus.constants import Action, StatusBit
from pybeckerplus.device import CentronicDevice
from pybeckerplus.exceptions import BeckerParseError

def test_parse_status_response():
    """Test parsing a standard status response (ID 0x80)."""
    # MAC: a0dc04fffe123456, Status: 1400 (Stopped + Lower Limit), Pos: 0000 (0%), Cnt: 0001
    raw_hex = "0700011AA0DC04FFFE1234560000000000000080A0001400000001000001"
    result = parse_packet(raw_hex)
    
    assert result["type"] == "device"
    assert result["mac_id"] == "a0dc04fffe123456"
    assert result["pos"] == 0.0
    assert result["status"] == b"\x14\x00"

def test_parse_unsolicited_report():
    """Test parsing an unsolicited status update (ID 0x52)."""
    # MAC: a0dc04fffe123456, Status: 1200 (Moving), Pos: 7F7F (~50%)
    # 0x26 length + 52 ID
    raw_hex = "07000126A0DC04FFFE1234560000000000000052A00012007F7F" + ("0" * 32)
    result = parse_packet(raw_hex)
    
    assert result["type"] == "device"
    assert result["mac_id"] == "a0dc04fffe123456"
    assert 49.0 < result["pos"] < 51.0
    assert bool(result["status"][0] & StatusBit.MOVING.value) is True

def test_parse_name_response():
    """Test parsing a device name response (ID 0x62)."""
    # MAC: a0dc04fffe123456, Name: 'Ost5' (4F 73 74 35)
    name_hex = "4F737435".ljust(64, '0')
    raw_hex = f"07000130A0DC04FFFE1234560000000000000062{name_hex}"
    result = parse_packet(raw_hex)
    
    assert result["name"] == "Ost5"

def test_device_state_logic():
    """Test that the CentronicDevice object correctly interprets status bits."""
    device = CentronicDevice("a0dc04fffe123456")
    
    # Payload: Moving=True, Overheated=True, FlyScreen=True
    # Status Byte 1: 0x02 (Moving) | 0x40 (Overheated) | 0x10 (Fixed) = 0x52
    # Status Byte 2: 0x20 (FlyScreen)
    device.update_from_payload(b"\x52\x20", 75.5)
    
    assert device.moving is True
    assert device.overheated is True
    assert device.fly_screen is True
    assert device.position == 75.5
    assert device.blocked is False

def test_command_building():
    """Test that outbound packets are formatted correctly."""
    mac = "A0DC04FFFE123456"
    
    # Test Action (UP)
    action_pkt = build_action_packet(mac, Action.UP)
    assert action_pkt.startswith("07010118")
    assert "20" in action_pkt  # Action.UP code
    
    # Test MoveTo (100%)
    move_pkt = build_moveto_packet(mac, 100.0, 5)
    assert "FFFF" in move_pkt  # 100% in little endian is FFFF
    assert "0005" in move_pkt  # Cnt 5 in big endian

def test_set_name_encoding():
    """Verify that names are hex-encoded and padded correctly."""
    mac = "A0DC04FFFE123456"
    pkt = build_set_name_packet(mac, "Kitchen")
    # 'Kitchen' in hex is 4B69746368656E
    assert "4B69746368656E" in pkt
    assert pkt.endswith("0000") # Padded

def test_invalid_packet():
    with pytest.raises(BeckerParseError):
        parse_packet("DEADC0DE")
