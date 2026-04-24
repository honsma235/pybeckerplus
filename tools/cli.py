import argparse
import asyncio
import logging
import sys
import os
import shlex
from typing import Optional

# Add the project root to sys.path to allow running the tool without installing the library
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from pybeckerplus import BeckerClient, Action

MONITOR_ENABLED = True

def print_device(device):
    print(f"Mac: {device.mac_id} ({device.name}), "
            f"SN: {device.serial_number}, FW: {device.firmware_version}), "
            f"Pos={device.position}%, RSSI={device.rssi}, Moving={device.moving}, "
            f"Limits(U={int(device.upper_limit)} L={int(device.lower_limit)}), "
            f"Status(Block={int(device.blocked)} OverH={int(device.overheated)}  Fly={int(device.fly_screen)})")

def get_target(mac_input: str) -> Optional[str]:
    """Helper to convert 'all' keyword to None for the client."""
    return None if mac_input.lower() == "all" else mac_input

def device_updated(device):
    """Callback triggered whenever a device state changes."""
    if MONITOR_ENABLED:
        print(f"\n[UPDATE] ", end="")
        print_device(device)
        print("beckerplus> ", end="", flush=True)

async def interactive_shell(client):
    """Main CLI logic."""
    global MONITOR_ENABLED
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
            
            elif cmd == "help":
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
  name <mac> <name>   - Set a new name for a device
  get-name <mac|all>  - Fetch name from device(s)
  help                - Show this menu
  exit                - Close connection and quit
""")

            elif cmd == "list":
                print(f"Stick Info: MAC={client.stick_mac or 'Unknown'}, "
                      f"InstallID={client.stick_install_id or 'Unknown'}, "
                      f"FW={client.stick_fw or 'Unknown'}")
                print("-" * 60)
                if not client.devices:
                    print("No devices discovered.")
                else:
                    for dev in client.devices.values():
                        print_device(dev)

            elif cmd == "monitor" and len(parts) > 1:
                MONITOR_ENABLED = parts[1].lower() == "on"
                print(f"Monitoring {'enabled' if MONITOR_ENABLED else 'disabled'}")

            elif cmd == "discovery":
                await client.start_discovery()

            elif cmd in ["up", "down", "stop"] and len(parts) > 1:
                action_map = {"up": Action.UP, "down": Action.DOWN, "stop": Action.STOP}
                await client.action(get_target(parts[1]), action_map[cmd])

            elif cmd == "move" and len(parts) > 2:
                await client.move_to(get_target(parts[1]), float(parts[2]))

            elif cmd == "status" and len(parts) > 1:
                await client.request_status(get_target(parts[1]))

            elif cmd == "name" and len(parts) > 2:
                await client.set_device_name(parts[1], parts[2])

            elif cmd == "get-name" and len(parts) > 1:
                await client.get_device_name(get_target(parts[1]))

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
                try:
                    await discovery_task
                except asyncio.CancelledError:
                    pass

async def main():
    """Single entry point to manage the event loop lifecycle."""
    parser = argparse.ArgumentParser(description="Becker Centronic Plus Interactive Tool")
    parser.add_argument("port", help="Serial port (e.g. COM3 or /dev/ttyUSB0)")
    parser.add_argument("-v", "--verbose", action="store_true", help="Enable verbose logging")

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