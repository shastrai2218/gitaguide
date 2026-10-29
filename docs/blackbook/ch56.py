# -*- coding: utf-8 -*-
from lib import B, CH, H2, H3, J, NUM, SUMMARY, T


def build5():
    CH(5, "Advantages, Limitations and Applications", "ch5")
    J("This chapter takes the results of Chapter 4 and states plainly what the system does well, where it "
      "falls short, and where it could be used. The advantages are limited to those the measurements "
      "support. The limitations are given as much space as the advantages because, for a system that gives "
      "guidance to people, the limits matter as much as the features.")

    H2("5.1", "Advantages", "s5_1")
    B("Safety that is part of the structure.",
      "A blocked turn is a code path that cannot reach retrieval or generation. This is enforced by tests, "
      "a production smoke test and a start-up self-test, and not by a prompt instruction that a model "
      "could ignore.")
    B("Verifiable citations.",
      "Every verse identifier in a reply is checked against the retrieved set, with a retry and then a "
      "deterministic fallback. An invented verse cannot silently pass as genuine scripture.")
    B("Emotion intensity and not only category.",
      "The arousal-weighted intensity score separates a mild concern from acute distress and is stored as "
      "audit context for the safety gate. The emotion model itself was measured on held-out data and "
      "matches its published score.")
    B("Two scriptures with explained retrieval.",
      "The Bhagavad Gita covers personal and spiritual questions and the Arthashastra covers "
      "professional and statecraft questions. Every retrieved verse carries the graph path that "
      "explains why it was chosen.")
    B("Fits a small deployment.",
      "INT8 quantisation, one thread and serialised turns bring the peak memory to 429 MiB.")

    H2("5.2", "Limitations and Challenges", "s5_2")
    B("Retrieval accuracy is the weakest result.",
      "A hit rate of 0.175 on the 40-query gold set is low in absolute terms. The ablations show the "
      "accuracy comes from the dense encoder and fusion, and the graph currently explains more than it "
      "retrieves.")
    B("Answer faithfulness is a concern.",
      "A preliminary human rating gave 2.77 out of 5, and a single automated judge rated 12 of 69 answers "
      "unfaithful although every one carried valid citations.")
    B("A real safety gap was found.",
      "Two replies presented historical violence as permitted while citing genuine verses. The cause was "
      "closed with a new output rule, which was tested on the same replies that revealed the problem and "
      "has not been tested on unseen prompts.")
    B("Intent classification is weaker live than expected.",
      "Live accuracy is 0.650, and scripture-question recall is 0.062 because the classifier confuses it "
      "with seek guidance and professional dilemma. Three ordinary queries were labelled crisis.")
    B("Dependency and bias are not measured.",
      "The literature\u2019s two most cited risks, dependency [6] and bias [4], have no dedicated measurement, "
      "and the crisis reply is a single fixed message and not a staged handoff.")
    B("Free-tier operation.",
      "Rate limits and counters reset on restart, and the free graph database pauses after three idle days "
      "unless it is kept alive.")

    H2("5.3", "Applications", "s5_3")
    B("Personal reflection.",
      "For a user working through a worry, a decision or a conflict, the system can offer a short reflection "
      "tied to cited verses, in English or Hinglish, with a helpline shown at all times. It is a reflection "
      "tool and not a replacement for care.")
    B("Professional and ethical decision support.",
      "The Arthashastra gives the system material on leadership, conduct and organisation that the Gita "
      "does not, so questions about work and management can be answered from a second text.")
    B("A safety-first pattern for other scriptures and domains.",
      "The ordering (check first, retrieve second, verify citations, store a trace) does not depend on the "
      "texts. It could be reused for other religious or advisory corpora, provided the safety suite is "
      "rebuilt for that domain.")
    SUMMARY("The system’s advantages are structural safety, checked citations, stored traces and a small "
            "footprint. Its limitations are weak retrieval and faithfulness, an unverified safety fix, and "
            "unmeasured dependency and bias.")


def build6():
    CH(6, "Conclusion and Future Scope", "ch6")
    J("This final chapter gives the single conclusion of the report and then sets out the work that should "
      "follow. The conclusion restates what the project built and what the measurements showed, without "
      "claiming more than they support.")
    H2("6.1", "Conclusion", "s6_1")
    J("GitaGuide is a research prototype for emotion-aware, explainable and safety-gated guidance grounded "
      "in the Bhagavad Gita and the Arthashastra. It places a layered crisis gate before retrieval and "
      "generation, verifies cited verse identifiers against the retrieved set, stores a decision trace for "
      "every turn and runs within a measured peak memory of 429 MiB. The safety ordering is tested and "
      "enforced, the citations are checked, and the decisions can be replayed.")
    J("The measurements also show where the work is not finished. Retrieval performance is weak, with a "
      "hit rate of 0.175 on 40 queries, and the knowledge graph mainly explains and re-scores results "
      "instead of retrieving additional passages. Faithfulness is a concern: a preliminary human rating "
      "gave 2.77 out of 5, and a single automated judge rated 12 of 69 answers unfaithful despite valid "
      "citations. A red-team review found two replies that presented scripture-sanctioned violence as "
      "permitted. An output-gate rule added in response caught both on replay without flagging the other "
      "31 captured replies, and the updated gate has since been deployed to production, but its performance "
      "on unseen prompts has not been established. Risks identified in prior work, including emotional "
      "dependency, gender and religious-interpretation bias and the experience of crisis replies, remain "
      "insufficiently addressed.")
    J("Three lessons from the project apply beyond it. A single retrieval number from a live LLM pipeline is "
      "one sample and not a measurement. A correctly cited verse is not evidence of a faithful answer. And a "
      "measured system is not necessarily the one that is deployed. For these reasons GitaGuide is offered "
      "only as a closed, invited research beta. These results describe a prototype under evaluation and "
      "not a system ready for public deployment.")
    H2("6.2", "Future Scope", "s6_2")
    J("The following work is listed in priority order.")
    NUM(1, "Independent retrieval evaluation.",
        "Write a rubric, use independent annotators and build a gold set that is not pooled from the "
        "configurations being compared. Then test the improvements the diagnostics suggest: denser emotion "
        "tags, margin-aware graph expansion, a larger encoder, and support for Hindi and Gujarati queries.")
    NUM(2, "Stronger intent and emotion evaluation.",
        "Use explicit class definitions, at least two independent raters, per-class precision, recall and F1, "
        "a better treatment of the scripture-question class, and a separate evaluation of the intensity score.")
    NUM(3, "Independent red-team testing of the deployed output gate.",
        "Build a new prompt set that is independent of the original failures, report false-positive and "
        "false-negative rates with confidence intervals, and repeat the fault-injection tests. Have a human "
        "read the replies that a language model graded.")
    NUM(4, "Multilingual safety testing.",
        "Test the gate in Hinglish, Hindi and Gujarati, where the rule table is not language understanding "
        "and the semantic layer carries more of the load.")
    NUM(5, "Reproducible experiments.",
        "Use versioned configurations that record models, encoders, corpus snapshots, gold sets and "
        "evaluation scripts, and add ablations that isolate the safety gate and emotion-aware routing.")
    NUM(6, "Structured user evaluation.",
        "Measure how the crisis reply is received, build a proxy for emotional dependency beyond the "
        "per-session rate limit, audit for gender and religious-interpretation bias, and move to staged, "
        "multi-turn crisis handling.")
    NUM(7, "Operations before any wider release.",
        "Clinician review of the safety suite, rate-limit state shared across workers, managed hosting in "
        "place of free tiers, database migrations, and monitoring with a tested rollback path.")
    SUMMARY("The report built a safety-first guidance pipeline, measured it honestly and found it sound in "
            "structure but weak in retrieval and faithfulness. The priority for future work is independent "
            "evaluation, first of retrieval and the safety gate, and then of how people actually experience "
            "the system.")
