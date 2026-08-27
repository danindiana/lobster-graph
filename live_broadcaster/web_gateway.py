import asyncio
import zmq
import zmq.asyncio
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse

app = FastAPI()

# Global list of connected WebSocket clients
active_connections = set()

html = """
<!DOCTYPE html>
<html>
    <head>
        <title>Live AI Dashboard</title>
        <style>
            body { 
                background-color: #0b0f19; 
                color: #58a6ff; 
                font-family: 'Courier New', Courier, monospace; 
                font-size: 20px; 
                margin: 0; 
                padding: 40px; 
            }
            #terminal {
                background: #010409;
                border: 1px solid #30363d;
                border-radius: 8px;
                padding: 20px;
                height: 80vh;
                overflow-y: auto;
                white-space: pre-wrap;
                word-wrap: break-word;
                box-shadow: 0 4px 20px rgba(0,0,0,0.5);
            }
            .header { margin-bottom: 20px; color: #c9d1d9; }
        </style>
    </head>
    <body>
        <div class="header">
            <h2>📡 ZeroMQ -> WebSocket Gateway</h2>
            <p>Native Pub/Sub streaming architecture. Decoupled and resilient.</p>
        </div>
        <div id="terminal"></div>
        <script>
            var ws = new WebSocket("ws://" + location.host + "/ws");
            var term = document.getElementById('terminal');
            
            ws.onmessage = function(event) {
                term.appendChild(document.createTextNode(event.data));
                term.scrollTop = term.scrollHeight; // Auto-scroll
            };
            
            ws.onclose = function(event) {
                term.innerHTML += "\\n\\n[Connection Closed. Gateway offline.]";
            };
        </script>
    </body>
</html>
"""

@app.on_event("startup")
async def startup_event():
    # Bind the ZeroMQ socket globally on startup
    app.state.zmq_ctx = zmq.asyncio.Context()
    app.state.zmq_sub = app.state.zmq_ctx.socket(zmq.SUB)
    app.state.zmq_sub.bind("tcp://127.0.0.1:5555")
    app.state.zmq_sub.setsockopt_string(zmq.SUBSCRIBE, "")
    
    # Start background listener task
    app.state.listener_task = asyncio.create_task(zmq_listener())

async def zmq_listener():
    try:
        while True:
            msg = await app.state.zmq_sub.recv_string()
            # Broadcast to all connected clients
            dead_connections = set()
            for ws in active_connections:
                try:
                    await ws.send_text(msg)
                except Exception:
                    dead_connections.add(ws)
            for ws in dead_connections:
                active_connections.remove(ws)
    except asyncio.CancelledError:
        pass

@app.on_event("shutdown")
async def shutdown_event():
    app.state.listener_task.cancel()
    app.state.zmq_sub.close()
    app.state.zmq_ctx.term()

@app.get("/")
async def get():
    return HTMLResponse(html)

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    active_connections.add(websocket)
    try:
        # Keep connection open
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        active_connections.remove(websocket)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8787)
