"""The real installer health probe must reject a missing/unsupported validator."""
from unittest.mock import patch
import importlib
import importlib.metadata
import pytest

from verification.test_unified_installer4 import (
    POWERSHELL, base_info, common_prelude, ps_literal, ps_object, run_json,
)

pytestmark = pytest.mark.skipif(POWERSHELL is None, reason='Windows PowerShell required')


@pytest.mark.parametrize('validator_version', [None, '4.22.0', '4.26.0', '5.0.0'])
def test_existing_main_environment_checks_jsonschema(tmp_path, validator_version):
    env = tmp_path / 'env'
    (env / 'Scripts').mkdir(parents=True)
    (env / 'Scripts/pythonw.exe').write_bytes(b'offline fixture')
    base = base_info()
    info = {**base, 'prefix': str(env), 'executable': str(env / 'Scripts/python.exe')}
    body = common_prelude()
    body += 'function Get-PythonInfo { return ' + ps_object(info) + ' }\n'
    body += '''function Invoke-Process {
        param($Executable, $Arguments, $WorkingDirectory, $TimeoutSeconds)
        $script:probe = $Arguments[-1]
        return [pscustomobject]@{ ExitCode=0; StdOut=''; StdErr='' }
    }
'''
    body += '$report = Get-EnvironmentReport ' + ps_literal(env) + ' ' + ps_object(base) + " 'main'\n"
    body += '$result = @{ probe=$script:probe; accepted=$report.OK }'
    result = run_json(tmp_path, body)
    assert result['accepted']
    requested = []
    original_import = importlib.import_module

    def load(name):
        requested.append(name)
        if name == 'jsonschema' and validator_version is None:
            raise ModuleNotFoundError('jsonschema')
        if name.startswith('tzdata.'):
            return original_import(name)

    versions = {'numpy': '2.3.5', 'pandas': '3.0.1', 'pyzmq': '27.1.0',
                'pywebview': '6.2.1', 'duckdb': '1.5.5', 'requests': '2.34.2',
                'jsonschema': validator_version}
    with patch.object(importlib, 'import_module', side_effect=load), \
         patch.object(importlib.metadata, 'version', side_effect=versions.__getitem__):
        if validator_version is None:
            with pytest.raises(ModuleNotFoundError, match='jsonschema'):
                exec(result['probe'], {})
        elif validator_version in ('4.22.0', '5.0.0'):
            with pytest.raises(AssertionError, match='jsonschema version mismatch'):
                exec(result['probe'], {})
        else:
            exec(result['probe'], {})
    assert 'jsonschema' in requested
