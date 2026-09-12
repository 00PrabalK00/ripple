import concurrent.futures
import unittest
from types import SimpleNamespace
from ripple.agent import Interpretation
from ripple.contracts import Target
from ripple.store import Store
from ripple.supervisor import Supervisor
try:
    from ripple.runtime import Runtime
except ImportError:
    Runtime = None


@unittest.skipIf(Runtime is None, 'source ROS Humble to test the runtime')
class RuntimeTests(unittest.TestCase):
    def test_valid_interpretation_is_journaled_and_proposed(self):
        runtime = Runtime.__new__(Runtime)
        runtime.store = Store()
        runtime.s = Supervisor(SimpleNamespace(), runtime.store)
        runtime.s.interpreting = True
        runtime.s.observe('station.B.available', True, 'test')
        runtime.s.observe('camera.B', 'CLEAR', 'test', 1)
        runtime.pending_versions = {k:f.version for k,f in runtime.s.facts.items()}
        runtime.automatic_interpretation = False
        runtime.conversation = []
        runtime.targets = lambda: {'B':Target('B','map',1,2,0)}
        runtime.pending_model = concurrent.futures.Future()
        runtime.pending_model.set_result(Interpretation(kind='mission', updates=[],
            destination='B', explanation='B is clear', clarification=None))
        runtime.finish_interpretation()
        self.assertFalse(runtime.s.interpreting, runtime.message)
        self.assertEqual(len(runtime.s.proposals), 1)
        receipt=next(r for r in runtime.store.receipts() if r['kind']=='interpretation')
        self.assertEqual(receipt['result']['kind'],'mission')
