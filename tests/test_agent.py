import json
import os
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import MagicMock, patch
from ripple.agent import interpret


class InterpretationTests(TestCase):
    def call(self, content, text='Packing A is closed'):
        client = MagicMock()
        client.__enter__.return_value = client
        client.chat.completions.create.return_value = SimpleNamespace(choices=[
            SimpleNamespace(message=SimpleNamespace(content=json.dumps(content), refusal=None), finish_reason='stop')])
        with patch.dict(os.environ, {'OPENROUTER_API_KEY': 'test-placeholder'}), patch('ripple.agent.OpenAI', return_value=client):
            return interpret(text, {})

    def test_unrelated_cannot_close_station(self):
        with self.assertRaises(ValueError):
            self.call(dict(kind='irrelevant', updates=[dict(station='A',available=False,evidence='Packing A is closed')],
                destination=None, explanation='Unrelated', clarification=None))

    def test_invented_evidence_is_rejected(self):
        with self.assertRaises(ValueError):
            self.call(dict(kind='update', updates=[dict(station='A',available=False,evidence='inspection today')],
                destination=None, explanation='Closed', clarification=None))

    def test_unknown_destination_is_rejected(self):
        with self.assertRaises(ValueError):
            self.call(dict(kind='mission', updates=[],destination='C', explanation='Go C', clarification=None))

    def test_valid_update_is_bounded(self):
        result=self.call(dict(kind='update', updates=[dict(station='A',available=False,evidence='Packing A is closed')],
            destination=None, explanation='A unavailable; no other clear station', clarification=None))
        self.assertFalse(result.updates[0].available)
