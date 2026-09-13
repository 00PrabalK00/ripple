import os
import unittest
from ripple_agent.channels import Ambiguous, stop_watcher

ME, PRABAL, STRANGER = ('11111111-0000-4000-8000-000000000001', '00000000-0000-4000-8000-00000000cafe',
                        '99999999-0000-4000-8000-000000000009')
WORKSPACE = '22222222-0000-4000-8000-000000000002'


def event(nid='n1', actor=PRABAL, text='send the robot to B', kind='message.received', channel='dm-1', message='m1'):
    return {'notification_id': nid, 'type': kind, 'actor': {'id': actor, 'name': 'Prabal Khare'},
            'content': {'messageContent': text, 'channel_id': channel}, 'resourceId': message}


class AmbiguousTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.delivered, self.logs, self.calls = [], [], []
        self.unread = True
        self.amb = Ambiguous('.', ME, WORKSPACE, {PRABAL: 'Prabal Khare'}, 'dm-1', self.delivered.append, self.logs.append)

        def run(command, payload=None):
            self.calls.append((command, payload))
            if command[:2] == ('notifications', 'mark-read'):
                return {'was_unread': self.unread}
            return {'id': 'sent-1'}
        self.amb.cli.run = run

    async def test_an_operator_message_is_claimed_acknowledged_and_delivered(self):
        await self.amb.handle(event())
        self.assertEqual(self.calls[0][0], ('notifications', 'mark-read', 'n1'))
        self.assertEqual(self.calls[1], (('chat', 'reactions', 'add', 'dm-1', 'm1'), {'emoji': '👀'}))
        self.assertEqual(self.delivered[0]['text'], 'send the robot to B')
        self.assertEqual(self.delivered[0]['reply_to'], 'dm-1')

    async def test_another_consumer_already_claimed_it(self):
        self.unread = False
        await self.amb.handle(event())
        self.assertEqual(self.delivered, [])

    async def test_strangers_and_its_own_messages_are_not_delivered(self):
        await self.amb.handle(event(nid='n2', actor=STRANGER))
        await self.amb.handle(event(nid='n3', actor=ME))
        await self.amb.handle(event(nid='n4', kind='task.assigned'))
        self.assertEqual(self.delivered, [])
        self.assertTrue(any('not an allowlisted operator' in line for line in self.logs))

    async def test_send_only_to_operator_conversations(self):
        with self.assertRaises(PermissionError):
            await self.amb.send('some-other-channel', 'hello')
        await self.amb.send('dm-1', 'x' * 5000)
        command, payload = self.calls[-1]
        self.assertEqual(command, ('chat', 'messages', 'send', 'dm-1'))
        self.assertEqual(len(payload['content']), 4000)

    def test_watcher_cleanup_only_stops_notification_watchers(self):
        stop_watcher(os.getpid())  # this test process is not a watcher: it must survive
        stop_watcher(999999999)    # no such process: nothing happens
        self.assertTrue(os.path.exists(f'/proc/{os.getpid()}'))


if __name__ == '__main__':
    unittest.main()
