
import os
import json
import asyncio
import websockets
from fastapi import FastAPI, WebSocket
from fastapi.responses import JSONResponse

app = FastAPI()

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
WS_URL = os.getenv("WS_URL")
OPENAI_REALTIME_URL = "wss://api.openai.com/v1/realtime?model=gpt-4o-realtime-preview"

@app.get("/")
def root():
    return {"status": "ok"}

@app.get("/webhooks/answer")
def answer():
    return JSONResponse([
        {
            "action": "connect",
            "endpoint": [
                {
                    "type": "websocket",
                    "uri": WS_URL
                }
            ]
        }
    ])

@app.get("/webhooks/event")
def event():
    return ""

async def connect_openai():
    if not OPENAI_API_KEY:
        raise Exception("OPENAI_API_KEY missing")

    headers = [("Authorization", f"Bearer {OPENAI_API_KEY}")]
    ws = await websockets.connect(OPENAI_REALTIME_URL, extra_headers=headers)
    return ws

@app.websocket("/ws")
async def vonage_ws(websocket: WebSocket):
    await websocket.accept()
    try:
        openai_ws = await connect_openai()
    except:
        await websocket.close()
        return

    async def user_to_openai():
        try:
            async for msg in websocket.iter_bytes():
                await openai_ws.send(msg)
        except:
            pass

    async def openai_to_user():
        try:
            async for msg in openai_ws:
                await websocket.send_bytes(msg)
        except:
            pass

    await asyncio.gather(user_to_openai(), openai_to_user())
