# ruff: noqa: T201, INP001, D100, D103, D401, PLR0912, PLR0915, PLR2004, BLE001, C901, E501

import argparse
import asyncio
import logging
import shlex
import sys
from pathlib import Path

# Add the src directory to sys.path to allow running the tool without
# installing the library
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))


from pybeckerplus import Action, BeckerClient, BeckerError, CentronicPlusDevice

live_status_enabled = True
_LOGGER = logging.getLogger(__name__)


def print_device(device: CentronicPlusDevice) -> None:
    print(
        f"MAC: {device.mac_id} ({device.name or 'No Name'}), "
        f"SN: {device.serial_number}, FW: {device.firmware_version}, "
        f"Pos={device.position}%, RSSI={device.rssi}, Avail={device.available}, "
        f"Moving={device.moving}, "
        f"Limits(U={int(device.upper_limit)} L={int(device.lower_limit)}), "
        f"Status(Block={int(device.blocked)} OverH={int(device.overheated)} "
        f"Freeze={int(device.anti_freeze)} Fly={int(device.fly_screen)})"
    )


def print_stick_info(client: BeckerClient) -> None:
    print(
        f"Stick Info: MAC={client.stick_mac or 'Unknown'}, "
        f"InstallID={client.stick_install_id or 'Unknown'}, "
        f"FW={client.stick_fw or 'Unknown'}, "
        f"ActivityPolling={'Active' if client.enable_polling else 'Off'}"
    )


def get_target(mac_input: str) -> str | None:
    """Helper to convert 'all' keyword to None for the client."""
    return None if mac_input.lower() == "all" else mac_input


def device_updated(device: CentronicPlusDevice) -> None:
    """Callback triggered whenever a device state changes."""
    if live_status_enabled:
        print("\n[UPDATE] ", end="")
        print_device(device)
        print("beckerplus> ", end="", flush=True)


async def _prompt_or_disconnect(
    prompt: str, disconnect_event: asyncio.Event
) -> str | None:
    input_task = asyncio.create_task(asyncio.to_thread(input, prompt))
    wait_disconnect = asyncio.create_task(disconnect_event.wait())

    try:
        done, _pending = await asyncio.wait(
            [input_task, wait_disconnect],
            return_when=asyncio.FIRST_COMPLETED,
        )
    except (asyncio.CancelledError, KeyboardInterrupt):
        # Suppress potential EOFError on shutdown
        input_task.add_done_callback(
            lambda f: f.exception() if not f.cancelled() else None
        )
        raise
    finally:
        wait_disconnect.cancel()
        if not input_task.done():
            input_task.add_done_callback(
                lambda f: f.exception() if not f.cancelled() else None
            )
            input_task.cancel()

    return await input_task if input_task in done else None


async def interactive_shell(
    client: BeckerClient, disconnect_event: asyncio.Event
) -> None:
    print("\n--- Becker Centronic Plus Interactive Shell ---")
    print("Type 'help' for commands, 'exit' to quit.\n")

    # Trigger initial discovery
    await client.start_monitoring(restart=False)

    while True:
        try:
            cmd_line = await _prompt_or_disconnect("beckerplus> ", disconnect_event)
            if cmd_line is None:
                break

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
  live_update <on|off>    - Enable or disable live status updates
  polling <on|off>    - Enable or disable background activity polling
  up <mac|all>        - Move device(s) up
  down <mac|all>      - Move device(s) down
  stop <mac|all>      - Stop device(s) movement
  move <mac|all> <0-100> - Move to specific position
  status <mac|all>    - Poll status for device(s)
  identify <mac>      - Identify device (jog)
  name <mac> <name>   - Set/Update name on hardware
  get-name <mac|all>  - Fetch name from device(s)
  help                - Show this menu
  exit                - Close connection and quit
""")

            elif cmd == "list":
                print_stick_info(client)
                print("-" * 60)
                if not client.devices:
                    print("No devices discovered.")
                else:
                    for dev in client.devices.values():
                        print_device(dev)

            elif cmd == "live_update" and len(parts) > 1:
                live_status_enabled = parts[1].lower() == "on"
                print(
                    f"Live status update {'enabled' if live_status_enabled else 'disabled'}"
                )

            elif cmd == "polling" and len(parts) > 1:
                client.enable_polling = parts[1].lower() == "on"
                print(
                    f"Activity polling {'enabled' if client.enable_polling else 'disabled'}"
                )

            elif cmd == "discovery":
                await client.start_monitoring(restart=True)

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
            pass


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

    disconnect_event = asyncio.Event()

    def handle_disconnect(exc: Exception | None) -> None:
        if exc:
            print(f"\nStick disconnected: {exc}")
        disconnect_event.set()

    client = BeckerClient(
        args.port,
        device_callback=device_updated,
        on_disconnect=handle_disconnect,
        enable_polling=True,
    )

    try:
        async with client:
            await client.initialize()
            print_stick_info(client)
            await interactive_shell(client, disconnect_event)
    except (KeyboardInterrupt, asyncio.CancelledError):
        _LOGGER.debug("Main loop interrupted")
    except BeckerError:
        # Handled by on_disconnect
        pass
    except Exception as exc:
        print(repr(exc))

    print("Done.")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        sys.exit(0)
