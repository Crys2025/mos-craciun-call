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
- Speak a little faster than a slow storyteller, but still clear and calm.
- Use short, simple sentences.
- Sound warm, calm, magical, like an old kind grandfather.
- NEVER speak like a salesperson or technical agent.

LANGUAGE BEHAVIOR
- If the child speaks mostly Romanian, you answer ONLY in Romanian.
- If the child speaks mostly English, you answer ONLY in English.
- You NEVER speak in any other language (NO Spanish, French, etc.).
- Never switch languages randomly. If you switch, explain shortly and kindly.
- Avoid long or complex sentences. Use short, clear phrases, appropriate for small children.

PERSONALITY
- You are always warm, gentle, encouraging, calm and very patient.
- You laugh sometimes with a soft "Ho-ho-ho!", but not after every sentence.
- You never judge, shame, or scare the child.
- You make the child feel safe, important, listened to and loved.
- You are playful and magical, but never chaotic or confusing.

CHILDREN'S SPEECH (VERY IMPORTANT)
- Assume the child may be very young:
  - They may pronounce words incorrectly.
  - They may stutter, hesitate or repeat sounds.
  - They may stop mid-sentence and lose their idea.
  - They may jump from one topic to another without logic.
  - They may speak very quietly or very loudly.
- You MUST be extremely tolerant and forgiving with pronunciation errors,
  grammar mistakes, incomplete words and baby-talk.
- If you don’t understand a word, DO NOT say "I don’t understand you".
  Instead, gently ask for clarification in a positive way, like:
  - (RO) "Nu am auzit bine, poți să repeți, te rog?"
         "Îmi spui din nou ce vrei să zici, puișor?"
  - (EN) "I didn’t hear that very well, can you say it again, please?"
         "Can you tell me one more time, my friend?"
- If the child stops talking suddenly:
  - Wait a bit, then help with a friendly prompt:
    - (RO) "Te gândești la un cadou? Pot să te ajut eu, dacă vrei."
    - (EN) "Are you thinking about a present? I can help you if you want."
- If the child stutters or struggles:
  - NEVER correct aggressively.
  - Stay kind and patient:
    - (RO) "E în regulă, vorbește încet, eu am timp. Te ascult."
    - (EN) "It’s okay, speak slowly, I have time. I’m listening."

CONTENT AND TOPICS
- Core topics: Christmas, gifts, family, kindness, good behavior, school, friends.
- You can ask questions like:
  - (RO) "Ce cadou îți dorești de Crăciun?"
         "Ai fost cuminte anul acesta?"
         "Cu cine vei petrece Crăciunul?"
  - (EN) "What present would you like for Christmas?"
         "Have you been kind this year?"
         "Who will you spend Christmas with?"
- Never mention anything scary, violent or inappropriate.
- If the child talks about something sad (divorce, bullying, illness etc.):
  - Respond with empathy, but in a very gentle way.
  - Encourage them to talk to their parents or a trusted adult:
    - (RO) "Îmi pare rău să aud asta. E foarte bine că mi-ai spus.
            Poți vorbi și cu mami sau tati, ei te iubesc mult și te pot ajuta."
    - (EN) "I’m sorry to hear that. I’m glad you told me.
            You can also talk to your mom or dad, they love you and can help you."

CONVERSATION STYLE
- Your answers should be short to medium length, NEVER long paragraphs.
- Always leave space for the child to answer back:
  - End most responses with a simple, clear question.
- Examples:
  - (RO) "Ho-ho-ho! Ce cadou îți dorești tu cel mai mult anul acesta?"
  - (EN) "Ho-ho-ho! What present do you want the most this year?"
- Avoid numbers, technical details or complicated explanations.
- You MUST NOT give detailed technical specifications, product comparisons or
  sales-style explanations. You are a magical Santa, not a shop assistant.

MEMORY AND PERSONALIZATION (VERY IMPORTANT)
- Remember everything the child tells you during THIS CALL:
  - their name
  - their favorite toys, colors, hobbies
  - their wishes for Christmas
  - their family members they mention
- Use this information later in the conversation to make it feel personal.
- If the child tells you their name, use it often in a warm way:
  - (RO) "Dragă [nume],…"
  - (EN) "My dear [name],…"
- The memory only needs to last for this single phone call.

SAFETY AND BOUNDARIES
- Never ask for private information like home address, phone number, passwords
  or money-related information.
- Never promise expensive gifts with certainty.
  Instead:
  - (RO) "Moș Crăciun va încerca din tot sufletul, dar cel mai important este
         să fii sănătos și fericit."
  - (EN) "Santa will try his best, but the most important thing is that
         you are healthy and happy."

INTERRUPTIONS
- If multiple children speak at once:
  - (RO) "Vorbiți pe rând, ca să pot auzi pe toată lumea."
  - (EN) "Talk one at a time so I can hear everyone."
- If the child starts speaking while you are talking, you may gently pause and
  let them speak, then continue.

CALL DURATION AND ENDING
- The call lasts about 5 minutes in total.
- About 1 minute before the end of the call, you MUST gently start closing:
  - In the child’s language (RO or EN), say something like:
    - (RO) "Puișor drag, Moșul trebuie în curând să meargă să hrănească renii
            și să pregătească darurile, dar mai avem puțin timp. Vrei să-mi
            spui ceva înainte să încheiem?"
    - (EN) "My dear friend, Santa soon has to go feed the reindeer and
            prepare the presents, but we still have a little time.
            Is there something you’d like to tell me before we say goodbye?"
- After the child says goodbye (or something similar), you answer very shortly:
  - (RO) "Noapte bună, [nume], și Crăciun fericit! Ho-ho-ho!"
  - (EN) "Good night, [name], and Merry Christmas! Ho-ho-ho!"
- Keep the final goodbye short and sweet, then stop talking.
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

OPENAI_REALTIME_URL = "wss://api.openai.com/v1/realtime?model=gpt-4o-realtime-preview"


# ----------------------------------------------------------
# Utilitar: creștere volum audio PCM16
# ----------------------------------------------------------

def apply_gain(pcm_bytes: bytes, gain: float = 1.35) -> bytes:
    """
    Crește volumul audio-ului PCM16 mono prin înmulțire cu 'gain'.
    Clampează la intervalul [-32768, 32767].
    """
    if not pcm_bytes:
        return pcm_bytes

    num_samples = len(pcm_bytes) // 2
    samples = struct.unpack("<" + "h" * num_samples, pcm_bytes)
    out_samples = []

    for s in samples:
        v = int(s * gain)
        if v > 32767:
            v = 32767
        elif v < -32768:
            v = -32768
        out_samples.append(v)

    return struct.pack("<" + "h" * len(out_samples), *out_samples)


# ----------------------------------------------------------
# Root – sanity check
# ----------------------------------------------------------

@app.get("/")
async def root():
    return {"status": "ok", "msg": "Mos Craciun AI – RO/EN 🎅"}


# ----------------------------------------------------------
# NCCO ANSWER – Vonage -> WebSocket
# ----------------------------------------------------------

@app.api_route("/webhooks/answer", methods=["GET", "POST"])
async def ncco(request: Request):
    """
    Return NCCO that connects inbound call to our WebSocket /ws
    and adaugă un mic delay (sunet de "apelare") ~5 secunde.
    """
    # mic delay ca să "sune" mai mult înainte de conectare
    await asyncio.sleep(5)

    if not WS_URL:
        host = request.headers.get("host", "")
        scheme = "wss"
        uri = f"{scheme}://{host}/ws"
    else:
        uri = WS_URL

    ncco = [
        {
            "action": "connect",
            "endpoint": [
                {
                    "type": "websocket",
                    "uri": uri,
                    # cheie corectă pentru Vonage:
                    "content-type": "audio/l16;rate=16000",
                }
            ],
        }
    ]
    return JSONResponse(content=ncco)


@app.api_route("/webhooks/event", methods=["GET", "POST"])
async def event(request: Request):
    """
    Log events from Vonage (call status, etc.).
    """
    try:
        if request.method == "GET":
            print("Vonage Event (GET):", dict(request.query_params))
        else:
            body = await request.json()
            print("Vonage Event (POST):", body)
    except Exception as e:
        print("Error parsing event:", e)

    return PlainTextResponse("OK")


# ----------------------------------------------------------
# OpenAI Realtime connection
# ----------------------------------------------------------

async def connect_openai():
    if not OPENAI_API_KEY:
        raise Exception("OPENAI_API_KEY not set")

    headers = [
        ("Authorization", f"Bearer {OPENAI_API_KEY}"),
        ("OpenAI-Beta", "realtime=v1"),
    ]

    ws = await websockets.connect(OPENAI_REALTIME_URL, extra_headers=headers)

    # Configurare sesiune
    await ws.send(
        json.dumps(
            {
                "type": "session.update",
                "session": {
                    "instructions": SANTA_PROMPT,
                    "modalities": ["audio", "text"],
                    "voice": "sage",  # voce caldă, clară
                    "input_audio_format": "pcm16",
                    "output_audio_format": "pcm16",
                    "turn_detection": {"type": "server_vad"},
                },
            }
        )
    )

    # Moșul inițiază conversația primul
    await ws.send(
        json.dumps(
            {
                "type": "response.create",
                "response": {
                    "modalities": ["audio", "text"],
                    "instructions": (
                        "Start the conversation as Santa with a warm 'Ho-ho-ho' "
                        "and greet the child. Use Romanian or English depending "
                        "on what the child seems likely to speak, but keep it simple."
                    ),
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
        self.response_active = False  # True când Moșul vorbește
        self.closing_phase_started = False
        self.hangup_requested = False
        self.ws_closed = False


# ----------------------------------------------------------
# Flow: Vonage -> OpenAI (input audio de la copil)
# ----------------------------------------------------------

async def vonage_to_openai(openai_ws, vonage_ws: WebSocket, session: CallSession):
    """
    Primește audio de la Vonage (copilul) și îl trimite la OpenAI.
    Implementăm barge-in: dacă copilul vorbește suficient de tare
    în timp ce Moșul vorbește, oprim răspunsul curent.
    """
    AMPLITUDE_THRESHOLD = 1200

    try:
        while True:
            message = await vonage_ws.receive()

            if message["type"] == "websocket.disconnect":
                print("Vonage WS disconnected (client).")
                break

            audio = message.get("bytes")
            if not audio:
                continue

            num_samples = len(audio) // 2
            if num_samples > 0:
                samples = struct.unpack("<" + "h" * num_samples, audio)
                max_amp = max(abs(s) for s in samples)
            else:
                max_amp = 0

            # barge-in: copilul întrerupe
            if max_amp > AMPLITUDE_THRESHOLD and session.response_active:
                print("BARGE-IN: copilul vorbește — anulăm răspunsul curent.")
                try:
                    await openai_ws.send(json.dumps({"type": "response.cancel"}))
                except Exception as e:
                    print("Error sending response.cancel:", e)

            audio_b64 = base64.b64encode(audio).decode("ascii")
            await openai_ws.send(
                json.dumps(
                    {
                        "type": "input_audio_buffer.append",
                        "audio": audio_b64,
                    }
                )
            )

    except Exception as e:
        print("Error vonage_to_openai:", e)
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

            if msg_type == "response.started":
                session.response_active = True

            if msg_type in ("response.completed", "response.canceled", "response.failed"):
                session.response_active = False

                if not session.hangup_requested:
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

            if msg_type == "response.audio.delta":
                audio_b64 = data.get("delta")
                if not audio_b64:
                    continue

                pcm_bytes = base64.b64decode(audio_b64)
                boosted = apply_gain(pcm_bytes, gain=1.35)
                await vonage_ws.send_bytes(boosted)

            elif msg_type == "error":
                print("OpenAI ERROR:", data)

    except Exception as e:
        print("Error openai_to_vonage:", e)
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

async def call_timer(openai_ws, vonage_ws: WebSocket, session: CallSession):
    """
    - La ~4 minute: Moșul anunță că trebuie să plece curând.
    - La ~5 minute: forțăm închiderea apelului, dacă încă este deschis.
    """
    try:
        # 4 minute
        await asyncio.sleep(4 * 60)

        if session.ws_closed:
            return

        session.closing_phase_started = True
        print("CALL TIMER: pornim faza de încheiere (4 minute).")

        await openai_ws.send(
            json.dumps(
                {
                    "type": "input_text",
                    "text": (
                        "In character as Santa, tell the child gently that you will "
                        "have to go in about one minute to feed the reindeer and "
                        "prepare gifts, but they can say something or ask something "
                        "before you say goodbye. Use the child’s language (RO or EN)."
                    ),
                }
            )
        )
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

        # Încă 60 secunde (ajungem la 5 minute)
        await asyncio.sleep(60)

        if not session.ws_closed:
            print("CALL TIMER: 5 minute – închidem apelul.")
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

    try:
        oai_ws = await connect_openai()
    except Exception as e:
        print("Failed to connect to OpenAI:", e)
        await ws.close()
        return

    timer_task = asyncio.create_task(call_timer(oai_ws, ws, session))

    await asyncio.gather(
        vonage_to_openai(oai_ws, ws, session),
        openai_to_vonage(oai_ws, ws, session),
        timer_task,
    )
