from __future__ import annotations

import scripts.check_experiment_ready as readiness


def test_missing_freeze_is_warning_before_final_check(monkeypatch, tmp_path, capsys) -> None:
    monkeypatch.setattr(readiness.Path, "resolve", lambda self: tmp_path / "scripts" / self.name)

    assert readiness.check_freeze(required=False) is True
    assert "[WARN]" in capsys.readouterr().out


def test_missing_freeze_fails_final_check(monkeypatch, tmp_path, capsys) -> None:
    monkeypatch.setattr(readiness.Path, "resolve", lambda self: tmp_path / "scripts" / self.name)

    assert readiness.check_freeze(required=True) is False
    assert "[FAIL]" in capsys.readouterr().out


def test_parse_args_enables_required_freeze() -> None:
    assert readiness.parse_args(["--require-freeze"]).require_freeze is True
    assert readiness.parse_args([]).require_freeze is False


def test_dataset_check_uses_canonical_loader(monkeypatch, tmp_path) -> None:
    dataset = tmp_path / "annotated.jsonl"
    dataset.write_text("{}\n", encoding="utf-8")
    monkeypatch.setattr(readiness, "DEFAULT_DATASET", dataset)
    monkeypatch.setattr(readiness, "load_dataset", lambda: [{"id": str(i)} for i in range(50)])

    assert readiness.check_dataset() is True
