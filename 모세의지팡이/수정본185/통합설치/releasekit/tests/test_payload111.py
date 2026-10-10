"""Current licensed MT5 outputs must reach both runtime and visible locations."""
from pathlib import Path
import zipfile

import pytest

from releasekit import builder, mt5, validation
from test_builder import fake_build, project, write


def files(payload):
    for name in mt5.MT5_PROGRAMS:
        value = ("new licensed " + name).encode()
        for folder in ("MT5", "Part1/program/MT5"):
            write(payload, folder + "/" + name + ".ex5", value)


@pytest.mark.parametrize("problem", ["missing", "different", "extra", "empty"])
def test_missing_or_stale_manual_mt5_payload_is_rejected(tmp_path, problem):
    files(tmp_path)
    selected = tmp_path / "MT5/DI_of_Moses.ex5"
    if problem == "missing":
        selected.unlink()
    elif problem == "different":
        selected.write_bytes(b"stale developer original")
    elif problem == "empty":
        selected.write_bytes(b"")
        (tmp_path / "Part1/program/MT5/DI_of_Moses.ex5").write_bytes(b"")
    else:
        (tmp_path / "MT5/extra.mq5").write_bytes(b"source must not be shipped")
    with pytest.raises(ValueError, match="MT5"):
        validation.verify_mt5_files(tmp_path)


def test_current_licensed_files_are_used_in_both_locations_and_next_version(fake_build, monkeypatch):
    root, unused, events, clock = fake_build
    def compile_current(root, stage, expires, **unused):
        inputs = b"\n".join(p.read_bytes() for p in sorted((Path(root) / "Part1/program/MT5").glob("*.mq5")))
        return [write(Path(stage), name + ".ex5", b"licensed:" + name.encode() + inputs) for name in mt5.MT5_PROGRAMS]
    monkeypatch.setattr(mt5, "build_mt5", compile_current)
    original = {p.relative_to(root).as_posix(): p.read_bytes() for p in (root / "Part1/program/MT5").iterdir()}
    for index in range(2):
        if index:
            write(root, "Part1/program/MT5/DI_of_Moses.mq5", b"// next revision source\n")
        final, report = builder.build(root, builder.BuildSettings("v4." + str(index), "2026-12-31", 10), emit=lambda unused: None)
        archive = root / "통합설치/.work" / Path(report["release_validation"]["report"]).parent.name / "payload.zip"
        with zipfile.ZipFile(archive) as package:
            for name in mt5.MT5_PROGRAMS:
                inputs = b"\n".join(p.read_bytes() for p in sorted((root / "Part1/program/MT5").glob("*.mq5")))
                expected = b"licensed:" + name.encode() + inputs
                assert package.read("MT5/" + name + ".ex5") == package.read("Part1/program/MT5/" + name + ".ex5") == expected
        assert final.is_file()
    original["Part1/program/MT5/DI_of_Moses.mq5"] = b"// next revision source\n"
    assert original == {p.relative_to(root).as_posix(): p.read_bytes() for p in (root / "Part1/program/MT5").iterdir()}


def test_payload_tampering_after_runtime_validation_withholds_installer(fake_build, monkeypatch):
    root, unused, events, clock = fake_build
    original = validation.validate_release
    def tamper(current_root, payload, *args, **kwargs):
        result = original(current_root, payload, *args, **kwargs)
        (Path(payload) / "MT5/PRICE_of_Moses.ex5").write_bytes(b"unlicensed stale binary")
        return result
    monkeypatch.setattr(validation, "validate_release", tamper)
    with pytest.raises(ValueError, match="라이선스"):
        builder.build(root, builder.BuildSettings("v4.2", "2026-12-31", 10), emit=lambda unused: None)
    assert not any(event[0] == "compile" for event in events)
    assert not list((root / "배포").rglob("*.exe"))
