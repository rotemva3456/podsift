"""'% new' on a tiny fixture: a repeat is mostly known, a new topic is mostly new."""
from companion.engine.novelty import concepts, percent_new, phrases

VLAN = ("A vlan splits one switch into many networks. The vlan tag rides in the frame. "
        "Trunk ports carry every vlan tag between switches. Spanning tree blocks loops, "
        "and spanning tree elects a root bridge. Trunk ports and the vlan tag again.")
VLAN_AGAIN = ("Recap: the vlan tag marks the frame, trunk ports carry the vlan tag, and spanning tree "
              "stops loops with a root bridge. Spanning tree and trunk ports, one more time.")
BGP = ("Bgp route selection starts with weight, then local preference. Local preference is shared "
       "inside the autonomous system. As path length breaks ties, and bgp route selection ends "
       "with router id. The as path and local preference matter most.")


def test_a_repeat_is_mostly_known_and_a_new_topic_is_mostly_new():
    heard = [[{"start": 0, "end": 60, "text": VLAN}]]          # segments or plain text both work
    repeat, repeat_new = percent_new(VLAN_AGAIN, heard)
    fresh, fresh_new = percent_new(BGP, heard)
    assert repeat is not None and fresh is not None
    assert repeat <= 0.35 < 0.8 <= fresh
    assert any("preference" in c for c in fresh_new)
    assert len(repeat_new) <= 1 and "trunk ports" not in repeat_new


def test_nothing_heard_means_all_new_and_no_concepts_means_unknown():
    assert percent_new(BGP, [])[0] == 1.0
    assert percent_new("hi there", [VLAN]) == (None, [])


def test_phrases_skip_stop_word_edges_and_bare_numbers():
    grams = phrases("The vlan tag is 12 and the trunk")
    assert "vlan tag" in grams and "vlan" in grams
    assert "the vlan" not in grams and "12" not in grams


def test_concepts_merge_contained_phrases_on_word_boundaries():
    docs = ["Relationship advice. Relationship goals. The ip address. The ip header. "
            "The vlan tag. The vlan tag again.", "something else entirely", "more text here"]
    picked = concepts(docs, min_df=1)[0]
    assert "relationship" in picked and "ip" in picked           # "ip" is not inside "relationship"
    assert sum(1 for p in picked if "vlan" in p) == 1           # "vlan" and "vlan tag" are one idea
