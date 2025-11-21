import os
import json
import base64
import asyncio

from fastapi import FastAPI, Request, WebSocket
from fastapi.responses import JSONResponse, PlainTextResponse
from fastapi.middleware.cors import CORSMiddleware

import websockets

# Dacă ai fișier separat cu promptul lui Moș Crăciun:
try:
    from santa_prompt import SANTA_PROMPT
except ImportError:
    # fallback simplu în caz că nu ai încă santa_prompt.py
    SANTA_PROMPT = (
        "You are Santa Claus talking to children on the phone. "
        "Speak warmly, kindly, and adapt to Romanian or English based on the child. "
        "Be very patient with kids who stutter, forget what they want to say, or mispronounce words."
    )


app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
WS_URL = os.getenv("WS_URL")

# Model Realtime (poți schimba cu altul compatibil dacă vrei)
OPENAI_REALTIME_URL = (
    "wss://api.openai.com/v1/realtime?model=gpt-4o-mini-realtime-preview"
)


@app.get("/")
async def root():
    return {"status": "ok", "message": "Mos Craciun AI is running!"}


# Vonage cheamă acest webhook ca să ia NCCO-ul
@app.api_route("/webhooks/answer", methods=["GET", "POST"])
async def answer(request: Request):
    if not WS_URL:
        # Mai bine răspundem clar în log
        print("ERROR: WS_URL is not set in environment variables!")
    ncco = [
        {
            "action": "connect",
            "endpoint": [
                {
                    "type": "websocket",
                    "uri": WS_URL,
                    "content-type": "audio/l16;rate=16000",
                    "headers": {
                        "lang": "ro-en",
                        "role": "santa"
                    },
                }
            ],
        }
    ]
    return JSONResponse(ncco)


# Vonage trimite aici evenimente (start, end, timeout, etc.)
@app.api_route("/webhooks/event", methods=["GET", "POST"])
async def events(request: Request):
    try:
        if request.method == "GET":
            # Evenimentele simple vin ca query params
            params = dict(request.query_params)
            print("Vonage Event (GET):", params)
        else:
            body = await request.json()
            print("Vonage Event (POST):", body)
    except Exception as e:
        print("Error parsing Vonage event:", e)
    return PlainTextResponse("OK")


# ===================== OpenAI Realtime ===================== #

async def connect_openai():
    """Deschide conexiunea WebSocket către OpenAI Realtime și setează sesiunea."""
    if not OPENAI_API_KEY:
        raise RuntimeError("OPENAI_API_KEY is not set!")

    headers = [
        ("Authorization", f"Bearer {OPENAI_API_KEY}"),
        ("OpenAI-Beta", "realtime=v1"),
    ]

    print("Connecting to OpenAI Realtime...")
    ws = await websockets.connect(OPENAI_REALTIME_URL, extra_headers=headers)

    # Configurăm sesiunea: audio in/out pcm16, instrucțiuni Moș Crăciun
    await ws.send(json.dumps({
        "type": "session.update",
        "session": {
            "instructions": SANTA_PROMPT,
            "input_audio_format": "pcm16",
            "output_audio_format": "pcm16",
            "modalities": ["audio"],
            "turn_detection": {"type": "server_vad"},
        },
    }))
    print("OpenAI Realtime session configured.")
    return ws


async def handle_openai_to_vonage(openai_ws, vonage_ws: WebSocket):
    """Primește audio de la OpenAI și îl trimite spre Vonage ca bytes."""
    try:
        async for message in openai_ws:
            data = json.loads(message)

            if data.get("type") == "response.audio.delta":
                audio_b64 = data.get("delta")
                if audio_b64:
                    audio_bytes = base64.b64decode(audio_b64)
                    await vonage_ws.send_bytes(audio_bytes)

            elif data.get("type") == "response.completed":
                # Răspuns audio complet
                pass

            elif data.get("type") == "error":
                print("OpenAI ERROR:", data)
    except Exception as e:
        print("Error in handle_openai_to_vonage:", e)
    finally:
        try:
            await vonage_ws.close()
        except Exception:
            pass
        try:
            await openai_ws.close()
        except Exception:
            pass


async def handle_vonage_to_openai(openai_ws, vonage_ws: WebSocket):
    """Primește audio brut L16 16kHz de la Vonage și îl trimite la OpenAI."""
    try:
        while True:
            msg = await vonage_ws.receive()

            if msg["type"] == "websocket.disconnect":
                print("Vonage WebSocket disconnected.")
                break

            if msg["type"] == "websocket.receive":
                audio_bytes = msg.get("bytes")
                if not audio_bytes:
                    # Vonage trimite doar audio binar, ignorăm textul
                    continue

                # Trimitem audio la OpenAI
                audio_b64 = base64.b64encode(audio_bytes).decode("ascii")

                await openai_ws.send(json.dumps({
                    "type": "input_audio_buffer.append",
                    "audio": audio_b64,
                }))

                # Cerem generarea unui răspuns audio
                await openai_ws.send(json.dumps({
                    "type": "response.create",
                    "response": {
                        "modalities": ["audio"],
                        "instructions": SANTA_PROMPT,
                    },
                }))

    except Exception as e:
        print("Error in handle_vonage_to_openai:", e)
    finally:
        try:
            await vonage_ws.close()
        except Exception:
            pass
        try:
            await openai_ws.close()
        except Exception:
            pass


# ===================== WebSocket endpoint pentru Vonage ===================== #

@app.websocket("/ws")
async def vonage_ws(websocket: WebSocket):
    """Punctul de intrare WebSocket pentru Vonage."""
    await websocket.accept()
    print("Vonage WebSocket connected.")

    try:
        openai_ws = await connect_openai()
    except Exception as e:
        print("Failed to connect to OpenAI:", e)
        await websocket.close()
        return

    # Rulează în paralel:
    # - citim audio de la Vonage și trimitem la OpenAI
    # - citim audio de la OpenAI și trimitem la Vonage
    await asyncio.gather(
        handle_vonage_to_openai(openai_ws, websocket),
        handle_openai_to_vonage(openai_ws, websocket),
    )

    print("Vonage WebSocket handler finished.")

