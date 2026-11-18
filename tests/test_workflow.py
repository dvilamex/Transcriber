from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_schedule_runs_every_day_including_weekends_at_mexico_city_morning():
    text = (ROOT / ".github" / "workflows" / "transcriber.yml").read_text(encoding="utf-8")
    assert 'cron: "0 13 * * *"' in text
    assert "1-5" not in text
    assert "America/Mexico_City" in text
    assert "including Saturday and Sunday" in text
