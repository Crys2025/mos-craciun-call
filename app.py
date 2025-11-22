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

SPEAKING SPEED
- Speak slightly faster than a normal storyteller (warm, clear, friendly).
- Do NOT speak too fast. Just a gentle increase in energy.

LANGUAGE BEHAVIOR
- If the child speaks mostly Romanian, you answer ONLY in Romanian.
- If the child speaks mostly English, you answer ONLY in English.
- You NEVER speak in any other language (NO Spanish, French, etc.).
- Never switch languages randomly. If switching is needed, explain gently.
- Use short, clear sentences appropriate for young children.

PERSONALITY
- You are warm, gentle, magical, patient.
- You laugh sometimes with a soft "Ho-ho-ho!", not too often.
- You never judge or scare the child.
- You are kind, encouraging, loving, reassuring.

CHILDREN'S SPEECH (IMPORTANT)
- Children may stutter, pause, mispronounce words, or jump between ideas.
- Be extremely patient and supportive.
- If you don't understand a word, ask gently:
  - (RO) "Nu am auzit bine, puișor. Poți să repeți?"
  - (EN) "I didn’t hear that well, my friend. Can you say it again?"
- If they stop talking, help with a friendly prompt:
  - (RO) "Te gândești la un cadou?"
  - (EN) "Are you thinking about a present?"

MEMORY
- Remember the child's name, wishes, hobbies, colors, toys, and family during THIS call.
- Use them later naturally.
- Memory resets each call.

CALL TOPICS
- Christmas, gifts, kindness, family, school, good behavior.
- Safe, warm topics.

SAFETY
- Never ask for private info: address, phone, passwords, money.
- If the child shares something sad, respond kindly and gently.

CALL ENDING (after ~5 minutes)
- One minute before ending, tell the child you must soon go feed the reindeer.
- After they say goodbye, reply shortly:
  - (RO) "Noapte bună, dragul meu [nume]! Crăciun fericit! Ho-ho-ho!"
  - (EN) "Good night, my dear [name]! Merry Christmas! Ho-ho-ho!"
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
WS_URL = os.getenv("WS_URL")  # ex: wss://mos-craciun-call-1.onrender.com/ws

OPENAI_REALTIME_URL = (
    "wss://api.openai.com/v1/realtime?model=gpt-4o-realtime-preview"
)


# ----------------------------------------------------------
# Utilitar: creștere volum audio PCM16
# ----------------------------------------------------------

def apply_gain(pcm_bytes: bytes, gain: float = 1.3) -> bytes:
    """
    Crește volumul audio PCM16 mono prin înmulțire cu gain.
    """
    if not pcm_bytes:
        return pcm_bytes

    num_samples = len(pcm_bytes) // 2
    samples = struct.unpack("<" + "h" * num_samples, pcm_bytes)
    boosted = []

    for s in samples:
        v = int(s * gain)
        if v > 32767:
            v = 32767
        elif v < -32768:
            v = -32768
        boosted.append(v)

    return struct.pack("<" + "h" * len(boosted), *boosted)


# ----------------------------------------------------------
# Root – sanity check
# ----------------------------------------------------------

@app.get("/")
async def root():
    return {"status": "ok", "msg": "Mos Craciun AI – RO/EN 🎅"}


# ----------------------------------------------------------
# NCCO ANSWER – Vonage -> WebSocket (cu 5 secunde sunat)
# ----------------------------------------------------------

@app.api_route("/webhooks/answer", methods=["GET", "POST"])
async def ncco(request: Request):
    """
    NCCO valid Vonage: sună 5 secunde și apoi conectează la WebSocket.
    """
    if not WS_URL:
        host = request.headers.get("host", "")
        uri = f"wss://{host}/ws"
    else:
        uri = WS_URL

    ncco = [
        {   # TRUC Vonage: Talk gol → permite pauză validă
            "action": "talk",
            "text": " "
        },
        {
            "action": "pause",
            "length": 5
        },
        {
            "action": "connect",
            "endpoint": [
                {
                    "type": "websocket",
                    "uri": uri,
                    "content-type": "audio/l16;rate=16000",
                    "headers": {}
                }
            ],
        }
    ]

    return JSONResponse(ncco)


# ----------------------------------------------------------
# Event Hook – pentru debug
# ----------------------------------------------------------

@app.api_route("/webhooks/event", methods=["GET", "POST"])
async def event(request: Request):
    try:
        if request.method == "GET":
            print("Vonage Event:", dict(request.query_params))
        else:
            print("Vonage Event POST:", await request.json())
    except Exception as e:
        print("Event parse error:", e)
    return PlainTextResponse("OK")
# ----------------------------------------------------------
# OpenAI Realtime connection (Moșul vorbește primul)
# ----------------------------------------------------------

async def connect_openai():
    if not OPENAI_API_KEY:
        raise Exception("OPENAI_API_KEY not set")

    headers = [
        ("Authorization", f"Bearer {OPENAI_API_KEY}"),
        ("OpenAI-Beta", "realtime=v1"),
    ]

    # Conectare WebSocket la OpenAI Realtime
    ws = await websockets.connect(OPENAI_REALTIME_URL, extra_headers=headers)

    # Configurare sesiune – voce, formate audio, VAD, instrucțiuni Moș Crăciun
    await ws.send(
        json.dumps(
            {
                "type": "session.update",
                "session": {
                    "instructions": SANTA_PROMPT,
                    "modalities": ["audio", "text"],
                    "voice": "sage",              # voce caldă, clară
                    "input_audio_format": "pcm16",
                    "output_audio_format": "pcm16",
                    "turn_detection": {"type": "server_vad"},
                },
            }
        )
    )

    # Moșul deschide conversația primul – salut inițial
    await ws.send(
        json.dumps(
            {
                "type": "input_text",
                "text": (
                    "As Santa Claus, start the call by greeting the child warmly. "
                    "Use Romanian if the child sounds Romanian, or English otherwise. "
                    "Say something like: 'Ho-ho-ho! Bună, dragă copil, sunt Moș Crăciun!' "
                    "or 'Ho-ho-ho! Hello, my dear child, I am Santa Claus!'. "
                    "Keep it short and friendly and then let the child speak."
                ),
            }
        )
    )

    # Cerem primul răspuns (salutul lui Moș Crăciun)
    await ws.send(
        json.dumps(
            {
                "type": "response.create",
                "response": {
                    "modalities": ["audio", "text"]
                },
            }
        )
    )

    return ws


# ----------------------------------------------------------
# Structură de sesiune pentru apel (stare comună)
# ----------------------------------------------------------

class CallSession:
    def __init__(self):
        self.start_time = time.time()
        self.response_active = False      # True când Moșul vorbește
        self.closing_phase_started = False
        self.hangup_requested = False
        self.ws_closed = False


# ----------------------------------------------------------
# Flow: Vonage -> OpenAI (input audio de la copil)
# ----------------------------------------------------------

async def vonage_to_openai(openai_ws, vonage_ws: WebSocket, session: CallSession):
    """
    Primește audio de la Vonage (copilul) și îl trimite la OpenAI.
    Implementăm și barge-in: dacă copilul vorbește suficient de tare
    în timp ce Moșul vorbește, oprim răspunsul curent.
    """
    AMPLITUDE_THRESHOLD = 1200  # prag pentru "copilul chiar vorbește"

    try:
        while True:
            msg = await vonage_ws.receive()

            if msg["type"] == "websocket.disconnect":
                print("Vonage WS disconnected (client).")
                break

            audio = msg.get("bytes")
            if not audio:
                # ignorăm eventuale text frames
                continue

            # Detectăm amplitudinea maximă (heuristic barge-in)
            num_samples = len(audio) // 2
            if num_samples > 0:
                samples = struct.unpack("<" + "h" * num_samples, audio)
                max_amp = max(abs(s) for s in samples)
            else:
                max_amp = 0

            # Dacă copilul vorbește tare și Moșul e în plin răspuns → barge-in
            if max_amp > AMPLITUDE_THRESHOLD and session.response_active:
                print("BARGE-IN: copilul vorbește – anulăm răspunsul curent.")
                try:
                    await openai_ws.send(
                        json.dumps(
                            {
                                "type": "response.cancel"
                            }
                        )
                    )
                except Exception as e:
                    print("Error sending response.cancel:", e)

            # Trimitem audio către OpenAI
            audio_b64 = base64.b64encode(audio).decode("ascii")
            try:
                await openai_ws.send(
                    json.dumps(
                        {
                            "type": "input_audio_buffer.append",
                            "audio": audio_b64,
                        }
                    )
                )
            except Exception as e:
                print("Error sending audio to OpenAI:", e)
                break

        # La ieșire – marcăm că vrem închiderea apelului
    except Exception as e:
        print("Error in vonage_to_openai:", e)
    finally:
        session.hangup_requested = True
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
# Flow: OpenAI -> Vonage (răspuns Moș Crăciun)
# ----------------------------------------------------------

async def openai_to_vonage(openai_ws, vonage_ws: WebSocket, session: CallSession):
    try:
        async for msg in openai_ws:
            try:
                data = json.loads(msg)
            except Exception as e:
                print("Error parsing OpenAI msg:", e)
                continue

            msg_type = data.get("type")

            # urmăriram starea răspunsului (Moșul vorbește / nu)
            if msg_type == "response.started":
                session.response_active = True

            if msg_type in ("response.completed", "response.canceled", "response.failed"):
                session.response_active = False

                # după orice răspuns, dacă nu vrem să închidem, pregătim următorul turn
                if not session.hangup_requested:
                    try:
                        await openai_ws.send(
                            json.dumps(
                                {
                                    "type": "response.create",
                                    "response": {
                                        "modalities": ["audio", "text"]
                                    },
                                }
                            )
                        )
                    except Exception as e:
                        print("Error creating next response:", e)

            # bucăți de audio generate de Moș Crăciun
            if msg_type == "response.audio.delta":
                audio_b64 = data.get("delta")
                if not audio_b64:
                    continue

                pcm_bytes = base64.b64decode(audio_b64)

                # creștem volumul ~30% pentru a se auzi mai tare în difuzor
                boosted = apply_gain(pcm_bytes, gain=1.3)

                try:
                    await vonage_ws.send_bytes(boosted)
                except Exception as e:
                    print("Error sending audio to Vonage:", e)
                    break

            elif msg_type == "error":
                print("OpenAI ERROR:", data)

    except Exception as e:
        print("Error in openai_to_vonage:", e)
    finally:
        session.hangup_requested = True
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
# Timer apel – 5 minute + mesaj de încheiere
# ----------------------------------------------------------

async def call_timer(openai_ws, session: CallSession):
    """
    - La ~4 minute: Moșul anunță că trebuie să plece curând (dar mai stă puțin).
    - La ~5 minute: dacă nu s-a închis deja, închidem apelul.
    """
    try:
        # așteptăm 4 minute înainte de pre-final
        await asyncio.sleep(4 * 60)

        if session.ws_closed:
            return

        session.closing_phase_started = True
        print("CALL TIMER: pornim faza de încheiere (4 minute).")

        # injectăm text – instrucțiuni ca Moșul să anunțe că pleacă în curând
        await openai_ws.send(
            json.dumps(
                {
                    "type": "input_text",
                    "text": (
                        "In character as Santa, tell the child gently that you will "
                        "have to go in about one minute to feed the reindeer and "
                        "prepare the presents. Invite the child to say something or "
                        "to say goodbye. Use the child’s language (Romanian or English). "
                        "Keep it short and warm, then let the child answer."
                    ),
                }
            )
        )
        # forțăm un răspuns pentru acest mesaj
        await openai_ws.send(
            json.dumps(
                {
                    "type": "response.create",
                    "response": {
                        "modalities": ["audio", "text"]
                    },
                }
            )
        )

        # mai așteptăm aproximativ 1 minut (până la 5 minute total)
        await asyncio.sleep(60)

        if not session.ws_closed:
            print("CALL TIMER: 5 minute – cerem închiderea apelului.")
            session.hangup_requested = True
            try:
                await openai_ws.close()
            except:
                pass

    except Exception as e:
        print("Error in call_timer:", e)


# ----------------------------------------------------------
# WebSocket endpoint pentru Vonage
# ----------------------------------------------------------

@app.websocket("/ws")
async def ws_handler(ws: WebSocket):
    await ws.accept()
    print("Vonage WebSocket connected.")

    session = CallSession()

    # conectăm la OpenAI Realtime
    try:
        oai_ws = await connect_openai()
    except Exception as e:
        print("Failed to connect to OpenAI:", e)
        await ws.close()
        return

    # pornim timerul de 5 minute (anunț la 4 min + închidere la 5)
    timer_task = asyncio.create_task(call_timer(oai_ws, session))

    # rulăm cele 3 task-uri în paralel:
    # - audio copil -> OpenAI
    # - audio Moș -> copil
    # - timer apel
    await asyncio.gather(
        vonage_to_openai(oai_ws, ws, session),
        openai_to_vonage(oai_ws, ws, session),
        timer_task,
    )
