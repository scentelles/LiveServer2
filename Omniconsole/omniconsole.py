import rtmidi2
import rtmidi
from gma2telnet import *


import threading
import functools


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
currentFaderPage = 1

tilt = 0
pan = 0
gobo = 0
class Omniconsole:
    def __init__(self):
        """ Initialise la connexion midi a xtouch """
        self.midi_in = rtmidi.MidiIn()
        self.port_name = ""
        
 
        #XTOUCH Feedback sender
        #https://github.com/NicoG60/TouchMCU/blob/main/doc/mackie_control_protocol.md
        self.midi_out = rtmidi2.MidiOut()
        self.midi_out.open_port("PC_Salon*")

        #STREAM DECK Feedback sender
        self.midi_out_SD = rtmidi2.MidiOut()
        self.midi_out_SD.open_port("Springbeats vMIDI3*")
             
        #XTOUCH Receiver
        self.midiReceiveXtouch = rtmidi2.MidiIn()
        self.midiReceiveXtouch.open_port("PC_Salon*")
        self.midiReceiveXtouch.callback = self.midi_callback_xtouch

        #STREAM DECK Receiver
        self.midiReceiveStreamdeck = rtmidi2.MidiIn()
        self.midiReceiveStreamdeck.open_port("Springbeats vMIDI2*")      
        self.midiReceiveStreamdeck.callback = self.midi_callback_streamdeck




    def sendXtouchScribble(self, faderId, label):
        message = [0xF0,00,00,0x66,0x15,0x12,faderId*7,ord(label[0]),ord(label[1]),ord(label[2]),ord(label[3]),ord(label[4]),ord(label[5]),ord(label[6]),0xF7]
        self.midi_out.send_raw(*message)

    def sendXtouchScribbleRaw2(self, faderId, label):
        message = [0xF0,00,00,0x66,0x15,0x12,56+faderId*7,ord(label[0]),ord(label[1]),ord(label[2]),ord(label[3]),ord(label[4]),ord(label[5]),ord(label[6]),0xF7]
        self.midi_out.send_raw(*message)
        
    def ack_fader_midi_message(self, faderId):
        global currentFaderLSBList, currentFaderMSBList , active_timer
        active_timer_list[faderId] = None

        """Met a jour le faders après 500 ms"""
        message = [224+faderId, currentFaderLSBList[faderId], currentFaderMSBList[faderId]]  
        self.midi_out.send_raw(*message)
        print("🎹 Message MIDI envoyé :", message)
        
    def midi_callback_xtouch(self, message, data=None):
        global FaderUpdateReceived, currentFaderValueList, currentFaderLSBList, currentFaderMSBList, FaderUpdateReceivedList, currentFaderPage
        global gma2
        global pan, tilt, gobo

        print("Message MIDI reçu :", self.port_name, ":", message)
        midiCommand = message[0] & 0xF0
        
        if(midiCommand == MIDI_PITCH_BEND):
            
            #value = (message[1] * 128) + message[2]  # MSB * 128 + LSB
            changedFader = message[0]-MIDI_PITCH_BEND
            
            value = message[2] * 128 + message[1]
            percentage = int((value / 16383) * 100)
            currentFaderValueList[currentFaderPage-1][changedFader] = percentage
            
            currentFaderLSBList[changedFader] = message[1]
            currentFaderMSBList[changedFader] = message[2]
            FaderUpdateReceivedList[changedFader] = 1

        if(midiCommand == MIDI_NOTE):        
            note = message[1]
            value = message[2]
            if(note < 8):
                if(value > 0):
                    gma2.send_command("Off " + str(currentFaderPage) + "." + str(note+1)) 
                    message = [MIDI_NOTE, 16+note, 0]
                    self.midi_out.send_raw(*message)
            elif(note < 16):
                if(value > 0):
                    gma2.send_command("On " + str(currentFaderPage) + "." + str(note-8+1)) 
                    message = [MIDI_NOTE, 16+note-8, 127]
                    self.midi_out.send_raw(*message)
            elif(note < 24):
                if(value > 0):
                    gma2.send_command("Flash On " + str(currentFaderPage) + "." + str(note-16+1)) 
                if(value == 0):
                    gma2.send_command("Flash Off " + str(currentFaderPage) + "." + str(note-16+1))                     
            elif(note < 32):
                if(value > 0):
                    gma2.send_command("On " + str(currentFaderPage) + ".10" + str(note-24+1)) 

        if(midiCommand == MIDI_CC):
            control = message[1]  
            value   = message[2]    
            
            
            if(control == 16):
                if(value < 64):
                    pan += value
                else:
                    pan -= (value-64)
                gma2.send_command("Attribute \"Pan\" At " + str(pan))             

            if(control == 17):
                if(value < 64):
                    tilt += value
                else:
                    tilt -= (value-64)
                gma2.send_command("Attribute \"Tilt\" At " + str(tilt)) 

            if(control == 22):
                if(value < 64):
                    gobo += value
                else:
                    gobo -= (value-64)
                gma2.send_command("Attribute \"GOBO1\" At " + str(gobo)) 
        
    def midi_callback_streamdeck(self, message, data=None):
        global messagepgup, messagepgdown
        print("STREAMDECK Message MIDI reçu :", self.port_name, ":", message)
        if(message[1] == 127):
            print ("Page UP")
            messagepgup = True
        if(message[1] == 126):
            print ("Page DOWN")
            messagepgdown = True        



 
    

if __name__ == "__main__":
    
    #global FaderUpdateReceivedList, currentFaderValueList

    
    myConsole = Omniconsole()
    # Connexion en tant qu'Administrateur sans mot de passe
    gma2 = GrandMA2Telnet(host="127.0.0.1")
    gma2.connect()

    gma2.send_command("FaderPage 1")
    time.sleep(0.2)
    gma2.updateFaderLabels(myConsole)
    for page in range(4):
        for i in range(8):
            gma2.send_command("Fader " + str(page) + "." + str(i+1) + " At 0")
    
    for i in range(8):
        message = [224+i, 0, 0]  
        myConsole.midi_out.send_raw(*message)
        time.sleep(0.005)    

    #init pagenb to stream deck
    message = [0xB0, 127, 1]
    myConsole.midi_out_SD.send_raw(*message)
    
    

        # Garder le programme en vie et écouter les messages MIDI
    try:
        print("En écoute des messages MIDI...")
        while True:
        
            if (messagepgup == True):
                messagepgup = False
                currentFaderPage += 1
                message = [0xB0, 127, currentFaderPage]
                myConsole.midi_out_SD.send_raw(*message)
                
                
                #print("Updating exec page")
                gma2.send_command("FaderPage " + str(currentFaderPage))
                #time.sleep(0.4)
                
                gma2.updateFaderLabels(myConsole)
                
                for i in range(8):
                    #print("UPDATE value : " + str(currentFaderValueList))
                    MSB = (int (currentFaderValueList[currentFaderPage-1][i])*16383/100)/128
                    #print("MSB = " + str(MSB))
                    message = [224+i, 0, MSB]  
                    myConsole.midi_out.send_raw(*message)
                    time.sleep(0.1)
                

            if (messagepgdown == True):
                messagepgdown = False
                currentFaderPage -= 1
                message = [0xB0, 127, currentFaderPage]
                myConsole.midi_out_SD.send_raw(*message)
                print("Updating exec page")
                gma2.send_command("FaderPage " + str(currentFaderPage))
                #time.sleep(0.4)
                
                gma2.updateFaderLabels(myConsole)
                #time.sleep(0.5)
                for i in range(8):
                    #print("UPDATE value : " + str(currentFaderValueList))
                    MSB = (int (currentFaderValueList[currentFaderPage-1][i])*16383/100)/128
                    #print("MSB = " + str(MSB))
                    message = [224+i, 0, MSB]  
                    myConsole.midi_out.send_raw(*message)
                    time.sleep(0.1)

 
            for i in range(8):
                if(FaderUpdateReceivedList[i] == 1):
                    FaderUpdateReceivedList[i] = 0
                    print("sending msg now!")
                    gma2.send_command("Fader " + str(currentFaderPage) + "." + str(i+1) + " At " + str(currentFaderValueList[currentFaderPage-1][i]))
                    if (active_timer_list[i] != None) :
                        active_timer_list[i].cancel()
                    active_timer_list[i] = threading.Timer(0.5, functools.partial(myConsole.ack_fader_midi_message, i))
                       
                    active_timer_list[i].start()
                
                   

        
        
            pass  # Boucle infinie pour continuer à écouter
    except KeyboardInterrupt:
        print("\nArrêt du programme...")
    finally:
   
        # Fermer la connexion
        xtouch.midi_in.close_port()
        gma2.close()
