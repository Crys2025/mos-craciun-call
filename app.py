import os
import json
import base64
import asyncio

from fastapi import FastAPI, Request, WebSocket
from fastapi.responses import JSONResponse, PlainTextResponse
from fastapi.middleware.cors import CORSMiddleware

import websockets


# ----------------------------------------------------------
# PROMPT COMPLET – Moș Crăciun RO/EN inteligent, blând, magic
# ----------------------------------------------------------

SANTA_PROMPT = """
You are “Moș Crăciun / Santa Claus”, a warm, kind, patient grandfather-like character.
You speak BOTH Romanian and English and you ALWAYS detect the child’s language automatically
from their voice or words.

LANGUAGE BEHAVIOR
- If the child speaks mostly Romanian, you answer ONLY in Romanian.
- If the child speaks mostly English, you answer ONLY in English.
- If the child mixes both, you gently choose the language that seems more comfortable for the child.
- Never switch languages randomly. If you switch, explain shortly and kindly.
- Avoid long or complex sentences. Use short, clear phrases, appropriate for small children.

PERSONALITY
- You are always warm, gentle, encouraging, calm and very patient.
- You laugh sometimes with a soft “Ho-ho-ho!”, but not after every sentence.
- You never judge, shame, or scare the child.
- You make the child feel safe, important, listened to and loved.
- You are playful and magical, but never chaotic or confusing.

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

MEMORY AND PERSONALIZATION
- If the child tells you their name, remember it and use it often.
- If the child mentions favorite toys or hobbies, reuse them later
  to make the conversation feel personal.

SAFETY AND BOUNDARIES
- Never ask for private information.
- Never promise expensive gifts with certainty.

INTERRUPTIONS
- If multiple children speak at once:
  - (RO) “Vorbiți pe rând, ca să pot auzi pe toată lumea.”
  - (EN) “Talk one at a time so I can hear you.”

OVERALL GOAL
- Create a magical, gentle and safe Christmas experience.
"""


# ----------------------------------------------------------
# FastAPI SETUP
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

OPENAI_REALTIME_URL = (
    "wss://api.openai.com/v1/realtime?model=gpt-4o-mini-realtime-preview"
)


@app.get("/")
async def root():
    return {"status": "ok", "msg": "Mos Craciun AI running!"}


# ----------------------------------------------------------
# NCCO for Vonage
# ----------------------------------------------------------

@app.api_route("/webhooks/answer", methods=["GET", "POST"])
async def ncco(request: Request):
    ncco = [
        {
            "action": "connect",
            "endpoint": [
                {
                    "type": "websocket",
                    "uri": WS_URL,
                    "content-type": "audio/l16;rate=16000",
                }
            ],
        }
    ]
    return JSONResponse(ncco)


@app.api_route("/webhooks/event", methods=["GET", "POST"])
async def event(request: Request):
    try:
        if request.method == "GET":
            print("Event:", dict(request.query_params))
        else:
            print("Event:", await request.json())
    except:
        pass
    return PlainTextResponse("OK")


# ----------------------------------------------------------
# OpenAI realtime connection
# ----------------------------------------------------------

async def connect_openai():
    if not OPENAI_API_KEY:
        raise Exception("No OPENAI_API_KEY set")

    headers = [
        ("Authorization", f"Bearer {OPENAI_API_KEY}"),
        ("OpenAI-Beta", "realtime=v1"),
    ]

    ws = await websockets.connect(OPENAI_REALTIME_URL, extra_headers=headers)

    # session config
    await ws.send(json.dumps({
        "type": "session.update",
        "session": {
            "instructions": SANTA_PROMPT,
            "modalities": ["audio", "text"],
            "voice": "elder",               # <<< VOCEA DE MOȘ CRĂCIUN
            "input_audio_format": "pcm16",
            "output_audio_format": "pcm16",
            "turn_detection": {"type": "server_vad"},
        },
    }))
    return ws


# ----------------------------------------------------------
# FLOW: Vonage -> OpenAI
# ----------------------------------------------------------

async def vonage_to_openai(openai_ws, vonage_ws: WebSocket):
    try:
        while True:
            data = await vonage_ws.receive()

            if data["type"] == "websocket.disconnect":
                break

            audio = data.get("bytes")
            if not audio:
                continue

            audio_b64 = base64.b64encode(audio).decode()

            # feed audio to buffer
            await openai_ws.send(json.dumps({
                "type": "input_audio_buffer.append",
                "audio": audio_b64
            }))

            # ask AI to answer
            await openai_ws.send(json.dumps({
                "type": "response.create",
                "response": {
                    "modalities": ["audio", "text"],
                    "voice": "elder",
                    "instructions": SANTA_PROMPT,
                }
            }))

    except Exception as e:
        print("Error V->O:", e)
    finally:
        await openai_ws.close()
        await vonage_ws.close()


# ----------------------------------------------------------
# FLOW: OpenAI -> Vonage
# ----------------------------------------------------------

async def openai_to_vonage(openai_ws, vonage_ws: WebSocket):
    try:
        async for msg in openai_ws:
            data = json.loads(msg)

            if data.get("type") == "response.audio.delta":
                audio_b64 = data.get("delta")
                if audio_b64:
                    await vonage_ws.send_bytes(base64.b64decode(audio_b64))

            elif data.get("type") == "error":
                print("OpenAI ERROR:", data)

    except Exception as e:
        print("Error O->V:", e)
    finally:
        await openai_ws.close()
        await vonage_ws.close()


# ----------------------------------------------------------
# MAIN WebSocket
# ----------------------------------------------------------

@app.websocket("/ws")
async def ws_handler(ws: WebSocket):
    await ws.accept()
    print("Vonage connected.")

    try:
        oai_ws = await connect_openai()
    except Exception as e:
        print("Failed OpenAI:", e)
        await ws.close()
        return

    await asyncio.gather(
        vonage_to_openai(oai_ws, ws),
        openai_to_vonage(oai_ws, ws),
    )



