import os
import time
import threading
import queue
import json
import re
import functools
import socket
import struct
import tkinter as tk
from tkinter import ttk, scrolledtext, messagebox

import rtmidi
import rtmidi2
from gma2telnet import GrandMA2Telnet

CONFIG_FILE = "omniconsole_config.json"

DEFAULT_CONFIG = {
    "gma2": {
        "host": "127.0.0.1",
        "port": 30000,
        "user": "Administrator",
        "password": ""
    },
    "xtouch_rotary_page1": {
        "32": "Fixture 101 thru 199",
        "33": "Group 10",
        "34": "Group 8",
        "35": "Group 2",
        "36": "Group 3",
        "37": "",
        "38": "Group 1",
        "39": "Group 6"
    },
    "xtouch_rotary_page2": {
        "32": "",
        "33": "",
        "34": "Group 9",
        "35": "Group 4",
        "36": "Group 3",
        "37": "",
        "38": "Group 1",
        "39": "Group 6"
    },
    "xtouch_buttons_page1": {},
    "xtouch_buttons_page2": {},
    "xtouch_buttons_page3": {},
    "xtouch_buttons_page4": {},
    "arduino_cc_mapping": {
        "7": "1.15",
        "8": "1.16"
    }
}

# --- MIDI Constants ---
MAX_EXEC_PAGE = 4
MAX_BUTTON_PAGE = MAX_EXEC_PAGE

MIDI_PITCH_BEND = 0xE0
MIDI_NOTE = 0x90
MIDI_CC = 0xB0

SCRIBBLE_COLOR_SYSEX_CMD = 0x14
SCRIBBLE_COLOR_MODE = "both"
SCRIBBLE_COLORS = [4] * 8
PAGE_CHANGE_DEBOUNCE = 0.15

# Thread-safe logging
log_queue = queue.Queue()

def log(msg, category="telnet"):
    print(f"[{category.upper()}] {msg}")
    log_queue.put((category, msg))

def log_activity(category="telnet"):
    log_queue.put((category, None))

def log_activity(category="telnet"):
    log_queue.put((category, None))

def _open_rtmidi2_port(midi_obj, port_match, direction_label, category="xtouch"):
    ports = list(midi_obj.ports)
    if not ports:
        log(f"⚠️ No MIDI {direction_label} ports detected.", category)
        return False
    try:
        midi_obj.open_port(port_match)
        log(f"✅ Ouvert {direction_label}: {port_match}", category)
        return True
    except ValueError:
        for name in ports:
            if port_match.lower() in name.lower():
                midi_obj.open_port(name)
                log(f"✅ Ouvert {direction_label}: {name}", category)
                return True
        try:
            matches = list(midi_obj.ports_matching(port_match))
        except AttributeError:
            matches = []
        if matches:
            midi_obj.open_port(matches[0])
            log(f"✅ Ouvert {direction_label}: {matches[0]}", category)
            return True
    
    available = ", ".join(ports)
    log(f"❌ MIDI {direction_label} port not found: '{port_match}'. Available: {available}", category)
    return False

def open_rtmidi_input(port_match, category="arduino"):
    midi_in = rtmidi.MidiIn()
    ports = midi_in.get_ports()
    if not ports:
        log("⚠️ No MIDI input ports detected (Arduino).", category)
        return None, None
    for index, name in enumerate(ports):
        if port_match.lower() in name.lower():
            midi_in.open_port(index)
            log(f"✅ Ouvert Arduino IN: {name}", category)
            return midi_in, name
    available = ", ".join(ports)
    log(f"❌ Arduino MIDI port not found: '{port_match}'. Available: {available}", category)
    return None, None


class OmniconsoleLogic:
    def __init__(self, gma2, config):
        self.gma2 = gma2
        self.config = config
        self.dmx_data = bytearray(512)
        self.artnet_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.artnet_socket.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        
        self.state_lock = threading.Lock()
        
        self.currentFaderValueList = [[0]*8 for _ in range(4)]
        self.currentFaderLSBList = [0]*8
        self.currentFaderMSBList = [0]*8
        self.FaderUpdateReceivedList = [None]*8
        
        self.active_timer_list = [None]*8
        
        self.pendingFaderPage = None
        self.pendingButtonPage = None
        self.pendingFaderPageAt = None
        self.pendingButtonPageAt = None
        self.currentFaderPage = 1
        self.currentButtonPage = 1
        
        self.gobo = 0
        self.prism = 0

        # XTouch States
        self.flash_requires_zero = [[False]*8 for _ in range(4)]
        self.on_off_state = [[None]*8 for _ in range(4)]
        self.on_off_zeroed = [[False]*8 for _ in range(4)]
        self.flash_zeroed = [[False]*8 for _ in range(4)]
        self.ch_pressed = [False]*8
        self.ch_skip_release_off = [False]*8
        self.ch_latched = [[False]*8 for _ in range(4)]
        
        self.scribble_colors = list(SCRIBBLE_COLORS)
        
        # Note queue for XTouch
        self._note_state = {}
        self._note_queue = queue.Queue()
        self._note_sender = threading.Thread(target=self._note_sender_loop, daemon=True)
        self._note_sender.start()
        
        # MIDI Ports
        self.midi_out = rtmidi2.MidiOut()
        self.midi_out_SD = rtmidi2.MidiOut()
        self.ma2_midi_out = rtmidi2.MidiOut()
        self.midiReceiveXtouch = rtmidi2.MidiIn()
        self.midiReceiveStreamdeck = rtmidi2.MidiIn()
        
        self.arduino_midi_in = None
        self.arduino_last_values = {}

        self.running = False
        self.main_loop_thread = None

    def start(self, test_mode=False):
        xtouch_in_port = "Springbeats vMIDI8" if test_mode else "Springbeats vMIDI5*"
        xtouch_out_port = "Springbeats vMIDI7" if test_mode else "Springbeats vMIDI4*" 
        
        _open_rtmidi2_port(self.midi_out, xtouch_out_port, "XTouch OUT", "xtouch")
        _open_rtmidi2_port(self.midi_out_SD, "Springbeats vMIDI3*", "StreamDeck OUT", "xtouch")
        
        midi_out_enabled = self.config.get("gma2", {}).get("midi_out_enabled", False)
        if midi_out_enabled:
            midi_port = self.config.get("gma2", {}).get("midi_out_port", "Springbeats vMIDI6")
            _open_rtmidi2_port(self.ma2_midi_out, midi_port, "MA2 MIDI OUT", "xtouch")
        
        if _open_rtmidi2_port(self.midiReceiveXtouch, xtouch_in_port, "XTouch IN", "xtouch"):
            self.midiReceiveXtouch.callback = self.midi_callback_xtouch
            
        if _open_rtmidi2_port(self.midiReceiveStreamdeck, "Springbeats vMIDI2*", "StreamDeck IN", "xtouch"):
            self.midiReceiveStreamdeck.callback = self.midi_callback_streamdeck
            
        # Arduino (midifader2grandma integration)
        arduino_port = "Arduino Leonardo"
        self.arduino_midi_in, _ = open_rtmidi_input(arduino_port)
        if self.arduino_midi_in:
            self.arduino_midi_in.set_callback(self.arduino_callback)
            try:
                self.arduino_midi_in.ignore_types(sysex=False, timing=False, active_sense=False)
            except TypeError:
                self.arduino_midi_in.ignore_types(sysex=False, timing=False)

        self.running = True
        self.main_loop_thread = threading.Thread(target=self.process_queues_loop, daemon=True)
        self.main_loop_thread.start()
        
        self.init_console_state()

    def stop(self):
        self.running = False
        if self.arduino_midi_in:
            self.arduino_midi_in.close_port()
        try:
            self.midiReceiveXtouch.close_port()
            self.midiReceiveStreamdeck.close_port()
            self.midi_out.close_port()
            self.midi_out_SD.close_port()
        except:
            pass

    def init_console_state(self):
        if not self.gma2.socket:
            return
        
        self.gma2.send_command("FaderPage 1")
        self.gma2.send_command("ButtonPage 1")
        time.sleep(0.2)
        self.gma2.updateFaderLabels(self, self.currentFaderPage)
        self.gma2.updateButtonLabels(self, self.currentButtonPage)
        for page in range(4):
            for i in range(8):
                self.gma2.send_command(f"Fader {page}.{i+1} At 0")
        
        for i in range(8):
            self._send_xtouch_fader(i, 0, 0)
            time.sleep(0.02)
        self.apply_on_off_leds_for_current_page()
        self.apply_flash_leds_for_current_page()

        # Init stream deck page 1
        self.midi_out_SD.send_raw(*[0xB0, 127, 1])

    # -------------------------------------------------------------
    # UI Commands & XTouch helpers
    # -------------------------------------------------------------
    def sendXtouchScribble(self, faderId, label):
        self.sendXtouchScribbleColor(faderId, self._get_scribble_color(faderId))
        msg = [0xF0,0,0,0x66,0x15,0x12,faderId*7] + [ord(c) for c in label[:7].ljust(7)] + [0xF7]
        self.midi_out.send_raw(*msg)

    def sendXtouchScribbleRaw2(self, faderId, label):
        self.sendXtouchScribbleColor(faderId, self._get_scribble_color(faderId))
        msg = [0xF0,0,0,0x66,0x15,0x12,56+faderId*7] + [ord(c) for c in label[:7].ljust(7)] + [0xF7]
        self.midi_out.send_raw(*msg)

    def _get_scribble_color(self, faderId):
        if faderId < 0 or faderId >= len(self.scribble_colors): return None
        return self.scribble_colors[faderId]

    def sendXtouchScribbleColor(self, faderId, color):
        if color is None: return
        self.midi_out.send_raw(*[0xF0, 0, 0, 0x66, 0x15, SCRIBBLE_COLOR_SYSEX_CMD, faderId, int(color) & 0x7F, 0xF7])
        self.midi_out.send_raw(*[0xF0, 0, 0, 0x66, 0x15, SCRIBBLE_COLOR_SYSEX_CMD, faderId * 7, int(color) & 0x7F, 0xF7])

    def _current_page_index(self): return max(0, min(self.currentFaderPage - 1, 3))
    def _current_button_page_index(self): return max(0, min(self.currentButtonPage - 1, 3))

    def _send_xtouch_flash(self, faderId, on): self._enqueue_note(16 + faderId, on)
    def _send_xtouch_led(self, note, on): self._enqueue_note(note, on)

    def _enqueue_note(self, note, on):
        desired = bool(on)
        if self._note_state.get(note) == desired: return
        self._note_state[note] = desired
        self._note_queue.put((note, desired))

    def _note_sender_loop(self):
        while True:
            note, on = self._note_queue.get()
            self.midi_out.send_raw(*[MIDI_NOTE, note, 127 if on else 0])
            time.sleep(0.005)

    def _set_on_off_leds(self, faderId, state, page_index=None):
        if page_index is None: page_index = self._current_page_index()
        if state is None:
            self.on_off_state[page_index][faderId] = None
            self._send_xtouch_led(faderId, False)
            self._send_xtouch_led(8 + faderId, False)
        elif state == "on":
            self.on_off_state[page_index][faderId] = "on"
            self.on_off_zeroed[page_index][faderId] = False
            self._send_xtouch_led(faderId, False)
            self._send_xtouch_led(8 + faderId, True)
            self._send_xtouch_flash(faderId, True)
        elif state == "off":
            self.on_off_state[page_index][faderId] = "off"
            self.on_off_zeroed[page_index][faderId] = False
            self._send_xtouch_led(faderId, True)
            self._send_xtouch_led(8 + faderId, False)
            self._send_xtouch_flash(faderId, False)
        elif state == "auto":
            self.on_off_state[page_index][faderId] = "auto"
            self.on_off_zeroed[page_index][faderId] = False
            self._send_xtouch_led(faderId, False)
            self._send_xtouch_led(8 + faderId, True)
            self._send_xtouch_flash(faderId, True)

    def _update_on_off_from_value(self, faderId, value, page_index=None):
        if page_index is None: page_index = self._current_page_index()
        if value <= 0:
            if self.on_off_state[page_index][faderId] == "off":
                self.on_off_state[page_index][faderId] = None
            self.on_off_zeroed[page_index][faderId] = True
            self.flash_zeroed[page_index][faderId] = True
            self._send_xtouch_led(faderId, False)
            self._send_xtouch_led(8 + faderId, False)
            return
        self.on_off_zeroed[page_index][faderId] = False
        state = self.on_off_state[page_index][faderId]
        if state is None: self._set_on_off_leds(faderId, "auto", page_index)
        else: self._set_on_off_leds(faderId, state, page_index)
        self.flash_zeroed[page_index][faderId] = False

    def apply_on_off_leds_for_current_page(self):
        p = self._current_page_index()
        for f in range(8):
            if self.on_off_zeroed[p][f]:
                self._send_xtouch_led(f, False)
                self._send_xtouch_led(8 + f, False)
            else:
                st = self.on_off_state[p][f]
                if st in ("on", "auto"): self._set_on_off_leds(f, st, p)
                elif st == "off":
                    if self.currentFaderValueList[p][f] > 0: self._set_on_off_leds(f, "off", p)
                    else: self._send_xtouch_led(f, False); self._send_xtouch_led(8 + f, False)
                else:
                    if self.currentFaderValueList[p][f] > 0: self._set_on_off_leds(f, "auto", p)
                    else: self._send_xtouch_led(f, False); self._send_xtouch_led(8 + f, False)

    def _update_flash_from_value(self, faderId, value, page_index=None):
        p = page_index if page_index is not None else self._current_page_index()
        if self.flash_zeroed[p][faderId]: self._send_xtouch_flash(faderId, False)
        else:
            st = self.on_off_state[p][faderId]
            if st in ("on", "auto"): self._send_xtouch_flash(faderId, True)
            elif st == "off": self._send_xtouch_flash(faderId, False)
            else:
                if value <= 0:
                    if self.flash_requires_zero[p][faderId]: self.flash_requires_zero[p][faderId] = False
                    self._send_xtouch_flash(faderId, False)
                else:
                    self._send_xtouch_flash(faderId, not self.flash_requires_zero[p][faderId])

    def apply_flash_leds_for_current_page(self):
        p = self._current_page_index()
        for f in range(8):
            if self.flash_zeroed[p][f]: self._send_xtouch_flash(f, False)
            else:
                st = self.on_off_state[p][f]
                if st in ("on", "auto"): self._send_xtouch_flash(f, True)
                elif st == "off": self._send_xtouch_flash(f, False)
                else:
                    v = self.currentFaderValueList[p][f]
                    self._send_xtouch_flash(f, v > 0 and not self.flash_requires_zero[p][f])

    def apply_ch_leds_for_current_button_page(self):
        p = self._current_button_page_index()
        for ch in range(8): self._send_xtouch_led(24 + ch, self.ch_latched[p][ch])

    def _send_xtouch_fader(self, faderId, lsb, msb, update_flash=True, update_on_off=True):
        self.midi_out.send_raw(*[MIDI_PITCH_BEND + faderId, lsb, msb])
        v = (msb << 7) | lsb
        if update_on_off: self._update_on_off_from_value(faderId, v)
        if update_flash: self._update_flash_from_value(faderId, v)

    def ack_fader_midi_message(self, faderId):
        with self.state_lock:
            self.active_timer_list[faderId] = None
            lsb = self.currentFaderLSBList[faderId]
            msb = self.currentFaderMSBList[faderId]
        self._send_xtouch_fader(faderId, lsb, msb, update_flash=False, update_on_off=False)

    # -------------------------------------------------------------
    # Callbacks
    # -------------------------------------------------------------
    def midi_callback_xtouch(self, message, data=None):
        log_activity("xtouch")
        cmd = message[0] & 0xF0
        if cmd == MIDI_PITCH_BEND:
            faderId = message[0] - MIDI_PITCH_BEND
            cv = message[2] * 128 + message[1]
            pct = int((cv / 16383) * 100)
            with self.state_lock:
                p = self.currentFaderPage - 1
                prev = self.currentFaderValueList[p][faderId]
                self.currentFaderValueList[p][faderId] = pct
                self.currentFaderLSBList[faderId] = message[1]
                self.currentFaderMSBList[faderId] = message[2]
                self.FaderUpdateReceivedList[faderId] = p
            
            # Send to MA2 or Art-Net or MIDI
            artnet_enabled = self.config.get("gma2", {}).get("artnet_enabled", False)
            midi_out_enabled = self.config.get("gma2", {}).get("midi_out_enabled", False)
            
            if artnet_enabled:
                universe = self.config.get("gma2", {}).get("artnet_universe", 0)
                artnet_ip = self.config.get("gma2", {}).get("artnet_ip", "255.255.255.255")
                dmx_val = int((cv / 16383.0) * 255)
                self.dmx_data[faderId] = dmx_val
                packet = b'Art-Net\x00' + struct.pack('<H', 0x5000) + struct.pack('>H', 14) + b'\x00\x00' + struct.pack('<H', universe) + struct.pack('>H', 512) + self.dmx_data
                try:
                    self.artnet_socket.sendto(packet, (artnet_ip, 6454))
                except Exception:
                    pass
            elif midi_out_enabled:
                val_7b = int((cv / 16383.0) * 127)
                note_num = faderId + 1  # Notes 1-8
                try:
                    self.ma2_midi_out.send_raw(*[0x90, note_num, val_7b])
                except Exception:
                    pass
            else:
                self.gma2.send_command(f"Exec {self.currentFaderPage}.{faderId+1} At {pct}")
            
            self._update_on_off_from_value(faderId, cv, p)
            if pct <= 0 and prev > 0: self.flash_zeroed[p][faderId] = True
            elif pct > 0: self.flash_zeroed[p][faderId] = False

        elif cmd == MIDI_NOTE:
            note = message[1]
            v = message[2]
            p = self._current_page_index()
            bp = self._current_button_page_index()
            
            # --- Dynamic Button mappings (Notes 0-31) ---
            if note < 32:
                row = note // 8
                faderId = note % 8
                
                page_for_config = self.currentButtonPage if row == 3 else self.currentFaderPage
                mapping_dict = self.config.get(f"xtouch_buttons_page{page_for_config}", {})
                
                if row == 0: default_cmd = "Off"
                elif row == 1: default_cmd = "On"
                elif row == 2: default_cmd = "Go"
                else: default_cmd = "Go"
                
                custom_cmd = mapping_dict.get(str(note), default_cmd)
                exec_num = faderId + 101 if row == 3 else faderId + 1
                
                if v > 0:
                    if custom_cmd in ["Flash", "Temp", "Go", "On", "Off", "Toggle", "Swop"]:
                        self.gma2.send_command(f"{custom_cmd} {page_for_config}.{exec_num}")
                    else:
                        self.gma2.send_command(custom_cmd)
                    
                    if custom_cmd == "Swop":
                        # Simulate Swop behavior on XTouch LEDs
                        for i in range(32):
                            if i != note:
                                self._send_xtouch_led(i, False)
                        self._send_xtouch_led(note, True)
                else:
                    if custom_cmd in ["Flash", "Temp", "Swop"]:
                        self.gma2.send_command(f"Off {page_for_config}.{exec_num}")

                # Internal LED tracking logic restored from original script
                if row == 0:
                    if v > 0:
                        self.flash_requires_zero[p][faderId] = True
                        self._send_xtouch_flash(faderId, False)
                        self._set_on_off_leds(faderId, "off")
                elif row == 1:
                    if v > 0:
                        self._set_on_off_leds(faderId, "on")
                elif row == 2:
                    st = self.on_off_state[p][faderId]
                    is_tmp = st is None or st == "off"
                    if v > 0:
                        if is_tmp: self._send_xtouch_flash(faderId, True)
                    else:
                        if is_tmp: self._send_xtouch_flash(faderId, False)
                elif row == 3:
                    ch = faderId
                    if custom_cmd in ["Go", "Toggle"]:
                        if v > 0:
                            self.ch_pressed[ch] = True
                            if self.ch_latched[bp][ch]:
                                self.ch_latched[bp][ch] = False
                                self.ch_skip_release_off[ch] = True
                                self._send_xtouch_led(note, False)
                            else:
                                self.ch_latched[bp][ch] = True
                                self._send_xtouch_led(note, True)
                        else:
                            self.ch_pressed[ch] = False
                            if self.ch_skip_release_off[ch]:
                                self.ch_skip_release_off[ch] = False
                            elif not self.ch_latched[bp][ch]:
                                self._send_xtouch_led(note, False)
                    else:
                        # Comportement momentané pour Flash, Temp, On, Off, etc.
                        if v > 0:
                            self._send_xtouch_led(note, True)
                        else:
                            self._send_xtouch_led(note, False)
                
                # Restore all LEDs after Swop release
                if custom_cmd == "Swop" and v == 0:
                    self.apply_on_off_leds_for_current_page()
                    self.apply_flash_leds_for_current_page()
                    self.apply_ch_leds_for_current_button_page()
                    
                return
            
            # Rotaries
            if 32 <= note < 40:
                if v > 0:
                    self.gma2.send_command("clear")
                    key = str(note)
                    mapping_dict = self.config.get(f"xtouch_rotary_page{self.currentFaderPage}", {})
                    cmd_str = mapping_dict.get(key, "")
                    if cmd_str:
                        self.gma2.send_command(cmd_str)

        elif cmd == MIDI_CC:
            c = message[1]
            v = message[2]
            if c == 16:
                self.gma2.send_command(f"Attribute \"Pan\" At ++{v}" if v < 64 else f"Attribute \"Pan\" At --{v-64}")
            elif c == 17:
                self.gma2.send_command(f"Attribute \"Tilt\" At ++{v}" if v < 64 else f"Attribute \"Tilt\" At --{v-64}")
            elif c == 20:
                self.gma2.send_command(f"Attribute \"ZOOM\" At ++{v}" if v < 64 else f"Attribute \"ZOOM\" At --{v-64}")
            elif c == 22:
                self.gobo = min(100, self.gobo + v) if v < 64 else max(0, self.gobo - (v-64))
                self.gma2.send_command("clear")
                self.gma2.send_command("fixture 301 thru 306")
                self.gma2.send_command(f"Attribute \"GOBO1\" At {self.gobo}")
            elif c == 23:
                self.prism = min(100, self.prism + v) if v < 64 else max(40, self.prism - (v-64))
                self.gma2.send_command("clear")
                self.gma2.send_command("fixture 301 thru 306")
                self.gma2.send_command(f"Attribute \"PRISMA1\" At {self.prism}")

    def midi_callback_streamdeck(self, message, data=None):
        if message[1] == 127:
            with self.state_lock:
                if self.currentFaderPage < MAX_EXEC_PAGE:
                    self.currentFaderPage += 1
                    self.pendingFaderPage = self.currentFaderPage
                    self.pendingFaderPageAt = time.time()
        elif message[1] == 126:
            with self.state_lock:
                if self.currentFaderPage > 1:
                    self.currentFaderPage -= 1
                    self.pendingFaderPage = self.currentFaderPage
                    self.pendingFaderPageAt = time.time()
        elif message[1] == 117:
            with self.state_lock:
                if self.currentButtonPage < MAX_BUTTON_PAGE:
                    self.currentButtonPage += 1
                    self.pendingButtonPage = self.currentButtonPage
                    self.pendingButtonPageAt = time.time()
        elif message[1] == 116:
            with self.state_lock:
                if self.currentButtonPage > 1:
                    self.currentButtonPage -= 1
                    self.pendingButtonPage = self.currentButtonPage
                    self.pendingButtonPageAt = time.time()

    def arduino_callback(self, event, data=None):
        log_activity("arduino")
        msg, _ = event
        if not msg: return
        if (msg[0] & 0xF0) == MIDI_CC and len(msg) >= 3:
            cc = str(msg[1])
            v = msg[2]
            executor = self.config.get("arduino_cc_mapping", {}).get(cc)
            if executor:
                pct = int(round(v * 100 / 127))
                if self.arduino_last_values.get(cc) != pct:
                    self.arduino_last_values[cc] = pct
                    self.gma2.send_command(f"Fader {executor} At {pct}")

    def process_queues_loop(self):
        while self.running:
            now = time.time()
            with self.state_lock:
                _pBtn = self.pendingButtonPage
                _pBtnAt = self.pendingButtonPageAt
                _pFdr = self.pendingFaderPage
                _pFdrAt = self.pendingFaderPageAt

            if _pBtn is not None and (_pBtnAt is None or (now - _pBtnAt) >= PAGE_CHANGE_DEBOUNCE):
                with self.state_lock:
                    pg = self.pendingButtonPage
                    self.pendingButtonPage = None
                    self.pendingButtonPageAt = None
                if pg:
                    self.midi_out_SD.send_raw(*[0xB0, 117, pg])
                    self.gma2.send_command(f"ButtonPage {pg}")
                    time.sleep(0.05)
                    self.gma2.updateButtonLabels(self, pg)
                    self.apply_ch_leds_for_current_button_page()

            if _pFdr is not None and (_pFdrAt is None or (now - _pFdrAt) >= PAGE_CHANGE_DEBOUNCE):
                with self.state_lock:
                    pg = self.pendingFaderPage
                    self.pendingFaderPage = None
                    self.pendingFaderPageAt = None
                if pg:
                    self.midi_out_SD.send_raw(*[0xB0, 127, pg])
                    self.gma2.send_command(f"FaderPage {pg}")
                    time.sleep(0.02)
                    self.gma2.updateFaderLabels(self, pg)
                    with self.state_lock:
                        fvals = [self.currentFaderValueList[pg-1][i] for i in range(8)]
                    for i in range(8):
                        v14 = int(fvals[i] * 16383 / 100)
                        self._send_xtouch_fader(i, v14 & 0x7F, (v14 >> 7) & 0x7F, False, False)
                        time.sleep(0.01)
                    self.apply_on_off_leds_for_current_page()
                    self.apply_flash_leds_for_current_page()

            for i in range(8):
                with self.state_lock:
                    upidx = self.FaderUpdateReceivedList[i]
                    if upidx is not None:
                        self.FaderUpdateReceivedList[i] = None
                        cv = (self.currentFaderMSBList[i] << 7) | self.currentFaderLSBList[i]
                        pct = self.currentFaderValueList[upidx][i]
                        cfp = self.currentFaderPage
                    else:
                        continue
                if upidx == (cfp - 1):
                    self._update_flash_from_value(i, cv, upidx)
                # Art-Net sends directly in callback, so we only need to sync if not in Art-Net mode
                if not self.config.get("gma2", {}).get("artnet_enabled", False):
                    self.gma2.send_command(f"Fader {upidx + 1}.{i + 1} At {pct}")
                with self.state_lock:
                    if self.active_timer_list[i]: self.active_timer_list[i].cancel()
                    self.active_timer_list[i] = threading.Timer(0.5, functools.partial(self.ack_fader_midi_message, i))
                    self.active_timer_list[i].start()
            time.sleep(0.02)


class ConfigManager:
    @staticmethod
    def load():
        if os.path.exists(CONFIG_FILE):
            try:
                with open(CONFIG_FILE, 'r') as f:
                    cfg = json.load(f)
                    # merge with default to ensure keys
                    for k, v in DEFAULT_CONFIG.items():
                        if k not in cfg:
                            cfg[k] = v
                    return cfg
            except Exception as e:
                log(f"⚠️ Erreur chargement config: {e}. Utilisation défauts.", "telnet")
        return dict(DEFAULT_CONFIG)

    @staticmethod
    def save(cfg):
        try:
            with open(CONFIG_FILE, 'w') as f:
                json.dump(cfg, f, indent=4)
                log("✅ Configuration sauvegardée.", "telnet")
        except Exception as e:
            log(f"❌ Erreur sauvegarde config: {e}", "telnet")


class OmniconsoleApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Omniconsole & FaderWing GUI")
        self.geometry("900x700")
        
        self.config = ConfigManager.load()
        self.gma2 = GrandMA2Telnet(
            host=self.config["gma2"]["host"],
            port=self.config["gma2"]["port"],
            user=self.config["gma2"]["user"],
            password=self.config["gma2"]["password"],
            logger=lambda msg: log(msg, "telnet")
        )
        original_send = self.gma2.send_command
        def tracked_send(cmd, *args, **kwargs):
            log_activity("telnet")
            return original_send(cmd, *args, **kwargs)
        self.gma2.send_command = tracked_send
        
        self.logic = OmniconsoleLogic(self.gma2, self.config)
        
        self.create_widgets()
        
        # Periodic log update
        self.after(100, self.update_logs)
        
        self.protocol("WM_DELETE_WINDOW", self.on_close)
        
        # Auto-connect on startup
        self.after(500, self.connect_all)

    def create_widgets(self):
        tab_control = ttk.Notebook(self)
        
        self.tab_main = ttk.Frame(tab_control)
        self.tab_config = ttk.Frame(tab_control)
        
        tab_control.add(self.tab_main, text="Console & Logs")
        tab_control.add(self.tab_config, text="Configuration & Mapping")
        tab_control.pack(expand=1, fill="both")
        
        # --- Main Tab ---
        top_frame = ttk.Frame(self.tab_main)
        top_frame.pack(fill="x", padx=10, pady=10)
        
        ttk.Button(top_frame, text="Rafraîchir Labels MA2", command=self.fetch_labels).pack(side="left", padx=5)
        ttk.Button(top_frame, text="Sauvegarder Config", command=self.save_config).pack(side="right", padx=5)
        
        log_frame = ttk.Frame(self.tab_main)
        log_frame.pack(fill="both", expand=True, padx=10, pady=10)
        
        self.log_texts = {}
        self.activity_counters = {"xtouch": 0, "arduino": 0, "telnet": 0}
        for idx, (cat, title) in enumerate([("xtouch", "Logs X-Touch & StreamDeck"), 
                                            ("arduino", "Logs Fader Wing (Arduino)"), 
                                            ("telnet", "Logs GrandMA2 Telnet")]):
            lf = ttk.LabelFrame(log_frame, text=title)
            lf.pack(side="left", fill="both", expand=True, padx=2)
            st = scrolledtext.ScrolledText(lf, state='disabled', bg="black", fg="lime", width=25)
            st.pack(fill="both", expand=True)
            self.log_texts[cat] = st

        # --- Config Tab ---
        notebook_cfg = ttk.Notebook(self.tab_config)
        notebook_cfg.pack(expand=1, fill="both", padx=10, pady=10)
        
        frame_gma = ttk.Frame(notebook_cfg)
        frame_xpage1 = ttk.Frame(notebook_cfg)
        frame_xpage2 = ttk.Frame(notebook_cfg)
        self.frame_xbtn = ttk.Frame(notebook_cfg)
        frame_arduino = ttk.Frame(notebook_cfg)
        
        notebook_cfg.add(frame_gma, text="Réseau MA2")
        notebook_cfg.add(frame_xpage1, text="X-Touch Rotary P1")
        notebook_cfg.add(frame_xpage2, text="X-Touch Rotary P2")
        notebook_cfg.add(self.frame_xbtn, text="X-Touch Boutons")
        notebook_cfg.add(frame_arduino, text="Arduino CC")

        # MA2 Network
        self.var_host = tk.StringVar(value=self.config["gma2"]["host"])
        self.var_port = tk.StringVar(value=str(self.config["gma2"]["port"]))
        self.var_user = tk.StringVar(value=self.config["gma2"]["user"])
        self.var_pwd = tk.StringVar(value=self.config["gma2"]["password"])
        
        ttk.Label(frame_gma, text="Hôte (IP):").grid(row=0, column=0, padx=5, pady=5)
        ttk.Entry(frame_gma, textvariable=self.var_host).grid(row=0, column=1)
        ttk.Label(frame_gma, text="Port:").grid(row=1, column=0, padx=5, pady=5)
        ttk.Entry(frame_gma, textvariable=self.var_port).grid(row=1, column=1)
        ttk.Label(frame_gma, text="User:").grid(row=2, column=0, padx=5, pady=5)
        ttk.Entry(frame_gma, textvariable=self.var_user).grid(row=2, column=1)
        ttk.Label(frame_gma, text="Mot de passe :").grid(row=3, column=0, pady=5)
        ttk.Entry(frame_gma, textvariable=self.var_pwd).grid(row=3, column=1)

        # Art-Net Options
        self.var_artnet_enabled = tk.BooleanVar(value=self.config.get("gma2", {}).get("artnet_enabled", False))
        ttk.Checkbutton(frame_gma, text="Activer l'envoi Faders via Art-Net (DMX 1-8)", variable=self.var_artnet_enabled).grid(row=4, column=0, columnspan=2, pady=5)
        
        ttk.Label(frame_gma, text="Univers Art-Net (0 = MA2 Univ 1) :").grid(row=5, column=0, pady=5)
        self.var_artnet_universe = tk.IntVar(value=self.config.get("gma2", {}).get("artnet_universe", 0))
        ttk.Entry(frame_gma, textvariable=self.var_artnet_universe).grid(row=5, column=1)

        # Test Mode
        self.var_test_mode = tk.BooleanVar(value=self.config.get("test_mode", False))
        ttk.Checkbutton(frame_gma, text="Mode Test (Ports MIDI 7 & 8 pour Simulateur)", variable=self.var_test_mode).grid(row=6, column=0, columnspan=2, pady=5)

        ttk.Label(frame_gma, text="IP Art-Net (Défaut Broadcast) :").grid(row=7, column=0, pady=5)
        self.var_artnet_ip = tk.StringVar(value=self.config.get("gma2", {}).get("artnet_ip", "255.255.255.255"))
        ttk.Entry(frame_gma, textvariable=self.var_artnet_ip).grid(row=7, column=1)

        # MIDI Out Options
        self.var_midi_out_enabled = tk.BooleanVar(value=self.config.get("gma2", {}).get("midi_out_enabled", False))
        ttk.Checkbutton(frame_gma, text="Activer l'envoi Faders via MIDI (Notes 1-8)", variable=self.var_midi_out_enabled).grid(row=8, column=0, columnspan=2, pady=5)
        
        ttk.Label(frame_gma, text="Port MIDI OUT vers MA2 :").grid(row=9, column=0, pady=5)
        self.var_midi_out_port = tk.StringVar(value=self.config.get("gma2", {}).get("midi_out_port", "Springbeats vMIDI6"))
        ttk.Entry(frame_gma, textvariable=self.var_midi_out_port).grid(row=9, column=1)

        # XTouch Pages
        self.xtouch_vars = {"page1": {}, "page2": {}}
        for i, (f_page, p_key) in enumerate([(frame_xpage1, "xtouch_rotary_page1"), (frame_xpage2, "xtouch_rotary_page2")]):
            for row, btn_id in enumerate(range(32, 40)):
                ttk.Label(f_page, text=f"Bouton Rotary {btn_id-31} (Note {btn_id}):").grid(row=row, column=0, padx=5, pady=2, sticky="e")
                var = tk.StringVar(value=self.config[p_key].get(str(btn_id), ""))
                ttk.Entry(f_page, textvariable=var, width=40).grid(row=row, column=1, pady=2)
                self.xtouch_vars[f"page{i+1}"][str(btn_id)] = var

        # XTouch Buttons (Dynamic UI)
        top_ctrl = ttk.Frame(self.frame_xbtn)
        top_ctrl.grid(row=0, column=0, columnspan=9, pady=5)
        
        ttk.Label(top_ctrl, text="Fader Page :").pack(side="left", padx=5)
        self.var_fader_page = tk.StringVar(value="1")
        cb_fp = ttk.Combobox(top_ctrl, textvariable=self.var_fader_page, values=["1","2","3","4"], width=3, state="readonly")
        cb_fp.pack(side="left", padx=5)
        
        ttk.Label(top_ctrl, text="Button Page :").pack(side="left", padx=5)
        self.var_btn_page = tk.StringVar(value="1")
        cb_bp = ttk.Combobox(top_ctrl, textvariable=self.var_btn_page, values=["1","2","3","4"], width=3, state="readonly")
        cb_bp.pack(side="left", padx=5)
        
        cb_fp.bind("<<ComboboxSelected>>", self._refresh_xbtn_ui)
        cb_bp.bind("<<ComboboxSelected>>", self._refresh_xbtn_ui)

        self.fader_labels = []
        self.button_labels = []
        self.xtouch_btn_vars = {} # holds vars for current viewed page
        
        # Headers
        for col in range(8):
            ttk.Label(self.frame_xbtn, text=f"Tr. {col+1}", font=("", 9, "bold")).grid(row=1, column=col+1, padx=2, pady=5)
            
        # Row 2: Fader Labels
        ttk.Label(self.frame_xbtn, text="Label (Fader)", foreground="gray").grid(row=2, column=0, sticky="e", padx=5)
        for col in range(8):
            lbl = ttk.Label(self.frame_xbtn, text="-", foreground="black", width=10, anchor="center")
            lbl.grid(row=2, column=col+1, padx=2, pady=2)
            self.fader_labels.append(lbl)
            
        button_rows = [
            ("Rec (Bouton Haut)", 0),
            ("Solo (Bouton 2)", 8),
            ("Mute (Sous Fader)", 16)
        ]
        
        for row_idx, (label, base_note) in enumerate(button_rows):
            ttk.Label(self.frame_xbtn, text=label, font=("", 9, "bold")).grid(row=row_idx+3, column=0, padx=5, pady=2, sticky="e")
            for col in range(8):
                note = base_note + col
                var = tk.StringVar()
                cb = ttk.Combobox(self.frame_xbtn, textvariable=var, values=["Off", "On", "Go", "Flash", "Temp", "Toggle", "Swop"], width=7)
                cb.grid(row=row_idx+3, column=col+1, padx=2, pady=2)
                var.trace_add("write", lambda *args, n=note, v=var: self._save_btn_var(n, v))
                self.xtouch_btn_vars[str(note)] = var
                
        # Row 6: Button Labels
        ttk.Label(self.frame_xbtn, text="Label (Bouton)", foreground="gray").grid(row=6, column=0, sticky="e", padx=5)
        for col in range(8):
            lbl = ttk.Label(self.frame_xbtn, text="-", foreground="black", width=10, anchor="center")
            lbl.grid(row=6, column=col+1, padx=2, pady=2)
            self.button_labels.append(lbl)
            
        ttk.Label(self.frame_xbtn, text="Select (Bas / 101)", font=("", 9, "bold")).grid(row=7, column=0, padx=5, pady=2, sticky="e")
        for col in range(8):
            note = 24 + col
            var = tk.StringVar()
            cb = ttk.Combobox(self.frame_xbtn, textvariable=var, values=["Off", "On", "Go", "Flash", "Temp", "Toggle", "Swop"], width=7)
            cb.grid(row=7, column=col+1, padx=2, pady=2)
            var.trace_add("write", lambda *args, n=note, v=var: self._save_btn_var(n, v))
            self.xtouch_btn_vars[str(note)] = var
            
        self._refresh_xbtn_ui()

        # Arduino Mappings
        ttk.Label(frame_arduino, text="Mapping CC -> Executor (ex: 7 -> 1.15)").grid(row=0, column=0, columnspan=2, pady=5)
        self.arduino_frame_list = ttk.Frame(frame_arduino)
        self.arduino_frame_list.grid(row=1, column=0, columnspan=2, sticky="nsew")
        
        self.arduino_vars = []
        for cc, exe in self.config.get("arduino_cc_mapping", {}).items():
            self.add_arduino_row(cc, exe)
            
        ttk.Button(frame_arduino, text="+ Ajouter Ligne CC", command=lambda: self.add_arduino_row("", "")).grid(row=2, column=0, pady=10)

    def _save_btn_var(self, note, var):
        row = note // 8
        faderId = note % 8
        pg = int(self.var_btn_page.get()) if row == 3 else int(self.var_fader_page.get())
        val = var.get().strip()
        page_dict = self.config.setdefault(f"xtouch_buttons_page{pg}", {})
        
        if row == 0: default_cmd = "Off"
        elif row == 1: default_cmd = "On"
        elif row == 2: default_cmd = "Go"
        else: default_cmd = "Go"
        
        if val and val != default_cmd:
            page_dict[str(note)] = val
        elif str(note) in page_dict:
            del page_dict[str(note)]

        # Synchronize visually with GrandMA2 OnPC
        if self.gma2.socket:
            cmd_to_assign = val if val else default_cmd
            if cmd_to_assign in ["Off", "On", "Go", "Flash", "Temp", "Toggle", "Swop"]:
                if row == 0:
                    self.gma2.send_command(f"Assign {cmd_to_assign} ExecButton3 {pg}.{faderId+1}")
                elif row == 1:
                    self.gma2.send_command(f"Assign {cmd_to_assign} ExecButton2 {pg}.{faderId+1}")
                elif row == 2:
                    self.gma2.send_command(f"Assign {cmd_to_assign} ExecButton1 {pg}.{faderId+1}")
                elif row == 3:
                    self.gma2.send_command(f"Assign {cmd_to_assign} ExecButton1 {pg}.{faderId+101}")
            
    def _refresh_xbtn_ui(self, event=None):
        fp = int(self.var_fader_page.get())
        bp = int(self.var_btn_page.get())
        
        for i in range(8):
            f_lbl = self.gma2.execIdToName.get((fp, i+1), "-")
            b_lbl = self.gma2.execIdToName.get((bp, i+101), "-")
            self.fader_labels[i].config(text=f_lbl[:10])
            self.button_labels[i].config(text=b_lbl[:10])
            
        f_dict = self.config.get(f"xtouch_buttons_page{fp}", {})
        b_dict = self.config.get(f"xtouch_buttons_page{bp}", {})
        
        for note in range(24):
            if note // 8 == 0: default_cmd = "Off"
            elif note // 8 == 1: default_cmd = "On"
            else: default_cmd = "Go"
            self.xtouch_btn_vars[str(note)].set(f_dict.get(str(note), default_cmd))
            
        for note in range(24, 32):
            self.xtouch_btn_vars[str(note)].set(b_dict.get(str(note), "Go"))

    def add_arduino_row(self, cc_val, exe_val):
        row = len(self.arduino_vars)
        var_cc = tk.StringVar(value=cc_val)
        var_exe = tk.StringVar(value=exe_val)
        ttk.Entry(self.arduino_frame_list, textvariable=var_cc, width=5).grid(row=row, column=0, padx=5, pady=2)
        ttk.Label(self.arduino_frame_list, text="->").grid(row=row, column=1)
        ttk.Entry(self.arduino_frame_list, textvariable=var_exe, width=10).grid(row=row, column=2, padx=5, pady=2)
        self.arduino_vars.append((var_cc, var_exe))

    def fetch_labels(self):
        if not self.gma2.socket:
            messagebox.showwarning("Erreur", "Non connecté à MA2 !")
            return
        threading.Thread(target=self._do_fetch_labels, daemon=True).start()

    def _do_fetch_labels(self):
        log("⏳ Récupération des labels depuis la console MA2...", "telnet")
        self.gma2.fetch_all_labels()
        log("✅ Labels récupérés, mise à jour des écrans...", "telnet")
        self.gma2.updateFaderLabels(self.logic, self.logic.currentFaderPage)
        self.gma2.updateButtonLabels(self.logic, self.logic.currentButtonPage)
        self.after(0, self._refresh_xbtn_ui)

    def save_config(self):
        self.config["gma2"]["host"] = self.var_host.get()
        self.config["gma2"]["port"] = int(self.var_port.get())
        self.config["gma2"]["user"] = self.var_user.get()
        self.config["gma2"]["password"] = self.var_pwd.get()
        self.config["gma2"]["artnet_enabled"] = self.var_artnet_enabled.get()
        self.config["gma2"]["artnet_universe"] = self.var_artnet_universe.get()
        self.config["gma2"]["artnet_ip"] = self.var_artnet_ip.get()
        self.config["gma2"]["midi_out_enabled"] = self.var_midi_out_enabled.get()
        self.config["gma2"]["midi_out_port"] = self.var_midi_out_port.get()
        self.config["test_mode"] = self.var_test_mode.get()
        
        for k, var in self.xtouch_vars["page1"].items():
            self.config["xtouch_rotary_page1"][k] = var.get()
        for k, var in self.xtouch_vars["page2"].items():
            self.config["xtouch_rotary_page2"][k] = var.get()
            
        # Buttons are saved interactively by _save_btn_var
        
        ard_map = {}
        for var_cc, var_exe in self.arduino_vars:
            c, e = var_cc.get().strip(), var_exe.get().strip()
            if c and e: ard_map[c] = e
        self.config["arduino_cc_mapping"] = ard_map
        
        ConfigManager.save(self.config)

    def connect_all(self):
        self.save_config() # Save before applying
        
        # update objects
        self.gma2.host = self.config["gma2"]["host"]
        self.gma2.port = self.config["gma2"]["port"]
        self.gma2.user = self.config["gma2"]["user"]
        self.gma2.password = self.config["gma2"]["password"]
        
        log("🔄 Tentative de connexion MA2...", "telnet")
        threading.Thread(target=self._do_connect, daemon=True).start()

    def _do_connect(self):
        if self.gma2.connect():
            # Start MIDI
            self.logic.stop()
            time.sleep(0.5)
            self.logic.start(test_mode=self.config.get("test_mode", False))
            
            # Auto-fetch labels on startup
            self.fetch_labels()
        else:
            log("❌ Échec connexion MA2.", "telnet")

    def update_logs(self):
        while not log_queue.empty():
            category, msg = log_queue.get()
            st = self.log_texts.get(category)
            if st:
                st.config(state='normal')
                if msg is None: # Indicateur d'activité
                    st.insert('end', "•")
                    self.activity_counters[category] += 1
                    if self.activity_counters[category] >= 50:
                        st.insert('end', "\n")
                        self.activity_counters[category] = 0
                else:
                    if self.activity_counters.get(category, 0) > 0:
                        st.insert('end', "\n")
                        self.activity_counters[category] = 0
                    timestamp = time.strftime("%H:%M:%S")
                    st.insert('end', f"[{timestamp}] {msg}\n")
                
                st.see('end')
                # Nettoyage pour ne pas surcharger la GUI
                lines = int(st.index('end-1c').split('.')[0])
                if lines > 300:
                    st.delete('1.0', f"{lines-300}.0")
                st.config(state='disabled')
        self.after(100, self.update_logs)

    def on_close(self):
        log("Fermeture en cours...", "telnet")
        self.logic.stop()
        self.gma2.close()
        self.destroy()

if __name__ == "__main__":
    app = OmniconsoleApp()
    app.mainloop()
