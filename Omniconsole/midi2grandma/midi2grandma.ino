#include <MIDIUSB.h>

/* ===== CONFIG ===== */
#define FADER1_PIN A0
#define FADER2_PIN A2
#define AVG_SIZE 5          // ⬅️ nombre de lectures pour la moyenne
#define DEADZONE 3
#define RAW_ENDZONE 20       // force 0/127 when average is close to ends
#define SEND_INTERVAL 15    // ms
#define MIDI_CC1 7           // CC1 utilisé 
#define MIDI_CC2 8           // CC2 utilisé 
#define MIDI_CHANNEL 1
/* ================== */

const int FADER_COUNT = 2;
const int faderPins[FADER_COUNT] = { FADER1_PIN, FADER2_PIN };
const int midiCC[FADER_COUNT] = { MIDI_CC1, MIDI_CC2 };

int readings[FADER_COUNT][AVG_SIZE];
int readIndex[FADER_COUNT] = { 0, 0 };
long total[FADER_COUNT] = { 0, 0 };

int lastMidiValue[FADER_COUNT] = { -1, -1 };
unsigned long lastSendTime[FADER_COUNT] = { 0, 0 };

void setup() {
  for (int f = 0; f < FADER_COUNT; f++) {
    pinMode(faderPins[f], INPUT);
  }

  // Initialisation du buffer
  for (int f = 0; f < FADER_COUNT; f++) {
    for (int i = 0; i < AVG_SIZE; i++) {
      readings[f][i] = analogRead(faderPins[f]);
      total[f] += readings[f][i];
    }
  }
}

void loop() {
  unsigned long now = millis();

  for (int f = 0; f < FADER_COUNT; f++) {
    // Retire l'ancienne valeur
    total[f] -= readings[f][readIndex[f]];

    // Nouvelle lecture
    readings[f][readIndex[f]] = analogRead(faderPins[f]);
    total[f] += readings[f][readIndex[f]];

    // Avance dans le buffer circulaire
    readIndex[f] = (readIndex[f] + 1) % AVG_SIZE;

    // Moyenne
    int avgRaw = total[f] / AVG_SIZE;

    // Conversion MIDI
    int midiValue;
    if (avgRaw <= RAW_ENDZONE) {
      midiValue = 0;
    } else if (avgRaw >= (1023 - RAW_ENDZONE)) {
      midiValue = 127;
    } else {
      midiValue = map(avgRaw, 0, 1023, 0, 127);
      midiValue = constrain(midiValue, 0, 127);
    }

    if (abs(midiValue - lastMidiValue[f]) >= DEADZONE &&
        (now - lastSendTime[f]) >= SEND_INTERVAL) {
      sendCC(midiCC[f], midiValue, MIDI_CHANNEL);
      lastMidiValue[f] = midiValue;
      lastSendTime[f] = now;
    }
  }
}

void sendCC(byte control, byte value, byte channel) {
  midiEventPacket_t event = {
    0x0B,
    (byte)(0xB0 | (channel - 1)),
    control,
    value
  };
  MidiUSB.sendMIDI(event);
  MidiUSB.flush();
}
