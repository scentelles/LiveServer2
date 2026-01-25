import argparse
import time

import rtmidi

from gma2telnet import GrandMA2Telnet


MIDI_CC_STATUS = 0xB0
DEFAULT_MIDI_PORT_MATCH = "Arduino Leonardo"
DEFAULT_CC = 7
DEFAULT_EXECUTOR = "1.15"
DEFAULT_USER = "Administrator"


def open_midi_input(port_match):
    midi_in = rtmidi.MidiIn()
    ports = midi_in.get_ports()
    if not ports:
        raise SystemExit("No MIDI input ports detected.")

    for index, name in enumerate(ports):
        if port_match.lower() in name.lower():
            midi_in.open_port(index)
            return midi_in, name

    raise SystemExit(f'MIDI input port not found: "{port_match}"')


def build_cc_callback(gma2, executor, cc_number, verbose):
    last_value = {"value": None}

    def _callback(event, data=None):
        message, _delta_time = event
        if not message:
            return

        status = message[0] & 0xF0
        if status != MIDI_CC_STATUS or len(message) < 3:
            return

        control = message[1]
        value = message[2]
        if control != cc_number:
            return

        percent = int(round(value * 100 / 127))
        if percent == last_value["value"]:
            return

        last_value["value"] = percent
        gma2.send_command(f"Fader {executor} At {percent}")
        if verbose:
            print(f"CC{control}={value} -> Fader {executor} At {percent}")

    return _callback


def main():
    parser = argparse.ArgumentParser(
        description="Listen to Arduino CC7 and drive a GrandMA2 executor via telnet."
    )
    parser.add_argument(
        "--midi-port",
        default=DEFAULT_MIDI_PORT_MATCH,
        help="Substring to match the MIDI input port name.",
    )
    parser.add_argument(
        "--executor",
        default=DEFAULT_EXECUTOR,
        help='Executor identifier (example: "1.1").',
    )
    parser.add_argument("--user", default=DEFAULT_USER, help="GrandMA2 user.")
    parser.add_argument("--password", default=None, help="GrandMA2 password.")
    parser.add_argument("--cc", type=int, default=DEFAULT_CC, help="CC number.")
    parser.add_argument("--host", default="127.0.0.1", help="GrandMA2 host.")
    parser.add_argument("--port", type=int, default=30000, help="GrandMA2 telnet port.")
    parser.add_argument("--verbose", action="store_true")

    args = parser.parse_args()

    gma2 = GrandMA2Telnet(
        host=args.host,
        port=args.port,
        user=args.user,
        password=args.password,
    )
    gma2.connect()

    midi_in, port_name = open_midi_input(args.midi_port)
    midi_in.set_callback(build_cc_callback(gma2, args.executor, args.cc, args.verbose))
    try:
        midi_in.ignore_types(sysex=False, timing=False, active_sense=False)
    except TypeError:
        midi_in.ignore_types(sysex=False, timing=False)

    print(f'Listening on "{port_name}" for CC{args.cc} -> executor {args.executor}.')
    try:
        while True:
            time.sleep(0.1)
    except KeyboardInterrupt:
        print("Stopping...")
    finally:
        midi_in.close_port()
        gma2.close()


if __name__ == "__main__":
    main()
