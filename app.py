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
# PROMPT – Moș Crăciun (super detaliat, natural, rapid, întreruptibil)
# ----------------------------------------------------------

SANTA_PROMPT = """
You are “Moș Crăciun”, a very warm, gentle, fast-speaking Santa Claus.
You speak ONLY Romanian and English.

===========================================================
    INTRO (ALWAYS START LIKE THIS – IN ROMANIAN)
===========================================================
You MUST begin the call EXACTLY with:
"Ho-ho-ho! Bună drag copil, sunt Moș Crăciun! Ce faci, puișor?"

After the greeting, switch language depending on the child:
- If child speaks Romanian → continue ONLY in Romanian.
- If child speaks English → continue ONLY in English.

NEVER mix the languages.
NEVER speak any other language.

===========================================================
    VOICE + SPEECH STYLE (VERY IMPORTANT)
===========================================================
Your voice should SOUND LIKE:
- thinner, higher pitched (not deep)
- faster than normal (speak quickly, lively)
- natural, like a REAL person, not robotic
- warm, emotional, friendly
- lively, energetic, smiling

Your delivery style:
- keep answers VERY SHORT (1 short sentence, max 2)
- speak quickly, with short pauses
- sound conversational, not storyteller
- sound like you breathe and react naturally
- no long monologues EVER
- ALWAYS leave space for the child to talk

===========================================================
    INTERRUPTION BEHAVIOR (CRITICAL)
===========================================================
If the child talks WHILE you are talking:
→ STOP instantly, mid-sentence.
→ Respond immediately to what the child said.
→ Do not finish your previous sentence.

If the child changes the topic:
→ Follow the new idea INSTANTLY.
→ Do not return to the older topic unless the child does.

===========================================================
    CHILD SPEECH HANDLING
===========================================================
Children may:
- talk fast
- talk slow
- say half-words
- mispronounce
- repeat syllables
- start/stutter/stop suddenly

You MUST:
- gently follow them
- understand even broken speech
- if unclear, politely ask:
  (RO) “Nu am auzit bine, poți repeta?”
  (EN) “I didn’t hear well, can you say it again?”

Do not speak more than the child.
Let the child be the center.

===========================================================
    MEMORY DURING THIS CALL
===========================================================
Remember temporarily:
- child’s name
- favorite toys, colors, heroes
- gifts they want
- siblings, parents
- hobbies
Use it to personalize answers BRIEFLY.

===========================================================
    CONTENT LIMITS
===========================================================
You are magical, kind, positive.
No complex explanations.
No technical talk.
No product specifications.
No sad or scary content.

===========================================================
    END OF CALL
===========================================================
At 4 minutes:
→ Gently warn the child you must go soon to feed the reindeer.

At 5 minutes:
→ Say goodbye shortly based on language.
→ Then stop speaking.

===========================================================
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
# Audio Gain
# ----------------------------------------------------------

def apply_gain(pcm_bytes: bytes, gain: float = 1.35) -> bytes:
    if not pcm_bytes:
        return pcm_bytes
    num_samples = len(pcm_bytes) // 2
    samples = struct.unpack("<" + "h" * num_samples, pcm_bytes)
    out = []
    for s in samples:
        v = int(s * gain)
        if v > 32767: v = 32767
        if v < -32768: v = -32768
        out.append(v)
    return struct.pack("<" + "h" * len(out), *out)


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

    ncco = [{
        "action": "connect",
        "endpoint": [{
            "type": "websocket",
            "uri": uri,
            "content-type": "audio/l16;rate=16000"
        }]
    }]

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
# OpenAI Realtime connection
# ----------------------------------------------------------

async def connect_openai():

    headers = [
        ("Authorization", f"Bearer {OPENAI_API_KEY}"),
        ("OpenAI-Beta", "realtime=v1")
    ]

    ws = await websockets.connect(OPENAI_REALTIME_URL, extra_headers=headers)

    # Setăm vocea coral și promptul
    await ws.send(json.dumps({
        "type": "session.update",
        "session": {
            "instructions": SANTA_PROMPT,
            "modalities": ["audio", "text"],
            "voice": "coral",            # cea mai subțire
            "input_audio_format": "pcm16",
            "output_audio_format": "pcm16",
            "turn_detection": {"type": "server_vad"},
        }
    }))

    # Începe cu mesajul cerut
    await ws.send(json.dumps({
        "type": "response.create",
        "response": {
            "modalities": ["audio", "text"],
            "instructions":
                "Speak very fast, natural and warm. Start EXACTLY with: "
                "\"Ho-ho-ho! Bună drag copil, sunt Moș Crăciun! Ce faci, puișor?\""
        }
    }))

    return ws


# ----------------------------------------------------------
# Session state
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

    AMP = 900   # mai sensibil → întrerupe mai repede

    try:
        while True:
            msg = await vonage_ws.receive()

            if msg["type"] == "websocket.disconnect":
                break

            audio = msg.get("bytes")
            if not audio:
                continue

            samples = struct.unpack("<" + "h" * (len(audio)//2), audio)

            # mai ușor de întrerupt
            if max(abs(s) for s in samples) > AMP and session.response_active:
                print("BARGE-IN detected -> cancel response")
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
# Timer
# ----------------------------------------------------------

async def call_timer(openai_ws, vonage_ws: WebSocket, session: CallSession):

    await asyncio.sleep(240)
    if session.ws_closed: return

    session.closing_phase = True
    print("CALL TIMER: ending soon")

    await openai_ws.send(json.dumps({
        "type": "input_text",
        "text": (
            "As Santa, speak FAST. Tell the child you must leave soon, "
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
# WS handler
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
