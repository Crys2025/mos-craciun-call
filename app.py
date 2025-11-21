import os
import json
import base64
import asyncio
import wave
import struct

from fastapi import FastAPI, Request, WebSocket
from fastapi.responses import JSONResponse, PlainTextResponse
from fastapi.middleware.cors import CORSMiddleware

import websockets


# ----------------------------------------------------------
# PROMPT – Moș Crăciun RO/EN, blând, fără răspunsuri tehnice
# ----------------------------------------------------------

SANTA_PROMPT = """
You are “Moș Crăciun / Santa Claus”, a warm, kind, patient grandfather-like character.
You speak ONLY Romanian and English and you ALWAYS detect the child’s language automatically
from their voice or words.

LANGUAGE BEHAVIOR
- If the child speaks mostly Romanian, you answer ONLY in Romanian.
- If the child speaks mostly English, you answer ONLY in English.
- If the child mixes both, you gently choose the language that seems more comfortable for the child.
- You NEVER speak in any other language (NO Spanish, French, etc.).
- Never switch languages randomly. If you switch, explain shortly and kindly.
- Avoid long or complex sentences. Use short, clear phrases, appropriate for small children.

PERSONALITY
- You are always warm, gentle, encouraging, calm and very patient.
- You laugh sometimes with a soft “Ho-ho-ho!”, but not after every sentence.
- You never judge, shame, or scare the child.
- You make the child feel safe, important, listened to and loved.
- You are playful and magical, but never chaotic or confusing.
- You NEVER sound like a salesperson or a technical support agent.

CHILDREN’S SPEECH (VERY IMPORTANT)
- Assume the child may be very young:
  - They may pronounce words incorrectly.
  - They may stutter, hesitate or repeat sounds.
  - They may stop mid-sentence and lose their idea.
  - They may jump from one topic to another without logic.
  - They may speak very quietly or very loudly.
- You MUST be extremely tolerant and forgiving with pronunciation errors,
  grammar mistakes, incomplete words and baby-talk.
- If you don’t understand a word, DO NOT say “I don’t understand you”.
  Instead, gently ask for clarification in a positive way, like:
  - (RO) “Nu am auzit bine, poți să repeți, te rog?”
         “Îmi spui din nou ce vrei să zici, puișor?”
  - (EN) “I didn’t hear that very well, can you say it again, please?”
         “Can you tell me one more time, my friend?”
- If the child stops talking suddenly:
  - Wait a bit, then help with a friendly prompt:
    - (RO) “Te gândești la un cadou? Pot să te ajut eu, dacă vrei.”
    - (EN) “Are you thinking about a present? I can help you if you want.”
- If the child stutters or struggles:
  - NEVER correct aggressively.
  - Stay kind and patient:
    - (RO) “E în regulă, vorbește încet, eu am timp. Te ascult.”
    - (EN) “It’s okay, speak slowly, I have time. I’m listening.”

CONTENT AND TOPICS
- Core topics: Christmas, gifts, family, kindness, good behavior, school, friends.
- You can ask questions like:
  - (RO) “Ce cadou îți dorești de Crăciun?”
         “Ai fost cuminte anul acesta?”
         “Cu cine vei petrece Crăciunul?”
  - (EN) “What present would you like for Christmas?”
         “Have you been kind this year?”
         “Who will you spend Christmas with?”
- Never mention anything scary, violent or inappropriate.
- If the child talks about something sad (divorce, bullying, illness etc.):
  - Respond with empathy, but in a very gentle way.
  - Encourage them to talk to their parents or a trusted adult:
    - (RO) “Îmi pare rău să aud asta. E foarte bine că mi-ai spus.
            Poți vorbi și cu mami sau tati, ei te iubesc mult și te pot ajuta.”
    - (EN) “I’m sorry to hear that. I’m glad you told me.
            You can also talk to your mom or dad, they love you and can help you.”

CONVERSATION STYLE
- Your answers should be short to medium length, NEVER long paragraphs.
- Always leave space for the child to answer back:
  - End most responses with a simple, clear question.
- Examples:
  - (RO) “Ho-ho-ho! Ce cadou îți dorești tu cel mai mult anul acesta?”
  - (EN) “Ho-ho-ho! What present do you want the most this year?”
- Avoid numbers, technical details or complicated explanations.
- You MUST NOT give detailed technical specifications, product comparisons or
  sales-style explanations. You are a magical Santa, not a shop assistant.

MEMORY AND PERSONALIZATION
- If the child tells you their name, remember it and use it often.
- If the child mentions favorite toys or hobbies, reuse them later
  to make the conversation feel personal.

SAFETY AND BOUNDARIES
- Never ask for private information (address, phone, passwords, money etc.).
- Never promise expensive gifts with certainty.
  Instead:
  - (RO) “Moș Crăciun va încerca din tot sufletul, dar cel mai important este
         să fii sănătos și fericit.”
  - (EN) “Santa will try his best, but the most important thing is that
         you are healthy and happy.”

INTERRUPTIONS
- If multiple children speak at once:
  - (RO) “Vorbiți pe rând, ca să pot auzi pe toată lumea.”
  - (EN) “Talk one at a time so I can hear you.”

OVERALL GOAL
- Create a magical, gentle and safe Christmas experience.
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
WS_URL = os.getenv("WS_URL")  # Vonage WebSocket URI, ex: wss://.../ws

# Model Realtime mare
OPENAI_REALTIME_URL = (
    "wss://api.openai.com/v1/realtime?model=gpt-4o-realtime-preview"
)

# ----------------------------------------------------------
# BACKGROUND AUDIO – Fireplace + Bells (background.wav)
# ----------------------------------------------------------

BACKGROUND_SAMPLES = None
BACKGROUND_INDEX = 0


def load_background_audio():
    """
    Load audio/background.wav as 16-bit PCM mono 16kHz samples.
    If missing or invalid, background will be disabled.
    """
    global BACKGROUND_SAMPLES
    path = os.path.join(os.path.dirname(__file__), "audio", "background.wav")
    if not os.path.exists(path):
        print("[BACKGROUND] No background.wav found, skipping.")
        return

    try:
        with wave.open(path, "rb") as wf:
            channels = wf.getnchannels()
            sampwidth = wf.getsampwidth()
            framerate = wf.getframerate()
            nframes = wf.getnframes()

            if channels != 1 or sampwidth != 2 or framerate != 16000:
                print(
                    f"[BACKGROUND] Invalid format. Need mono, 16-bit, 16kHz. "
                    f"Got channels={channels}, sampwidth={sampwidth}, framerate={framerate}"
                )
                return

            frames = wf.readframes(nframes)
            # Convert to list of int16 samples
            BACKGROUND_SAMPLES = list(
                struct.unpack("<" + "h" * (len(frames) // 2), frames)
            )
            print(
                f"[BACKGROUND] Loaded {len(BACKGROUND_SAMPLES)} samples "
                f"({nframes / framerate:.1f} seconds)."
            )
    except Exception as e:
        print("[BACKGROUND] Error loading background.wav:", e)


def mix_with_background(fg_bytes: bytes) -> bytes:
    """
    Mix AI voice (fg_bytes) with low-volume background fireplace.
    Both are 16-bit PCM mono 16kHz.
    """
    global BACKGROUND_INDEX, BACKGROUND_SAMPLES
    if not BACKGROUND_SAMPLES:
        # no background, return original
        return fg_bytes

    # unpack foreground samples
    num_samples = len(fg_bytes) // 2
    fg_samples = list(struct.unpack("<" + "h" * num_samples, fg_bytes))

    out_samples = []
    bg_len = len(BACKGROUND_SAMPLES)
    # volum background mai mic (îl ținem discret)
    BG_VOLUME = 0.3

    for i in range(num_samples):
        bg = BACKGROUND_SAMPLES[BACKGROUND_INDEX]
        BACKGROUND_INDEX = (BACKGROUND_INDEX + 1) % bg_len

        mixed = fg_samples[i] + int(bg * BG_VOLUME)

        # clamp la int16
        if mixed > 32767:
            mixed = 32767
        elif mixed < -32768:
            mixed = -32768

        out_samples.append(mixed)

    return struct.pack("<" + "h" * len(out_samples), *out_samples)


# ----------------------------------------------------------
# Root – sanity check
# ----------------------------------------------------------

@app.get("/")
async def root():
    return {"status": "ok", "msg": "Mos Craciun AI cu fireplace 🎅🔥"}


# ----------------------------------------------------------
# NCCO ANSWER – Vonage -> WebSocket
# ----------------------------------------------------------

@app.api_route("/webhooks/answer", methods=["GET", "POST"])
async def ncco(request: Request):
    """
    Return NCCO that connects inbound call to our WebSocket /ws
    """
    if not WS_URL:
        # fallback: construim ws url relativ la domeniu
        host = request.headers.get("host", "")
        scheme = "wss"
        WS_FALLBACK = f"{scheme}://{host}/ws"
        uri = WS_FALLBACK
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
    return JSONResponse(ncco)


@app.api_route("/webhooks/event", methods=["GET", "POST"])
async def event(request: Request):
    """
    Just log events from Vonage (call status, etc.).
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

    # Sesiune inițială pentru Moș Crăciun
    await ws.send(
        json.dumps(
            {
                "type": "session.update",
                "session": {
                    "instructions": SANTA_PROMPT,
                    "modalities": ["audio", "text"],
                    "voice": "ballad",  # voce caldă, de povestitor
                    "input_audio_format": "pcm16",
                    "output_audio_format": "pcm16",
                    "turn_detection": {"type": "server_vad"},
                },
            }
        )
    )

    # Una singură – modelul va începe să răspundă când detectează vorbire
    await ws.send(
        json.dumps(
            {
                "type": "response.create",
                "response": {
                    "modalities": ["audio", "text"]
                    # voice este setat la nivel de session, nu mai punem aici
                },
            }
        )
    )

    return ws


# ----------------------------------------------------------
# Flow: Vonage -> OpenAI
# ----------------------------------------------------------

async def vonage_to_openai(openai_ws, vonage_ws: WebSocket):
    try:
        while True:
            message = await vonage_ws.receive()

            if message["type"] == "websocket.disconnect":
                print("Vonage WS disconnected (client).")
                break

            audio = message.get("bytes")
            if not audio:
                # ignore text frames (Vonage trimite doar audio)
                continue

            audio_b64 = base64.b64encode(audio).decode("ascii")

            # Adăugăm audio în bufferul de intrare
            await openai_ws.send(
                json.dumps(
                    {
                        "type": "input_audio_buffer.append",
                        "audio": audio_b64,
                    }
                )
            )

            # Nu chemăm response.create de fiecare dată,
            # server_vad se ocupă de detectarea sfârșitului de propoziție.

    except Exception as e:
        print("Error vonage_to_openai:", e)
    finally:
        try:
            await openai_ws.close()
        except:
            pass
        try:
            await vonage_ws.close()
        except:
            pass


# ----------------------------------------------------------
# Flow: OpenAI -> Vonage (cu mix background)
# ----------------------------------------------------------

async def openai_to_vonage(openai_ws, vonage_ws: WebSocket):
    try:
        async for msg in openai_ws:
            try:
                data = json.loads(msg)
            except Exception as e:
                print("Error parsing OpenAI msg:", e)
                continue

            msg_type = data.get("type")

            # audio deltas
            if msg_type == "response.audio.delta":
                audio_b64 = data.get("delta")
                if not audio_b64:
                    continue

                pcm_bytes = base64.b64decode(audio_b64)

                # mix cu fireplace + clopoței
                mixed = mix_with_background(pcm_bytes)

                await vonage_ws.send_bytes(mixed)

            elif msg_type == "response.completed":
                # la final de răspuns, cerem un nou response pentru următorul turn
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

            elif msg_type == "error":
                # log erori de la OpenAI
                print("OpenAI ERROR:", data)

    except Exception as e:
        print("Error openai_to_vonage:", e)
    finally:
        try:
            await openai_ws.close()
        except:
            pass
        try:
            await vonage_ws.close()
        except:
            pass


# ----------------------------------------------------------
# WebSocket endpoint pentru Vonage
# ----------------------------------------------------------

@app.websocket("/ws")
async def ws_handler(ws: WebSocket):
    await ws.accept()
    print("Vonage WebSocket connected.")

    # load background audio o singură dată
    global BACKGROUND_SAMPLES
    if BACKGROUND_SAMPLES is None:
        load_background_audio()

    # conectăm la OpenAI
    try:
        oai_ws = await connect_openai()
    except Exception as e:
        print("Failed to connect to OpenAI:", e)
        await ws.close()
        return

    # rulăm bidirecțional: Vonage <-> OpenAI
    await asyncio.gather(
        vonage_to_openai(oai_ws, ws),
        openai_to_vonage(oai_ws, ws),
    )
