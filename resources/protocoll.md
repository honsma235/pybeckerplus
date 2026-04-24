# Becker CentronicPlus USB Protocol Description

## 1. Physical Layer & Framing

- **Interface**: Serial over USB (CDC).
- **Configuration**: 115200 Baud, 8N1, No Parity, No Flow Control.
- **Encoding**: ASCII-encoded Hexadecimal.
- **Envelope**: `[STX]` (0x02) + `<Hex Data>` + `[ETX]` (0x03).

Requests sent to the stick are acknowledged with a double CRLF sequence (`\r\n\r\n`).

Messages received from the stick typically terminate with a single LF (`\n`).

**Note**: Character positions 7 & 8 (byte 4) represent the payload length in bytes (the number of hex pairs). For readability, this specific length byte is not individually broken out in the request/response templates below.

## 2. Commands & Message Overview

The following table provides a quick reference for all known commands. For detailed packet structures refer to the linked sections. Field descriptions follow [below](#3-field-descriptions), 

| Chapter | Function |
|---|---|
| [2.1](#21-action-commands) | Action Commands (Up/Down/Stop/Presets) |
| [2.2](#22-moveto-command) | MoveTo Command (Absolute Positioning) |
| [2.3](#23-unsolicited-status-report) | Unsolicited Status Report |
| [2.4](#24-status--position-request) | Status & Position Request |
| [2.5](#25-serial-number-sn--firmware-fw) | SN & Firmware Request |
| [2.6](#26-parent-mac-id) | Parent MAC ID (Mesh Topology) |
| [2.7](#27-device-name-management) | Device Name Management (Get/Set) |
| [2.8](#28-stick-identity--firmware) | Stick Identity (MAC/FW) |
| [2.9](#29-pairing--teach-in-commands) | Pairing / Teach-in |


Requests are sent to the USB stick.
Responses are sent back from the stick. The interpretation of the data at the positions marked with 'XX' is unknown.

### 2.1 Action Commands

These commands, along with the "MoveTo" command, typically trigger an "Unsolicited Status Report" once the requested movement is complete.

| Type | Structure |
|---|---|
| Request (direct) | `07010118[....MAC_ID....]01013400000000000000[CMD]0000000501` |
| Request (global) | `0709011A000000000000000001013400000000002000[CMD]000000[CNT]0501` |

### 2.2 MoveTo Command

| Type | Structure |
|---|---|
| Request (direct) | `0701011A[....MAC_ID....]010134000000005340000000[POS][CNT]0501` |
| Request (global) | `0709011A0000000000000000010134000000005340000000[POS][CNT]0501` |

### 2.3 Unsolicited Status Report

Sent by a device to the stick (and forwarded to the host) whenever a movement completes (e.g., after manual remote control operation) or a status attribute changes.

| Type | Structure |
|---|---|
| Response | `07000126[....MAC_ID....]XXXXXXXXXXXXXX52XX[RSSI][STATUS][POS]XXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXX` |

### 2.4 Status & Position Request

| Type | Structure |
|---|---|
| Request (direct) | `0701011A[....MAC_ID....]0101340000000080A00000000000[CNT]0501` |
| Request (global) | `0709011A00000000000000000101340000000080A00000000000[CNT]0501` |
| Response | `0700011A[....MAC_ID....]XXXXXXXXXXXXXX80XX[RSSI][STATUS][POS][CNT]XXXX` |

### 2.5 Serial Number (SN) & Firmware (FW)

| Type | Structure |
|---|---|
| Request (direct) | `07010119[....MAC_ID....]01013400000000510000000000[CNT]0501` |
| Request (global) | `07090119000000000000000001013400000000510000000000[CNT]0501` |
| Response | `0700012B[....MAC_ID....]XXXXXXXXXXXXXX51XXXXXXXXXXXXXXXXXX[SN]XX[FW]XXXXXXXXXX[CNT][XX]` |

### 2.6 Parent MAC ID

Used to identify the mesh network topology.

| Type | Structure |
|---|---|
| Request | `0701011A[....MAC_ID....]0101340000000083800000000000[CNT]0501` |
| Response | `0700011A[....MAC_ID....]XXXXXX[ROOT_PFX]83[PARENT_ID]XXXX[CNT]XXXX` |

### 2.7 Device Name Management

Used to retrieve or set the human-readable name of a device. Both "Get" and "Set" operations trigger a Device Name Response from the target.

| Type | Structure |
|---|---|
| Get Name Request (direct) | `07010130[....MAC_ID....]80013400000000600000000000000000000000000000000000000000000000000000000000000000` |
| Get Name Request (global) | `07090130000000000000000080013400000000600000000000000000000000000000000000000000000000000000000000000000` |
| Set Name Request | `07010130[....MAC_ID....]8001340000000061[NAME]` |
| Response | `07000130[....MAC_ID....]XXXXXXXXXXXXXXXX62[NAME]` |

### 2.8 Stick Identity & Firmware

These requests target the USB stick directly to retrieve its hardware identity and internal firmware version.

| Type | Structure |
|---|---|
| MAC Request | `0717010B0000000000000000000000` |
| MAC Response | `07270111[STICK_MAC][INSTALL_ID]XXXXXXXXXX` |
| FW Request | `071E010B0000000000000000000000` |
| FW Response | `072E010CXXXXXXXXXXXXXXXX[STICK_FW]XX` |

### 2.9 Pairing / Teach-in Commands

These action commands are used to manage device pairing and the "teach-in" process.  
They can be used to pair additional Centronic or Centronic Plus devices, like hand-held or wall tranismitter, to the target.

**Note**: The pairing, or teach-in of new devices into the current Centronic Plus network is not yet fully understood.

| Type | Structure |
|---|---|
| Request  | `07010118[....MAC_ID....]01013400000000[CMD]2000ffba00000501` |

## 3. Field descriptions

| Length | Field | Value/Example | Description |
| :--- | :--- | :--- | :--- |
| 8 | MAC_ID | `a0dc04fffe...` | 64-bit hardware address of the target device. |
| 1 | CMD | Var | Action code, see below. |
| 2 | CNT | Var | In requests: Continuously increasing sequence index. Responses match the sequence index from the request. |
| 2 | POS | Var | Linear position mapping: 0% (open/top) = `0x0000` to 100% (closed/bottom) = `0xFFFF`. Transmitted in little-endian format. |
| 2 | STATUS | `0014` | Two status bytes, see below. |
| 1 | RSSI | Var | RSSI (Received Signal Strength Indicator) of the device; higher values indicate better signal quality. |
| 5 | SN | `0254123456` | Device serial number. |
| 3 | FW | `03010F` | Firmware version (formatted as `03.01.15`). |
| 4 | ROOT_PFX | `a0dc04ff` | The 32-bit prefix of the parent device MAC. |
| 4 | PARENT_ID | `fe......` | The 32-bit suffix of the parent device MAC. Combining ROOT_PFX and PARENT_ID yields the full parent MAC_ID. |
| 32 | NAME | `4f737435...` | UTF-8 encoded, then Hex-encoded name string, padded with `00`. |
| 8 | STICK_MAC | `A0DC04FF........` | The 64-bit address of the USB Stick. |
| 4 | INSTALL_ID | `........` | The 32-bit ID of the current installation. |
| 3 | STICK_FW | `010703` | Stick Firmware (e.g., 1.7.3). |
| 1 | PAIR | Var | Action Code (see below). |

---

### 3.1 Action Commands (CMD)
- `10`: Stop
- `20`: Up / Open
- `40`: Down / Close
- `24`: Go to Preset 1
- `44`: Go to Preset 2
- `31`: Set Current Position as Preset 1
- `51`: Set Current Position as Preset 2
- `17`: Delete both Presets
- `D1`: Toggle Fly-screen Protection  
Description: In the upper range of the travel path, the drive reacts significantly earlier to obstacles. This prevents damage to insect screens installed immediately below the top limit.

---

### 3.2 Status Bytes (STATUS)

**Status Byte 1: Core Motor State**  
This byte reports the physical state of the motor and primary error flags.

| Bit | Hex | Logical Meaning | Description |
| :--- | :--- | :--- | :--- |
| 0 | `0x01` | - | Unknown, always 0 |
| 1 | `0x02` | **Moving** | Indicates the motor is currently **In Motion** (1) or Stationary (0). |
| 2 | `0x04` | **Upper Limit** | Set when the device has reached the **Upper Limit Switch** (0% Open). |
| 3 | `0x08` | **Lower Limit** | Set when the device has reached the **Lower Limit Switch** (100% Closed). |
| 4 | `0x10` | - | Unknown, always 1 |
| 5 | `0x20` | - | Unknown, always 0 |
| 6 | `0x40` | **Overheated** | Error flag: Motor has reached thermal safety limits. |
| 7 | `0x80` | **Blocked** | Error flag: **Obstacle Detected**; motor stopped via safety cutout. |

**Status Byte 2: Auxiliary States**  
This byte is used for extended/auxiliary reporting.

| Bit | Hex | Logical Meaning | Description |
| :--- | :--- | :--- | :--- |
| 0 | `0x01` | - | Unknown, always 0 |
| 1 | `0x02` | - | Unknown, always 0 |
| 2 | `0x04` | - | Unknown, always 0 |
| 3 | `0x08` | - | Unknown, always 0 |
| 4 | `0x10` | - | Unknown, always 0 |
| 5 | `0x20` | **Fly-screen** | Reports if **Fly-screen Protection** is currently enabled. |
| 6 | `0x40` | - | Unknown, always 0 |
| 7 | `0x80` | - | Unknown, always 0 |

---

### 3.3 Known Pairing Commands (PAIR)

- `9a`: **Centronic Plus:** Activate teach-in.
- `9b`: **Centronic Plus:** Deactivate teach-in.
- `9c`: **Centronic Plus:** Delete all pairings.
- `96`: **Centronic:** Activate teach-in (Master).
- `97`: **Centronic:** Activate teach-in.
- `98`: **Centronic:** Activate teach-out.
- `99`: **Centronic:** Deactivate teach-in/teach-out.
- `9d`: **Centronic:** Delete all pairings.
