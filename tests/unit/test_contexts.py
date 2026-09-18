from app.domain.contexts import deterministic_id


def test_same_input_same_output():
    a = deterministic_id("acme", "123@g.us", ["m1", "m2"], "v1")
    b = deterministic_id("acme", "123@g.us", ["m1", "m2"], "v1")
    assert a == b


def test_message_id_order_does_not_affect_hash():
    a = deterministic_id("acme", "123@g.us", ["m1", "m2", "m3"], "v1")
    b = deterministic_id("acme", "123@g.us", ["m3", "m1", "m2"], "v1")
    assert a == b


def test_different_message_ids_change_hash():
    a = deterministic_id("acme", "123@g.us", ["m1", "m2"], "v1")
    b = deterministic_id("acme", "123@g.us", ["m1", "m3"], "v1")
    assert a != b


def test_different_extractor_version_changes_hash():
    a = deterministic_id("acme", "123@g.us", ["m1", "m2"], "v1")
    b = deterministic_id("acme", "123@g.us", ["m1", "m2"], "v2")
    assert a != b
