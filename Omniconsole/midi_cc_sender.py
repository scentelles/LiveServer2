import argparse
import time

import rtmidi2


def main():
    parser = argparse.ArgumentParser(
        description="Simple MIDI CC sender for Springbeats vMIDI8."
    )
    parser.add_argument(
        "--port",
        default="Springbeats vMIDI8*",
        help="MIDI OUT port name or glob pattern.",
    )
    parser.add_argument(
        "--port-index",
        type=int,
        default=None,
        help="Open MIDI output port by index.",
    )
    parser.add_argument(
        "--list-ports",
        action="store_true",
        help="List MIDI output ports and exit.",
    )
    parser.add_argument(
        "--cc",
        type=int,
        default=16,
        help="CC number (0-127).",
    )
    parser.add_argument(
        "--value",
        type=int,
        default=0,
        help="CC value (0-127).",
    )
    parser.add_argument(
        "--sweep",
        action="store_true",
        help="Sweep CC value 0->127->0.",
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=0.02,
        help="Delay between sweep steps in seconds.",
    )
    args = parser.parse_args()

    if not 0 <= args.cc <= 127:
        raise SystemExit("CC must be 0-127.")
    if not 0 <= args.value <= 127:
        raise SystemExit("Value must be 0-127.")

    midi_out = rtmidi2.MidiOut()
    ports = list(midi_out.ports)
    if args.list_ports:
        print("MIDI OUT ports:")
        for idx, name in enumerate(ports):
            print(f"  [{idx}] {name}")
        return

    if args.port_index is not None:
        if args.port_index < 0 or args.port_index >= len(ports):
            raise SystemExit(f"Port index out of range: {args.port_index}")
        midi_out.open_port(args.port_index)
        port_label = ports[args.port_index]
    else:
        try:
            midi_out.open_port(args.port)
            port_label = args.port
        except ValueError:
            matches = list(midi_out.ports_matching(args.port))
            if matches:
                first = matches[0]
                midi_out.open_port(first)
                port_label = first
            else:
                available = "\n  ".join(f"[{i}] {n}" for i, n in enumerate(ports))
                raise SystemExit(
                    f'Port not found: "{args.port}"\nAvailable ports:\n  {available}'
                )

    print(f'Sending to "{port_label}"')

    status = 0xB0

    if args.sweep:
        try:
            for value in list(range(128)) + list(range(127, -1, -1)):
                midi_out.send_raw(status, args.cc, value)
                print(f"CC{args.cc}={value}")
                time.sleep(args.delay)
        except KeyboardInterrupt:
            print("Stopping...")
    else:
        midi_out.send_raw(status, args.cc, args.value)
        print(f"CC{args.cc}={args.value}")

    midi_out.close_port()


if __name__ == "__main__":
    main()
