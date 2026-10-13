from pathlib import Path

from config import Settings, _database_path


def test_paper_and_live_database_defaults_are_separate(monkeypatch):
    monkeypatch.delenv("DATABASE_PATH", raising=False)

    monkeypatch.setenv("ENABLE_LIVE_TRADING", "false")
    paper_path = _database_path()

    monkeypatch.setenv("ENABLE_LIVE_TRADING", "true")
    live_path = _database_path()

    assert paper_path.name == "paper-trading.db"
    assert live_path.name == "trading.db"
    assert paper_path != live_path


def test_explicit_database_path_is_respected(monkeypatch, tmp_path):
    explicit = tmp_path / "chosen.db"
    monkeypatch.setenv("DATABASE_PATH", str(explicit))

    assert _database_path() == explicit


def test_paper_starting_capital_must_be_positive():
    settings = Settings(paper_starting_capital_usdt=0.0)

    assert "PAPER_STARTING_CAPITAL_USDT must be >0" in settings.validate()
