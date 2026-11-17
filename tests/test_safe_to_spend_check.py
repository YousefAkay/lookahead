"""The honesty check's report (eval/safe_to_spend_check.py) on made-up records."""

from datetime import date
from pathlib import Path

from eval.safe_to_spend_check import dated_path, near_edge, reading, report


def rec(persona: str, safe: int | None, usual: int, actual: int, stayed: bool) -> dict:
    return {"persona": persona, "archetype": "SAL", "origin": "2026-01-05", "days": 10,
            "safe": safe, "usual": usual, "actual": actual, "stayed": stayed,
            "below_at_origin": False}  # fmt: skip


def test_near_the_edge_is_a_safe_amount_at_most_twice_the_usual() -> None:
    rows = [rec("p1", 2_000, 1_000, 0, True), rec("p1", 2_001, 1_000, 0, True),
            rec("p1", None, 1_000, 0, False), rec("p1", 500, 0, 0, True)]  # fmt: skip
    assert near_edge(rows) == [rows[0]]


def test_the_reading_follows_the_rule_written_before_the_run() -> None:
    def rows(n_personas: int, per: int, stayed: int) -> list[dict]:
        out = []
        for p in range(n_personas):
            out += [rec(f"p{p}", 1_500, 1_000, 1_000, k < stayed) for k in range(per)]
        return out

    assert reading(rows(4, 10, 10)).startswith("too few")  # 4 personas
    assert reading(rows(5, 5, 5)).startswith("too few")  # 25 origins
    assert reading(rows(5, 10, 8)) == "the 80 percent holds near the edge"
    assert reading(rows(5, 10, 7)) == "slightly optimistic near the edge"
    assert reading(rows(5, 10, 6)).startswith("optimistic near the edge")


def test_the_report_has_the_near_the_edge_rows_with_their_n() -> None:
    rows = [
        rec("p1", 1_500, 1_000, 1_000, True),
        rec("p2", 1_500, 1_000, 1_000, False),
        rec("p1", 1_500, 1_000, 2_000, False),
        rec("p3", 50_000, 1_000, 1_000, True),
    ]
    text = report(rows)
    assert (
        "near the edge, actual everyday spending at or below it | 50% (1 of 2, 2 personas)" in text
    )
    assert "near the edge, actual everyday spending above it | 0% (0 of 1, 1 personas)" in text
    assert "Reading (rule written before the run): too few" in text


def test_the_reading_boundaries_at_30_origins() -> None:
    def rows(n: int) -> list[dict]:
        return [rec(f"p{k % 5}", 1_500, 1_000, 1_000, True) for k in range(n)]

    assert reading(rows(29)).startswith("too few")
    assert reading(rows(30)) == "the 80 percent holds near the edge"


def test_exactly_twice_the_usual_is_near_the_edge_whatever_was_spent() -> None:
    rows = [rec("p1", 2_000, 1_000, 2_500, False)]  # exactly 2 times, spent above it
    assert near_edge(rows) == rows
    assert "above it | 0% (0 of 1, 1 personas)" in report(rows)


def test_a_second_run_the_same_day_never_overwrites(tmp_path: Path) -> None:
    first = dated_path(tmp_path, date(2026, 11, 17))
    first.write_text("scored", encoding="utf-8")
    second = dated_path(tmp_path, date(2026, 11, 17))
    assert second != first and second.name == "safe_to_spend_check_2026-11-17-2.md"
    assert first.read_text(encoding="utf-8") == "scored"
