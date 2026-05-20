# pybeckerplus

> [!WARNING]
> **Work in Progress:** This library is currently in early development and is not considered production-ready. Use it with caution and expect potential breaking changes.


A Python library implementing the Becker Centronic Plus USB protocol using `asyncio`. This library was designed to be used in [this](https://github.com/honsma235/hass-becker-centronic-plus) custom Home Assistant integration to control Becker Centronic Plus devices via a USB stick.

## Features
- **Async-first**: Built on [serialx](https://github.com/puddly/serialx).
- **Strict Parsing**: Uses regex to strictly enforce protocol data structures.
- **Device Registry**: Automatically tracks device states (position, status bits, names).
- **Global Discovery**: Support for mesh-wide status and info requests.

## Documentation
For detailed information about the communication protocol, see the [Protocol Description](resources/protocol.md).

## CLI Usage
You can test your USB stick directly from the command line:

```bash
# Run the monitor tool (Linux)
python tools/cli.py /dev/ttyUSB0

# Run the monitor tool (Windows)
python tools/cli.py COM3
```
