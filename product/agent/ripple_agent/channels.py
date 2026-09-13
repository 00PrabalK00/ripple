"""Human channels. Ambiguous DMs are the primary one; the local dashboard is the other.

Ambiguous protocol (from the workspace operating guide): stream events with
`notifications watch`, claim each one with `mark-read` and act only when
`was_unread` is true, then reply in the originating conversation.
"""
import asyncio
import json
import os
import signal
import time
from pathlib import Path
from .workspace import AmbiguousCLI

VERSION = 'ambiguous@0.9.0'
# One watcher per identity: a second makes the server drop connections (close code 4012), and events delivered
# to a watcher left over from an earlier run (its agent killed, the npx child orphaned) are lost.
PIDFILE = Path('/tmp/ripple-ambiguous-watch.pid')


def stop_watcher(pid):
    """Stop a watcher's whole process group (npm, sh, node), only if it really is a notifications watcher."""
    try:
        if b'notifications' in Path(f'/proc/{pid}/cmdline').read_bytes():
            os.killpg(pid, signal.SIGTERM)
    except (FileNotFoundError, ProcessLookupError, PermissionError):
        pass


class Ambiguous:
    def __init__(self, root, user_id, workspace_id, operators, escalation_channel, deliver, log):
        self.root = root
        self.cli = AmbiguousCLI(root, user_id, workspace_id)
        self.operators = dict(operators)  # actor id -> display name (host configuration)
        self.escalation_channel = escalation_channel
        self.allowed_channels = {escalation_channel}
        self.deliver, self.log = deliver, log
        self.connected = False
        self.received = self.sent = self.errors = 0
        self.last_event_at = None
        self.identity_ok = None

    async def verify(self):
        try:
            await asyncio.to_thread(self.cli.verify_identity)
            self.identity_ok = True
        except Exception as exc:
            self.identity_ok = False
            self.log('Ambiguous identity check failed: ' + str(exc))
        return self.identity_ok

    async def listen(self):
        while True:
            proc = None
            try:
                try:
                    stop_watcher(int(PIDFILE.read_text()))
                except (FileNotFoundError, ValueError):
                    pass
                with open('/tmp/ripple-ambiguous-watch.log', 'ab') as err:
                    proc = await asyncio.create_subprocess_exec(
                        'npx', '--yes', VERSION, 'notifications', 'watch', cwd=str(self.cli.cwd),
                        env=self.cli.env, stdout=asyncio.subprocess.PIPE, stderr=err, start_new_session=True)
                    PIDFILE.write_text(str(proc.pid))
                    self.connected = True
                    self.log('Listening for Ambiguous messages')
                    async for raw in proc.stdout:
                        line = raw.decode(errors='replace').strip()
                        if not line.startswith('{'):
                            continue
                        try:
                            await self.handle(json.loads(line))
                        except Exception as exc:
                            self.errors += 1
                            self.log('An Ambiguous event could not be handled: ' + str(exc))
                    await proc.wait()
            except asyncio.CancelledError:
                if proc is not None:
                    stop_watcher(proc.pid)
                raise
            except Exception as exc:
                self.errors += 1
                self.log('Ambiguous listener error: ' + str(exc))
            self.connected = False
            await asyncio.sleep(5)

    @staticmethod
    def parse(event):
        payload = event.get('payload') or {}
        content = event.get('content') or payload.get('data') or {}
        actor = event.get('actor') or payload.get('actor') or {}
        return dict(
            notification_id=event.get('notification_id') or event.get('id'),
            type=event.get('type') or event.get('eventType') or event.get('event_type') or '',
            actor_id=actor.get('id'), actor_name=actor.get('name'),
            text=content.get('messageContent') or content.get('content') or content.get('text') or content.get('preview') or '',
            channel_id=content.get('channel_id'), thread_id=content.get('thread_id'),
            message_id=event.get('resourceId') or payload.get('resourceId') or content.get('message_id'))

    async def handle(self, event):
        e = self.parse(event)
        self.last_event_at = time.time()
        if not e['notification_id']:
            return
        claim = await asyncio.to_thread(self.cli.run, ('notifications', 'mark-read', str(e['notification_id'])))
        if not (isinstance(claim, dict) and claim.get('was_unread')):
            return  # another consumer already handled it
        if e['type'] and e['type'] != 'message.received':
            return  # informational notice, not a request
        if not e['text'] or not e['channel_id'] or e['actor_id'] == self.cli.user_id:
            return
        self.received += 1
        if e['actor_id'] not in self.operators:
            self.log(f"Ignored an Ambiguous message from {e['actor_name'] or 'an unknown sender'}: not an allowlisted operator")
            return
        self.allowed_channels.add(e['channel_id'])
        if e['message_id']:
            try:
                await asyncio.to_thread(self.cli.run, ('chat', 'reactions', 'add', e['channel_id'], e['message_id']),
                                        {'emoji': '👀'})
            except Exception:
                pass  # acknowledgement is a courtesy
        self.deliver(dict(channel='ambiguous', operator_id=e['actor_id'],
                          operator=e['actor_name'] or self.operators[e['actor_id']], text=e['text'],
                          message_id=e['message_id'], reply_to=e['channel_id'], thread_id=e['thread_id']))

    async def send(self, channel_id, text, thread_id=None):
        if channel_id not in self.allowed_channels:
            raise PermissionError('channel is not an operator conversation')
        payload = {'content': text[:4000]}
        if thread_id:
            payload['thread_id'] = thread_id
        result = await asyncio.to_thread(self.cli.run, ('chat', 'messages', 'send', channel_id), payload)
        self.sent += 1
        return result.get('id') if isinstance(result, dict) else None

    def status(self):
        return {'connected': self.connected, 'identity_ok': self.identity_ok, 'received': self.received,
                'sent': self.sent, 'errors': self.errors}
