"""group() builds ContextUnits from MessageEnvelopes (PRD §7, T10).

Grouping rule, per chat, in timestamp order:
- a reply (quoted_message_id) always joins its quoted message's unit, even
  across a gap bigger than the temporal window (PRD §7.1 "replies/citações");
- otherwise, consecutive messages stay in the same unit while the gap to the
  previous one is <= gap_minutes; a bigger gap starts a new unit;
- a unit never grows past max_messages_per_unit (PRD §7.2 "não agrupar um
  dia inteiro") — PRD gives no exact number, only "tamanho máximo
  configurável", so this is a parameter, not a hardcoded constant.
"""
from __future__ import annotations

from app.domain.contexts import ContextUnit, deterministic_id
from app.domain.messages import MessageEnvelope

DEFAULT_MAX_MESSAGES_PER_UNIT = 20


def group(
    messages: list[MessageEnvelope],
    gap_minutes: int,
    *,
    max_messages_per_unit: int = DEFAULT_MAX_MESSAGES_PER_UNIT,
    extractor_version: str = "v1",
) -> list[ContextUnit]:
    gap_seconds = gap_minutes * 60

    by_chat: dict[tuple[str, str], list[MessageEnvelope]] = {}
    for msg in messages:
        by_chat.setdefault((msg.company, msg.chat_id), []).append(msg)

    result: list[ContextUnit] = []
    for (company, chat_id), chat_msgs in by_chat.items():
        chat_msgs = sorted(chat_msgs, key=lambda m: m.timestamp or 0)
        units: list[list[MessageEnvelope]] = []
        unit_of: dict[str, int] = {}
        current_idx = -1
        last_ts: int | None = None

        for msg in chat_msgs:
            joined_as_reply = False
            if msg.quoted_message_id and msg.quoted_message_id in unit_of:
                target = unit_of[msg.quoted_message_id]
                if len(units[target]) < max_messages_per_unit:
                    units[target].append(msg)
                    if msg.message_id:
                        unit_of[msg.message_id] = target
                    joined_as_reply = True

            if not joined_as_reply:
                starts_new_unit = (
                    current_idx == -1
                    or len(units[current_idx]) >= max_messages_per_unit
                    or (
                        last_ts is not None
                        and msg.timestamp is not None
                        and msg.timestamp - last_ts > gap_seconds
                    )
                )
                if starts_new_unit:
                    units.append([msg])
                    current_idx = len(units) - 1
                else:
                    units[current_idx].append(msg)
                if msg.message_id:
                    unit_of[msg.message_id] = current_idx
                if msg.timestamp is not None:
                    last_ts = msg.timestamp

        for unit_msgs in units:
            message_ids = [m.message_id for m in unit_msgs if m.message_id]
            result.append(
                ContextUnit(
                    id=deterministic_id(company, chat_id, message_ids, extractor_version),
                    company=company,
                    chat_id=chat_id,
                    message_ids=message_ids,
                )
            )
    return result
