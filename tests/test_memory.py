"""Conversation memory: what reaches the next prompt, and what must not.

Step C of Phase 1b asked whether the generation prompt receives history at all.
It already did - :meth:`MemoryContext.render` has been injecting a rolling
summary plus the last ``memory_last_n_turns`` turns since the memory stage was
written, and ``build_answer_prompt`` has been placing that block in every
prompt. The first test here pins that, so the answer to "does it?" is a test
result rather than a claim.

What was missing is narrower and is what these tests are mostly about: history
is the one place where an old reply's *shape* outlives the prompt that produced
it. A session that began before the Phase 1 response contract carries replies
with a "Why this verse" section and, in the oldest, pasted verse text - and it
feeds both back into every subsequent prompt, re-teaching the model the format
testers asked us to drop.
"""

from __future__ import annotations

import pytest

from app.config import Settings
from app.db.repository import InMemoryRepository, MessageRecord
from app.pipeline.memory import ConversationMemory, MemoryContext, strip_verse_payload

OLD_STYLE_REPLY = """Do the work in front of you and let the result be what it is (BG-2.47).

### Why this verse
- **BG-2.47**: retrieved because (BG-2.47) -[:HAS_THEME]-> theme 'duty' <-[:HAS_THEME]- (BG-3.19)
  Text: But you have only the right to work, but none to the fruit thereof.
"""


@pytest.fixture
def memory(repository: InMemoryRepository, settings: Settings) -> ConversationMemory:
    return ConversationMemory(repository=repository, llm=None, settings=settings)


async def seed(repository: InMemoryRepository, session_id: str, turns) -> None:
    await repository.ensure_session(session_id, "en-IN")
    for index, (role, content, citations) in enumerate(turns):
        await repository.add_message(
            MessageRecord(
                id=f"{session_id}-{index}",
                session_id=session_id,
                role=role,
                content=content,
                citations=list(citations),
            )
        )


# -------------------------------------------------- history is already passed
async def test_recent_turns_and_the_summary_both_reach_the_prompt(
    memory: ConversationMemory, repository: InMemoryRepository
) -> None:
    """Step C's question, answered as a test rather than as a claim."""
    await seed(
        repository,
        "s1",
        [
            ("user", "My manager takes credit for my work.", []),
            ("assistant", "Hold your own standard (BG-2.47).", ["BG-2.47"]),
        ],
    )
    await repository.update_summary("s1", "The person is being credited unfairly at work.")

    context = await memory.load("s1")
    rendered = context.render()
    assert "RECENT TURNS:" in rendered
    assert "My manager takes credit" in rendered
    assert "CONVERSATION SUMMARY SO FAR:" in rendered
    assert "credited unfairly" in rendered
    assert context.trace().recent_turns_injected == 2


async def test_history_is_bounded_by_the_configured_turn_count(
    repository: InMemoryRepository,
) -> None:
    """Unbounded history is a prompt that grows until it fails."""
    settings = Settings(mock_mode=True, environment="test", memory_last_n_turns=4)
    memory = ConversationMemory(repository=repository, llm=None, settings=settings)
    await seed(
        repository,
        "s2",
        [("user" if index % 2 == 0 else "assistant", f"turn {index}", []) for index in range(12)],
    )
    context = await memory.load("s2")
    assert len(context.recent_turns) <= 4


# ------------------------------------------------------- what must not get in
def test_the_why_section_is_stripped_but_the_reference_is_kept() -> None:
    """Refs only - the id is the thread of the conversation, the path is not.

    "explain that verse more" is only answerable because BG-2.47 is still
    visible in the history, so ids stay. The graph path and the pasted verse
    text are 50-170 words of format the reply contract dropped, so they go.
    """
    cleaned = strip_verse_payload(OLD_STYLE_REPLY)
    assert "BG-2.47" in cleaned
    assert "Why this verse" not in cleaned
    assert "HAS_THEME" not in cleaned
    assert "you have only the right to work" not in cleaned


def test_a_reply_that_is_only_a_why_section_is_not_reduced_to_nothing() -> None:
    """Something odd is better in the history than a blank assistant turn."""
    only_section = "### Why this verse\n- **BG-2.47**: retrieved because ..."
    assert strip_verse_payload(only_section).strip()


def test_a_modern_reply_passes_through_untouched() -> None:
    reply = "Hold your own standard rather than the credit for it (BG-2.47). What will you do?"
    assert strip_verse_payload(reply) == reply


async def test_an_old_session_does_not_re_teach_the_dropped_format(
    memory: ConversationMemory, repository: InMemoryRepository
) -> None:
    """The end-to-end version: a pre-contract reply in the database.

    This is the case that actually happens - the beta has sessions from before
    9 Sep - and the check that matters is on ``render()``, because that is the
    string the prompt is built from.
    """
    await seed(
        repository,
        "s3",
        [("user", "What should I do?", []), ("assistant", OLD_STYLE_REPLY, ["BG-2.47"])],
    )
    rendered = (await memory.load("s3")).render()
    assert "BG-2.47" in rendered
    assert "Why this verse" not in rendered
    assert "HAS_THEME" not in rendered
    assert "right to work" not in rendered


# ------------------------------------------------------------ cited-ref memory
async def test_cited_refs_are_collected_newest_first_without_duplicates(
    memory: ConversationMemory, repository: InMemoryRepository
) -> None:
    """A follow-up prefers what they were just shown, so order matters."""
    await seed(
        repository,
        "s4",
        [
            ("user", "first", []),
            ("assistant", "a (BG-2.47)", ["BG-2.47"]),
            ("user", "second", []),
            ("assistant", "b (BG-2.47) (AS-1.9.1)", ["BG-2.47", "AS-1.9.1"]),
        ],
    )
    context = await memory.load("s4")
    assert context.cited_refs == ["BG-2.47", "AS-1.9.1"]
    assert context.cited_verse_ids() == ["AS-1.9.1", "BG-2.47"]


def test_cited_verse_ids_is_bounded() -> None:
    """A long session must not paste forty ids into every follow-up prompt."""
    context = MemoryContext(session_id="s", cited_refs=[f"BG-1.{n}" for n in range(20)])
    assert len(context.cited_verse_ids()) == 4


async def test_a_fresh_session_has_no_refs_and_renders_nothing(
    memory: ConversationMemory, repository: InMemoryRepository
) -> None:
    await repository.ensure_session("s5", "en-IN")
    context = await memory.load("s5")
    assert context.is_empty
    assert context.render() == ""
    assert context.cited_verse_ids() == []


# ------------------------------------------------- the follow-up prompt path
def test_a_followup_prompt_names_the_verses_already_discussed() -> None:
    """The whole point of carrying refs: "that verse" must resolve."""
    import asyncio

    from app.pipeline.generation import build_answer_prompt
    from app.pipeline.retrieval import InMemoryHybridRetriever
    from app.pipeline.tiers import FOLLOWUP_TIER, GUIDANCE_TIER

    trace = asyncio.run(InMemoryHybridRetriever().retrieve("anger at work"))
    context = MemoryContext(
        session_id="s",
        recent_turns=[("user", "boss ne daanta"), ("assistant", "Anger (BG-2.62).")],
        cited_refs=["BG-2.62"],
    )

    followup = build_answer_prompt("aur kya", trace, memory=context, tier=FOLLOWUP_TIER)
    assert "ALREADY DISCUSSED" in followup
    assert "BG-2.62" in followup

    # And a first-turn guidance prompt does not carry it: there is no "that
    # verse" yet, and naming one invites the model to continue a thought that
    # does not exist.
    guidance = build_answer_prompt("boss ne daanta", trace, memory=context, tier=GUIDANCE_TIER)
    assert "ALREADY DISCUSSED" not in guidance
