"""Local operator panel. Every mutation is serialized by Runtime."""
from contextlib import asynccontextmanager
from io import BytesIO
from pathlib import Path
from typing import Literal
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel, Field
from PIL import Image
from starlette.middleware.trustedhost import TrustedHostMiddleware
import yaml
from .runtime import ROOT, Runtime


@asynccontextmanager
async def lifespan(app):
    load_dotenv(ROOT / '.env')
    app.state.runtime = Runtime()
    yield
    app.state.runtime.close()


app = FastAPI(title='Ripple for Robotics', lifespan=lifespan)
app.add_middleware(TrustedHostMiddleware, allowed_hosts=['localhost', '127.0.0.1', 'testserver'])


@app.middleware('http')
async def local_mutations(request: Request, call_next):
    if request.method == 'POST':
        origin = request.headers.get('origin')
        if origin and origin not in ('http://localhost:8050', 'http://127.0.0.1:8050'):
            return Response('Invalid origin', status_code=403)
        if not request.headers.get('content-type', '').startswith('application/json'):
            return Response('JSON required', status_code=415)
    return await call_next(request)


class Command(BaseModel):
    action: Literal['instruction', 'approve', 'reject', 'pause', 'cancel', 'reconcile', 'calibrate', 'describe']
    text: str = Field(default='', max_length=4000)
    proposal_id: str = ''
    operator: str = Field(default='local operator', min_length=1, max_length=100)
    enabled: bool = False
    roi: list[int] = Field(default_factory=lambda: [200, 140, 240, 200], min_length=4, max_length=4)


@app.get('/')
def index():
    return FileResponse(ROOT / 'ripple/static/index.html')


@app.get('/api/state')
def state(request: Request):
    return request.app.state.runtime.call('state')


@app.post('/api/command')
def command(body: Command, request: Request):
    try:
        return request.app.state.runtime.call(body.action, **body.model_dump(exclude={'action'}))
    except (ValueError, KeyError) as error:
        raise HTTPException(400, str(error)) from error


@app.get('/api/map')
def map_info():
    return yaml.safe_load((ROOT / 'smr300l_gazebo_ros2control/maps/smr_map.yaml').read_text())


@app.get('/api/map.png')
def map_image():
    output = BytesIO()
    with Image.open(ROOT / 'smr300l_gazebo_ros2control/maps/smr_map.pgm') as im:
        im.save(output, format='PNG')
    return Response(output.getvalue(), media_type='image/png')


@app.get('/api/camera.jpg')
def camera_image(request: Request):
    try:
        return Response(request.app.state.runtime.camera.jpeg(), media_type='image/jpeg',
                        headers={'Cache-Control': 'no-store'})
    except ValueError as error:
        raise HTTPException(503, str(error)) from error
