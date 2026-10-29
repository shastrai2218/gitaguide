# -*- coding: utf-8 -*-
from lib import B, CH, H2, H3, J, LEAD, NUM, SUMMARY, T, F


def build():
    CH(1, "Introduction", "ch1")
    J("This report describes GitaGuide, an emotion-aware guidance system built on two Indian scriptures, "
      "the Bhagavad Gita and Kautilya’s Arthashastra, and the work done to measure how well it actually "
      "behaves. The first chapter explains the background of the topic, why the project was taken up, "
      "what problem it tries to solve, what it contributes, and how the rest of the report is arranged. "
      "Later chapters give the literature review, the design and implementation, the measured results, "
      "and the conclusions. Citations in square brackets refer to the numbered list at the end of the report.")

    H2("1.1", "Background of the Project Topic", "s1_1")
    J("Large language models (LLMs) have made it easy to build a chatbot that answers in the voice of a "
      "religious text. The Bhagavad Gita has attracted more of these systems than most texts, partly "
      "because its dialogue between Krishna and Arjuna already reads like a counselling conversation about "
      "duty, grief and doubt. At the same time, the use of LLMs in mental health and emotional support has "
      "grown quickly, and two systematic reviews of that field both describe uneven evaluation and limited "
      "clinical validation [2], [7]. A proposal for the responsible development of such tools stresses "
      "that they need to be evaluated, not only demonstrated [1].")
    J("Retrieval-augmented generation (RAG) and knowledge graphs were developed in parallel. They give "
      "a way to tie a model’s answer to specific source passages instead of leaving it to generate freely, "
      "and this is often presented as the answer to the hallucination problem. For a scripture, grounding "
      "matters in a particular way: a user may not know the text well enough to notice that a quoted verse "
      "does not exist, or does not say what the answer claims it says.")
    J("Grounding is not a full solution, though, and the Gita chatbot case shows why. In 2023, an "
      "investigation found that several deployed Gita chatbots told users that killing could be acceptable "
      "when it was framed as dharma, and that they cited real verses while doing so [3]. A retrieved and "
      "correctly cited verse can still be used to justify something harmful. For this reason, the project "
      "treats safety as a property of the whole pipeline and not as a filter added at the end.")

    H2("1.2", "Motivation and Scope of the Report", "s1_2")
    J("The motivation for the project comes from three documented failures, and each one shaped a "
      "design decision.")
    B("Safety is usually not part of the architecture.",
      "Most scripture chatbots send every message straight to generation. The 2023 reporting found that "
      "this produces answers that endorse violence when the question is framed around duty, because "
      "nothing checks the message before the model sees it [3].")
    B("Emotion is treated as a label rather than a magnitude.",
      "Systems that detect sentiment usually output positive, negative or neutral. That cannot tell a "
      "passing worry from a crisis. The size of the emotion, not just its category, is needed to decide how "
      "careful a reply should be.")
    B("Citations are rarely verified against what was retrieved.",
      "A model told to cite its sources can still produce a verse number that looks plausible but does not "
      "exist. Without a programmatic check, this is invisible until a reader happens to notice.")
    J("The scope of this report is the whole project, from corpus preparation to a deployed closed beta: "
      "the system design, the deployment, and the evaluation, including the results that did not reach "
      "the project’s own targets. Retrieval accuracy is the clearest example. It is low, and Chapter 4 "
      "reports it as measured. The system is a research prototype shared with a small group of invited "
      "testers. It is not a commercial product and it is not a clinical tool.")

    H2("1.3", "Problem Statement", "s1_3")
    J("Scripture-grounded and emotionally responsive AI systems share a set of specific, documented "
      "shortcomings. Together they define the problem this project addresses.")
    B("Unsafe by construction.",
      "There is usually no dedicated safety layer between the user’s message and the generation call, "
      "so a provocative or ambiguous message is handled in the same way as a routine one [3].")
    B("No model of emotional intensity.",
      "Emotion category alone cannot separate mild curiosity from real distress, so the system cannot "
      "decide how much caution a reply needs.")
    B("Citations that cannot be trusted.",
      "If a generated citation is not checked against the retrieved set, an invented verse identifier looks "
      "the same as a real one to the person reading it [5].")
    B("A single scripture and opaque retrieval.",
      "The academic systems found in the review address the Bhagavad Gita alone [8], [9], with no coverage "
      "of a practical text such as the Arthashastra, and they do not explain why one verse was chosen "
      "over another.")
    J("The problem statement of the project is therefore this: to design and build a guidance system in "
      "which a crisis check runs before anything else, the emotion and its intensity are measured, every "
      "citation is verified against what was retrieved, every decision can be replayed, and the system’s "
      "performance is measured honestly, including where it fails.")

    H2("1.4", "Salient Contribution", "s1_4")
    J("The contributions of the project are listed below. Each one was measured and not only designed.")
    NUM(1, "An ordered, safety-first pipeline.",
        "A layered crisis gate that blocks when a check cannot be completed runs before memory, routing, retrieval and generation. A blocked "
        "turn therefore cannot reach generation and cannot carry a citation. This is enforced in code and "
        "checked by unit tests, a production smoke test and the API layer itself.")
    NUM(2, "A dual-scripture knowledge graph.",
        "The graph holds 3,836 verses from the Bhagavad Gita and the Arthashastra, connected by emotion, "
        "theme and concept relationships, with a hub-suppression step that keeps it inside the limits of "
        "a free graph database tier.")
    NUM(3, "Citation-verified generation.",
        "Every verse identifier in a generated answer is checked against the retrieved set. A wrong "
        "citation costs one corrective retry, and an answer that still fails is replaced by a deterministic "
        "answer composed directly from the retrieved verses.")
    NUM(4, "A measured evaluation that reports negative results.",
        "The safety gate, conversational router, retrieval, emotion model, intent classifier, answer "
        "faithfulness and a moral red-team set were all evaluated, and the results are reported even where "
        "they fall short.")
    NUM(5, "A safety failure found, fixed and regression-tested.",
        "A red-team review found two replies that presented scripture-sanctioned violence as permitted. "
        "They were traced to a gap in the output gate, which was closed with a new rule and four "
        "regression tests.")
    NUM(6, "A record of measurement pitfalls.",
        "The report documents a non-reproducible retrieval instrument, an encoder mismatch that scored "
        "higher than the correct configuration, a deployment missing a required data directory, a pooled "
        "gold set that scored its own contributing configurations almost by construction, and an automated "
        "judge that returned no usable verdicts.")

    H2("1.5", "Organization of the Report", "s1_5")
    J("The report has six chapters. Chapter 1 gives the background, motivation, problem statement and "
      "contributions. Chapter 2 reviews the literature on scripture-grounded AI, emotion-aware and "
      "safety-focused language models, graph-based retrieval and the critical work on religious "
      "chatbots, and ends with the research gap. Chapter 3 describes the methodology and implementation: "
      "the block diagrams, the hardware and software, the algorithms behind each stage of the pipeline, "
      "and the deployed application. Chapter 4 presents the measured results and discusses what they "
      "mean. Chapter 5 sets out the advantages, limitations and applications of the system. Chapter 6 "
      "gives the conclusion and the scope for future work. The references and four appendices follow.")
    SUMMARY("The chapter introduced the topic, explained why a safety-first design was chosen after "
            "documented failures in existing Gita chatbots, stated the problem the project addresses, "
            "and listed its contributions. The next chapter reviews the literature that these "
            "contributions are measured against.")
