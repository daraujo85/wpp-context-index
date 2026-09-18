"""Unit tests for app.services.grouping.group (PRD §7, T10)."""
from __future__ import annotations

from app.domain.messages import MessageEnvelope
from app.services.grouping import group

_CTX = dict(company="acme", chat_id="1203@g.us", chat_name="Operacao", is_group=True)


def _msg(message_id, timestamp, *, quoted_message_id=None, body="oi"):
    return MessageEnvelope(
        company=_CTX["company"],
        chat_id=_CTX["chat_id"],
        chat_name=_CTX["chat_name"],
        is_group=_CTX["is_group"],
        message_id=message_id,
        sender_id="5511@c.us",
        sender_name=None,
        timestamp=timestamp,
        type="text",
        body=body,
        quoted_message_id=quoted_message_id,
        has_media=False,
    )


def test_prd_section7_example_becomes_one_unit():
    # 14:10..14:13, all gaps <= 1 minute, well within a 20-minute window.
    base = 1700000000
    msgs = [
        _msg("1", base, body="o erro continua no vencimento"),
        _msg("2", base + 60, body="acontece so no cliente antigo?"),
        _msg("3", base + 120, body="sim"),
        _msg("4", base + 120, body="[imagem]"),
        _msg("5", base + 180, body="aqui aparece 500 quando salvo"),
    ]
    units = group(msgs, gap_minutes=20)
    assert len(units) == 1
    assert units[0].message_ids == ["1", "2", "3", "4", "5"]
    assert units[0].company == "acme"
    assert units[0].chat_id == "1203@g.us"


def test_reply_outside_temporal_window_still_joins_quoted_units():
    base = 1700000000
    gap = 20 * 60
    msgs = [
        _msg("1", base),
        # big gap -> starts its own temporal unit
        _msg("2", base + gap + 60),
        # replies to "1", arrives even later -> outside "1"'s temporal window,
        # but must join "1"'s unit rather than "2"'s or a new one.
        _msg("3", base + 2 * gap + 120, quoted_message_id="1"),
    ]
    units = group(msgs, gap_minutes=20)
    assert len(units) == 2
    unit_by_id = {}
    for u in units:
        for mid in u.message_ids:
            unit_by_id[mid] = u
    assert unit_by_id["1"] is unit_by_id["3"]
    assert unit_by_id["2"] is not unit_by_id["1"]


def test_entire_day_is_never_a_single_unit():
    base = 1700000000
    # 50 consecutive messages, 1 minute apart, all within the temporal window.
    msgs = [_msg(str(i), base + i * 60) for i in range(50)]
    units = group(msgs, gap_minutes=20)
    assert len(units) > 1
    total = sum(len(u.message_ids) for u in units)
    assert total == 50
