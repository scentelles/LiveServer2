import argparse
import time

import rtmidi

from gma2telnet import GrandMA2Telnet


MIDI_CC_STATUS = 0xB0
DEFAULT_MIDI_PORT_MATCH = "Arduino Leonardo"
DEFAULT_CCS = "7,8"
DEFAULT_EXECUTORS = "1.15,1.16"
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


def parse_cc_list(raw_value):
    items = [item.strip() for item in raw_value.split(",") if item.strip()]
    if not items:
        raise SystemExit("CC list cannot be empty.")

    try:
        ccs = [int(item) for item in items]
    except ValueError:
        raise SystemExit(f'Invalid CC list: "{raw_value}".')

    for cc in ccs:
        if cc < 0 or cc > 127:
            raise SystemExit(f"CC out of range (0-127): {cc}")

    if len(set(ccs)) != len(ccs):
        raise SystemExit("Duplicate CC entries are not allowed.")

    return ccs


def parse_executor_list(raw_value):
    items = [item.strip() for item in raw_value.split(",") if item.strip()]
    if not items:
        raise SystemExit("Executor list cannot be empty.")
    return items


def build_cc_callback(gma2, cc_to_executor, verbose):
    last_values = {cc: None for cc in cc_to_executor}

    def _callback(event, data=None):
        message, _delta_time = event
        if not message:
            return

        status = message[0] & 0xF0
        if status != MIDI_CC_STATUS or len(message) < 3:
            return

        control = message[1]
        value = message[2]
        executor = cc_to_executor.get(control)
        if executor is None:
            return

        percent = int(round(value * 100 / 127))
        if percent == last_values[control]:
            return

        last_values[control] = percent
        gma2.send_command(f"Fader {executor} At {percent}")
        if verbose:
            print(f"CC{control}={value} -> Fader {executor} At {percent}")

    return _callback


def main():
    parser = argparse.ArgumentParser(
        description="Listen to Arduino CCs and drive GrandMA2 executors via telnet."
    )
    parser.add_argument(
        "--midi-port",
        default=DEFAULT_MIDI_PORT_MATCH,
        help="Substring to match the MIDI input port name.",
    )
    parser.add_argument(
        "--executor",
        default=DEFAULT_EXECUTORS,
        help='Comma-separated executor list (example: "1.1,1.2").',
    )
    parser.add_argument("--user", default=DEFAULT_USER, help="GrandMA2 user.")
    parser.add_argument("--password", default=None, help="GrandMA2 password.")
    parser.add_argument(
        "--cc",
        default=DEFAULT_CCS,
        help='Comma-separated CC list (example: "7,8").',
    )
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

    ccs = parse_cc_list(args.cc)
    executors = parse_executor_list(args.executor)
    if len(ccs) != len(executors):
        raise SystemExit("Number of CCs must match number of executors.")

    cc_pairs = list(zip(ccs, executors))
    cc_to_executor = {cc: executor for cc, executor in cc_pairs}

    midi_in, port_name = open_midi_input(args.midi_port)
    midi_in.set_callback(build_cc_callback(gma2, cc_to_executor, args.verbose))
    try:
        midi_in.ignore_types(sysex=False, timing=False, active_sense=False)
    except TypeError:
        midi_in.ignore_types(sysex=False, timing=False)

    mapping_label = ", ".join(f"CC{cc} -> {executor}" for cc, executor in cc_pairs)
    print(f'Listening on "{port_name}" for {mapping_label}.')
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
