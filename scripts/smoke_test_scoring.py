"""Manual smoke test for scoring.py Task 7 — Scoring engine.

Pure Python — no server or network required.

    python scripts/smoke_test_scoring.py

What this tests
---------------
1. Instant correct answer    : time_remaining == time_limit → full BASE_POINTS (1000)
2. Buzzer correct answer     : time_remaining == 0 → MIN_POINTS floor (500)
3. Mid-timer correct answer  : score falls between 500 and 1000 proportionally
4. Streak 2-in-a-row         : second consecutive correct answer earns 1.1x multiplier
5. Streak 3-in-a-row         : third consecutive correct answer earns 1.2x (capped)
6. Wrong answer              : scores 0 and resets the streak to 0
7. Timeout (no answer)       : answer_elapsed=None scores 0 and resets streak
8. Fastest answer tracking   : fastest_answer stores the lowest elapsed time seen
9. Leaderboard sort          : score descending; ties broken by name ascending
10. Final rankings shape     : rank, name, score, correct, streak, fastest_answer fields
"""

import math
import sys

sys.path.insert(0, ".")

from src.scoring import (  # noqa: E402
    PlayerGameStats,
    build_final_rankings,
    build_leaderboard,
    score_answer,
)

PASS = "\033[32mPASS\033[0m"
FAIL = "\033[31mFAIL\033[0m"

BASE = 1000
FLOOR = 500


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------


def check(label: str, condition: bool, detail: str = "") -> None:
    """Print a PASS/FAIL line; exit immediately on first failure."""
    status = PASS if condition else FAIL
    suffix = f"  ({detail})" if detail else ""
    print(f"  [{status}] {label}{suffix}")
    if not condition:
        sys.exit(1)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    """Run end-to-end smoke checks for scoring behavior and ranking outputs."""
    print("\nSmoke test — Scoring engine (Task 7)\n")

    # ------------------------------------------------------------------
    # 1. Instant correct answer → full 1000 points
    # ------------------------------------------------------------------
    print("1. Instant correct answer → BASE_POINTS (1000)")

    stats = PlayerGameStats(name="Alice")
    r = score_answer(
        stats, is_correct=True, time_remaining=10.0, time_limit=10.0, answer_elapsed=0.1
    )

    check("score is 1000", r.score == BASE, str(r.score))
    check("total is 1000", r.total == BASE, str(r.total))
    check("streak is 1", r.streak == 1, str(r.streak))
    check("was_correct is True", r.was_correct is True)
    check(
        "fastest_answer recorded",
        stats.fastest_answer == 0.1,
        str(stats.fastest_answer),
    )

    # ------------------------------------------------------------------
    # 2. Buzzer answer (time_remaining == 0) → floor of 500
    # ------------------------------------------------------------------
    print("\n2. Buzzer answer → MIN_POINTS floor (500)")

    stats2 = PlayerGameStats(name="Bob")
    r2 = score_answer(
        stats2,
        is_correct=True,
        time_remaining=0.0,
        time_limit=10.0,
        answer_elapsed=10.0,
    )

    check("score is 500", r2.score == FLOOR, str(r2.score))
    check("total is 500", r2.total == FLOOR, str(r2.total))

    # ------------------------------------------------------------------
    # 3. Mid-timer answer → proportional score between 500 and 1000
    # ------------------------------------------------------------------
    print("\n3. Mid-timer answer → proportional score in [500, 1000]")

    stats3 = PlayerGameStats(name="Carol")
    # 5s remaining out of 10s → base = 1000 * 0.5 = 500 → floor = 500
    # Try 7s remaining → base = 700 → floor = 700
    r3 = score_answer(
        stats3, is_correct=True, time_remaining=7.0, time_limit=10.0, answer_elapsed=3.0
    )
    expected = math.floor(BASE * (7.0 / 10.0))  # 700

    check(
        f"score is {expected} (floor(1000 * 7/10))",
        r3.score == expected,
        str(r3.score),
    )
    check("score is in [500, 1000]", FLOOR <= r3.score <= BASE, str(r3.score))

    # ------------------------------------------------------------------
    # 4. Streak 2-in-a-row → 1.1x multiplier on second answer
    # ------------------------------------------------------------------
    print("\n4. Streak 2-in-a-row → 1.1x multiplier")

    stats4 = PlayerGameStats(name="Dave")
    score_answer(
        stats4,
        is_correct=True,
        time_remaining=10.0,
        time_limit=10.0,
        answer_elapsed=0.5,
    )
    r4b = score_answer(
        stats4,
        is_correct=True,
        time_remaining=10.0,
        time_limit=10.0,
        answer_elapsed=0.5,
    )
    expected_2 = math.floor(BASE * 1.1)  # 1100

    check("streak is 2", r4b.streak == 2, str(r4b.streak))
    check(
        f"score is {expected_2} (1000 * 1.1x)", r4b.score == expected_2, str(r4b.score)
    )

    # ------------------------------------------------------------------
    # 5. Streak 3-in-a-row → 1.2x multiplier (capped)
    # ------------------------------------------------------------------
    print("\n5. Streak 3-in-a-row → 1.2x multiplier (capped)")

    stats5 = PlayerGameStats(name="Eve")
    score_answer(
        stats5,
        is_correct=True,
        time_remaining=10.0,
        time_limit=10.0,
        answer_elapsed=0.5,
    )
    score_answer(
        stats5,
        is_correct=True,
        time_remaining=10.0,
        time_limit=10.0,
        answer_elapsed=0.5,
    )
    r5c = score_answer(
        stats5,
        is_correct=True,
        time_remaining=10.0,
        time_limit=10.0,
        answer_elapsed=0.5,
    )
    expected_3 = math.floor(BASE * 1.2)  # 1200

    check("streak is 3", r5c.streak == 3, str(r5c.streak))
    check(
        f"score is {expected_3} (1000 * 1.2x)", r5c.score == expected_3, str(r5c.score)
    )

    # Confirm streak 4 is still 1.2x (cap holds)
    r5d = score_answer(
        stats5,
        is_correct=True,
        time_remaining=10.0,
        time_limit=10.0,
        answer_elapsed=0.5,
    )
    check("streak 4+ still earns 1.2x", r5d.score == expected_3, str(r5d.score))

    # ------------------------------------------------------------------
    # 6. Wrong answer → 0 points, streak reset
    # ------------------------------------------------------------------
    print("\n6. Wrong answer → 0 points, streak reset to 0")

    stats6 = PlayerGameStats(
        name="Frank", current_streak=3, longest_streak=3, total_score=800
    )
    r6 = score_answer(
        stats6,
        is_correct=False,
        time_remaining=5.0,
        time_limit=10.0,
        answer_elapsed=2.0,
    )

    check("score is 0", r6.score == 0, str(r6.score))
    check("total unchanged at 800", r6.total == 800, str(r6.total))
    check("streak reset to 0", r6.streak == 0, str(r6.streak))
    check("was_correct is False", r6.was_correct is False)
    check(
        "longest_streak preserved at 3",
        stats6.longest_streak == 3,
        str(stats6.longest_streak),
    )

    # ------------------------------------------------------------------
    # 7. Timeout (answer_elapsed=None) → 0 points, streak reset
    # ------------------------------------------------------------------
    print("\n7. Timeout (answer_elapsed=None) → 0 points, streak reset")

    stats7 = PlayerGameStats(name="Grace", current_streak=2, longest_streak=2)
    r7 = score_answer(
        stats7,
        is_correct=False,
        time_remaining=0.0,
        time_limit=10.0,
        answer_elapsed=None,
    )

    check("score is 0", r7.score == 0, str(r7.score))
    check("streak reset to 0", r7.streak == 0, str(r7.streak))
    check(
        "longest_streak preserved at 2",
        stats7.longest_streak == 2,
        str(stats7.longest_streak),
    )

    # ------------------------------------------------------------------
    # 8. Fastest answer tracking — lowest elapsed time is kept
    # ------------------------------------------------------------------
    print("\n8. Fastest answer tracking")

    stats8 = PlayerGameStats(name="Hank")
    score_answer(
        stats8, is_correct=True, time_remaining=8.0, time_limit=10.0, answer_elapsed=2.0
    )
    check(
        "fastest set to 2.0 after first answer",
        stats8.fastest_answer == 2.0,
        str(stats8.fastest_answer),
    )

    score_answer(
        stats8, is_correct=True, time_remaining=9.0, time_limit=10.0, answer_elapsed=1.0
    )
    check(
        "fastest updated to 1.0 (lower elapsed)",
        stats8.fastest_answer == 1.0,
        str(stats8.fastest_answer),
    )

    # A slower correct answer should not overwrite the record
    score_answer(
        stats8, is_correct=True, time_remaining=5.0, time_limit=10.0, answer_elapsed=5.0
    )
    check(
        "fastest stays at 1.0 after slower answer",
        stats8.fastest_answer == 1.0,
        str(stats8.fastest_answer),
    )

    # ------------------------------------------------------------------
    # 9. Leaderboard sort — score desc, name asc on tie
    # ------------------------------------------------------------------
    print("\n9. Leaderboard sort: score descending, name ascending on tie")

    s_alice = PlayerGameStats(name="Alice", total_score=1500)
    s_bob = PlayerGameStats(name="Bob", total_score=1000)
    s_carol = PlayerGameStats(name="Carol", total_score=1000)  # tie with Bob

    lb = build_leaderboard({"Alice": s_alice, "Carol": s_carol, "Bob": s_bob})
    names = [row["name"] for row in lb]

    check("Alice is first (highest score)", names[0] == "Alice", str(names))
    check("Bob before Carol on tie (name asc)", names[1] == "Bob", str(names))
    check("Carol is third", names[2] == "Carol", str(names))
    check("all scores present", all("score" in row for row in lb))

    # ------------------------------------------------------------------
    # 10. Final rankings shape
    # ------------------------------------------------------------------
    print("\n10. Final rankings shape and field completeness")

    s_x = PlayerGameStats(
        name="Xavier",
        total_score=2000,
        correct_count=2,
        longest_streak=2,
        fastest_answer=0.8,
    )
    s_y = PlayerGameStats(
        name="Yvonne",
        total_score=500,
        correct_count=1,
        longest_streak=1,
        fastest_answer=1.5,
    )

    rankings = build_final_rankings({"Xavier": s_x, "Yvonne": s_y})

    check("two ranking rows", len(rankings) == 2, str(len(rankings)))

    required_fields = {"rank", "name", "score", "correct", "streak", "fastest_answer"}
    check(
        "all required fields present in rank-1 row",
        required_fields.issubset(rankings[0].keys()),
        str(rankings[0].keys()),
    )

    xav = next(r for r in rankings if r["name"] == "Xavier")
    yvo = next(r for r in rankings if r["name"] == "Yvonne")
    xav_fastest = xav["fastest_answer"]
    yvo_fastest = yvo["fastest_answer"]

    check("Xavier is rank 1", xav["rank"] == 1, str(xav["rank"]))
    check("Yvonne is rank 2", yvo["rank"] == 2, str(yvo["rank"]))
    check("Xavier correct count is 2", xav["correct"] == 2, str(xav["correct"]))
    check("Xavier longest_streak is 2", xav["streak"] == 2, str(xav["streak"]))
    check(
        "Xavier fastest_answer is 0.8",
        isinstance(xav_fastest, (int, float)) and abs(float(xav_fastest) - 0.8) < 1e-9,
        str(xav_fastest),
    )
    check(
        "Yvonne fastest_answer is 1.5",
        isinstance(yvo_fastest, (int, float)) and abs(float(yvo_fastest) - 1.5) < 1e-9,
        str(yvo_fastest),
    )

    print("\nAll checks passed.\n")


if __name__ == "__main__":
    main()
