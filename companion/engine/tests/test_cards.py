"""SM-2 scheduling and the show-trivia filter."""
import pytest

from companion.engine.cards import DAY, grade_value, is_meta, parse_cards, schedule

NOW = 1_000_000.0


def test_sm2_intervals_grow_with_good_answers():
    state = {}
    intervals = []
    for _ in range(4):
        state = schedule(state, "good", now=NOW)
        intervals.append(state["interval"])
    assert intervals == [1, 4, 10, 25]
    assert state["reps"] == 4 and state["ease"] == 2.5 and state["due"] == NOW + 25 * DAY


def test_sm2_again_hard_and_easy():
    known = {"interval": 10, "ease": 2.5, "reps": 3, "lapses": 1}
    again = schedule(known, 0, now=NOW)
    assert again == {"interval": 0, "ease": 2.3, "reps": 0, "lapses": 2, "due": NOW + 600}
    hard = schedule(known, "hard", now=NOW)
    assert hard["interval"] == round(round(10 * 2.35) * 0.6) and hard["ease"] == pytest.approx(2.35)
    easy = schedule(known, "4", now=NOW)
    assert easy["interval"] == round(10 * 2.6) and easy["ease"] == pytest.approx(2.6)
    floor = schedule({"ease": 1.3, "reps": 2, "interval": 3}, "again", now=NOW)
    assert floor["ease"] == 1.3
    with pytest.raises(ValueError):
        grade_value("perfect")


def test_meta_cards_are_filtered_but_real_ones_survive():
    assert is_meta("Who are the co-hosts of this show?")
    assert is_meta("Where can you leave a rating?", "Apple Podcasts")
    assert is_meta("How do you join the Slack community?")
    assert not is_meta("What does a credit rating measure?", "Default risk.")
    assert not is_meta("How does a host subscribe to a multicast group?", "IGMP join.")


def test_parse_cards_reads_the_cited_second():
    text = ("Q: What does a subnet mask do?\nA: It splits the address into network and host. [03:25]\n\n"
            "Q: What is the name of the podcast?\nA: Packet Pushers [00:05]\n\n"
            "Q: What is VLSM?\nA: Variable length subnet masks.\n")
    cards = parse_cards(text, prefix="ep1:")
    assert [(c["question"], c["answer"], c["at"]) for c in cards] == [
        ("What does a subnet mask do?", "It splits the address into network and host.", 205),
        ("What is VLSM?", "Variable length subnet masks.", None)]
    assert cards[0]["id"].startswith("ep1:")
