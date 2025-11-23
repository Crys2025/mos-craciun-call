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

START OF CALL (ALWAYS THE SAME)
- You ALWAYS start the call IN ROMANIAN with EXACTLY:
  "Ho-ho-ho! Bună drag copil, sunt Moș Crăciun! Ce faci, puișor?"
- After saying this sentence, you MUST STOP and stay silent.
- Do NOT add anything else after this first sentence.
- Wait for the child to speak next.

ANSWER STYLE (VERY IMPORTANT)
- Your answers must be VERY SHORT and DIRECT.
- Maximum 1–2 short sentences each time you speak.
- Always answer EXACTLY to what the child just said.
- Do NOT change the topic.
- Do NOT add extra stories, explanations or side comments.
- Do NOT repeat the same ideas.
- If the child asks about a car, speak only about that car.
- If the child asks about school, speak only about school.
- Keep everything simple, concrete and on-topic.

VOICE & NATURAL STYLE
- Speak a bit FASTER than a normal storyteller.
- Sound like a real human grandfather: natural rhythm, small pauses, not robotic.
- Use simple words and short phrases.
- Use "Ho-ho-ho!" only sometimes, at the start of a short answer, not every time.
- Never speak like a salesperson or technical agent.

LANGUAGE BEHAVIOR
- After the first Romanian greeting, detect the child’s language:
  - If the child mostly uses Romanian → answer ONLY in Romanian.
  - If the child mostly uses English → answer ONLY in English.
- Do NOT mix Romanian and English in the same answer.
- NEVER speak any other language.
- Do not randomly switch languages. Switch only if the child clearly changes.

INTERRUPTIONS (VERY IMPORTANT)
- The system may CUT your audio when the child starts talking (barge-in).
- If that happens, treat it as the child interrupting you on purpose.
- Your NEXT answer after an interruption should:
  - Be very short.
  - Acknowledge the interruption kindly:
    - (RO) You can start with something like:
      "Te ascult, puișor, vrei să-mi spui altceva?"
    - (EN) Or:
      "I’m listening, my friend, do you want to tell me something else?"
  - Then follow ONLY the NEW idea from the child, not your old sentence.

CHILD SPEECH
- The child might:
  - Pronounce words incorrectly.
  - Stutter, hesitate or repeat sounds.
  - Change topic suddenly.
  - Be very quiet or very loud.
- Always be extremely tolerant.
- If you don’t understand, DO NOT say “I don’t understand”.
  Instead:
  - (RO) "Nu am auzit bine, poți repeta?"
  - (EN) "I didn’t hear well, can you say it again?"

WHEN THE CHILD IS QUIET
- Sometimes the child will be silent for a few seconds.
- If there is a pause and the child says nothing:
  - Gently take the initiative with ONE very short question:
    - (RO) For example: "Puișor, la ce cadou te gândești acum?"
    - (EN) For example: "My friend, what present are you thinking about now?"
  - Then wait again for the child.
- Do NOT start long monologues. Just one short question, then silence.

TOPICS
- Christmas, gifts, family, kindness, school, friends, good behavior.
- Keep everything positive, kind and safe.
- Never talk about violence, scary things, adult topics.

MEMORY DURING THIS CALL
- Remember and reuse during THIS call:
  - The child’s name.
  - Their gift wishes.
  - Their favorite toys, colors, hobbies.
  - Family members they mention.
- Use this naturally, but briefly:
  - (RO) "Dragă [nume]..."
  - (EN) "My dear [name]..."
- Do NOT overuse this. Just sometimes, to feel personal.

ENDING
- Around 4 minutes into the call:
  - In the child’s language, say very briefly that you must leave soon
    to feed the reindeer and prepare presents.
  - Ask if they want to tell you one more thing.
- Around 5 minutes:
  - Say a very short and warm goodbye:
    - (RO) "Noapte bună, [nume], și Crăciun fericit! Ho-ho-ho!"
    - (EN) "Good night, [name], and Merry Christmas! Ho-ho-ho!"
  - Then stop speaking completely.
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
                    "content-type": "audio/l16;rate=16000",
                }
            ],
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
    if not OPENAI_API_KEY:
        raise Exception("OPENAI_API_KEY not set")

    headers = [
        ("Authorization", f"Bearer {OPENAI_API_KEY}"),
        ("OpenAI-Beta", "realtime=v1"),
    ]

    ws = await websockets.connect(OPENAI_REALTIME_URL, extra_headers=headers)

    # Sesiune cu voce subțire și prompt detaliat
    await ws.send(
        json.dumps(
            {
                "type": "session.update",
                "session": {
                    "instructions": SANTA_PROMPT,
                    "modalities": ["audio", "text"],
                    "voice": "coral",  # mai subțire, caldă
                    "input_audio_format": "pcm16",
                    "output_audio_format": "pcm16",
                    "turn_detection": {"type": "server_vad"},
                },
            }
        )
    )

    # Moșul începe cu mesajul fix în română, apoi așteaptă copilul
    await ws.send(
        json.dumps(
            {
                "type": "response.create",
                "response": {
                    "modalities": ["audio", "text"],
                    "instructions": (
                        "Say ONLY this exact sentence in Romanian and nothing else:\n"
                        "Ho-ho-ho! Bună drag copil, sunt Moș Crăciun! Ce faci, puișor?\n"
                        "Then stop speaking and wait silently for the child."
                    ),
                },
            }
        )
    )

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
        # pentru tăcere: ultima dată când am auzit copilul
        self.last_child_audio_time = time.time()


# ----------------------------------------------------------
# Vonage -> OpenAI (input copil + barge-in foarte sensibil)
# ----------------------------------------------------------

async def vonage_to_openai(openai_ws, vonage_ws: WebSocket, session: CallSession):

    # prag foarte mic pentru întrerupere
    AMP_BARGE_IN = 300
    AMP_SPEECH = 150  # pentru a considera că e vorbire reală

    try:
        while True:
            msg = await vonage_ws.receive()

            if msg["type"] == "websocket.disconnect":
                print("Vonage WS disconnected.")
                break

            audio = msg.get("bytes")
            if not audio or len(audio) < 2:
                continue

            num_samples = len(audio) // 2
            samples = struct.unpack("<" + "h" * num_samples, audio)
            max_amp = max(abs(s) for s in samples)

            # copilul face zgomot / vorbește → actualizăm ultima activitate
            if max_amp > AMP_SPEECH:
                session.last_child_audio_time = time.time()

            # barge-in foarte rapid: la zgomot mic, dacă Moșul vorbește
            if max_amp > AMP_BARGE_IN and session.response_active:
                print("BARGE-IN: copilul întrerupe, anulăm răspunsul curent.")
                try:
                    await openai_ws.send(json.dumps({"type": "response.cancel"}))
                except Exception as e:
                    print("Error sending response.cancel:", e)

            # trimitem audio copil -> OpenAI
            await openai_ws.send(
                json.dumps(
                    {
                        "type": "input_audio_buffer.append",
                        "audio": base64.b64encode(audio).decode(),
                    }
                )
            )

    except Exception as e:
        print("Error V->O:", e)

    finally:
        session.hangup = True
        if not session.ws_closed:
            session.ws_closed = True
            try:
                await openai_ws.close()
            except Exception:
                pass
            try:
                await vonage_ws.close()
            except Exception:
                pass


# ----------------------------------------------------------
# OpenAI -> Vonage (răspuns Moș Crăciun)
# ----------------------------------------------------------

async def openai_to_vonage(openai_ws, vonage_ws: WebSocket, session: CallSession):

    try:
        async for raw in openai_ws:
            try:
                data = json.loads(raw)
            except Exception as e:
                print("Error parsing OpenAI msg:", e)
                continue

            t = data.get("type")

            if t == "response.started":
                session.response_active = True

            if t in ("response.completed", "response.canceled", "response.failed"):
                session.response_active = False

                # pregătim următorul răspuns (următorul turn al copilului)
                if not session.hangup:
                    await openai_ws.send(
                        json.dumps(
                            {
                                "type": "response.create",
                                "response": {"modalities": ["audio", "text"]},
                            }
                        )
                    )

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
        if not session.ws_closed:
            session.ws_closed = True
            try:
                await openai_ws.close()
            except Exception:
                pass
            try:
                await vonage_ws.close()
            except Exception:
                pass


# ----------------------------------------------------------
# Watcher pentru tăcere – Moșul preia inițiativa
# ----------------------------------------------------------

async def silence_watcher(openai_ws, session: CallSession):
    SILENCE_SECONDS = 7  # după ~7s de liniște, Moșul pune o întrebare scurtă

    try:
        while not session.ws_closed and not session.hangup:
            await asyncio.sleep(1)

            # dacă Moșul vorbește, nu intervenim
            if session.response_active:
                continue

            now = time.time()
            if now - session.last_child_audio_time > SILENCE_SECONDS:
                print("SILENCE: copilul e liniștit, Moșul pune o întrebare scurtă.")
                session.last_child_audio_time = now  # reset ca să nu repete imediat

                await openai_ws.send(
                    json.dumps(
                        {
                            "type": "input_text",
                            "text": (
                                "The child has been quiet for a few seconds. "
                                "As Santa, ask ONE very short, simple question "
                                "to gently keep the conversation going, using "
                                "the language you have been using (RO or EN). "
                                "Keep it strictly on-topic and very brief."
                            ),
                        }
                    )
                )
                await openai_ws.send(
                    json.dumps(
                        {
                            "type": "response.create",
                            "response": {"modalities": ["audio", "text"]},
                        }
                    )
                )

    except Exception as e:
        print("Error in silence_watcher:", e)


# ----------------------------------------------------------
# Timer 4 + 5 minute
# ----------------------------------------------------------

async def call_timer(openai_ws, vonage_ws: WebSocket, session: CallSession):

    try:
        # după 4 minute – anunțăm că pleacă în curând
        await asyncio.sleep(240)

        if session.ws_closed:
            return

        session.closing_phase = True
        print("CALL TIMER: începe faza de încheiere (4 minute).")

        await openai_ws.send(
            json.dumps(
                {
                    "type": "input_text",
                    "text": (
                        "Around 4 minutes have passed. As Santa, tell the child "
                        "very briefly that you must leave soon to feed the "
                        "reindeer and prepare gifts, and ask if they want to "
                        "tell you one more thing. Use RO or EN based on the "
                        "language you have been using. Keep it very short."
                    ),
                }
            )
        )
        await openai_ws.send(
            json.dumps(
                {
                    "type": "response.create",
                    "response": {"modalities": ["audio", "text"]},
                }
            )
        )

        # încă 60 secunde până la 5 minute
        await asyncio.sleep(60)

        if not session.ws_closed:
            print("CALL TIMER: 5 minute – închidem apelul.")
            session.hangup = True
            session.ws_closed = True
            try:
                await openai_ws.close()
            except Exception:
                pass
            try:
                await vonage_ws.close()
            except Exception:
                pass

    except Exception as e:
        print("Error in call_timer:", e)


# ----------------------------------------------------------
# WebSocket handler
# ----------------------------------------------------------

@app.websocket("/ws")
async def ws_handler(ws: WebSocket):
    await ws.accept()
    print("Vonage WebSocket connected.")

    session = CallSession()

    try:
        oai_ws = await connect_openai()
    except Exception as e:
        print("Failed to connect to OpenAI:", e)
        await ws.close()
        return

    timer = asyncio.create_task(call_timer(oai_ws, ws, session))
    silence = asyncio.create_task(silence_watcher(oai_ws, session))

    await asyncio.gather(
        vonage_to_openai(oai_ws, ws, session),
        openai_to_vonage(oai_ws, ws, session),
        timer,
        silence,
    )
