import argparse
import time

import rtmidi2


def print_ports():
    midi_in = rtmidi2.MidiIn()
    print("MIDI IN ports:")
    for name in midi_in.ports:
        print(f"  {name}")


def main():
    parser = argparse.ArgumentParser(
        description="Simple MIDI logger for OMNICONSOLE driver port."
    )
    parser.add_argument(
        "--port",
        default="OMNICONSOLE*",
        help="MIDI IN port name or glob pattern.",
    )
    parser.add_argument(
        "--list-ports",
        action="store_true",
        help="List MIDI input ports and exit.",
    )
    args = parser.parse_args()

    if args.list_ports:
        print_ports()
        return

    midi_in = rtmidi2.MidiIn()
    midi_in.open_port(args.port)

    def _callback(message, timestamp=None):
        print(f"IN: {message}")

    midi_in.callback = _callback
    try:
        midi_in.ignore_types(midi_sysex=False)
    except TypeError:
        midi_in.ignore_types(sysex=False, timing=False)

    print(f'Listening on "{args.port}" (press Ctrl+C to stop)')
    try:
        while True:
            time.sleep(0.1)
    except KeyboardInterrupt:
        print("Stopping...")
    finally:
        midi_in.close_port()


if __name__ == "__main__":
    main()
