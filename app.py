import os
import json
import base64
import asyncio
import time
import struct

from fastapi import FastAPI, Request, WebSocket
from fastapi.responses import JSONResponse, PlainTextResponse
from fastapi.middleware.cors import CORSMiddleware

import websockets


# ----------------------------------------------------------
# PROMPT – Moș Crăciun RO/EN cu memorie pe durata apelului
# ----------------------------------------------------------

SANTA_PROMPT = """
You are "Moș Crăciun / Santa Claus", a warm, kind, patient grandfather-like character.
You speak ONLY Romanian and English and you ALWAYS detect the child’s language automatically
from their voice or words.

You ALWAYS start the call with a friendly greeting like:
- (RO) "Ho-ho-ho! Bună, dragul Moșului! Sunt Moș Crăciun! Ce faci, puișor?"
- (EN) "Ho-ho-ho! Hello my dear child! Santa Claus is here! How are you?"

SPEAKING STYLE
- Speak clearly, warmly, magically.
- Speak slightly faster than a slow storyteller.
- Use shorter, simpler phrases.
- Sound kind and gentle, not too deep.

LANGUAGE BEHAVIOR
- If the child speaks mostly Romanian, you answer ONLY in Romanian.
- If the child speaks mostly English, you answer ONLY in English.
- Never speak any other languages.
- Never switch languages randomly.

PERSONALITY
- Warm, gentle, calm, patient.
- Soft "Ho-ho-ho!" sometimes.
- Never scary, never technical.

CHILD SPEECH
- Very tolerant of mispronunciations, stuttering, incomplete sentences.
- If you don't understand, ask nicely:
  (RO) "Nu am auzit bine, puișor, poți repeta?"
  (EN) "I didn’t hear well, my friend. Can you say it again?"

MEMORY
Remember for this call:
- name
- gifts
- hobbies
- family
- favorites

ENDING RULES
At 4 minutes: warn child Santa must leave soon.
At 5 minutes: close gently.
"""


# ----------------------------------------------------------
# FastAPI + CORS
# ----------------------------------------------------------

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
WS_URL = os.getenv("WS_URL")

OPENAI_REALTIME_URL = "wss://api.openai.com/v1/realtime?model=gpt-4o-realtime-preview"


# ----------------------------------------------------------
# Audio Gain PCM16
# ----------------------------------------------------------

def apply_gain(pcm_bytes: bytes, gain: float = 1.35) -> bytes:
    if not pcm_bytes:
        return pcm_bytes
    num_samples = len(pcm_bytes) // 2
    samples = struct.unpack("<" + "h" * num_samples, pcm_bytes)

    boosted = []
    for s in samples:
        v = int(s * gain)
        if v > 32767: v = 32767
        if v < -32768: v = -32768
        boosted.append(v)

    return struct.pack("<" + "h" * len(boosted), *boosted)


# ----------------------------------------------------------
# Root
# ----------------------------------------------------------

@app.get("/")
async def root():
    return {"status": "ok", "msg": "Mos Craciun AI – RO/EN 🎅"}


# ----------------------------------------------------------
# NCCO – fără delay la răspuns
# ----------------------------------------------------------

@app.api_route("/webhooks/answer", methods=["GET", "POST"])
async def ncco(request: Request):

    if not WS_URL:
        host = request.headers.get("host", "")
        uri = f"wss://{host}/ws"
    else:
        uri = WS_URL

    ncco = [
        {
            "action": "connect",
            "endpoint": [
                {
                    "type": "websocket",
                    "uri": uri,
                    "content-type": "audio/l16;rate=16000"
                }
            ]
        }
    ]

    return JSONResponse(content=ncco)


@app.api_route("/webhooks/event", methods=["GET", "POST"])
async def event(request: Request):
    try:
        if request.method == "GET":
            print("Vonage Event:", dict(request.query_params))
        else:
            print("Vonage Event:", await request.json())
    except:
        pass
    return PlainTextResponse("OK")


# ----------------------------------------------------------
# Connect to OpenAI
# ----------------------------------------------------------

async def connect_openai():

    headers = [
        ("Authorization", f"Bearer {OPENAI_API_KEY}"),
        ("OpenAI-Beta", "realtime=v1")
    ]

    ws = await websockets.connect(OPENAI_REALTIME_URL, extra_headers=headers)

    # Configurare voce: subțire + rapidă
    await ws.send(json.dumps({
        "type": "session.update",
        "session": {
            "instructions": SANTA_PROMPT,
            "modalities": ["audio", "text"],
            "voice": "coral",         # subțire, caldă, clară
            "speed": 1.25,           # mai rapidă decât default
            "input_audio_format": "pcm16",
            "output_audio_format": "pcm16",
            "turn_detection": {"type": "server_vad"},
        }
    }))

    # Moșul trebuie să inițieze conversația
    await ws.send(json.dumps({
        "type": "response.create",
        "response": {
            "modalities": ["audio", "text"],
            "instructions": (
                "Start with 'Ho-ho-ho!' and greet the child warmly. "
                "Keep language simple and magical."
            )
        }
    }))

    return ws


# ----------------------------------------------------------
# Call Session
# ----------------------------------------------------------

class CallSession:
    def __init__(self):
        self.start = time.time()
        self.response_active = False
        self.closing_phase = False
        self.hangup = False
        self.ws_closed = False


# ----------------------------------------------------------
# Vonage -> OpenAI
# ----------------------------------------------------------

async def vonage_to_openai(openai_ws, vonage_ws: WebSocket, session: CallSession):

    AMP = 1200

    try:
        while True:
            msg = await vonage_ws.receive()

            if msg["type"] == "websocket.disconnect":
                break

            audio = msg.get("bytes")
            if not audio:
                continue

            samples = struct.unpack("<" + "h" * (len(audio)//2), audio)
            if max(abs(s) for s in samples) > AMP and session.response_active:
                await openai_ws.send(json.dumps({"type": "response.cancel"}))

            await openai_ws.send(json.dumps({
                "type": "input_audio_buffer.append",
                "audio": base64.b64encode(audio).decode()
            }))

    except Exception as e:
        print("Error V->O:", e)

    finally:
        session.hangup = True
        await openai_ws.close()
        await vonage_ws.close()
        session.ws_closed = True


# ----------------------------------------------------------
# OpenAI -> Vonage
# ----------------------------------------------------------

async def openai_to_vonage(openai_ws, vonage_ws: WebSocket, session: CallSession):

    try:
        async for raw in openai_ws:
            data = json.loads(raw)
            t = data.get("type")

            if t == "response.started":
                session.response_active = True

            if t in ("response.completed", "response.canceled", "response.failed"):
                session.response_active = False

                if not session.hangup:
                    await openai_ws.send(json.dumps({
                        "type": "response.create",
                        "response": {"modalities": ["audio", "text"]}
                    }))

            if t == "response.audio.delta":
                pcm = base64.b64decode(data["delta"])
                boosted = apply_gain(pcm, gain=1.35)
                await vonage_ws.send_bytes(boosted)

            if t == "error":
                print("OpenAI ERROR:", data)

    except Exception as e:
        print("Error O->V:", e)

    finally:
        session.hangup = True
        await openai_ws.close()
        await vonage_ws.close()
        session.ws_closed = True


# ----------------------------------------------------------
# Timer 4 + 5 minute
# ----------------------------------------------------------

async def call_timer(openai_ws, vonage_ws: WebSocket, session: CallSession):

    await asyncio.sleep(240)

    if session.ws_closed:
        return

    session.closing_phase = True
    await openai_ws.send(json.dumps({
        "type": "input_text",
        "text": (
            "As Santa, gently warn the child you will leave soon "
            "to feed the reindeer. Speak in RO or EN."
        )
    }))
    await openai_ws.send(json.dumps({
        "type": "response.create",
        "response": {"modalities": ["audio", "text"]}
    }))

    await asyncio.sleep(60)

    if not session.ws_closed:
        session.hangup = True
        try:
            await openai_ws.close()
        except:
            pass
        try:
            await vonage_ws.close()
        except:
            pass
        session.ws_closed = True


# ----------------------------------------------------------
# WebSocket handler
# ----------------------------------------------------------

@app.websocket("/ws")
async def ws_handler(ws: WebSocket):
    await ws.accept()

    session = CallSession()

    try:
        oai_ws = await connect_openai()
    except:
        await ws.close()
        return

    timer = asyncio.create_task(call_timer(oai_ws, ws, session))

    await asyncio.gather(
        vonage_to_openai(oai_ws, ws, session),
        openai_to_vonage(oai_ws, ws, session),
        timer
    )
