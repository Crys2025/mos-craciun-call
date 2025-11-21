import os
import json
import base64
import asyncio

from fastapi import FastAPI, Request, WebSocket
from fastapi.responses import JSONResponse, PlainTextResponse
from fastapi.middleware.cors import CORSMiddleware

import websockets
from dotenv import load_dotenv

from santa_prompt import SANTA_PROMPT

load_dotenv()

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
if not OPENAI_API_KEY:
    raise RuntimeError("Set OPENAI_API_KEY in environment variables!")

# Realtime endpoint – poți schimba modelul dacă vrei altul compatibil realtime
OPENAI_REALTIME_URL = "wss://api.openai.com/v1/realtime?model=gpt-4o-mini-realtime-preview"

app = FastAPI()

# CORS – util pentru debugging
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.api_route("/webhooks/answer", methods=["GET", "POST"])
async def answer(request: Request):
    """Vonage Answer URL – întoarce NCCO care conectează apelul la WebSocket."""
    ncco = [
        {
            "action": "connect",
            "endpoint": [
                {
                    "type": "websocket",
                    # Vonage va deschide WebSocket la acest URL (setezi WS_URL în Render)
                    "uri": os.getenv("WS_URL", "wss://your-render-service.onrender.com/ws"),
                    "content-type": "audio/l16;rate=16000",
                    "headers": {
                        "lang": "ro-en",
                        "role": "santa"
                    }
                }
            ]
        }
    ]
    return JSONResponse(ncco)


@app.post("/webhooks/event")
async def events(request: Request):
    """Vonage Event URL – loghează evenimentele apelului (start, end, etc.)."""
    body = await request.json()
    print("Vonage event:", body)
    return PlainTextResponse("OK")


async def connect_openai():
    """Deschide WebSocket la OpenAI Realtime."""
    headers = {
        "Authorization": f"Bearer {OPENAI_API_KEY}",
        "OpenAI-Beta": "realtime=v1",
    }
    ws = await websockets.connect(OPENAI_REALTIME_URL, extra_headers=headers)
    return ws


async def handle_openai_events(openai_ws, vonage_ws: WebSocket):
    """Citește evenimentele de la OpenAI și trimite audio spre Vonage."""
    try:
        async for message in openai_ws:
            try:
                data = json.loads(message)
            except Exception:
                # dacă nu e JSON valid, ignorăm
                continue

            event_type = data.get("type")
            if event_type == "response.audio.delta":
                # OpenAI trimite bucăți audio codate base64
                delta_b64 = data.get("delta")
                if not delta_b64:
                    continue
                audio_bytes = base64.b64decode(delta_b64)
                # trimitem audio ca binary către Vonage
                await vonage_ws.send_bytes(audio_bytes)

            elif event_type == "error":
                print("OpenAI error:", data)
    except Exception as e:
        print("Error in handle_openai_events:", e)


async def handle_vonage_stream(openai_ws, vonage_ws: WebSocket, instructions: str):
    """Primește audio (binary) de la Vonage și îl trimite la OpenAI."""
    try:
        while True:
            msg = await vonage_ws.receive()

            if msg["type"] == "websocket.disconnect":
                print("Vonage WS disconnected")
                break

            if msg["type"] == "websocket.receive":
                # poate fi text (JSON) sau bytes (audio)
                if "text" in msg and msg["text"] is not None:
                    try:
                        data = json.loads(msg["text"])
                        print("Vonage text event:", data)
                    except Exception:
                        pass

                if "bytes" in msg and msg["bytes"] is not None:
                    audio_bytes = msg["bytes"]
                    if not audio_bytes:
                        continue

                    # Encodăm în base64 pentru input_audio_buffer.append
                    audio_b64 = base64.b64encode(audio_bytes).decode("ascii")

                    # 1) Adăugăm audio în bufferul modelului
                    await openai_ws.send(
                        json.dumps(
                            {
                                "type": "input_audio_buffer.append",
                                "audio": audio_b64,
                            }
                        )
                    )

                    # 2) Cerem modelului să creeze un răspuns audio
                    await openai_ws.send(
                        json.dumps(
                            {
                                "type": "response.create",
                                "response": {
                                    "modalities": ["audio"],
                                    "instructions": instructions,
                                },
                            }
                        )
                    )

    except Exception as e:
        print("Error in handle_vonage_stream:", e)


@app.websocket("/ws")
async def vonage_ws(websocket: WebSocket):
    """Punctul WebSocket unde se conectează Vonage."""
    await websocket.accept()
    print("Vonage WebSocket connected")

    # 1) ne conectăm la OpenAI
    openai_ws = await connect_openai()
    print("Connected to OpenAI Realtime")

    # 2) configurăm sesiunea la OpenAI (instrucțiuni Moș Crăciun + audio in/out)
    session_update = {
        "type": "session.update",
        "session": {
            "instructions": SANTA_PROMPT,
            "input_audio_format": "pcm16",
            "output_audio_format": "pcm16",
            "modalities": ["audio", "text"],
        },
    }
    await openai_ws.send(json.dumps(session_update))

    try:
        await asyncio.gather(
            handle_openai_events(openai_ws, websocket),
            handle_vonage_stream(openai_ws, websocket, SANTA_PROMPT),
        )
    finally:
        try:
            await openai_ws.close()
        except Exception:
            pass
        print("OpenAI WS closed")
        await websocket.close()
        print("Vonage WS closed")


@app.get("/")
async def root():
    return {"status": "ok", "message": "Mos Craciun AI is running!"}
