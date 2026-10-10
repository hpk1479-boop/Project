"""Public example selection is reference retrieval, never execution routing."""

import ast
import json
from pathlib import Path
import shutil

import pytest

from common_ai import reference_pack as reference
import moses_language as language


@pytest.fixture(autouse=True)
def clear_pack_cache():
    reference.load_pack.cache_clear()
    reference._index.cache_clear()
    yield
    reference.load_pack.cache_clear()
    reference._index.cache_clear()


def test_approved_pack_contains_100_complete_public_examples():
    pack = reference.load_pack()
    assert len(pack["examples"]) == 100
    assert {example["id"] for example in pack["examples"]} == {f"{number:03}" for number in range(1, 101)}
    assert pack["version"] and pack["core_rules"]
    assert all(set(example) == {"id", "input", "meaning"} for example in pack["examples"])
    content = json.dumps(json.loads(language.language_path().read_text(encoding="utf-8")), ensure_ascii=False)
    for private_marker in ("C:/", "C:\\\\", "api_key", "password", "adapter_path", "def register", "ConditionSpec("):
        assert private_marker not in content


def test_load_and_repeated_selection_read_file_only_once(monkeypatch):
    original_open = Path.open
    reads = []

    def counted_open(path, *args, **kwargs):
        if path.name == "language.json":
            reads.append(path.name)
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", counted_open)
    first = reference.load_pack()
    assert reference.load_pack() is first
    reference.select_examples("15분 상승추세에 1분 올존")
    reference.select_examples("5분 새 FVG에서 1분 올존")
    assert reads == ["language.json"]
    with pytest.raises(TypeError):
        first["version"] = "changed"
    with pytest.raises(TypeError):
        first["examples"][0]["meaning"] = "changed"


def test_simple_trend_request_selects_related_examples_without_extra_families():
    examples = reference.select_examples("15분 상승추세에 1분 올존 전략 만들어줘")
    assert len(examples) == 2
    assert all("추세" in example["meaning"] and "OZ" in example["meaning"] for example in examples)
    assert all("FVG" not in example["meaning"] and "WONBI" not in example["meaning"] for example in examples)


def test_new_fvg_request_prefers_new_object_example_to_existing_state():
    examples = reference.select_examples("5분봉 새 FVG에서 나오는 1분 올존")
    assert examples
    assert "바로 그 FVG" in examples[0]["meaning"]
    assert "새로 생성" in examples[0]["meaning"]


def test_tf_aliases_share_retrieval_without_fixed_sentence_matching():
    korean = reference.select_examples("15분 상승추세에 1분 올존")
    normalized = reference.select_examples("15m 상승 trend 1m OZ")
    assert {example["id"] for example in korean} == {example["id"] for example in normalized}
    assert "tf:60" in reference._terms("1시간")
    assert "tf:60" in reference._terms("60분")


def test_unrelated_text_or_example_number_is_not_a_strategy_selector():
    assert reference.select_examples("오늘 날씨 어때") == []
    assert reference.select_examples("#088") == []
    assert reference.select_examples("") == []
    assert reference.select_examples("골드", limit=2) == []
    assert "domain:di" not in reference._terms("studio")


def test_budget_selects_whole_examples_without_truncating_meaning():
    query = "15분 상승추세에 1분 올존"
    complete = reference.select_examples(query, limit=1)
    serialized_length = len(json.dumps(complete, ensure_ascii=False, separators=(",", ":")))
    assert reference.select_examples(query, limit=1, max_chars=serialized_length) == complete
    assert reference.select_examples(query, max_chars=2) == []
    for example in reference.select_examples(query, max_chars=200):
        original = next(item for item in reference.load_pack()["examples"] if item["id"] == example["id"])
        assert example == dict(original)


def test_project_move_uses_new_module_sibling_pack(tmp_path, monkeypatch):
    source = language.language_path()
    relocated = tmp_path / "moved_project" / "moses_language"
    relocated.mkdir(parents=True)
    shutil.copyfile(source, relocated / source.name)
    monkeypatch.setattr(language, "__file__", str(relocated / "__init__.py"))
    assert len(reference.load_pack()["examples"]) == 100
    assert reference.select_examples("미국장 시작 알려줘")[0]["input"] == "미국장 시작하면 알려줘"


def test_nested_oz_reference_uses_active_lifetime_and_fvg_uses_origin():
    nested = reference.select_examples("1시간 브레이커올존 부모 활성 동안 15분 브레이커올존 3분 올존")[0]
    assert "가격 박스가 아니라" in nested["meaning"]
    assert "역크로스" in nested["meaning"] and "타이머 초기화" in nested["meaning"]
    fvg_origin = next(example for example in reference.load_pack()["examples"] if example["id"] == "090")
    assert "발생·시작" in fvg_origin["meaning"]
    assert "현재 가격만" in fvg_origin["meaning"]


def test_runtime_contains_no_example_id_or_sentence_execution_branches():
    tree = ast.parse(Path(reference.__file__).read_text(encoding="utf-8"))
    constants = [node.value for node in ast.walk(tree) if isinstance(node, ast.Constant) and isinstance(node.value, str)]
    assert not any(value in {f"{number:03}" for number in range(1, 101)} for value in constants)
    assert not any(example["input"] in constants for example in reference.load_pack()["examples"])
    assert not any(isinstance(node, ast.Import) and any(alias.name.startswith(("Part1", "Part2")) for alias in node.names) for node in ast.walk(tree))


def test_invalid_pack_is_an_explicit_error_without_silent_fallback(tmp_path):
    invalid = tmp_path / "reference_pack.json"
    invalid.write_text('{"version":"test","core_rules":[],"examples":[{"id":"x","input":"test"}]}', encoding="utf-8")
    with pytest.raises(ValueError, match="예제 구조"):
        reference.load_pack(invalid)


def test_related_contract_topics_are_selected_by_common_public_terms():
    assert reference.relevant_kinds("15분 상승추세에 1분 올존 전략 만들어줘") == ["TREND", "OZ_ALERT"]
    topics = reference.relevant_kinds("1분 50 200 골크 후 5분 fvg 새로 생기면 올존")
    assert {"MA_CROSS", "FVG_NEW", "OZ_ALERT"} <= set(topics)
    assert "PERCENTILE_OUT" not in topics and "REGIME_BAND" not in topics
    assert reference.relevant_kinds("오늘 날씨 어때") == []
    assert reference.relevant_kinds("#072") == []


def test_contract_term_matching_distinguishes_state_cross_and_price_touch():
    assert reference.relevant_kinds("정배열일때 알려줘") == ["MA_STATE"]
    assert reference.relevant_kinds("17헐 168헐 골크나면 알려줘") == ["MA_CROSS"]
    assert reference.relevant_kinds("나스닥 일봉이 20일선 돌파하면") == ["MA_PRICE_CROSS"]
    assert reference.relevant_kinds("5분 SMA120 터치하면") == ["MA_PRICE_TOUCH"]
    assert reference.relevant_kinds("TREND_METRIC rsi14 <= 30") == ["TREND_METRIC"]
