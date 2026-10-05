"""AI-only generation, named lifecycle templates, and storage boundaries."""
from __future__ import annotations
import ast
import copy
import json
from pathlib import Path
import socket
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT/'tests'), str(ROOT.parent/'Part1/program')]
from lab import catalog, storage
from lab.ai.intent import recipe_from_intent
from lab.ai.schema import output_schema
from lab.ai_compiler import inherited_recipe
from lab.compiler import compile_recipe
from test_ai_intent_v2 import intent


def inherited(number, **params):
    value = intent()
    value['interpretation'].update(steps=[], base_special='SPECIAL'+str(number),
                                  inherit_base_rules=True, special_parameters=params)
    return value


def module_for(value, stem='Test_SPECIAL777'):
    recipe = recipe_from_intent(value)
    source = compile_recipe(recipe, stem+'.py')
    module = types.ModuleType(stem)
    module.__file__ = stem+'.py'
    sys.modules[stem] = module
    exec(compile(source, stem+'.py', 'exec'), module.__dict__)
    return module, source


class AIOnlyGeneration(unittest.TestCase):
    def setUp(self):
        self.network = patch.object(socket.socket, 'connect', side_effect=AssertionError('Network forbidden'))
        self.network.start()
    def tearDown(self): self.network.stop()

    def test_source_lookup_is_current_read_only_information(self):
        from lab.ai.tools import ReadOnlyWorkspace
        for number in range(1,8):
            with self.subTest(number=number):
                path = catalog.source_path(number)
                self.assertEqual(path, ROOT.parent/'Part1/program/SPECIAL'/f'SPECIAL{number}.py')
                info = ReadOnlyWorkspace().describe_special(number)
                self.assertEqual(info['number'], number)
                self.assertNotIn('slots', info)
                self.assertNotIn('source_text', info)
                self.assertNotIn('schema_version', info)

    def test_manual_builder_and_slot_contract_are_removed(self):
        from lab import compiler
        for name in ('new','load','slot','pad','condition','normalize_profiles'):
            self.assertFalse(hasattr(catalog, name), name)
        self.assertFalse(hasattr(compiler, 'prepare_manual'))
        schema = output_schema()
        self.assertNotIn('slots', json.dumps(schema))
        for key in ('steps','branches'):
            self.assertNotIn('maxItems', schema['properties']['interpretation']['anyOf'][0]['properties'][key])

    def test_recipe_v1_is_rejected_before_creating_files(self):
        old = {'schema_version':1, 'base':'CUSTOM', 'slots':[], 'source_text':'print(1)'}
        with tempfile.TemporaryDirectory() as folder, patch.object(storage, 'ROOT', Path(folder)):
            for operation in (storage.preview, storage.generate, storage.load_saved_recipe):
                with self.subTest(operation=operation.__name__), self.assertRaises(ValueError): operation(old)
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
            self.assertFalse((Path(folder)/'generated').exists())

    def test_generation_numbering_and_existing_files_are_protected(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(storage,'ROOT',Path(folder)), \
             patch.object(storage,'_number_paths',return_value=iter([])):
            # Use actual generated-directory enumeration after reserving originals.
            target=Path(folder)/'generated';target.mkdir()
            (target/'SPECIAL021.py').write_text('ORIGINAL',encoding='utf-8')
            (target/'Test_SPECIAL022.py').write_text('EXISTING',encoding='utf-8')
            with patch.object(storage,'_number_paths',side_effect=lambda:iter([target])):
                recipe=recipe_from_intent(intent())
                first=storage.generate(recipe);second=storage.generate(recipe)
            self.assertEqual(first['filename'],'Test_SPECIAL023.py')
            self.assertEqual(second['filename'],'Test_SPECIAL024.py')
            self.assertEqual((target/'SPECIAL021.py').read_text('utf-8'),'ORIGINAL')
            self.assertEqual((target/'Test_SPECIAL022.py').read_text('utf-8'),'EXISTING')
            sidecar=json.loads((target/'Test_SPECIAL023.recipe.json').read_text('utf-8'))
            self.assertEqual(sidecar['recipe']['schema_version'],2)
            self.assertNotIn('manual',sidecar)

    def test_invalid_filename_and_code_fields_are_rejected(self):
        recipe=recipe_from_intent(intent())
        for filename in ('SPECIAL001.py','Test_SPECIAL000.py','../Test_SPECIAL008.py'):
            with self.subTest(filename=filename), self.assertRaises(ValueError): compile_recipe(recipe,filename)
        for field in ('source_text','manual_code','slots'):
            bad=copy.deepcopy(recipe);bad[field]='not allowed'
            with self.subTest(field=field), self.assertRaises(ValueError):compile_recipe(bad)

    def test_draft_accepts_only_validated_ai_recipe(self):
        with tempfile.TemporaryDirectory() as folder,patch.object(storage,'ROOT',Path(folder)):
            draft={'recipe':recipe_from_intent(intent())}
            storage.save_draft(draft)
            target=Path(folder)/'projects/last_draft.json'
            before=target.read_bytes()
            self.assertEqual(storage.load_saved_draft(json.loads(before)),draft)
            with self.assertRaises(ValueError): storage.save_draft(dict(draft,manual_code='print(1)'))
            self.assertEqual(target.read_bytes(),before)

    def test_legacy_generated_file_remains_view_only(self):
        with tempfile.TemporaryDirectory() as folder,patch.object(storage,'ROOT',Path(folder)):
            target=Path(folder)/'generated';target.mkdir()
            source='# preserved legacy source\nraise RuntimeError("never execute")\n'
            (target/'Test_SPECIAL210.py').write_text(source,encoding='utf-8')
            (target/'Test_SPECIAL210.recipe.json').write_text(json.dumps({'recipe':{'schema_version':1,'slots':[{}]}}),encoding='utf-8')
            viewed=storage.reopen('Test_SPECIAL210.py')
            self.assertEqual(viewed['code'],source)
            self.assertNotIn('recipe',viewed)
            self.assertNotIn('manual',viewed)

    def test_inherited_templates_do_not_create_legacy_recipes_or_slots(self):
        for number in (4,5):
            with self.subTest(number=number):
                meaning=recipe_from_intent(inherited(number))['strategy_intent']
                template=inherited_recipe(meaning)
                self.assertIn('parameters',template)
                self.assertNotIn('slots',template)
                self.assertNotIn('schema_version',template)
                _module,source=module_for(inherited(number),f'Test_SPECIAL{700+number}')
                self.assertNotIn('from lab',source)
                self.assertNotIn('import Part3',source)

    def test_special4_named_parameters_generate_actual_requested_gates(self):
        value=inherited(4,touch_tf='5m',touch_hma_period=73,middle_ema_fast=37,middle_ema_slow=99,
            middle_hma_period=96,execution_ema_fast=12,execution_ema_slow=26,lookback=3)
        _module,source=module_for(value)
        for fragment in ("'hma_73'","'hma_96'","'ema_37'","'ema_99'","'ema_12'","'ema_26'"):
            self.assertIn(fragment,source)
        self.assertIn('boundary_epoch - 300',source)
        self.assertNotIn("p[3]",source)

    def test_special4_gate_values_and_atr_disable(self):
        import pandas as pd
        mod,_source=module_for(inherited(4,touch_hma_period=50,lookback=2,atr_gate=False))
        runtime=mod.TestSPECIAL777Runtime
        frame=lambda **columns:pd.DataFrame(columns)
        data={'1m':frame(close=[120,121,122],ema_50=[110]*3,ema_200=[100]*3),
              '3m':frame(ema_50=[110]*3,ema_200=[100]*3,hma_168=[95,96,97]),
              '15m':frame(hma_50=[100,101,102])}
        self.assertTrue(runtime._current_trend_allowed(data,'LONG')[0])
        self.assertFalse(runtime._current_trend_allowed(data,'SHORT')[0])
        data['1m']['ema_50']=90
        self.assertFalse(runtime._current_trend_allowed(data,'LONG')[0])
        self.assertFalse(runtime._excursion_exceeded(pd.DataFrame({'high':[1000],'low':[0]}),{}))

    def test_special5_named_parameters_and_cancellation(self):
        value=inherited(5,source_tfs=['15m'],ema_filter=False,max_bars_after_b0=5,
            cancel_parent_opposite_hma=False,cancel_child_opposite_hma=False,hma_fast=7,hma_slow=19)
        mod,source=module_for(value)
        runtime=mod.TestSPECIAL777Runtime
        self.assertEqual(runtime._lower_max_bars(None),5)
        self.assertTrue(runtime._source_ema_trend_allowed(None,'LONG','15m'))
        self.assertEqual(mod.SOURCE_TFS,('15m',))
        self.assertIn('parent cancellation disabled',source)
        self.assertIn('child cancellation disabled',source)
        self.assertIn('hma_7',source);self.assertIn('hma_19',source)

    def test_special4_timeframe_conflict_is_not_silently_changed(self):
        with self.assertRaisesRegex(ValueError,'다릅니다'):
            recipe_from_intent(inherited(4,execution_tf='3m'))
        value=inherited(4);value['interpretation']['final']['tfs']=['1m','3m']
        with self.assertRaisesRegex(ValueError,'하나'):
            recipe_from_intent(value)

    def test_special5_seconds_window_is_not_substituted_for_bar_expiry(self):
        value=inherited(5);value['interpretation']['final_window_sec']=600
        with self.assertRaisesRegex(ValueError,'봉 수'):
            recipe_from_intent(value)


if __name__=='__main__':unittest.main()
