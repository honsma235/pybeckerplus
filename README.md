# pybeckerplus

> [!WARNING]
> **Work in Progress:** This library is currently in early development and is not considered production-ready. Use it with caution and expect potential breaking changes.


A Python library implementing the Becker Centronic Plus USB protocol using `asyncio`. This library was designed to be used in [this](https://github.com/honsma235/hass-becker-centronic-plus) custom Home Assistant integration to control Centronic Plus devices via a Becker Centronic Plus USB stick (ordering codes `4036 200 001 0` or `4036 000 009 0`).
> [!IMPORTANT]
> This integration does **not** work with the older **non-Plus** Centronic USB sticks (`4035 200 041 0` or `4035 000 041 0`)!

## Features
- **Async-first**: Built on [serialx](https://github.com/puddly/serialx) for high-performance, non-blocking I/O
- **Device Registry**: Automatically tracks device states (position, status bits, names).
- **Global Discovery**: Support for automated mesh-wide status and info requests.
- **Activity Polling**: Optional status updates while devices are moving or recovering.

## Known Limitations

- Currently only tested with roller shutter drives of the **C01 PLUS** series.
- It likely does not yet support the **EVO 20 R PLUS** series or sun protection drives of the **Cxx PLUS** series.
- Does not support pairing the Becker USB stick with covers or performing initial commissioning. This functionality is not yet implemented to ensure setup reliability.
> [!TIP]
> Pairing and initial commissioning (e.g., setting end-stop positions) can be performed using a computer or mobile device with the [Becker Tool](https://l.ead.me/beypHO) app, available on the Microsoft Store, Google Play Store, and Apple App Store.

> [!CAUTION]
> Configuring end-stops is a critical task. Incorrect settings can lead to hardware damage. It is your responsibility to ensure you follow the manufacturer's instructions or consider hiring a professional installer.

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

## Development Setup

1. Install [uv](https://astral.sh) if you haven't already.
2. Clone the repository and run:

```bash
uv sync --all-groups
```

This automatically installs the correct Python version, creates a virtual environment, and installs all core and development dependencies.
