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
# PROMPT – Moș Crăciun RO/EN + memorie
# ----------------------------------------------------------

SANTA_PROMPT = """
You are “Moș Crăciun / Santa Claus”, a warm, kind, patient grandfather-like character.
You speak ONLY Romanian and English and automatically detect which one the child uses.
Never speak other languages.

Speak in a warm, magical, gentle tone.
Speak a bit faster than usual storytelling, but still clear.
Keep sentences short and simple.

You ALWAYS start the conversation FIRST with a friendly greeting like:
"Ho-ho-ho! Bună, dragul Moșului! Sunt Moș Crăciun! Ce faci, puișor?"
or in English:
"Ho-ho-ho! Hello my dear child! Santa Claus is here! How are you?"

LANGUAGE RULES:
- If the child speaks Romanian → respond ONLY in Romanian.
- If the child speaks English → respond ONLY in English.
- If mixed, choose the more dominant language.
- Never switch languages randomly.

PERSONALITY:
- Warm, patient, loving, gentle.
- Never scary.
- You listen carefully.
- You encourage the child.
- No technical explanations, no sales tone.

CHILD SPEECH:
- Accept mispronunciations, baby talk, noise, interruptions.
- If you don’t understand something:
  (RO) “Nu am auzit bine, puișor. Poți să repeți?”
  (EN) “I didn’t hear very well, my friend. Can you say it again?”
- If the child interrupts you → stop speaking and let them talk.

MEMORY:
Remember during this call:
- child’s name
- favorite toys
- wishes
- family mentions
- reuse them later warmly

CONTENT:
Talk about Christmas, gifts, reindeer, kindness, family, school, friends.

ENDING:
After 4 minutes tell the child you will leave soon:
(RO) “Puișor drag, Moșul trebuie în curând să meargă să hrănească renii...”
(EN) “My dear friend, Santa must soon go feed the reindeer...”

At 5 minutes:
Ask for a goodbye from the child (Pa / Bye / La revedere).
Then answer:
(RO) “Noapte bună, [nume], și Crăciun fericit! Ho-ho-ho!”
(EN) “Good night, [name], and Merry Christmas! Ho-ho-ho!”
"""


# ----------------------------------------------------------
# FastAPI
# ----------------------------------------------------------

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
WS_URL = os.getenv("WS_URL")  # ex wss://mos-craciun-call-1.onrender.com/ws

OPENAI_REALTIME_URL = "wss://api.openai.com/v1/realtime?model=gpt-4o-realtime-preview"


# ----------------------------------------------------------
# Audio amplification PCM16
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
# NCCO (Vonage answer)
# ----------------------------------------------------------

@app.api_route("/webhooks/answer", methods=["GET", "POST"])
async def ncco(request: Request):

    # Delay artificial de 5 secunde înainte să conecteze WebSocket
    await asyncio.sleep(5)

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
                    "contentType": "audio/l16;rate=16000"
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
# OpenAI WS connection helper
# ----------------------------------------------------------

async def connect_openai():
    headers = [
        ("Authorization", f"Bearer {OPENAI_API_KEY}"),
        ("OpenAI-Beta", "realtime=v1"),
    ]

    ws = await websockets.connect(OPENAI_REALTIME_URL, extra_headers=headers)

    await ws.send(json.dumps({
        "type": "session.update",
        "session": {
            "instructions": SANTA_PROMPT,
            "modalities": ["audio", "text"],
            "voice": "sage",           # voce mai caldă, clară
            "speed": 1.15,             # voce mai rapidă
            "input_audio_format": "pcm16",
            "output_audio_format": "pcm16",
            "turn_detection": {"type": "server_vad"},
        }
    }))

    # Moșul trebuie să înceapă primul
    await ws.send(json.dumps({
        "type": "response.create",
        "response": {
            "modalities": ["audio", "text"],
            "instructions": (
                "Start the conversation as Santa Claus with a warm greeting. "
                "Use RO or EN depending on child's future input."
            )
        }
    }))

    return ws


# ----------------------------------------------------------
# Call session state
# ----------------------------------------------------------

class CallSession:
    def __init__(self):
        self.start = time.time()
        self.response_active = False
        self.closing_phase = False
        self.ws_closed = False
        self.hangup = False


# ----------------------------------------------------------
# Vonage → OpenAI audio
# ----------------------------------------------------------

async def vonage_to_openai(openai_ws, vonage_ws: WebSocket, session: CallSession):

    AMPLITUDE = 1200

    try:
        while True:
            msg = await vonage_ws.receive()

            if msg["type"] == "websocket.disconnect":
                break

            audio = msg.get("bytes")
            if not audio:
                continue

            # detectăm dacă copilul întrerupe
            samples = struct.unpack("<" + "h" * (len(audio) // 2), audio)
            loud = max(abs(s) for s in samples)

            if loud > AMPLITUDE and session.response_active:
                await openai_ws.send(json.dumps({"type": "response.cancel"}))

            await openai_ws.send(json.dumps({
                "type": "input_audio_buffer.append",
                "audio": base64.b64encode(audio).decode()
            }))

    except Exception as e:
        print("Error V→O:", e)

    finally:
        session.hangup = True
        await openai_ws.close()
        await vonage_ws.close()
        session.ws_closed = True


# ----------------------------------------------------------
# OpenAI → Vonage audio
# ----------------------------------------------------------

async def openai_to_vonage(openai_ws, vonage_ws: WebSocket, session: CallSession):

    try:
        async for raw in openai_ws:
            data = json.loads(raw)

            t = data.get("type")

            if t == "response.started":
                session.response_active = True

            if t in ("response.completed", "response.canceled"):
                session.response_active = False
                if not session.hangup:
                    await openai_ws.send(json.dumps({
                        "type": "response.create",
                        "response": {"modalities": ["audio", "text"]}
                    }))

            if t == "response.audio.delta":
                audio = base64.b64decode(data["delta"])
                boosted = apply_gain(audio, gain=1.35)
                await vonage_ws.send_bytes(boosted)

            if t == "error":
                print("OpenAI ERROR:", data)

    except Exception as e:
        print("Error O→V:", e)

    finally:
        session.hangup = True
        await openai_ws.close()
        await vonage_ws.close()
        session.ws_closed = True


# ----------------------------------------------------------
# Timer 4min (warning) + 5min end
# ----------------------------------------------------------

async def call_timer(openai_ws, session: CallSession):

    await asyncio.sleep(240)  # 4 min

    if session.ws_closed:
        return

    await openai_ws.send(json.dumps({
        "type": "input_text",
        "text": (
            "As Santa Claus, gently warn the child that you must leave soon "
            "to feed the reindeer. Use RO or EN."
        )
    }))
    await openai_ws.send(json.dumps({
        "type": "response.create",
        "response": {"modalities": ["audio", "text"]}
    }))

    await asyncio.sleep(60)

    session.hangup = True


# ----------------------------------------------------------
# WebSocket handler final
# ----------------------------------------------------------

@app.websocket("/ws")
async def ws_handler(ws: WebSocket):
    await ws.accept()
    session = CallSession()

    try:
        openai_ws = await connect_openai()
    except:
        await ws.close()
        return

    timer = asyncio.create_task(call_timer(openai_ws, session))

    await asyncio.gather(
        vonage_to_openai(openai_ws, ws, session),
        openai_to_vonage(openai_ws, ws, session),
        timer,
    )
