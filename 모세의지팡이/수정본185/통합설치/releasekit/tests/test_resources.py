"""Resource packaging behavior using isolated synthetic projects only."""
from pathlib import Path
import shutil

import pytest

from releasekit.resources import (ResourceManifest, ResourceReference,
                                  discover_resources, required_documents,
                                  validate_payload)


def write(root, name, data='fixture'):
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data if isinstance(data, bytes) else data.encode('utf-8'))
    return path


@pytest.fixture
def project(tmp_path):
    root = tmp_path / 'current project'
    write(root, 'Part3/lab/server.py', "DOCS = ['README.md', 'docs/guide.md']\nraise RuntimeError('must not execute original')\n")
    write(root, 'Part3/README.md')
    write(root, 'Part3/docs/guide.md')
    write(root, 'Part3/web/index.html', '<html><head><script src="app.js"></script></head></html>')
    write(root, 'Part3/web/app.js', '// no services')
    return root


def test_current_document_declaration_includes_new_future_document_without_importing_server(project):
    assert required_documents(project) == ('Part3/README.md', 'Part3/docs/guide.md')
    write(project, 'Part3/lab/server.py', "DOCS = ['README.md', 'docs/guide.md', 'NEW_VERSION_NOTES.rst']\nraise RuntimeError('must not run')\n")
    write(project, 'Part3/NEW_VERSION_NOTES.rst')
    assert 'Part3/NEW_VERSION_NOTES.rst' in discover_resources(project).files


def test_missing_document_fails_before_issuing_a_release(project):
    (project / 'Part3/README.md').unlink()
    with pytest.raises(ValueError, match='Part3/README.md'):
        required_documents(project)


@pytest.mark.parametrize('bad', ['../outside.md', '/root.md', 'C:/private.md', 'docs/../../key.md', './guide.md', 'docs//guide.md'])
def test_document_declaration_cannot_escape_or_store_absolute_paths(project, bad):
    write(project, 'Part3/lab/server.py', 'DOCS = ' + repr([bad]))
    with pytest.raises(ValueError, match='상대 경로'):
        required_documents(project)


def test_dynamic_document_list_is_reported_instead_of_executing_original_code(project):
    write(project, 'Part3/lab/server.py', 'DOCS = load_private_settings()')
    with pytest.raises(ValueError, match='정적인'):
        required_documents(project)


def test_synthetic_project_without_document_declaration_is_supported(project):
    (project / 'Part3/lab/server.py').unlink()
    assert required_documents(project) == ()
    write(project, 'Part3/lab/server.py', 'VALUE = 1')
    assert required_documents(project) == ()


def test_new_asset_suffixes_are_preserved_but_sources_models_and_user_state_are_excluded(project):
    included = ['Part3/web/font.woff2', 'Part3/web/new_picture.webp', 'Part3/web/decoder.wasm', 'Part3/docs/sample.csv']
    excluded = ['Part3/web/original.py', 'Part3/web/model.gguf', 'Part3/web/key.pem',
                'Part3/web/.keys/release_rsa.json', 'Part3/web/projects/connections.json',
                'Part3/web/Models/model.bin', 'Part3/docs/logs/private.txt']
    for name in included + excluded:
        write(project, name)
    files = set(discover_resources(project).files)
    assert set(included) <= files
    assert not set(excluded) & files


def test_html_css_and_module_references_follow_local_assets_and_ignore_external_urls(project):
    write(project, 'Part3/web/index.html', '<link rel="stylesheet" href="theme.css?v=2"><script src="module.mjs"></script><img src="image.svg#logo"><a href="https://example.com/help">help</a>')
    write(project, 'Part3/web/theme.css', '@import "nested/extra.css"; @font-face{src:url(../fonts/new.woff2)} .x{background:url(data:image/png;base64,AAAA)}')
    write(project, 'Part3/web/nested/extra.css', '/* url(missing.png) */ body{color:white}')
    write(project, 'Part3/web/module.mjs', "import './nested/new.mjs'; new URL('../fonts/data.bin', import.meta.url); import('https://example.com/external.js');")
    write(project, 'Part3/web/nested/new.mjs', '// new module')
    write(project, 'Part3/web/image.svg', '<svg/>')
    write(project, 'Part3/fonts/new.woff2', b'font bytes')
    write(project, 'Part3/fonts/data.bin', b'data bytes')
    manifest = discover_resources(project)
    assert 'Part3/fonts/new.woff2' in manifest.files
    assert 'Part3/fonts/data.bin' in manifest.files
    assert any(ref.source == 'Part3/web/module.mjs' and ref.target == 'Part3/fonts/data.bin' for ref in manifest.references)


def test_missing_script_or_stylesheet_fails_instead_of_shipping_blank_ui(project):
    write(project, 'Part3/web/index.html', '<script src="missing.js"></script>')
    with pytest.raises(ValueError, match='missing.js'):
        discover_resources(project)


def test_web_reference_cannot_read_project_external_file(project):
    write(project, 'Part3/web/index.html', '<script src="../../../outside.js"></script>')
    with pytest.raises(ValueError, match='프로젝트 밖'):
        discover_resources(project)


def test_web_reference_cannot_package_original_code_or_secret_settings(project):
    write(project, 'Part3/projects/connections.json', '{"private":true}')
    write(project, 'Part3/web/index.html', '<a href="../projects/connections.json">download</a>')
    with pytest.raises(ValueError, match='개인 설정'):
        discover_resources(project)


def test_static_python_file_read_is_verified_without_executing_the_source(project):
    code = "from pathlib import Path\nROOT = Path(__file__).resolve().parents[1]\nASSET = ROOT / 'web' / 'future.dat'\nvalue = ASSET.read_bytes()\nunknown = settings_path.read_text()\nraise RuntimeError('must not execute')\n"
    write(project, 'Part3/lab/future.py', code)
    write(project, 'Part3/web/future.dat', b'future bytes')
    manifest = discover_resources(project, [('Part3/lab/future.py', project / 'Part3/lab/future.py')])
    assert ResourceReference('Part3/lab/future.py', 'Part3/web/future.dat', 'static_python_resource_read') in manifest.references
    assert manifest.unresolved_python_reads == ({'source': 'Part3/lab/future.py', 'line': 5, 'operation': 'read_text', 'reason': 'runtime_or_optional_path_not_inferred_as_required_resource'},)
    (project / 'Part3/web/future.dat').unlink()
    with pytest.raises(ValueError, match='future.dat'):
        discover_resources(project, ['Part3/lab/future.py'])


def test_payload_validation_requires_resources_and_initial_empty_output_directory(project, tmp_path):
    manifest = discover_resources(project)
    payload = tmp_path / 'installed copy'
    for name in manifest.files:
        target = payload / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(project / name, target)
    with pytest.raises(ValueError, match='Part3/TEST_SPECIAL/'):
        validate_payload(payload, manifest)
    (payload / 'Part3/TEST_SPECIAL').mkdir()
    assert validate_payload(payload, manifest)['resources'] == len(manifest.files)
    (payload / 'Part3/web/app.js').unlink()
    with pytest.raises(ValueError, match='app.js'):
        validate_payload(payload, manifest)


def test_compiled_virtual_code_reference_does_not_require_exposed_source(tmp_path):
    manifest = ResourceManifest((), (), (ResourceReference('Part3/lab/catalog.py', 'Part1/program/contract.py', 'static_python_code_read'),))
    assert validate_payload(tmp_path, manifest, ['Part1/program/contract.py'])['references'] == 1
    assert not (tmp_path / 'Part1/program/contract.py').exists()
    with pytest.raises(ValueError, match='contract.py'):
        validate_payload(tmp_path, manifest)
