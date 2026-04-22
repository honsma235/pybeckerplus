# pybeckerplus

A Python library implementing the Becker Centronic Plus USB protocol using `asyncio`. This library is designed to be used in Home Assistant integrations to control Becker Centronic Plus devices via a USB stick.

## Features
- **Async-first**: Built on `pyserial-asyncio`.
- **Strict Parsing**: Uses regex to strictly enforce protocol data structures.
- **Device Registry**: Automatically tracks device states (position, status bits, names).
- **Global Discovery**: Support for mesh-wide status and info requests.

## Documentation
For detailed information about the communication protocol, see the [Protocol Description](resources/protocoll.md).

## CLI Usage
You can test your USB stick directly from the command line:

```bash
# Run the monitor tool (Linux)
python tools/cli.py /dev/ttyUSB0

# Run the monitor tool (Windows)
python tools/cli.py COM3
```
