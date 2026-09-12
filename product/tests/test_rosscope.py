import json
import tempfile
import time
import unittest
from pathlib import Path
from ripple_edge.rosscope import RosScopeReader

class RosScopeTests(unittest.TestCase):
    def bridge(self, directory, body):
        path=Path(directory)/'bridge'
        path.write_text('#!/usr/bin/python3\n'+body)
        path.chmod(0o700)
        return path

    def test_response_domain_and_collection_age(self):
        with tempfile.TemporaryDirectory() as directory:
            report={'source':'RosScope','schema_version':1,'domain':'7'}
            binary=self.bridge(directory,'print('+repr(json.dumps(report))+')\n')
            reader=RosScopeReader(binary,7)
            reader.collect()
            self.assertTrue(reader.snapshot().fresh)
            reader.started=time.monotonic()-91
            self.assertFalse(reader.snapshot().fresh)
            reader=RosScopeReader(binary,0)
            reader.collect()
            self.assertFalse(reader.snapshot().fresh)
            self.assertIsNone(reader.snapshot().value['report'])

    def test_hung_bridge_is_bounded(self):
        with tempfile.TemporaryDirectory() as directory:
            binary=self.bridge(directory,'import time\ntime.sleep(60)\n')
            reader=RosScopeReader(binary,0,timeout_s=.2)
            start=time.monotonic()
            reader.collect()
            self.assertLess(time.monotonic()-start,3.)
            self.assertFalse(reader.snapshot().fresh)
            self.assertIn('TimeoutError',reader.snapshot().value['error'])
