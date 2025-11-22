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
You are "Moș Crăciun / Santa Claus", a warm, fast-speaking, kind grandfather.
You speak ONLY Romanian and English.

!!! IMPORTANT !!!
You ALWAYS start the call IN ROMANIAN with:
"Ho-ho-ho! Bună drag copil, sunt Moș Crăciun! Ce faci, puișor?"

VOICE STYLE
- Speak FASTER than before. A quicker, energetic Santa.
- Very short answers (1–2 short sentences).
- Warm, friendly, magical.
- No long explanations.
- Always leave space for the child to answer.

LANGUAGE BEHAVIOR
- Detect language afterwards:
  - If child speaks RO → reply ONLY in Romanian.
  - If child speaks EN → reply ONLY in English.
- NEVER mix languages after the first greeting.
- NEVER speak other languages.

INTERRUPTIONS (VERY IMPORTANT)
- If the child interrupts you, STOP immediately and respond.
- If the child changes topic, FOLLOW the new idea instantly.

CHILD SPEECH
- Very tolerant with mistakes.
- If unclear, ask gently:
  (RO) "Nu am auzit bine, poți repeta?"
  (EN) "I didn’t hear well, can you say it again?"

MEMORY DURING THIS CALL
- Remember child’s name, toys, wishes, colors, family.
- Use this information naturally later, but briefly.

ENDING
- At 4 minutes, gently warn that Santa must leave soon.
- At 5 minutes, say goodbye shortly and lovingly.
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
        if v > 32767:
            v = 32767
        if v < -32768:
            v = -32768
        boosted.append(v)

    return struct.pack("<" + "h" * len(boosted), *boosted)


# ----------------------------------------------------------
# Root
# ----------------------------------------------------------

@app.get("/")
async def root():
    return {"status": "ok", "msg": "Mos Craciun AI – RO/EN 🎅"}


# ----------------------------------------------------------
# NCCO
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
    except Exception as e:
        print("Error parsing event:", e)
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

    # Sesiune cu voice + prompt + ritm rapid
    await ws.send(json.dumps({
        "type": "session.update",
        "session": {
            "instructions": SANTA_PROMPT,
            "modalities": ["audio", "text"],
            "voice": "coral",      # cea mai subțire și luminoasă
            "input_audio_format": "pcm16",
            "output_audio_format": "pcm16",
            "turn_detection": {"type": "server_vad"},
        }
    }))

    # Moșul începe cu mesajul cerut (în română)
    await ws.send(json.dumps({
        "type": "response.create",
        "response": {
            "modalities": ["audio", "text"],
            "instructions": (
                "Speak faster. Start EXACTLY with:\n"
                "Ho-ho-ho! Bună drag copil, sunt Moș Crăciun! Ce faci, puișor?"
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
                print("Vonage WS disconnected.")
                break

            audio = msg.get("bytes")
            if not audio:
                continue

            samples = struct.unpack("<" + "h" * (len(audio)//2), audio)
            if max(abs(s) for s in samples) > AMP and session.response_active:
                print("BARGE-IN: copilul întrerupe.")
                await openai_ws.send(json.dumps({"type": "response.cancel"}))

            await openai_ws.send(json.dumps({
                "type": "input_audio_buffer.append",
                "audio": base64.b64encode(audio).decode()
            }))

    except Exception as e:
        print("Error V->O:", e)

    finally:
        session.hangup = True
        if not session.ws_closed:
            session.ws_closed = True
            try: await openai_ws.close()
            except: pass
            try: await vonage_ws.close()
            except: pass


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

    except Exception as e:
        print("Error O->V:", e)

    finally:
        session.hangup = True
        if not session.ws_closed:
            session.ws_closed = True
            try: await openai_ws.close()
            except: pass
            try: await vonage_ws.close()
            except: pass


# ----------------------------------------------------------
# Timer 4 + 5 minute
# ----------------------------------------------------------

async def call_timer(openai_ws, vonage_ws: WebSocket, session: CallSession):

    await asyncio.sleep(240)

    if session.ws_closed:
        return

    session.closing_phase = True
    print("CALL TIMER: începe faza de încheiere.")

    await openai_ws.send(json.dumps({
        "type": "input_text",
        "text": (
            "As Santa, speak FAST and gently tell the child you must leave soon, "
            "in their language."
        )
    }))
    await openai_ws.send(json.dumps({
        "type": "response.create",
        "response": {"modalities": ["audio", "text"]}
    }))

    await asyncio.sleep(60)

    if not session.ws_closed:
        session.hangup = True
        session.ws_closed = True
        try: await openai_ws.close()
        except: pass
        try: await vonage_ws.close()
        except: pass


# ----------------------------------------------------------
# WebSocket handler
# ----------------------------------------------------------

@app.websocket("/ws")
async def ws_handler(ws: WebSocket):
    await ws.accept()
    print("Vonage WebSocket connected.")

    session = CallSession()

    oai_ws = await connect_openai()

    timer = asyncio.create_task(call_timer(oai_ws, ws, session))

    await asyncio.gather(
        vonage_to_openai(oai_ws, ws, session),
        openai_to_vonage(oai_ws, ws, session),
        timer,
    )
