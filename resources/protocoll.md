# Becker CentronicPlus USB Protocol Description

## 1. Physical Layer & Framing
- **Interface**: Serial over USB (CDC).
- **Configuration**: 115200 Baud, 8N1, No Parity, No Flow Control.
- **Encoding**: ASCII-encoded Hexadecimal.
- **Envelope**: `[STX]` (0x02) + `<Hex Data>` + `[ETX]` (0x03).

Requests send to the stick are acknowledged with a double CRLF (`\r\n\r\n`).

Messages from the stick usually terminate with a single CR (`\n`).

## 2. Basic Commands
**Direction**: Host → Stick. Targets a specific physical device by MAC ID.

### 2.1 Action Commands
Request Structure (direct): `07010118[MAC_ID]01013400000000000000[CMD]0000000501`

Request Structure (global): `0709011a000000000000000001013400000000002000[CMD]000000[CNT]0501`

| Length | Field | Value/Example | Description |
| :--- | :--- | :--- | :--- |
| 8 | MAC_ID | `a0dc04fffe...` | 64-bit hardware address of the target device. |
| 1 | CMD | Var | Action Code (see below). |
| 2 | CNT | Var | Host-incremented sequence index. |

### Known Action Commands (CMD)
- `10`: **Stop**.
- `20`: **Up / Open**.
- `40`: **Down / Close**.
- `24`: **Go to Preset 1**.
- `44`: **Go to Preset 2**.
- `31`: **Set Current Position as Preset 1**.
- `51`: **Set Current Position as Preset 2**.
- `17`: **Delete both Presets**.
- `d1`: **Toggle Fly-screen Protection**.

---

### 2.2 MoveTo Command
Request Structure (direct): `0701011a[MAC_ID]010134000000005340000000[POS][CNT]0501`

Request Structure (global): `0709011a0000000000000000010134000000005340000000[POS][CNT]0501`

| Length | Field | Value/Example | Description |
| :--- | :--- | :--- | :--- |
| 8 | MAC_ID | `a0dc04fffe...` | 64-bit hardware address of the target device. |
| 2 | POS | Var | linear mapping between 0% (open) = 0x0000 and 100% (closed) = 0xffff, transmitted in little-endian format. |
| 2 | CNT | Var | Host-incremented sequence index. |

---

## **3. Information Request & Response Pairs**

### **3.1 Status & Position**
Request Structure (direct): `0701011a[MAC_ID]0101340000000080a00000000000[CNT]0501`

Request Structure (global): `0709011a00000000000000000101340000000080a00000000000[CNT]0501`

| Length | Field | Value/Example | Description |
| :--- | :--- | :--- | :--- |
| 8 | MAC_ID | `a0dc04fffe...` | 64-bit hardware address of the target device. |
| 2 | CNT | Var | Host-incremented sequence index. |

Response Structure: `0700011A[MAC_ID]XXXXXXXXXXXXXX80XXXX[STATUS][POS][CNT]0001`

| Length | Field | Value/Example | Description |
| :--- | :--- | :--- | :--- |
| 8 | MAC_ID | `a0dc04fffe...` | 64-bit hardware address of the target device. |
| 2 | STATUS | `0014` | Two status bytes, see below. |
| 2 | POS | Var | linear mapping between 0% (open) = 0x0000 and 100% (closed) = 0xffff, transmitted in little-endian format. |
| 2 | CNT | Var | Matches the sequence index from request. |

### **Status Byte 1: Core Motor State**
This byte reports the physical state of the motor and primary error flags.

| Bit | Hex | Logical Meaning | Description |
| :--- | :--- | :--- | :--- |
| **0** | `0x01` | - | Unknown, always 0 |
| **1** | `0x02` | **Moving** | Indicates the motor is currently **In Motion** (1) or Stationary (0). |
| **2** | `0x04` | **Lower Limit** | Set when the device has reached the **Lower Limit Switch** (0% Open). |
| **3** | `0x08` | **Upper Limit** | Set when the device has reached the **Upper Limit Switch** (100% Closed). |
| **4** | `0x10` | - | Unknown, always 1 |
| **5** | `0x20` | - | Unknown, always 0 |
| **6** | `0x40` | **Overheated** | Error flag: Motor has reached thermal limits. |
| **7** | `0x80` | **Blocked** | Error flag: **Obstacle Detected**; motor stopped via safety. |

### **Status Byte 2: Auxiliary States**
This byte is used for extended/auxiliary reporting.

| Bit | Hex | Logical Meaning | Description |
| :--- | :--- | :--- | :--- |
| **0** | `0x01` | - | Unknown, always 0 |
| **1** | `0x02` | - | Unknown, always 0 |
| **2** | `0x04` | - | Unknown, always 0 |
| **3** | `0x08` | - | Unknown, always 0 |
| **4** | `0x10` | - | Unknown, always 0 |
| **5** | `0x20` | **Fly-screen** | Reports if **Fly-screen Protection** is currently enabled. |
| **6** | `0x40` | - | Unknown, always 0 |
| **7** | `0x80` | - | Unknown, always 0 |

---

### **3.2 Serial Number (SN) & Firmware (FW)**
Request Structure (direct): `07010119[MAC_ID]01013400000000510000000000[CNT]0501`

Request Structure (global): `07090119000000000000000001013400000000510000000000[CNT]0501`

| Length | Field | Value/Example | Description |
| :--- | :--- | :--- | :--- |
| 8 | MAC_ID | `a0dc04fffe...` | 64-bit hardware address of the target device. |
| 2 | CNT | Var | Host-incremented sequence index. |

Response Structure: `0700012B[MAC_ID]XXXXXXXXXXXXXX51XXXXXXXXXXXXXXXXXX[SN]XX[FW]XXXXXXXXXX[CNT][XX]`

| Length | Field | Value/Example | Description |
| :--- | :--- | :--- | :--- |
| 8 | MAC_ID | `a0dc04fffe...` | 64-bit hardware address of the target device. |
| 5 | SN | `0254123456` | Contains SN. |
| 3 | FW | `03010F` | Firmware Version (e.g. formatting: `03.01.15`). |
| 2 | CNT | Var | Matches the sequence index from request. |

---

### **3.3 Parent MAC ID**
Used to identify the mesh network layout.

Request Structure: `0701011a[MAC_ID]0101340000000083800000000000[CNT]0501`,

| Length | Field | Value/Example | Description |
| :--- | :--- | :--- | :--- |
| 8 | MAC_ID | `a0dc04fffe...` | 64-bit hardware address of the target device. |
| 2 | CNT | Var | Host-incremented sequence index. |

Response Structure: `0700011A[MAC_ID]XXXXXX[ROOT_PFX]83[PARENT_ID]XXXX[CNT]XXXX`,

| Length | Field | Value/Example | Description |
| :--- | :--- | :--- | :--- |
| 8 | MAC_ID | `a0dc04fffe...` | 64-bit hardware address of the responding device. |
| 4 | ROOT_PFX | `a0dc04ff` | Manufacturer-specific prefix. |
| 4 | PARENT_ID | `fe......` | The 32-bit unique identifier of the parent device. |
| 2 | CNT | Var | Matches the sequence index from the request. |

---

### **3.4 Device Name Management**
Used to retrieve or set the human-readable name of the device.

#### **3.4.1 Get Device Name**

Request Structure (direct): `07010130[MAC_ID]80013400000000600000000000000000000000000000000000000000000000000000000000000000`,

Request Structure (global): `07090130000000000000000080013400000000600000000000000000000000000000000000000000000000000000000000000000`,

| Length | Field | Value/Example | Description |
| :--- | :--- | :--- | :--- |
| 8 | MAC_ID | `a0dc04fffe...` | 64-bit hardware address of the target device. |

#### **3.4.1 Set Device Name**

Request Structure: `07010130[MAC_ID]8001340000000061[NAME]`

| Length | Field | Value/Example | Description |
| :--- | :--- | :--- | :--- |
| 8 | MAC_ID | `a0dc04fffe...` | 64-bit hardware address of the target. |
| 32 | NAME | `4f737435...` | UTF-8 encoded, then Hex-encoded name, padded with `00`. |


#### **3.4.3 Device Name Response**

Response Structure: `07000130[MAC_ID]XXXXXXXXXXXXXXXX62[NAME]`,

| Length | Field | Value/Example | Description |
| :--- | :--- | :--- | :--- |
| 8 | MAC_ID | `a0dc04fffe...` | 64-bit hardware address of the responding device. |
| 32 | NAME | `4F737431...` | UTF-8 encoded, then Hex-encoded name, padded with `00`. |

---

### **3.5 Stick Identity & Firmware**
These requests are sent to the USB stick itself to retrieve its own identity and firmware version.

**MAC Request:** `0717010b0000000000000000000000`
**MAC Response:** `07270111[STICK_MAC][INSTALL_ID]XXXXXXXXXX`

| Length | Field | Value/Example | Description |
| :--- | :--- | :--- | :--- |
| 8 | STICK_MAC | `A0DC04FF........` | The 64-bit address of the USB Stick. |
| 4 | INSTALL_ID | `........` | The 32-bit ID of the current installation. |

**FW Request:** `071e010b0000000000000000000000`
**FW Response:** `072E010CXXXXXXXXXXXXXXXX[STICK_FW]XX`

| Length | Field | Value/Example | Description |
| :--- | :--- | :--- | :--- |
| 3 | STICK_FW | `010703` | Stick Firmware (e.g., 1.7.3). |

---

## **4. Pairing / Teach-in Commands**
These are action commands (sent from Host to Stick) used to manage device pairing.

**Data structure:** `07010118[MAC_ID]01013400000000[CMD]2000ffba00000501`

| Length | Field | Value/Example | Description |
| :--- | :--- | :--- | :--- |
| 8 | MAC_ID | `a0dc04fffe...` | 64-bit hardware address of the target. |
| 1 | PAIR | Var | Action Code (see below). |

### Known Pairing Commands (PAIR)
- `9a`: **Centronic Plus:** Activate teach-in. |
- `9b`: **Centronic Plus:** Deactivate teach-in. |
- `9c`: **Centronic Plus:** Delete all pairings. |
- `96`: **Centronic:** Activate teach-in (Master). |
- `97`: **Centronic:** Activate teach-in. |
- `98`: **Centronic:** Activate teach-out. |
- `99`: **Centronic:** Deactivate teach-in/teach-out. |
- `9d`: **Centronic:** Delete all pairings. |

---

### **5 Unsolicited Status Report**
Sent by a device to the Stick (and then to the Host) whenever a movement has completed (e.g., via manual remote control) or a status has changed.

Response Structure: `07000126[MAC_ID]XXXXXXXXXXXXXX52XXXX[STATUS][POS]XXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXX`
| Length | Field | Value/Example | Description |
| :--- | :--- | :--- | :--- |
| 8 | MAC_ID | `a0dc04fffe...` | 64-bit hardware address of the reporting device. |
| 2 | STATUS | `0014` | Two status bytes, same as in response to status request 3.1. |
| 2 | POS | Var | linear mapping between 0% (open) = 0x0000 and 100% (closed) = 0xffff, transmitted in little-endian format. |
