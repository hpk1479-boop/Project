"""Current storage boundaries, independent of retired source-copy templates.

These checks retain the valid storage cases formerly collected together with
the removed INHERITED compiler. Their inputs use the public canonical path.
"""
from __future__ import annotations

import ast
import copy
import json
from pathlib import Path
import socket
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'tests')]
from lab import storage
from lab.ai.intent import recipe_from_intent
from lab.compiler import compile_recipe
from test_ai_intent_v2 import intent


class StorageBoundaries(unittest.TestCase):
    def setUp(self):
        self.network = patch.object(socket.socket, 'connect', side_effect=AssertionError('Network forbidden'))
        self.network.start()

    def tearDown(self):
        self.network.stop()

    def test_recipe_v1_is_rejected_before_creating_files(self):
        old = {'schema_version': 1, 'base': 'CUSTOM', 'slots': [], 'source_text': 'print(1)'}
        with tempfile.TemporaryDirectory() as folder, patch.object(storage, 'ROOT', Path(folder)):
            for operation in (storage.preview, storage.generate, storage.load_saved_recipe):
                with self.subTest(operation=operation.__name__), self.assertRaises(ValueError):
                    operation(old)
            self.assertEqual(list(Path(folder).iterdir()), [])

    def test_manual_source_argument_cannot_generate(self):
        recipe = recipe_from_intent(intent())
        with tempfile.TemporaryDirectory() as folder, patch.object(storage, 'ROOT', Path(folder)):
            for operation in (storage.preview, storage.generate):
                with self.subTest(operation=operation.__name__), self.assertRaises(TypeError):
                    operation(recipe, 'open("bad", "w")')
            self.assertEqual(list(Path(folder).iterdir()), [])

    def test_preview_is_not_generation(self):
        recipe = recipe_from_intent(intent())
        with tempfile.TemporaryDirectory() as folder, patch.object(storage, 'ROOT', Path(folder)):
            preview = storage.preview(recipe)
            ast.parse(preview['code'])
            self.assertIn('def register(manager)', preview['code'])
            self.assertFalse((Path(folder) / 'generated').exists())
            self.assertFalse((Path(folder) / 'TEST_SPECIAL').exists())

    def test_generation_numbering_and_existing_files_are_protected(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(storage, 'ROOT', Path(folder)), \
                patch.object(storage, '_number_paths', return_value=iter([])):
            target = Path(folder) / 'TEST_SPECIAL'
            target.mkdir()
            (target / 'SPECIAL021.py').write_text('ORIGINAL', encoding='utf-8')
            (target / 'Test_SPECIAL022.py').write_text('EXISTING', encoding='utf-8')
            with patch.object(storage, '_number_paths', side_effect=lambda: iter([target])):
                recipe = recipe_from_intent(intent())
                first = storage.generate(recipe)
                second = storage.generate(recipe)
            self.assertEqual(first['filename'], 'Test_SPECIAL023.py')
            self.assertEqual(second['filename'], 'Test_SPECIAL024.py')
            self.assertEqual((target / 'SPECIAL021.py').read_text('utf-8'), 'ORIGINAL')
            self.assertEqual((target / 'Test_SPECIAL022.py').read_text('utf-8'), 'EXISTING')
            sidecar = json.loads((target / 'Test_SPECIAL023.recipe.json').read_text('utf-8'))
            self.assertEqual(sidecar['recipe']['schema_version'], 2)
            self.assertNotIn('manual', sidecar)

    def test_invalid_filename_and_code_fields_are_rejected(self):
        recipe = recipe_from_intent(intent())
        for filename in ('SPECIAL001.py', 'Test_SPECIAL000.py', '../Test_SPECIAL008.py'):
            with self.subTest(filename=filename), self.assertRaises(ValueError):
                compile_recipe(recipe, filename)
        for field in ('source_text', 'manual_code', 'slots'):
            bad = copy.deepcopy(recipe)
            bad[field] = 'not allowed'
            with self.subTest(field=field), self.assertRaises(ValueError):
                compile_recipe(bad)

    def test_draft_accepts_only_validated_ai_recipe(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(storage, 'ROOT', Path(folder)):
            draft = {'recipe': recipe_from_intent(intent())}
            storage.save_draft(draft)
            target = Path(folder) / 'projects/last_draft.json'
            before = target.read_bytes()
            self.assertEqual(storage.load_saved_draft(json.loads(before)), draft)
            with self.assertRaises(ValueError):
                storage.save_draft(dict(draft, manual_code='print(1)'))
            self.assertEqual(target.read_bytes(), before)

    def test_legacy_generated_file_remains_view_only(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(storage, 'ROOT', Path(folder)):
            target = Path(folder) / 'generated'
            target.mkdir()
            source = '# preserved legacy source\nraise RuntimeError("never execute")\n'
            (target / 'Test_SPECIAL210.py').write_text(source, encoding='utf-8')
            (target / 'Test_SPECIAL210.recipe.json').write_text(
                json.dumps({'recipe': {'schema_version': 1, 'slots': [{}]}}), encoding='utf-8')
            viewed = storage.reopen('Test_SPECIAL210.py')
            self.assertEqual(viewed['code'], source)
            self.assertNotIn('recipe', viewed)
            self.assertNotIn('manual', viewed)


if __name__ == '__main__':
    unittest.main()
