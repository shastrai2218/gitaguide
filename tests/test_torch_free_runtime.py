"""The serving runtime must not import torch.

Both models run as ONNX graphs on onnxruntime, so a serving container has no
reason to carry a training framework - torch is roughly 4.4 GB installed, which
dominates the image. Nothing enforces that by itself: one convenience import of
``sentence_transformers`` anywhere under ``app/`` would quietly put it back, and
the only symptom would be a much larger image.

So this runs the import in a **subprocess** with a meta-path finder that raises
on ``torch`` and ``sentence_transformers``. A subprocess is necessary because
the dev environment has both installed and pytest has almost certainly imported
them already; blocking them in-process would not prove anything.
"""

from __future__ import annotations

import subprocess
import sys
import textwrap

#: Modules the serving path must be able to build without.
BANNED = ("torch", "sentence_transformers")

PROBE = textwrap.dedent(
    """
    import sys

    BANNED = {banned!r}


    class Blocker:
        \"\"\"Raise if anything tries to import a banned module.\"\"\"

        def find_module(self, name, path=None):
            return self.find_spec(name, path)

        def find_spec(self, name, path=None, target=None):
            root = name.split(".")[0]
            if root in BANNED:
                raise ImportError(
                    f"the serving runtime imported {{name!r}}, which it must not"
                )
            return None


    sys.meta_path.insert(0, Blocker())

    # The whole serving surface, in the order the app builds it.
    import app.config
    import app.db
    import app.db.repository
    import app.embeddings
    import app.llm.factory
    import app.pipeline.emotion
    import app.pipeline.generation
    import app.pipeline.intent
    import app.pipeline.memory
    import app.pipeline.orchestrator
    import app.pipeline.retrieval
    import app.pipeline.safety
    import app.api.beta
    import app.api.routes
    import app.main

    # Construct the real (non-mock) model backends. Weights are not loaded -
    # that is lazy - but this proves the classes themselves are torch-free.
    settings = app.config.Settings(mock_mode=False)
    app.embeddings.OnnxMiniLMEmbedder(settings)
    app.pipeline.emotion.OnnxGoEmotionsBackend(settings)

    # And the offline path, end to end, with a real turn through the pipeline.
    import asyncio
    from app.pipeline.orchestrator import build_orchestrator
    from app.pipeline.schemas import UserTurn

    async def one_turn():
        offline = app.config.Settings(mock_mode=True, environment="test")
        repository = await app.db.build_repository(offline)
        orchestrator = build_orchestrator(settings=offline, repository=repository)
        result = await orchestrator.handle_turn(
            UserTurn(message="My manager took credit for my work")
        )
        assert result.answer, "the pipeline produced no answer"
        await orchestrator.aclose()

    asyncio.run(one_turn())

    for name in BANNED:
        assert name not in sys.modules, f"{{name}} reached sys.modules"

    print("TORCH_FREE_OK")
    """
).format(banned=BANNED)


def test_serving_path_imports_and_runs_without_torch() -> None:
    result = subprocess.run(
        [sys.executable, "-c", PROBE],
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert "TORCH_FREE_OK" in result.stdout, (
        "the serving runtime could not be built without torch:\n"
        f"--- stdout ---\n{result.stdout}\n--- stderr ---\n{result.stderr}"
    )


def test_torch_reference_is_not_reachable_from_the_app_package() -> None:
    """The torch encoder lives under ``eval/``; ``app/`` must not reference it.

    Guards the arrangement itself rather than one import: if someone moves the
    reference encoder back into ``app.embeddings`` for convenience, the serving
    requirements silently stop being sufficient.
    """
    from pathlib import Path

    app_root = Path(__file__).resolve().parent.parent / "app"
    offenders = [
        path.relative_to(app_root.parent).as_posix()
        for path in app_root.rglob("*.py")
        if "sentence_transformers" in path.read_text(encoding="utf-8")
    ]
    assert not offenders, f"app/ must not import sentence_transformers: {offenders}"
