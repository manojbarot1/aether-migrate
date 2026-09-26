from aether.audit.writer import GENESIS_HASH, compute_hash


def test_hash_is_deterministic_and_key_order_independent() -> None:
    a = compute_hash(GENESIS_HASH, {"b": 1, "a": {"y": 2, "x": 1}})
    b = compute_hash(GENESIS_HASH, {"a": {"x": 1, "y": 2}, "b": 1})
    assert a == b
    assert len(a) == 64


def test_hash_depends_on_previous_hash_and_content() -> None:
    base = compute_hash(GENESIS_HASH, {"action": "x"})
    assert compute_hash("f" * 64, {"action": "x"}) != base
    assert compute_hash(GENESIS_HASH, {"action": "y"}) != base
