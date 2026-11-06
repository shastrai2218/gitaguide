"""Unit tests for TopicTracker and Topic-Aware Memory in GitaGuide 2.0.
"""

import pytest

from app.db.repository import MessageRecord
from app.pipeline.memory import TopicTracker
from app.pipeline.schemas import TopicStatus


def test_topic_creation_and_separation():
    messages = [
        MessageRecord(
            id="1",
            session_id="s1",
            role="user",
            content="my father took loan of 1 cr and completed order of government but now they are not paying us",
        ),
        MessageRecord(
            id="2",
            session_id="s1",
            role="assistant",
            content="Hold your ground and follow audit procedures. Can you check the contract?",
        ),
        MessageRecord(
            id="3",
            session_id="s1",
            role="user",
            content="she broke up with me and she cheated",
        ),
    ]

    context = TopicTracker.extract_from_messages(messages)

    # 1. Both topics must be active simultaneously
    assert len(context.active_topics) == 2, f"Expected 2 active topics, got {len(context.active_topics)}"
    categories = {t.category for t in context.active_topics}
    assert "financial" in categories
    assert "relationship" in categories

    # 2. Financial topic must NOT contain relationship facts
    fin_topic = next(t for t in context.active_topics if t.category == "financial")
    assert any("loan" in f.lower() for f in fin_topic.user_facts)
    assert not any("cheated" in f.lower() for f in fin_topic.user_facts)

    # 3. Relationship topic must NOT contain financial facts
    rel_topic = next(t for t in context.active_topics if t.category == "relationship")
    assert any("broke up" in f.lower() or "cheated" in f.lower() for f in rel_topic.user_facts)
    assert not any("loan" in f.lower() for f in rel_topic.user_facts)

    # 4. Multi-topic flag must be True
    assert context.is_multi_topic is True


def test_commitment_separation_rule():
    """An assistant recommendation must NEVER be recorded as a user decision, even if user says ok."""
    messages = [
        MessageRecord(
            id="1",
            session_id="s2",
            role="user",
            content="I am having trouble sleeping because of work pressure.",
        ),
        MessageRecord(
            id="2",
            session_id="s2",
            role="assistant",
            content="You could try journaling for 10 minutes before bed.",
        ),
        MessageRecord(
            id="3",
            session_id="s2",
            role="user",
            content="ok",
        ),
    ]

    context = TopicTracker.extract_from_messages(messages)
    for topic in context.active_topics:
        # User decision must be empty
        assert len(topic.user_decisions) == 0, f"Expected 0 user decisions, found {topic.user_decisions}"
        # Assistant recommendation must be recorded separately
        assert any("journaling" in r.lower() for r in topic.assistant_recommendations)


def test_explicit_user_decision_recorded():
    """An explicit first-person commitment IS recorded as a user decision."""
    messages = [
        MessageRecord(
            id="1",
            session_id="s3",
            role="user",
            content="I have a terrible manager.",
        ),
        MessageRecord(
            id="2",
            session_id="s3",
            role="assistant",
            content="Have you thought about other teams?",
        ),
        MessageRecord(
            id="3",
            session_id="s3",
            role="user",
            content="I decided to submit my resignation letter tomorrow morning.",
        ),
    ]

    context = TopicTracker.extract_from_messages(messages)
    career_topic = next(t for t in context.active_topics if t.category == "career")
    assert len(career_topic.user_decisions) == 1
    assert "resignation" in career_topic.user_decisions[0].lower()


def test_topic_pausing_and_resolution():
    """Topics can be paused, resumed or resolved based on user cues."""
    messages = [
        MessageRecord(
            id="1",
            session_id="s4",
            role="user",
            content="my father took loan of 1 cr from bank",
        ),
        MessageRecord(
            id="2",
            session_id="s4",
            role="user",
            content="leave that aside for now",
        ),
    ]

    context = TopicTracker.extract_from_messages(messages)
    assert len(context.active_topics) == 0
    assert len(context.paused_topics) == 1
    assert context.paused_topics[0].status == TopicStatus.PAUSED

    # Now user says problem is resolved
    messages.append(
        MessageRecord(
            id="3",
            session_id="s4",
            role="user",
            content="update: the problem is resolved, bank approved our extension",
        )
    )
    context2 = TopicTracker.extract_from_messages(messages)
    assert len(context2.resolved_topics) == 1 or len(context2.active_topics) == 1

