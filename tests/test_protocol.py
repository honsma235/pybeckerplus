# ruff: noqa: S101, D100, PLR2004, F403, F405, ERA001, INP001
# ty:ignore[not-subscriptable]

from unittest.mock import MagicMock

from pybeckerplus.client import BeckerClient
from pybeckerplus.constants import Action, PairingAction, StatusBit
from pybeckerplus.device import CentronicDevice
from pybeckerplus.packet import *


def test_parse_status_response() -> None:
    """Test parsing a standard status response (ID 0x80)."""
    # MAC: a0dc04fffe123456, RSSI: 40 (64), Status: 1400 (Stopped + Lower Limit),
    # Pos: 0000 (0%), Cnt: 0001
    raw_hex = "0700011AA0DC04FFFE1234560000000000000080A0401400000001000001"
    result = parse_packet(raw_hex)

    assert result["type"] == "device"
    assert result["mac_id"] == "a0dc04fffe123456"
    assert result["pos"] == 0.0
    assert result["status"] == b"\x14\x00"
    assert result["rssi"] == 64


def test_parse_unsolicited_report() -> None:
    """Test parsing an unsolicited status update (ID 0x52)."""
    # MAC: a0dc04fffe123456, RSSI: 4B (75), Status: 1200 (Moving), Pos: 7F7F (~50%)
    # 0x26 length + 52 ID
    raw_hex = "07000126A0DC04FFFE1234560000000000000052A04B12007F7F" + ("0" * 32)
    result = parse_packet(raw_hex)

    assert result["type"] == "device"
    assert result["mac_id"] == "a0dc04fffe123456"
    assert 49.0 < result["pos"] < 51.0
    assert bool(result["status"][0] & StatusBit.MOVING.value) is True
    assert result["rssi"] == 75


def test_parse_name_response() -> None:
    """Test parsing a device name response (ID 0x62)."""
    # MAC: a0dc04fffe123456, Name: 'Ost5' (4F 73 74 35)
    name_hex = "4F737435".ljust(64, "0")
    raw_hex = f"07000130A0DC04FFFE1234560000000000000062{name_hex}"
    result = parse_packet(raw_hex)

    assert result["name"] == "Ost5"


def test_parse_info_response() -> None:
    """Test parsing device info response (SN/FW)."""
    # MAC: a0dc04fffe123456, SN: 0254123456, FW: 03010F (3.1.15)
    mac = "A0DC04FFFE123456"
    sn = "0254123456"
    fw = "03010F"
    cnt = "0001"
    # Pattern: 8 + 16 + 14 + 2(51) + 18 + 10(SN) + 2 + 6(FW) + 10 + 4(CNT) + 4
    raw_hex = f"0700012B{mac}{'0' * 14}51{'0' * 18}{sn}00{fw}{'0' * 10}{cnt}0000"
    result = parse_packet(raw_hex)
    assert result["mac_id"] == mac.lower()
    assert result["sn"] == sn
    assert result["fw"] == "03.01.15"


def test_parse_parent_mac_response() -> None:
    """Test parsing parent MAC response."""
    mac = "A0DC04FFFE123456"
    root = "A0DC04FF"
    parent = "FEABCDEF"
    cnt = "0005"
    # Pattern: 8 + 16 + 6 + 8(ROOT) + 2(83) + 8(PARENT) + 4 + 4(CNT) + 4
    raw_hex = f"0700011A{mac}{'0' * 6}{root}83{parent}{'0' * 4}{cnt}0000"
    result = parse_packet(raw_hex)
    assert result["mac_id"] == mac.lower()
    assert result["parent_mac"] == (root + parent).lower()


def test_parse_stick_responses() -> None:
    """Test parsing responses from the USB stick itself."""
    # Stick Info: MAC=A0DC04FFFFFFFFFF, InstallID=12345678
    info_hex = "07270111A0DC04FFFFFFFFFF123456780000000000"
    info_res = parse_packet(info_hex)
    assert info_res["type"] == "stick_info"
    assert info_res["mac_id"] == "a0dc04ffffffffff"
    assert info_res["install_id"] == "12345678"

    # Stick FW: 010703 (1.7.3)
    fw_hex = "072E010C000000000000000001070300"
    fw_res = parse_packet(fw_hex)
    assert fw_res["type"] == "stick_fw"
    assert fw_res["fw"] == "01.07.03"


def test_device_state_logic() -> None:
    """Test that the CentronicDevice object correctly interprets status bits."""
    mock_client = MagicMock(spec=BeckerClient)
    device = CentronicDevice("a0dc04fffe123456", mock_client)

    # Payload: Moving=True, Overheated=True, AntiFreeze=True, FlyScreen=True
    # Status Byte 1: 0x02 (Moving) | 0x40 (Overheated) | 0x10 (Fixed) = 0x52
    # Status Byte 2: 0x10 (AntiFreeze) | 0x20 (FlyScreen)
    device.update_from_payload(b"\x52\x30", 75.5, 80)

    assert device.moving is True
    assert device.overheated is True
    assert device.anti_freeze is True
    assert device.fly_screen is True
    assert device.position == 75.5
    assert device.rssi == 80
    assert device.blocked is False


def test_build_action_commands() -> None:
    """Test that outbound action and movement packets are formatted correctly."""
    mac = "A0DC04FFFE123456"

    # Action (UP) - Direct
    action_pkt = build_action_packet(mac, Action.UP)
    assert action_pkt.startswith("07010118")
    assert "20" in action_pkt

    # Action (UP) - Global
    up_pkt = build_global_action_packet(Action.UP, 10)
    assert up_pkt.startswith("0709011A0000000000000000")
    assert "20" in up_pkt
    assert "000A" in up_pkt

    # MoveTo (100%) - Direct
    move_pkt = build_moveto_packet(mac, 100.0, 5)
    assert "FFFF" in move_pkt  # 100% in little endian
    assert "0005" in move_pkt  # Cnt 5 in big endian

    # MoveTo (50%) - Global
    global_move_pkt = build_global_moveto_packet(50.0, 20)
    assert "FF7F" in global_move_pkt  # 50% in little endian
    assert "0014" in global_move_pkt


def test_build_query_commands() -> None:
    """Test building status and metadata query packets (direct and global)."""
    mac = "A0DC04FFFE123456"

    # Status Request (Direct and Global)
    assert "80A0" in build_status_request(mac, 1)
    assert "80A0" in build_global_status_request(2)

    # Parent MAC Request
    assert "8380" in build_parent_mac_request(mac, 3)

    # SN/FW Global Request
    assert "510000000000" in build_global_info_request(4)


def test_build_config_commands() -> None:
    """Test building naming and configuration packets."""
    mac = "A0DC04FFFE123456"

    # Name Requests
    assert build_global_name_request().startswith("07090130")
    assert build_get_name_packet(mac).startswith("07010130")

    # Set Name (Encoding & Padding)
    pkt = build_set_name_packet(mac, "Kitchen")
    # 'Kitchen' in hex is 4B69746368656E
    assert "4B69746368656E" in pkt
    assert pkt.endswith("0000")

    # Pairing
    pairing_pkt = build_pairing_packet(mac, PairingAction.ACTIVATE_CENTRONIC_PLUS)
    assert "9A" in pairing_pkt
    assert "2000FFBA" in pairing_pkt


def test_build_system_commands() -> None:
    """Test building hardware-level system commands (Identify, Stick info)."""
    mac_input = "A0DC04FFFE123456"
    formatted_mac = format_mac(mac_input)

    # Identify (Jog)
    pkt = build_identify_packet(mac_input)
    expected_packet_suffix = "010134000000000020008100000000010501"
    assert pkt == f"0701011A{formatted_mac}{expected_packet_suffix}"

    # Stick Requests
    assert build_stick_info_request() == "0717010B0000000000000000000000"
    assert build_stick_fw_request() == "071E010B0000000000000000000000"


def test_invalid_packet() -> None:
    """Verify that unparsable packets return None as intended."""
    assert parse_packet("DEADC0DE") is None
