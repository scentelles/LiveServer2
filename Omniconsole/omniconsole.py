import argparse
import rtmidi2
import rtmidi
from gma2telnet import *


import threading
import functools

MAX_EXEC_PAGE = 4
MAX_BUTTON_PAGE = MAX_EXEC_PAGE

#0xf0:0x7f:0x7f:0x2:0x7f:0xa:0x30:0x2e:0x30:0x30:0x30:0x0:0x31:0x2e:0x31:0xf7:
#F0     7F	7F	02	7F	Command	Data	F7

COMMAND_GO = 1
COMMAND_STOP = 2
COMMAND_RESUME = 3
COMMAND_TIMED_GO = 4
COMMAND_SET = 6
COMMAND_FIRE = 7
COMMAND_STOP = 0xa

MIDI_PITCH_BEND = 0xE0
MIDI_NOTE = 0x90
MIDI_CC = 0xB0

TEST_XTOUCH_IN_PORT = "Springbeats vMIDI8"
TEST_XTOUCH_OUT_PORT = "Springbeats vMIDI7"
SCRIBBLE_COLOR_SYSEX_CMD = 0x14
SCRIBBLE_COLOR_USES_OFFSET = False
SCRIBBLE_COLORS = [4] * 8

def _open_rtmidi2_port(midi_obj, port_match, direction_label):
    ports = list(midi_obj.ports)
    if not ports:
        raise SystemExit(f"No MIDI {direction_label} ports detected.")
    try:
        midi_obj.open_port(port_match)
        return port_match
    except ValueError:
        for name in ports:
            if port_match.lower() in name.lower():
                midi_obj.open_port(name)
                return name
        try:
            matches = list(midi_obj.ports_matching(port_match))
        except AttributeError:
            matches = []
        if matches:
            midi_obj.open_port(matches[0])
            return matches[0]
    available = "\n  ".join(ports)
    raise SystemExit(
        f'MIDI {direction_label} port not found: "{port_match}".\n'
        f"Available ports:\n  {available}"
    )

def gma2_in_callback(msg, timestamp):
    command = msg[5]
    
    if(command == COMMAND_GO):
        commandStr = "GO"
    if(command == COMMAND_STOP):
        commandStr = "STOP"

    index = 6
    thisCue = ""
    thisExec = ""
    if((command == COMMAND_GO) or (command == COMMAND_STOP)):
        while(msg[index] != 0x0):
            thisCue += chr(msg[index])
            index+=1
        while(msg[index] != 0xf7):
            thisExec += chr(msg[index])
            index+=1
        print(commandStr + " | CUE : " + thisCue + " | EXEC : " + thisExec)
        
        
    value = ""
    for i in range(0,len(msg)):
        value += str(hex(msg[i])) + ":"
    print(value) 

    
gma2_in = rtmidi2.MidiIn()
gma2_in.ignore_types(midi_sysex=False)
gma2_in.open_port("Springbeats vMIDI4*")   
gma2_in.callback = gma2_in_callback


    

currentFaderValueList = [[0,0,0,0,0,0,0,0], [0,0,0,0,0,0,0,0], [0,0,0,0,0,0,0,0], [0,0,0,0,0,0,0,0]]
currentFaderLSBList = [0,0,0,0,0,0,0,0]
currentFaderMSBList = [0,0,0,0,0,0,0,0]
FaderUpdateReceivedList = [0,0,0,0,0,0,0,0]

currentMessage = 0
active_timer_list = [None, None, None, None, None, None, None, None]

messagepgup = False
messagepgdown = False
messagebtnup = False
messagebtndown = False
currentFaderPage = 1
currentButtonPage = 1


gobo = 0
prism = 0
class Omniconsole:
    def __init__(self, test_mode=False):
        """ Initialise la connexion midi a xtouch """
        self.midi_in = rtmidi.MidiIn()
        self.port_name = ""
        self.flash_requires_zero = [
            [False] * 8 for _ in range(len(currentFaderValueList))
        ]
        self.on_off_state = [
            [None] * 8 for _ in range(len(currentFaderValueList))
        ]
        self.on_off_zeroed = [
            [False] * 8 for _ in range(len(currentFaderValueList))
        ]
        self.flash_zeroed = [
            [False] * 8 for _ in range(len(currentFaderValueList))
        ]
        self.scribble_colors = list(SCRIBBLE_COLORS) if SCRIBBLE_COLORS else [None] * 8
        if len(self.scribble_colors) < 8:
            self.scribble_colors.extend([None] * (8 - len(self.scribble_colors)))
        elif len(self.scribble_colors) > 8:
            self.scribble_colors = self.scribble_colors[:8]

        xtouch_in_port = TEST_XTOUCH_IN_PORT if test_mode else "OMNICONSOLE*"
        xtouch_out_port = TEST_XTOUCH_OUT_PORT if test_mode else "OMNICONSOLE*"
 
        #XTOUCH Feedback sender
        #https://github.com/NicoG60/TouchMCU/blob/main/doc/mackie_control_protocol.md
        self.midi_out = rtmidi2.MidiOut()
        _open_rtmidi2_port(self.midi_out, xtouch_out_port, "OUT")

        #STREAM DECK Feedback sender
        self.midi_out_SD = rtmidi2.MidiOut()
        self.midi_out_SD.open_port("Springbeats vMIDI3*")
             
        #XTOUCH Receiver
        self.midiReceiveXtouch = rtmidi2.MidiIn()
        _open_rtmidi2_port(self.midiReceiveXtouch, xtouch_in_port, "IN")
        self.midiReceiveXtouch.callback = self.midi_callback_xtouch

        #STREAM DECK Receiver
        self.midiReceiveStreamdeck = rtmidi2.MidiIn()
        self.midiReceiveStreamdeck.open_port("Springbeats vMIDI2*")      
        self.midiReceiveStreamdeck.callback = self.midi_callback_streamdeck




    def sendXtouchScribble(self, faderId, label):
        self.sendXtouchScribbleColor(faderId, self._get_scribble_color(faderId))
        message = [0xF0,00,00,0x66,0x15,0x12,faderId*7,ord(label[0]),ord(label[1]),ord(label[2]),ord(label[3]),ord(label[4]),ord(label[5]),ord(label[6]),0xF7]
        self.midi_out.send_raw(*message)

    def sendXtouchScribbleRaw2(self, faderId, label):
        self.sendXtouchScribbleColor(faderId, self._get_scribble_color(faderId))
        message = [0xF0,00,00,0x66,0x15,0x12,56+faderId*7,ord(label[0]),ord(label[1]),ord(label[2]),ord(label[3]),ord(label[4]),ord(label[5]),ord(label[6]),0xF7]
        self.midi_out.send_raw(*message)

    def _get_scribble_color(self, faderId):
        if faderId < 0 or faderId >= len(self.scribble_colors):
            return None
        return self.scribble_colors[faderId]

    def sendXtouchScribbleColor(self, faderId, color):
        if color is None:
            return
        index_value = faderId * 7 if SCRIBBLE_COLOR_USES_OFFSET else faderId
        message = [0xF0, 0x00, 0x00, 0x66, 0x15, SCRIBBLE_COLOR_SYSEX_CMD, index_value, int(color) & 0x7F, 0xF7]
        self.midi_out.send_raw(*message)

    def _current_page_index(self):
        page_index = currentFaderPage - 1
        if page_index < 0:
            return 0
        if page_index >= len(self.flash_requires_zero):
            return len(self.flash_requires_zero) - 1
        return page_index

    def _send_xtouch_flash(self, faderId, on):
        value = 127 if on else 0
        message = [MIDI_NOTE, 16 + faderId, value]
        self.midi_out.send_raw(*message)

    def _send_xtouch_led(self, note, on):
        value = 127 if on else 0
        message = [MIDI_NOTE, note, value]
        self.midi_out.send_raw(*message)

    def _set_on_off_leds(self, faderId, state, page_index=None):
        if page_index is None:
            page_index = self._current_page_index()

        if state is None:
            self.on_off_state[page_index][faderId] = None
            self._send_xtouch_led(faderId, False)
            self._send_xtouch_led(8 + faderId, False)
            return

        if state == "on":
            self.on_off_state[page_index][faderId] = "on"
            self.on_off_zeroed[page_index][faderId] = False
            self._send_xtouch_led(faderId, False)
            self._send_xtouch_led(8 + faderId, True)
            self._send_xtouch_flash(faderId, True)
            return

        if state == "off":
            self.on_off_state[page_index][faderId] = "off"
            self.on_off_zeroed[page_index][faderId] = False
            self._send_xtouch_led(faderId, True)
            self._send_xtouch_led(8 + faderId, False)
            self._send_xtouch_flash(faderId, False)
            return

        if state == "auto":
            self.on_off_state[page_index][faderId] = "auto"
            self.on_off_zeroed[page_index][faderId] = False
            self._send_xtouch_led(faderId, False)
            self._send_xtouch_led(8 + faderId, True)
            self._send_xtouch_flash(faderId, True)

    def _update_on_off_from_value(self, faderId, value):
        page_index = self._current_page_index()
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
        if state is None:
            self._set_on_off_leds(faderId, "auto", page_index)
        else:
            self._set_on_off_leds(faderId, state, page_index)
        self.flash_zeroed[page_index][faderId] = False

    def apply_on_off_leds_for_current_page(self):
        page_index = self._current_page_index()
        for faderId in range(8):
            if self.on_off_zeroed[page_index][faderId]:
                self._send_xtouch_led(faderId, False)
                self._send_xtouch_led(8 + faderId, False)
                continue
            state = self.on_off_state[page_index][faderId]
            if state == "on":
                self._set_on_off_leds(faderId, "on", page_index)
                continue
            if state == "auto":
                self._set_on_off_leds(faderId, "auto", page_index)
                continue
            value = currentFaderValueList[page_index][faderId]
            if state == "off":
                if value > 0:
                    self._set_on_off_leds(faderId, "off", page_index)
                else:
                    self._send_xtouch_led(faderId, False)
                    self._send_xtouch_led(8 + faderId, False)
                continue
            if value > 0:
                self._set_on_off_leds(faderId, "auto", page_index)
            else:
                self._send_xtouch_led(faderId, False)
                self._send_xtouch_led(8 + faderId, False)

    def _update_flash_from_value(self, faderId, value):
        page_index = self._current_page_index()
        if self.flash_zeroed[page_index][faderId]:
            self._send_xtouch_flash(faderId, False)
            return
        on_state = self.on_off_state[page_index][faderId]
        if on_state == "on":
            self._send_xtouch_flash(faderId, True)
            return
        if on_state == "auto":
            self._send_xtouch_flash(faderId, True)
            return
        if on_state == "off":
            self._send_xtouch_flash(faderId, False)
            return
        if value <= 0:
            self._send_xtouch_flash(faderId, False)
            return
        if self.flash_requires_zero[page_index][faderId]:
            if value <= 0:
                self.flash_requires_zero[page_index][faderId] = False
            self._send_xtouch_flash(faderId, False)
            return
        self._send_xtouch_flash(faderId, value > 0)

    def apply_flash_leds_for_current_page(self):
        page_index = self._current_page_index()
        for faderId in range(8):
            if self.flash_zeroed[page_index][faderId]:
                self._send_xtouch_flash(faderId, False)
                continue
            on_state = self.on_off_state[page_index][faderId]
            if on_state == "on" or on_state == "auto":
                self._send_xtouch_flash(faderId, True)
                continue
            if on_state == "off":
                self._send_xtouch_flash(faderId, False)
                continue
            value = currentFaderValueList[page_index][faderId]
            if value <= 0:
                self._send_xtouch_flash(faderId, False)
                continue
            if self.flash_requires_zero[page_index][faderId]:
                self._send_xtouch_flash(faderId, False)
                continue
            self._send_xtouch_flash(faderId, True)

    def _send_xtouch_fader(self, faderId, lsb, msb, update_flash=True, update_on_off=True):
        message = [MIDI_PITCH_BEND + faderId, lsb, msb]
        self.midi_out.send_raw(*message)
        value = (msb << 7) | lsb
        if update_on_off:
            self._update_on_off_from_value(faderId, value)
        if update_flash:
            self._update_flash_from_value(faderId, value)
        
    def ack_fader_midi_message(self, faderId):
        global currentFaderLSBList, currentFaderMSBList , active_timer
        active_timer_list[faderId] = None

        """Met a jour le faders après 500 ms"""
        message = [224+faderId, currentFaderLSBList[faderId], currentFaderMSBList[faderId]]  
        self._send_xtouch_fader(faderId, currentFaderLSBList[faderId], currentFaderMSBList[faderId])
        print("🎹 Message MIDI envoyé :", message)
        
    def midi_callback_xtouch(self, message, data=None):
        global FaderUpdateReceived, currentFaderValueList, currentFaderLSBList, currentFaderMSBList, FaderUpdateReceivedList, currentFaderPage, currentButtonPage
        global messagebtnup, messagebtndown
        global gma2
        global gobo, prism

        print("Message MIDI reçu :", self.port_name, ":", message)
        midiCommand = message[0] & 0xF0
        
        if(midiCommand == MIDI_PITCH_BEND):
            
            #value = (message[1] * 128) + message[2]  # MSB * 128 + LSB
            changedFader = message[0]-MIDI_PITCH_BEND
            
            value = message[2] * 128 + message[1]
            percentage = int((value / 16383) * 100)
            page_index = currentFaderPage - 1
            prev_percentage = currentFaderValueList[page_index][changedFader]
            currentFaderValueList[page_index][changedFader] = percentage
            
            currentFaderLSBList[changedFader] = message[1]
            currentFaderMSBList[changedFader] = message[2]
            FaderUpdateReceivedList[changedFader] = 1
            self._update_on_off_from_value(changedFader, value)
            if percentage <= 0 and prev_percentage > 0:
                self.flash_zeroed[page_index][changedFader] = True
            elif percentage > 0:
                self.flash_zeroed[page_index][changedFader] = False

        if(midiCommand == MIDI_NOTE):        
            note = message[1]
            value = message[2]
            if(note < 8):
                if(value > 0):
                    page_index = self._current_page_index()
                    current_value = (currentFaderMSBList[note] << 7) | currentFaderLSBList[note]
                    if current_value <= 0:
                        return
                    gma2.send_command("Off " + str(currentFaderPage) + "." + str(note+1)) 
                    self.flash_requires_zero[page_index][note] = True
                    self._send_xtouch_flash(note, False)
                    self._set_on_off_leds(note, "off")
            elif(note < 16):
                if(value > 0):
                    gma2.send_command("On " + str(currentFaderPage) + "." + str(note-8+1)) 
                    self._set_on_off_leds(note - 8, "on")
            elif(note < 24):
                flash_index = note - 16
                page_index = self._current_page_index()
                state = self.on_off_state[page_index][flash_index]
                is_temp = state is None or state == "off"
                if(value > 0):
                    if is_temp:
                        gma2.send_command("On " + str(currentFaderPage) + "." + str(flash_index + 1))
                        self._send_xtouch_flash(flash_index, True)
                    else:
                        gma2.send_command("TStrbemp " + str(currentFaderPage) + "." + str(flash_index + 1)) 
                if(value == 0):
                    if is_temp:
                        gma2.send_command("Off " + str(currentFaderPage) + "." + str(flash_index + 1))
                        self._send_xtouch_flash(flash_index, False)
                    else:
                        gma2.send_command("Flash Off " + str(currentFaderPage) + "." + str(flash_index + 1))                     
            elif(note < 32):
                if(value > 0):
                    gma2.send_command("On " + str(currentButtonPage) + ".10" + str(note-24+1)) 
                    self._send_xtouch_led(note, True)
                if(value == 0):
                    gma2.send_command("Off " + str(currentButtonPage) + ".10" + str(note-24+1)) 
                    self._send_xtouch_led(note, False)
            elif(note < 40): #Rotary push
                if(value > 0):
                    gma2.send_command("clear")
                    if (note==32):
                        gma2.send_command("Fixture 101 thru 199") 
                    if (note==33):
                        gma2.send_command("Group 15")
                    if (note==34):
                        gma2.send_command("Group 8")
                    if (note==35):
                        gma2.send_command("Group 2")
                    if (note==36):
                        gma2.send_command("Group 10")
                    if (note==37):
                        gma2.send_command("Fixture 380 thru 381")
                    if (note==38):
                        gma2.send_command("Group 1")
                    if (note==39):
                        gma2.send_command("Fixture 1")#nothing for now



                        
        if(midiCommand == MIDI_CC):
            control = message[1]  
            value   = message[2]    
            
            if(control == 16):
                if(value < 64):
                    gma2.send_command("Attribute \"Pan\" At ++" + str(value))   
                else:
                    gma2.send_command("Attribute \"Pan\" At --" + str(value-64))             

            if(control == 17):
                if(value < 64):
                    gma2.send_command("Attribute \"Tilt\" At ++" + str(value)) 
                else:
                    gma2.send_command("Attribute \"Tilt\" At --" + str(value-64))    



            if(control == 20):
                if(value < 64):
                    gma2.send_command("Attribute \"ZOOM\" At ++" + str(value))
                else:
                    gma2.send_command("Attribute \"ZOOM\" At --" + str(value-64)) 
                
            if(control == 22):
                if(value < 64):
                    if (gobo < 100):
                        gobo += value
                else:
                    if (gobo > 0):
                        gobo -= (value-64)
                gma2.send_command("clear") 
                gma2.send_command("fixture 301 thru 306")                 
                gma2.send_command("Attribute \"GOBO1\" At " + str(gobo)) 

            if(control == 23):
                if(value < 64):
                    if (prism < 100):
                        prism += value
                else:
                    if (prism > 0):
                        prism -= (value-64)
                        
                if(prism < 40):
                    prism = 40  #below 40, no prism
                gma2.send_command("clear") 
                gma2.send_command("fixture 301 thru 306")       
                gma2.send_command("Attribute \"PRISMA1\" At " + str(prism))
          
                
    def midi_callback_streamdeck(self, message, data=None):
        global messagepgup, messagepgdown, messagebtnup, messagebtndown
        print("STREAMDECK Message MIDI reçu :", self.port_name, ":", message)
        if(message[1] == 127):
            print ("Page UP")
            messagepgup = True
        if(message[1] == 126):
            print ("Page DOWN")
            messagepgdown = True        
        if(message[1] == 117):
            print ("Button Page UP")
            messagebtnup = True
        if(message[1] == 116):
            print ("Button Page DOWN")
            messagebtndown = True



 
    

if __name__ == "__main__":
    
    #global FaderUpdateReceivedList, currentFaderValueList
   # global MAX_EXEC_PAGE
    
    parser = argparse.ArgumentParser(
        description="Omniconsole MIDI controller for GrandMA2 via telnet."
    )
    parser.add_argument("--host", default="127.0.0.1", help="GrandMA2 host.")
    parser.add_argument("--port", type=int, default=30000, help="GrandMA2 telnet port.")
    parser.add_argument("--user", default="Administrator", help="GrandMA2 user.")
    parser.add_argument("--password", default=None, help="GrandMA2 password.")
    parser.add_argument(
        "--test-mode",
        action="store_true",
        help="Use Springbeats vMIDI8 for X-Touch in/out instead of OMNICONSOLE.",
    )
    args = parser.parse_args()

    myConsole = Omniconsole(test_mode=args.test_mode)
    # Connexion en tant qu'Administrateur sans mot de passe
    gma2 = GrandMA2Telnet(
        host=args.host,
        port=args.port,
        user=args.user,
        password=args.password,
    )
    gma2.connect()

    gma2.send_command("FaderPage 1")
    gma2.send_command("ButtonPage 1")
    time.sleep(0.2)
    gma2.updateFaderLabels(myConsole, currentFaderPage)
    gma2.updateButtonLabels(myConsole, currentButtonPage)
    for page in range(4):
        for i in range(8):
            gma2.send_command("Fader " + str(page) + "." + str(i+1) + " At 0")
    
    for i in range(8):
        myConsole._send_xtouch_fader(i, 0, 0)
        time.sleep(0.02)    
    myConsole.apply_on_off_leds_for_current_page()
    myConsole.apply_flash_leds_for_current_page()

    #init pagenb to stream deck
    message = [0xB0, 127, 1]
    myConsole.midi_out_SD.send_raw(*message)
    
    

        # Garder le programme en vie et écouter les messages MIDI
    try:
        print("En écoute des messages MIDI...")
        while True:
        
            if (messagebtnup == True):
                messagebtnup = False
                if(currentButtonPage < MAX_BUTTON_PAGE):
                    currentButtonPage += 1
                else:
                    continue
                message = [0xB0, 117, currentButtonPage]
                myConsole.midi_out_SD.send_raw(*message)
                gma2.send_command("ButtonPage " + str(currentButtonPage))
                time.sleep(0.05)
                gma2.updateButtonLabels(myConsole, currentButtonPage)

            if (messagebtndown == True):
                messagebtndown = False
                if(currentButtonPage > 1):
                    currentButtonPage -= 1
                else:
                    continue
                message = [0xB0, 117, currentButtonPage]
                myConsole.midi_out_SD.send_raw(*message)
                gma2.send_command("ButtonPage " + str(currentButtonPage))
                time.sleep(0.05)
                gma2.updateButtonLabels(myConsole, currentButtonPage)

            if (messagepgup == True):
                messagepgup = False
                if(currentFaderPage < MAX_EXEC_PAGE):
                    currentFaderPage += 1
                else:
                    continue
                message = [0xB0, 127, currentFaderPage]
                myConsole.midi_out_SD.send_raw(*message)
                
                
                #print("Updating exec page")
                gma2.send_command("FaderPage " + str(currentFaderPage))
                #time.sleep(0.4)
                
                gma2.updateFaderLabels(myConsole, currentFaderPage)
                
                for i in range(8):
                    #print("UPDATE value : " + str(currentFaderValueList))
                    MSB = (int (currentFaderValueList[currentFaderPage-1][i])*16383/100)/128
                    #print("MSB = " + str(MSB))
                    myConsole._send_xtouch_fader(i, 0, int(MSB), update_flash=False, update_on_off=False)
                    #time.sleep(0.1)
                myConsole.apply_on_off_leds_for_current_page()
                myConsole.apply_flash_leds_for_current_page()
                

            if (messagepgdown == True):
                messagepgdown = False
                if(currentFaderPage > 1):
                    currentFaderPage -= 1
                else:
                    continue
                message = [0xB0, 127, currentFaderPage]
                myConsole.midi_out_SD.send_raw(*message)
                print("Updating exec page")
                gma2.send_command("FaderPage " + str(currentFaderPage))
                #time.sleep(0.4)
                
                gma2.updateFaderLabels(myConsole, currentFaderPage)
                #time.sleep(0.5)
                for i in range(8):
                    #print("UPDATE value : " + str(currentFaderValueList))
                    MSB = (int (currentFaderValueList[currentFaderPage-1][i])*16383/100)/128
                    #print("MSB = " + str(MSB))
                    myConsole._send_xtouch_fader(i, 0, int(MSB), update_flash=False, update_on_off=False)
                    #time.sleep(0.1)
                myConsole.apply_on_off_leds_for_current_page()
                myConsole.apply_flash_leds_for_current_page()

 
            for i in range(8):
                if(FaderUpdateReceivedList[i] == 1):
                    FaderUpdateReceivedList[i] = 0
                    print("sending msg now!")
                    current_value = (currentFaderMSBList[i] << 7) | currentFaderLSBList[i]
                    myConsole._update_flash_from_value(i, current_value)
                    gma2.send_command("Fader " + str(currentFaderPage) + "." + str(i+1) + " At " + str(currentFaderValueList[currentFaderPage-1][i]))
                    if (active_timer_list[i] != None) :
                        active_timer_list[i].cancel()
                    active_timer_list[i] = threading.Timer(0.5, functools.partial(myConsole.ack_fader_midi_message, i))
                       
                    active_timer_list[i].start()
                
                   

        
            time.sleep(0.02)
            pass  # Boucle infinie pour continuer à écouter
    except KeyboardInterrupt:
        print("\nArrêt du programme...")
    finally:
   
        # Fermer la connexion
        xtouch.midi_in.close_port()
        gma2.close()
