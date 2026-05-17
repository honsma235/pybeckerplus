# ruff: noqa: T201, INP001, D100, D103, D401, PLR0912, PLR0915, PLR2004, BLE001, C901

import argparse
import asyncio
import logging
import shlex
import sys
from pathlib import Path

# Add the src directory to sys.path to allow running the tool without
# installing the library
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))


import contextlib

from pybeckerplus import Action, BeckerClient, CentronicDevice

monitor_enabled = True


def print_device(device: CentronicDevice) -> None:
    print(
        f"Mac: {device.mac_id} ({device.name}), "
        f"SN: {device.serial_number}, FW: {device.firmware_version}), "
        f"Pos={device.position}%, RSSI={device.rssi}, Moving={device.moving}, "
        f"Limits(U={int(device.upper_limit)} L={int(device.lower_limit)}), "
        f"Status(Block={int(device.blocked)} OverH={int(device.overheated)}  "
        f"Fly={int(device.fly_screen)})"
    )


def get_target(mac_input: str) -> str | None:
    """Helper to convert 'all' keyword to None for the client."""
    return None if mac_input.lower() == "all" else mac_input


def device_updated(device: CentronicDevice) -> None:
    """Callback triggered whenever a device state changes."""
    if monitor_enabled:
        print("\n[UPDATE] ", end="")
        print_device(device)
        print("beckerplus> ", end="", flush=True)


async def interactive_shell(client: BeckerClient) -> None:
    """Main CLI logic."""
    loop = asyncio.get_event_loop()

    print("\n--- Becker Centronic Plus Interactive Shell ---")
    print("Type 'help' for commands, 'exit' to quit.\n")

    # Trigger initial discovery
    discovery_task = asyncio.create_task(client.start_discovery())

    while True:
        try:
            cmd_line = await loop.run_in_executor(None, input, "beckerplus> ")
            if not cmd_line.strip():
                continue

            parts = shlex.split(cmd_line)
            cmd = parts[0].lower()

            if cmd in ["exit", "quit"]:
                break

            if cmd == "help":
                print("""
Commands:
  list                - List all discovered devices
  discovery           - Manually trigger mesh discovery
  monitor <on|off>    - Enable or disable live status updates
  up <mac|all>        - Move device(s) up
  down <mac|all>      - Move device(s) down
  stop <mac|all>      - Stop device(s) movement
  move <mac|all> <0-100> - Move to specific position
  status <mac|all>    - Poll status for device(s)
  identify <mac>      - Identify device (jog)
  name <mac> <name>   - Set a new name for a device
  get-name <mac|all>  - Fetch name from device(s)
  help                - Show this menu
  exit                - Close connection and quit
""")

            elif cmd == "list":
                print(
                    f"Stick Info: MAC={client.stick_mac or 'Unknown'}, "
                    f"InstallID={client.stick_install_id or 'Unknown'}, "
                    f"FW={client.stick_fw or 'Unknown'}"
                )
                print("-" * 60)
                if not client.devices:
                    print("No devices discovered.")
                else:
                    for dev in client.devices.values():
                        print_device(dev)

            elif cmd == "monitor" and len(parts) > 1:
                monitor_enabled = parts[1].lower() == "on"
                print(f"Monitoring {'enabled' if monitor_enabled else 'disabled'}")

            elif cmd == "discovery":
                await client.start_discovery()

            elif cmd in ["up", "down", "stop"] and len(parts) > 1:
                target = get_target(parts[1])
                action_map = {"up": Action.UP, "down": Action.DOWN, "stop": Action.STOP}
                if target is None:
                    await client.global_action(action_map[cmd])
                elif device := client.get_device(target):
                    await getattr(device, cmd)()
                else:
                    print(f"Device {target} not found")

            elif cmd == "move" and len(parts) > 2:
                target = get_target(parts[1])
                pos = float(parts[2])
                if target is None:
                    await client.global_move_to(pos)
                elif device := client.get_device(target):
                    await device.move_to(pos)
                else:
                    print(f"Device {target} not found")

            elif cmd == "status" and len(parts) > 1:
                target = get_target(parts[1])
                if target is None:
                    await client.global_request_status()
                elif device := client.get_device(target):
                    await device.request_status()
                else:
                    print(f"Device {target} not found")

            elif cmd == "identify" and len(parts) > 1:
                if device := client.get_device(parts[1]):
                    await device.identify()
                else:
                    print(f"Device {parts[1]} not found")

            elif cmd == "name" and len(parts) > 2:
                if device := client.get_device(parts[1]):
                    await device.set_name(parts[2])
                else:
                    print(f"Device {parts[1]} not found")

            elif cmd == "get-name" and len(parts) > 1:
                target = get_target(parts[1])
                if target is None:
                    await client.global_get_device_names()
                elif device := client.get_device(target):
                    await device.get_name()
                else:
                    print(f"Device {target} not found")

            else:
                print(f"Unknown command or missing arguments: {cmd}")

        except (EOFError, KeyboardInterrupt):
            break
        except Exception as e:
            print(f"Error: {e}")
        finally:
            # Cleanup background discovery if shell is exiting
            if not discovery_task.done():
                discovery_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await discovery_task


async def main() -> None:
    """Single entry point to manage the event loop lifecycle."""
    parser = argparse.ArgumentParser(
        description="Becker Centronic Plus Interactive Tool"
    )
    parser.add_argument("port", help="Serial port (e.g. COM3 or /dev/ttyUSB0)")
    parser.add_argument(
        "-v", "--verbose", action="store_true", help="Enable verbose logging"
    )

    args = parser.parse_args()

    if args.verbose:
        logging.basicConfig(level=logging.DEBUG)

    client = BeckerClient(args.port, device_callback=device_updated)

    try:
        await client.connect()
        await interactive_shell(client)
    except KeyboardInterrupt:
        pass
    finally:
        await client.close()
        print("Done.")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        sys.exit(0)
