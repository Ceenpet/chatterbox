"""Exercise the real warm_up method without importing the GPU runtime."""
import ast
from pathlib import Path
import types
import unittest

class Tests(unittest.TestCase):
    def test_rng_restored_on_success_and_failure_without_playback(self):
        tree = ast.parse(Path(__file__).with_name('engine.py').read_text(encoding='utf-8'))
        engine = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == 'VoiceEngine')
        method = next(n for n in engine.body if isinstance(n, ast.FunctionDef) and n.name == 'warm_up')
        for fail in (False, True):
            restored = []
            state = object()
            scope = {'rng_snapshot': lambda: state, 'restore_rng': restored.append}
            exec(compile(ast.Module(body=[method], type_ignores=[]), 'engine.py', 'exec'), scope)
            calls = []
            def synthesize(text):
                calls.append(text)
                if fail:
                    raise RuntimeError('CUDA failure')
                return object(), {'synthesis_seconds': 1}
            target = types.SimpleNamespace(synthesize=synthesize)
            if fail:
                with self.assertRaises(RuntimeError): scope['warm_up'](target)
            else:
                self.assertEqual(scope['warm_up'](target)['synthesis_seconds'], 1)
            self.assertEqual(restored, [state])
            self.assertEqual(len(calls), 1)
