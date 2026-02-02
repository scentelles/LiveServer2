import argparse
import queue
import time
import tkinter as tk
from tkinter import ttk
import math

import rtmidi
import rtmidi2


MIDI_PITCH_BEND = 0xE0
MIDI_NOTE = 0x90
MIDI_CC = 0xB0

DEFAULT_IN_PORT_MATCH = "Springbeats vMIDI7"
DEFAULT_OUT_PORT_MATCH = "Springbeats vMIDI8"
SCRIBBLE_SYSEX_PREFIX = [0xF0, 0x00, 0x00, 0x66, 0x15, 0x12]
SCRIBBLE_LEN = 7
LED_OFF_COLOR = "#2b2b2b"
LED_ON_COLOR = "#2ecc71"
TRACE = False

def _select_port_index(ports, port_match):
    if not port_match:
        return None
    needle = port_match.lower()
    for index, name in enumerate(ports):
        if needle in name.lower():
            return index
    return None


def _open_midi_port(midi_cls, port_match, direction_label):
    midi = midi_cls()
    ports = midi.get_ports()
    if not ports:
        raise SystemExit(f"No MIDI {direction_label} ports detected.")

    index = _select_port_index(ports, port_match)
    if index is None:
        available = "\n  ".join(f"[{idx}] {name}" for idx, name in enumerate(ports))
        raise SystemExit(
            f'MIDI {direction_label} port not found for "{port_match}".\n'
            f"Available {direction_label} ports:\n  {available}"
        )

    midi.open_port(index)
    return midi, ports[index]


def open_midi_out(port_match=DEFAULT_OUT_PORT_MATCH, port_index=None):
    midi_out = rtmidi2.MidiOut()
    ports = list(midi_out.ports)
    if not ports:
        raise SystemExit("No MIDI output ports detected.")

    if port_index is not None:
        if port_index < 0 or port_index >= len(ports):
            raise SystemExit(f"Port index out of range: {port_index}")
        midi_out.open_port(port_index)
        return midi_out, ports[port_index]

    try:
        midi_out.open_port(port_match)
        return midi_out, port_match
    except ValueError:
        for name in ports:
            if port_match.lower() in name.lower():
                midi_out.open_port(name)
                return midi_out, name
        matches = list(midi_out.ports_matching(port_match))
        if matches:
            midi_out.open_port(matches[0])
            return midi_out, matches[0]

    available = "\n  ".join(f"[{idx}] {name}" for idx, name in enumerate(ports))
    raise SystemExit(
        f'MIDI output port not found: "{port_match}".\n'
        f"Available output ports:\n  {available}"
    )


def open_midi_in(port_match=DEFAULT_IN_PORT_MATCH):
    return _open_midi_port(rtmidi.MidiIn, port_match, "IN")


def _send_message(midi_out, message):
    if TRACE:
        print(f"OUT: {message}")
    if hasattr(midi_out, "send_message"):
        midi_out.send_message(message)
    else:
        midi_out.send_raw(*message)


def send_pitch_bend(midi_out, fader_index, percent):
    if fader_index < 0 or fader_index > 7:
        raise ValueError("fader_index must be 0-7.")
    if percent < 0 or percent > 100:
        raise ValueError("percent must be 0-100.")

    value = int(round(percent * 16383 / 100))
    lsb = value & 0x7F
    msb = (value >> 7) & 0x7F
    _send_message(midi_out, [MIDI_PITCH_BEND + fader_index, lsb, msb])


def send_note(midi_out, note, on):
    if note < 0 or note > 127:
        raise ValueError("note must be 0-127.")
    value = 127 if on else 0
    _send_message(midi_out, [MIDI_NOTE, note, value])


def send_cc(midi_out, control, value):
    if control < 0 or control > 127:
        raise ValueError("control must be 0-127.")
    if value < 0 or value > 127:
        raise ValueError("value must be 0-127.")
    _send_message(midi_out, [MIDI_CC, control, value])


def parse_on_off(raw):
    raw_lower = raw.strip().lower()
    if raw_lower in ("1", "on", "true", "yes"):
        return True
    if raw_lower in ("0", "off", "false", "no"):
        return False
    raise ValueError("Expected on/off or 1/0.")


def print_help():
    print("Commands:")
    print("  fader <1-8> <0-100>          Set fader position (percent)")
    print("  note <0-39> <on|off>         Press/release button")
    print("  cc <0-127> <0-127>           Send CC value")
    print("  sweep <1-8> <from> <to> <step> <delay_ms>")
    print("  demo                         Run a short demo sequence")
    print("  help                         Show this help")
    print("  quit                         Exit")


def run_demo(midi_out, delay):
    for idx in range(8):
        send_pitch_bend(midi_out, idx, 0)
    time.sleep(delay)
    for idx in range(8):
        send_pitch_bend(midi_out, idx, 50)
    time.sleep(delay)
    for idx in range(8):
        send_pitch_bend(midi_out, idx, 100)
    time.sleep(delay)

    for note in range(8):
        send_note(midi_out, note, True)
    time.sleep(delay)
    for note in range(8):
        send_note(midi_out, note, False)
    time.sleep(delay)

    send_cc(midi_out, 16, 10)
    send_cc(midi_out, 16, 70)
    send_cc(midi_out, 17, 5)
    send_cc(midi_out, 17, 90)


def parse_scribble_message(message):
    if len(message) < len(SCRIBBLE_SYSEX_PREFIX) + 1 + SCRIBBLE_LEN + 1:
        return None
    if message[: len(SCRIBBLE_SYSEX_PREFIX)] != SCRIBBLE_SYSEX_PREFIX:
        return None

    offset = message[6]
    index = None
    row = None
    if 0 <= offset <= 49 and offset % 7 == 0:
        index = offset // 7
        row = 0
    elif 56 <= offset <= 105 and (offset - 56) % 7 == 0:
        index = (offset - 56) // 7
        row = 1
    if index is None or index < 0 or index > 7:
        return None

    raw_label = message[7 : 7 + SCRIBBLE_LEN]
    label_chars = []
    for value in raw_label:
        if 32 <= value <= 126:
            label_chars.append(chr(value))
        else:
            label_chars.append(" ")
    label = "".join(label_chars)
    return index, row, label


def run_cli(midi_out, midi_in, demo_delay):
    print_help()
    try:
        while True:
            raw = input("> ").strip()
            if not raw:
                continue
            if raw in ("quit", "exit", "q"):
                break
            if raw in ("help", "h", "?"):
                print_help()
                continue

            parts = raw.split()
            command = parts[0].lower()

            try:
                if command == "fader" and len(parts) == 3:
                    idx = int(parts[1]) - 1
                    percent = int(parts[2])
                    send_pitch_bend(midi_out, idx, percent)
                elif command == "note" and len(parts) == 3:
                    note = int(parts[1])
                    on = parse_on_off(parts[2])
                    send_note(midi_out, note, on)
                elif command == "cc" and len(parts) == 3:
                    control = int(parts[1])
                    value = int(parts[2])
                    send_cc(midi_out, control, value)
                elif command == "sweep" and len(parts) == 6:
                    idx = int(parts[1]) - 1
                    start = int(parts[2])
                    end = int(parts[3])
                    step = int(parts[4])
                    delay_ms = int(parts[5])
                    if step == 0:
                        raise ValueError("step must be non-zero.")
                    step_dir = 1 if end >= start else -1
                    step = abs(step) * step_dir
                    for value in range(start, end + step, step):
                        send_pitch_bend(midi_out, idx, max(0, min(100, value)))
                        time.sleep(delay_ms / 1000.0)
                elif command == "demo":
                    run_demo(midi_out, demo_delay)
                else:
                    print("Unknown command or wrong arguments.")
                    print_help()
            except ValueError as exc:
                print(f"Error: {exc}")
    except KeyboardInterrupt:
        print("Stopping...")
    finally:
        if midi_in:
            midi_in.close_port()
        midi_out.close_port()


def run_gui(midi_out, midi_in):
    root = tk.Tk()
    root.title("X-Touch Extender Simulator")

    scribble_vars_top = []
    scribble_vars_bottom = []
    fader_vars = []
    fader_programmatic = [False] * 8
    event_queue = queue.Queue()
    button_widgets = {}
    button_led_state = {}
    button_pressed_state = {}
    knob_widgets = {}
    knob_value_vars = {}
    knob_value_cache = {}

    scribble_bg = "#1f1f1f"
    scribble_fg = "#f0f0f0"

    main_frame = ttk.Frame(root, padding=10)
    main_frame.pack(fill="both", expand=True)

    strips_frame = ttk.Frame(main_frame)
    strips_frame.pack(side="top", fill="x")

    knob_min_angle = -135
    knob_max_angle = 135
    knob_size = 54
    knob_radius = knob_size // 2 - 4

    def _value_to_angle(value):
        return knob_min_angle + (value / 127.0) * (knob_max_angle - knob_min_angle)

    def _angle_to_value(angle):
        angle = max(knob_min_angle, min(knob_max_angle, angle))
        percent = (angle - knob_min_angle) / (knob_max_angle - knob_min_angle)
        return int(round(percent * 127))

    def _draw_knob_pointer(canvas, pointer_id, value):
        angle_deg = _value_to_angle(value)
        angle_rad = math.radians(angle_deg)
        center = knob_size / 2
        end_x = center + knob_radius * math.cos(angle_rad)
        end_y = center + knob_radius * math.sin(angle_rad)
        canvas.coords(pointer_id, center, center, end_x, end_y)

    def _set_knob_value(knob_index, value, send=True):
        value = max(0, min(127, value))
        if value == knob_value_cache.get(knob_index, 0):
            return
        knob_value_cache[knob_index] = value
        knob_value_vars[knob_index].set(value)
        knob = knob_widgets.get(knob_index)
        if knob:
            _draw_knob_pointer(knob["canvas"], knob["pointer"], value)
        if send:
            send_cc(midi_out, 16 + knob_index, value)

    def _make_knob_handler(knob_index):
        def _on_knob(event):
            center = knob_size / 2
            dx = event.x - center
            dy = event.y - center
            if dx == 0 and dy == 0:
                return
            angle = math.degrees(math.atan2(dy, dx))
            value = _angle_to_value(angle)
            _set_knob_value(knob_index, value, send=True)

        return _on_knob

    for idx in range(8):
        column = ttk.Frame(strips_frame, padding=(4, 2))
        column.pack(side="left", fill="y", expand=True)

        def _make_note_handler(note_number, pressed):
            def _handler(_event):
                if pressed:
                    button_pressed_state[note_number] = True
                else:
                    button_pressed_state[note_number] = False
                send_note(midi_out, note_number, pressed)
                update_button_color(note_number)

            return _handler

        def _add_button(parent, note_number, label, width=8):
            btn = tk.Button(
                parent,
                text=label,
                width=width,
                bg=LED_OFF_COLOR,
                fg="white",
                relief="raised",
                activebackground="#4a4a4a",
            )
            btn.pack(side="top", pady=2, fill="x")
            btn.bind("<ButtonPress-1>", _make_note_handler(note_number, True))
            btn.bind("<ButtonRelease-1>", _make_note_handler(note_number, False))
            button_widgets[note_number] = btn
            button_led_state[note_number] = False
            button_pressed_state[note_number] = False

        rotary_push_label = ttk.Label(column, text=f"P{idx+1}")
        rotary_push_label.pack(side="top")
        _add_button(column, idx + 32, f"PUSH {idx+1}", width=10)

        knob_label = ttk.Label(column, text=f"K{idx+1}")
        knob_label.pack(side="top")

        knob_var = tk.IntVar(value=0)
        knob_value_vars[idx] = knob_var
        knob_value_cache[idx] = 0

        knob_canvas = tk.Canvas(
            column,
            width=knob_size,
            height=knob_size,
            highlightthickness=0,
            bg=root.cget("bg"),
        )
        knob_canvas.pack(side="top", pady=(2, 4))
        center = knob_size / 2
        knob_canvas.create_oval(
            center - knob_radius,
            center - knob_radius,
            center + knob_radius,
            center + knob_radius,
            outline="#5c5c5c",
            width=2,
        )
        pointer_id = knob_canvas.create_line(
            center,
            center,
            center,
            center - knob_radius,
            fill="#1abc9c",
            width=3,
            capstyle="round",
        )
        knob_widgets[idx] = {"canvas": knob_canvas, "pointer": pointer_id}
        _draw_knob_pointer(knob_canvas, pointer_id, 0)

        knob_canvas.bind("<Button-1>", _make_knob_handler(idx))
        knob_canvas.bind("<B1-Motion>", _make_knob_handler(idx))

        knob_value_label = ttk.Label(column, textvariable=knob_var)
        knob_value_label.pack(side="top")

        scribble_top_var = tk.StringVar(value="       ")
        scribble_bottom_var = tk.StringVar(value="       ")
        scribble_vars_top.append(scribble_top_var)
        scribble_vars_bottom.append(scribble_bottom_var)

        scribble_frame = tk.Frame(
            column,
            bg=scribble_bg,
            bd=1,
            relief="sunken",
        )
        scribble_frame.pack(side="top", fill="x", pady=(2, 4))
        scribble_top = tk.Label(
            scribble_frame,
            textvariable=scribble_top_var,
            bg=scribble_bg,
            fg=scribble_fg,
            font=("Courier New", 11),
            anchor="center",
            width=9,
            pady=2,
        )
        scribble_top.pack(side="top", fill="x")
        scribble_bottom = tk.Label(
            scribble_frame,
            textvariable=scribble_bottom_var,
            bg=scribble_bg,
            fg=scribble_fg,
            font=("Courier New", 11),
            anchor="center",
            width=9,
            pady=2,
        )
        scribble_bottom.pack(side="top", fill="x")
        button_column = ttk.Frame(column, padding=(4, 0))
        button_column.pack(side="top", fill="y", expand=False)
        _add_button(button_column, idx + 0, f"OFF {idx+1}")
        _add_button(button_column, idx + 8, f"ON {idx+1}")
        _add_button(button_column, idx + 16, f"FLASH {idx+1}")
        _add_button(button_column, idx + 24, f"CH {idx+1}")

        fader_var = tk.IntVar(value=0)
        fader_vars.append(fader_var)

        def _make_scale_callback(fader_index):
            def _on_scale(value):
                if fader_programmatic[fader_index]:
                    return
                percent = int(float(value))
                send_pitch_bend(midi_out, fader_index, percent)

            return _on_scale

        scale = ttk.Scale(
            column,
            from_=100,
            to=0,
            variable=fader_var,
            command=_make_scale_callback(idx),
            orient="vertical",
        )
        scale.pack(side="top", fill="y", expand=True, padx=4, pady=6)

        ttk.Label(column, text=f"F{idx+1}").pack(side="top")

    log_frame = ttk.Frame(main_frame, padding=(0, 10))
    log_frame.pack(side="bottom", fill="both", expand=False)
    ttk.Label(log_frame, text="MIDI IN").pack(anchor="w")
    log_list = tk.Listbox(log_frame, height=6)
    log_list.pack(fill="x", expand=True)

    def enqueue_message(event, data=None):
        message, _delta = event
        if message:
            event_queue.put(message)

    if midi_in:
        midi_in.set_callback(enqueue_message)

    def update_button_color(note_number):
        widget = button_widgets.get(note_number)
        if not widget:
            return
        led_on = button_led_state.get(note_number, False)
        pressed = button_pressed_state.get(note_number, False)
        if pressed:
            widget.configure(bg="#3498db")
        else:
            widget.configure(bg=LED_ON_COLOR if led_on else LED_OFF_COLOR)

    def update_gui():
        while True:
            try:
                message = event_queue.get_nowait()
            except queue.Empty:
                break

            log_list.insert("end", f"{message}")
            if log_list.size() > 200:
                log_list.delete(0)

            if not message:
                continue

            if message[0] == 0xF0:
                parsed = parse_scribble_message(message)
                if parsed:
                    index, row, label = parsed
                    if row == 0:
                        scribble_vars_top[index].set(label.ljust(SCRIBBLE_LEN))
                    else:
                        scribble_vars_bottom[index].set(label.ljust(SCRIBBLE_LEN))
                continue

            status = message[0] & 0xF0
            if status == MIDI_PITCH_BEND and len(message) >= 3:
                fader_index = message[0] - MIDI_PITCH_BEND
                value = (message[2] << 7) | message[1]
                percent = int(round(value * 100 / 16383))
                if 0 <= fader_index < 8:
                    fader_programmatic[fader_index] = True
                    fader_vars[fader_index].set(percent)
                    fader_programmatic[fader_index] = False
            elif status == MIDI_NOTE and len(message) >= 3:
                note = message[1]
                value = message[2]
                if note in button_widgets:
                    button_led_state[note] = value > 0
                    update_button_color(note)
            elif status == MIDI_CC and len(message) >= 3:
                control = message[1]
                value = message[2]
                knob_index = control - 16
                if 0 <= knob_index < 8 and knob_index in knob_value_vars:
                    _set_knob_value(knob_index, value, send=False)

        root.after(50, update_gui)

    root.after(50, update_gui)

    try:
        root.mainloop()
    finally:
        if midi_in:
            midi_in.close_port()
        midi_out.close_port()


def main():
    parser = argparse.ArgumentParser(
        description="X-Touch Extender simulator for omniconsole.py"
    )
    parser.add_argument(
        "--list-ports",
        action="store_true",
        help="List available MIDI input/output ports and exit.",
    )
    parser.add_argument(
        "--cli",
        action="store_true",
        help="Run in command-line mode instead of GUI.",
    )
    parser.add_argument(
        "--out-port",
        default=DEFAULT_OUT_PORT_MATCH,
        help="Substring to match the MIDI output port name.",
    )
    parser.add_argument(
        "--out-port-index",
        type=int,
        default=None,
        help="Open MIDI output port by index.",
    )
    parser.add_argument(
        "--demo",
        action="store_true",
        help="Run a short demo sequence and exit.",
    )
    parser.add_argument(
        "--demo-delay",
        type=float,
        default=0.25,
        help="Delay between demo steps in seconds.",
    )
    parser.add_argument(
        "--trace",
        action="store_true",
        help="Print every MIDI message sent.",
    )
    args = parser.parse_args()

    if args.list_ports:
        tmp_out = rtmidi.MidiOut()
        tmp_in = rtmidi.MidiIn()
        print("MIDI OUT ports:")
        for idx, name in enumerate(tmp_out.get_ports()):
            print(f"  [{idx}] {name}")
        print("MIDI IN ports:")
        for idx, name in enumerate(tmp_in.get_ports()):
            print(f"  [{idx}] {name}")
        return

    global TRACE
    TRACE = args.trace

    midi_out, out_name = open_midi_out(args.out_port, args.out_port_index)
    print(f'Connected MIDI OUT: "{out_name}"')

    midi_in = None
    if not args.demo:
        midi_in, in_name = open_midi_in()
        print(f'Connected MIDI IN: "{in_name}"')

    if midi_in and not args.demo:
        midi_in.ignore_types(sysex=False, timing=False, active_sense=False)

    if args.demo:
        run_demo(midi_out, args.demo_delay)
        midi_out.close_port()
        return

    if args.cli:
        run_cli(midi_out, midi_in, args.demo_delay)
    else:
        run_gui(midi_out, midi_in)


if __name__ == "__main__":
    main()
