"""Shared language meaning and portable loading, without API or engine runs."""
import json
from pathlib import Path
import shutil

import pytest

import moses_language as language
from common_ai import reference_pack as reference


@pytest.fixture(autouse=True)
def clean_language_cache():
    reference.load_pack.cache_clear()
    yield
    reference.load_pack.cache_clear()


def _raw_dictionary():
    return json.loads(language.language_path().read_text(encoding="utf-8"))


def _custom_dictionary(tmp_path, monkeypatch, raw=None):
    folder = tmp_path / "portable_project" / "moses_language"
    folder.mkdir(parents=True)
    path = folder / "language.json"
    path.write_text(json.dumps(raw if raw is not None else _raw_dictionary(), ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(language, "__file__", str(folder / "__init__.py"))
    return path


def test_reference_and_command_read_one_immutable_dictionary(monkeypatch):
    original = Path.open
    reads = []

    def counted_open(path, *args, **kwargs):
        if path.name == "language.json":
            reads.append(path)
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", counted_open)
    dictionary = language.load_dictionary()
    command = language.command_language()
    pack = reference.load_pack()
    language.public_vocabulary()
    reference.select_examples("15분 상승추세에 1분 올존")
    reference.relevant_kinds("5분 새 FVG")
    assert len(reads) == 1
    assert pack["examples"] is dictionary["examples"]
    assert pack["condition_terms"] is dictionary["condition_terms"]
    assert "골드" in command["symbols"]["XAUUSD"]
    assert "골크" in command["cross"]["LONG"]
    assert "데크" in command["cross"]["SHORT"]
    with pytest.raises(TypeError):
        dictionary["command_language"]["symbols"]["XAUUSD"] = ()
    command["symbols"]["XAUUSD"].append("private_edit")
    assert "private_edit" not in language.command_language()["symbols"]["XAUUSD"]


def test_shared_alias_change_is_available_to_command_and_ai(tmp_path, monkeypatch):
    raw = _raw_dictionary()
    raw["command_language"]["phrase_aliases"]["원비"].append("통통")
    raw["concepts"]["wonbi"].append("통통")
    raw["condition_terms"]["WONBI_TOUCH"].append("통통")
    _custom_dictionary(tmp_path, monkeypatch, raw)
    assert "통통" in language.command_language()["phrase_aliases"]["원비"]
    assert "domain:wonbi" in reference._terms("15분 통통")
    assert reference.relevant_kinds("15분 통통") == ["WONBI_TOUCH"]


def test_hot_reload_invalidation_refreshes_both_views(tmp_path, monkeypatch):
    path = _custom_dictionary(tmp_path, monkeypatch)
    original = reference.load_pack()
    raw = json.loads(path.read_text(encoding="utf-8"))
    raw["command_language"]["phrase_aliases"]["원비"].append("통통")
    raw["concepts"]["wonbi"].append("통통")
    raw["condition_terms"]["WONBI_TOUCH"].append("통통")
    path.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
    assert reference.load_pack() is original
    assert "통통" not in language.command_language()["phrase_aliases"]["원비"]
    language.cache_clear()
    assert reference.load_pack() is not original
    assert "통통" in language.command_language()["phrase_aliases"]["원비"]
    assert "domain:wonbi" in reference._terms("통통")
    assert reference.relevant_kinds("통통") == ["WONBI_TOUCH"]


def test_example_edit_after_reload_updates_related_reference_selection(tmp_path, monkeypatch):
    raw = _raw_dictionary()
    raw['examples'] = [
        {'id': 'fvg', 'input': '3분 FVG', 'meaning': '3분 FVG 조건'},
        {'id': 'wonbi', 'input': '15분 원비', 'meaning': '15분 WONBI 조건'},
    ]
    path = _custom_dictionary(tmp_path, monkeypatch, raw)
    assert reference.select_examples('15분 원비', limit=1)[0]['id'] == 'wonbi'
    raw['examples'][0]['input'], raw['examples'][1]['input'] = (
        raw['examples'][1]['input'], raw['examples'][0]['input'])
    raw['examples'][0]['meaning'], raw['examples'][1]['meaning'] = (
        raw['examples'][1]['meaning'], raw['examples'][0]['meaning'])
    path.write_text(json.dumps(raw, ensure_ascii=False), encoding='utf-8')
    language.cache_clear()
    selected = reference.select_examples('15분 원비', limit=1)[0]
    assert selected['id'] == 'fvg'
    assert selected['input'] == '15분 원비' and selected['meaning'] == '15분 WONBI 조건'


def test_concept_edit_after_reload_rebuilds_topic_matching(tmp_path, monkeypatch):
    raw = _raw_dictionary()
    raw['concepts']['fvg'].append('통통')
    raw['examples'] = [
        {'id': 'new_term', 'input': '통통', 'meaning': '통통'},
        {'id': 'existing_wonbi', 'input': '원비', 'meaning': '원비'},
    ]
    path = _custom_dictionary(tmp_path, monkeypatch, raw)
    assert reference.select_examples('통통', limit=1)[0]['id'] == 'new_term'
    raw['concepts']['fvg'].remove('통통')
    raw['concepts']['wonbi'].append('통통')
    path.write_text(json.dumps(raw, ensure_ascii=False), encoding='utf-8')
    language.cache_clear()
    # The newly defined word still retrieves its own complete example. A stale
    # FVG index would instead select the unrelated existing WONBI sentence.
    assert reference.select_examples('통통', limit=1)[0]['id'] == 'new_term'


def test_public_vocabulary_contains_terms_without_settings_or_all_examples(tmp_path, monkeypatch):
    raw = _raw_dictionary()
    raw["runtime"] = {"api_key": "test_private_marker"}
    raw["command_language"]["api_key"] = "test_private_marker"
    raw["syntax_literals"] = {"parser.regex": "test_private_parser_regex"}
    _custom_dictionary(tmp_path, monkeypatch, raw)
    public = language.public_vocabulary()
    assert public["aliases"]["wonbi_side"]["LOWER"]
    assert public["condition_terms"]["WONBI_TOUCH"]
    assert public["concepts"]["wonbi"]
    text = json.dumps(public, ensure_ascii=False)
    assert "test_private_marker" not in text
    assert "test_private_parser_regex" not in text
    assert not ({"examples", "defaults", "provenance", "runtime", "api_key"} & set(public))
    assert "defaults" not in public["aliases"]
    public["aliases"]["wonbi_side"]["LOWER"].clear()
    assert language.command_language()["wonbi_side"]["LOWER"]


def test_parser_syntax_is_data_and_never_an_ai_example(tmp_path, monkeypatch):
    raw = _raw_dictionary()
    raw["syntax_literals"] = {"parser.regex": r"\d+\s*분"}
    _custom_dictionary(tmp_path, monkeypatch, raw)
    assert language.syntax_literal("parser.regex") == r"\d+\s*분"
    assert "syntax_literals" not in language.public_vocabulary()
    with pytest.raises(KeyError):
        language.syntax_literal("unknown")


def test_invalid_parser_syntax_is_rejected(tmp_path):
    raw = _raw_dictionary()
    raw["syntax_literals"] = {"parser.regex": ["not", "a string"]}
    path = tmp_path / "invalid_syntax.json"
    path.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(ValueError, match="해석 문법"):
        language.load_dictionary(path)


def test_invalid_hot_reload_retains_every_last_good_view(tmp_path, monkeypatch):
    raw = _raw_dictionary()
    raw['syntax_literals'] = {'parser.term': '마지막 정상 문법'}
    path = _custom_dictionary(tmp_path, monkeypatch, raw)
    dictionary = language.load_dictionary()
    pack = reference.load_pack()
    command = language.command_language()
    before = reference.select_examples('15분 상승추세에 1분 올존')
    path.write_text('{broken json', encoding='utf-8')
    with pytest.raises(ValueError):
        language.reload_dictionary()
    assert language.load_dictionary() is dictionary
    assert reference.load_pack() is pack
    assert language.command_language() == command
    assert language.syntax_literal('parser.term') == '마지막 정상 문법'
    assert reference.select_examples('15분 상승추세에 1분 올존') == before


def test_successful_hot_reload_publishes_once_for_all_views(tmp_path, monkeypatch):
    raw = _raw_dictionary()
    raw['syntax_literals'] = {'parser.term': '원래 문법'}
    path = _custom_dictionary(tmp_path, monkeypatch, raw)
    old = language.load_dictionary()
    raw['syntax_literals']['parser.term'] = '새 문법'
    raw['command_language']['phrase_aliases']['원비'].append('통통')
    raw['condition_terms']['WONBI_TOUCH'].append('통통')
    path.write_text(json.dumps(raw, ensure_ascii=False), encoding='utf-8')
    original, reads = Path.open, []

    def counted(location, *args, **kwargs):
        if location == path:
            reads.append(location)
        return original(location, *args, **kwargs)

    monkeypatch.setattr(Path, 'open', counted)
    new = language.reload_dictionary()
    assert new is not old and language.load_dictionary() is new
    assert language.syntax_literal('parser.term') == '새 문법'
    assert '통통' in language.command_language()['phrase_aliases']['원비']
    assert reference.relevant_kinds('통통') == ['WONBI_TOUCH']
    assert reference.load_pack()['examples'] is new['examples']
    assert len(reads) == 1


def test_dictionary_relocation_preserves_command_and_reference_meaning(tmp_path, monkeypatch):
    source = language.language_path()
    moved = tmp_path / "different_drive" / "project" / "moses_language"
    moved.mkdir(parents=True)
    shutil.copyfile(source, moved / "language.json")
    monkeypatch.setattr(language, "__file__", str(moved / "__init__.py"))
    assert language.language_path() == moved / "language.json"
    assert language.command_language()["symbols"]["XAUUSD"][:2] == ["골드", "금"]
    assert reference.select_examples("미국장 시작 알려줘")[0]["input"] == "미국장 시작하면 알려줘"
    assert len(reference.load_pack()["examples"]) == 100


def test_legacy_explicit_reference_pack_keeps_retrieval_contract(tmp_path):
    raw = _raw_dictionary()
    legacy = {"version": raw["reference_version"], **{
        name: raw[name] for name in ("core_rules", "condition_terms", "examples")}}
    path = tmp_path / "custom_reference.json"
    path.write_text(json.dumps(legacy, ensure_ascii=False), encoding="utf-8")
    query = "15분 상승추세에 1분 올존"
    assert reference.select_examples(query, pack_path=path) == reference.select_examples(query)
    assert reference.relevant_kinds(query, pack_path=path) == reference.relevant_kinds(query)


def test_invalid_dictionary_is_rejected_instead_of_using_partial_data(tmp_path):
    raw = _raw_dictionary()
    raw["examples"][0].pop("meaning")
    path = tmp_path / "invalid_language.json"
    path.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(ValueError, match="예제 구조"):
        language.load_dictionary(path)


def test_language_data_keeps_distinct_windows_and_event_identity_rules():
    rules = "\n".join(reference.load_pack()["core_rules"])
    assert "within-N and active-for-M are separate windows" in rules
    assert "never reuse old OUT/touch/creation" in rules
    assert "origin/start inside" in rules
    assert "not a price box" in rules
    assert reference.select_examples("#088") == []
    assert reference.select_examples("#090") == []
