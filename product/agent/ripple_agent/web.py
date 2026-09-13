"""Local dashboard and JSON API. Bound to localhost; POSTs need JSON and a local origin."""
import asyncio
import time
from pathlib import Path
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.responses import FileResponse, JSONResponse, Response
from starlette.routing import Route
from . import mapview

STATIC = Path(__file__).resolve().parent / 'static'


def build_app(orch, edge, port, test_api=False):
    origins = {f'http://127.0.0.1:{port}', f'http://localhost:{port}'}
    cache = {'map': None, 'png': None, 'labeled': None, 'labeled_at': 0}

    class LocalOnly(BaseHTTPMiddleware):
        async def dispatch(self, request, call_next):
            if request.method == 'POST':
                origin = request.headers.get('origin')
                if origin and origin not in origins:
                    return Response('Invalid origin', status_code=403)
                if not request.headers.get('content-type', '').startswith('application/json'):
                    return Response('JSON required', status_code=415)
            return await call_next(request)

    def map_msg():
        return edge.keepouts.map[0] if edge.keepouts.map else None

    def base_png():
        msg = map_msg()
        if msg is None:
            return None
        if cache['map'] is not msg:
            cache['map'], cache['png'] = msg, mapview.base_png(msg)
        return cache['png']

    def labeled_png():
        msg, geom = map_msg(), edge.geometry()
        if msg is None or geom is None:
            return None
        dests = edge.site.destinations(geom)
        from ripple_edge.geometry import Region
        pose = edge.tools._val('pose')
        return mapview.labeled_png(
            msg, geom, [d for d in dests.values()],
            [{'name': a['name'], 'polygon': Region.load(a['region']).polygon(geom)} for a in edge.site.areas.values()],
            edge.site.active_keepouts(), pose)

    orch.labeled_png = labeled_png

    async def index(request):
        return FileResponse(STATIC / 'index.html', headers={'Cache-Control': 'no-store'})

    async def state(request):
        return JSONResponse(orch.state())

    async def map_png(request):
        png = base_png()
        if png is None:
            return Response('Map not received yet', status_code=503)
        return Response(png, media_type='image/png', headers={'Cache-Control': 'no-store'})

    async def labeled(request):
        png = labeled_png()
        return Response(png, media_type='image/png') if png else Response('Map not received yet', status_code=503)

    async def ask(request):
        body = await request.json()
        text = str(body.get('text', '')).strip()[:2000]
        if not text:
            return JSONResponse({'error': 'Type an instruction first'}, status_code=400)
        selection = body.get('selection')
        if not (isinstance(selection, list) and len(selection) == 4 and all(isinstance(v, (int, float)) for v in selection)):
            selection = None
        orch.submit(orch.dashboard_message(text, selection))
        return JSONResponse({'queued': True})

    async def stop(request):
        asyncio.ensure_future(orch.stop_now(orch.dashboard_message('Stop robot')))
        return JSONResponse({'stopping': True})

    async def draw(request):
        body = await request.json()
        bounds, action = body.get('bounds'), body.get('action')
        name = str(body.get('name') or '').strip()[:60]
        if action not in ('keepout', 'area') or not isinstance(bounds, list) or len(bounds) != 4:
            return JSONResponse({'error': 'Draw a rectangle, then choose an action'}, status_code=400)
        if action == 'area' and not name:
            return JSONResponse({'error': 'Give the area a name'}, status_code=400)
        return JSONResponse(await orch.dashboard_draw(action, [float(v) for v in bounds], name,
                                                      str(body.get('reason') or '').strip()[:200]))

    async def reopen(request):
        body = await request.json()
        return JSONResponse(await orch.dashboard_reopen(str(body.get('id', ''))))

    async def lessons(request):
        return JSONResponse({'lessons': orch.memory.public(), 'suggestions': orch.memory.report()})

    async def forget_lesson(request):
        body = await request.json()
        lesson_id = str(body.get('id', ''))
        forgotten = orch.memory.forget(lesson_id)
        if forgotten:
            orch.note('operator', f'Forgot site lesson {lesson_id}', operator='Local operator', channel='dashboard')
        return JSONResponse({'forgotten': forgotten})

    async def test_tool(request):
        """Verification only (--test-api): call an edge tool as the local dashboard operator."""
        if not test_api:
            return Response('Test API disabled', status_code=404)
        body = await request.json()
        name, args = body.get('name'), dict(body.get('args') or {})
        if (edge.tools.level(name) == 'command' or name in ('escape', 'lifecycle_reset')) and 'authorization_id' not in args:
            args['authorization_id'] = edge.auths.record('dashboard', 'local-dashboard', body.get('text') or 'test: ' + str(name)).id
        return JSONResponse(await edge.tools.call(name, args))

    async def test_incident(request):
        """Verification only (--test-api): open an edge incident as an event would."""
        if not test_api:
            return Response('Test API disabled', status_code=404)
        body = await request.json()
        iid = edge.tools.open_incident(body.get('kind', 'navigation_failed'), body.get('cause', 'unknown'),
                                       goal=edge.navigator.public())
        return JSONResponse({'incident_id': iid})

    routes = [Route('/', index), Route('/api/state', state), Route('/api/map.png', map_png),
              Route('/api/test/tool', test_tool, methods=['POST']), Route('/api/test/incident', test_incident, methods=['POST']),
              Route('/api/map-labeled.png', labeled), Route('/api/ask', ask, methods=['POST']),
              Route('/api/stop', stop, methods=['POST']), Route('/api/draw', draw, methods=['POST']),
              Route('/api/keepouts/reopen', reopen, methods=['POST']),
              Route('/api/lessons', lessons), Route('/api/lessons/forget', forget_lesson, methods=['POST'])]
    return Starlette(routes=routes, middleware=[
        Middleware(TrustedHostMiddleware, allowed_hosts=['localhost', '127.0.0.1']), Middleware(LocalOnly)])
