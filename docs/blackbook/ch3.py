# -*- coding: utf-8 -*-
from lib import ALG, B, CH, EQ, FIG, H2, H3, J, LEAD, NUM, SUMMARY, T, F, TBL


def build():
    CH(3, "Methodology and Implementation", "ch3")
    J("This chapter explains how GitaGuide is built. It starts with the block diagrams that show the "
      "system from four angles, then describes the hardware it runs on and the software that implements "
      "each stage of the pipeline, with the algorithms written out where the logic is not obvious. It "
      "then shows the deployed application and closes with the method used to evaluate the system. "
      "The central design rule is that a turn runs in one fixed order and that the safety check comes "
      "before everything else that could go wrong.")

    # ------------------------------------------------------------------ 3.1
    H2("3.1", "Block Diagram", "s3_1")
    H3("3.1.1", "The pipeline")
    J(f"{F('pipeline')} shows one turn from the user’s message to the stored trace. The order is "
      "emotion, intent, input safety gate, memory, router, retrieval, grounded generation, output gate "
      "and persistence. Because the gate sits before memory, routing, retrieval and generation, nothing "
      "downstream can weaken it. A blocked turn leaves after the third stage with a fixed helpline reply and "
      "never enters the later stages. Routes that need no verse, such as a greeting, skip retrieval.")
    FIG("pipeline", "img/image1.png",
        "The GitaGuide pipeline. Shaded stages are safety gates. A blocked turn leaves after stage 3 with the "
        "helpline response only; routes that need no verse skip stage 6.", w=8.6)
    J("The orchestrator enforces this order. It is also checked in three other places: by unit tests, by a "
      "smoke test run against the production deployment after every release, and by the API adapter, which "
      "drops the citations of any blocked turn even if some were present.")
    H3("3.1.2", "Component diagram")
    J(f"{F('component')} shows the deployed components. The React interface runs on Vercel and talks to a "
      "FastAPI backend on an AWS EC2 instance in Mumbai, behind a TLS proxy. The backend calls three "
      "managed services: a Neo4j AuraDB graph for retrieval, a Supabase PostgreSQL database for sessions "
      "and traces, and NVIDIA’s hosted inference service for the language model. Sign-in is handled by "
      "Clerk. The emotion model and the embedding model run inside the API process itself, as ONNX "
      "models on the CPU.")
    FIG("component", "img/component.png", "Component diagram of the deployed system.", w=14.4)
    H3("3.1.3", "Sequence of one turn")
    J(f"{F('sequence')} shows the order of calls in a normal turn. The crisis short-circuit is not drawn as a "
      "separate lane. It returns straight after the input safety check, before the router, the retriever or "
      "the generator are called.")
    FIG("sequence", "img/sequence.png", "Sequence diagram of one normal turn.", w=14.4)

    # ------------------------------------------------------------------ 3.2
    H2("3.2", "Hardware Description", "s3_2")
    J(f"{T('hw')} lists the hardware and managed infrastructure. GitaGuide needs no special hardware: "
      "both neural models run on the CPU, and the only model that needs a GPU, the language model, is "
      "called as a hosted service.")
    TBL("hw", "Hardware and infrastructure",
        ["Component", "Description"],
        [["API host (production)", "AWS EC2 t3.small (2 vCPU, 2 GiB RAM) in Mumbai (ap-south-1), running the "
                                   "FastAPI backend with Docker Compose behind a Caddy TLS proxy"],
         ["API host (original beta)", "A 512 MiB free-tier instance with 0.1 CPU. The memory work described "
                                      "below was done for this host"],
         ["Graph database", "Neo4j AuraDB Free: 3,836 verse nodes and 14,709 relationships after hub suppression"],
         ["Relational database", "Supabase-managed PostgreSQL (session pooler, asyncpg driver)"],
         ["Front-end hosting", "Vercel (static React build)"],
         ["Development machine", "Windows 11 workstation used for model export, testing and report builds"]],
        [4.0, 10.6])
    H3("3.2.1", "Fitting a 512 MiB instance")
    J("The first deployment ran on a free instance with 512 MiB of memory and 0.1 of a CPU. The embedding "
      "model in 32-bit floating point did not fit. Measured on the host’s exact shape, the service "
      "idled at 497 MiB and peaked at 511 MiB during a single turn, and a second concurrent turn was "
      "killed by the operating system with no message in the application log. Four settings, measured "
      "on 7 September 2026, made it fit.")
    J("Four settings made it fit. The embedder was quantised to INT8, which saved 37 MiB; this is only correct "
      "if the graph is embedded with the same encoder, because a mismatched index does not raise an error "
      "but quietly returns worse neighbours. One ONNX thread saved 47 MiB, because on a 0.1 CPU a second "
      "thread cannot run in parallel. Limiting the malloc arenas saved 14.5 MiB. Serialising turns with a "
      "semaphore of width one means peak memory is the memory of one turn. Together they brought the peak "
      "from 511 MiB to 429 MiB, leaving 81.7 MiB of headroom (see Chapter 4).")
    J("On 19 September 2026 the API was moved to the EC2 instance, so the 512 MiB limit is no longer the "
      "constraint. The settings were kept because the graph is embedded with the INT8 encoder. The move "
      "also cut latency, because the API now sits close to its databases: a turn that took 27.7 seconds "
      "before took 15.5 seconds on the same query.")

    # ------------------------------------------------------------------ 3.3
    H2("3.3", "Software Description, Flowchart and Algorithms", "s3_3")
    H3("3.3.1", "Technology stack")
    TBL("sw", "Software stack",
        ["Layer", "Technology", "Purpose"],
        [["Backend and API", "Python, FastAPI 0.141, Pydantic 2.13, Uvicorn",
          "Thin HTTP layer. All decisions live in the orchestrator, which the command line, the evaluation "
          "scripts and the API all share"],
         ["Frontend", "React 18 with Vite, plain CSS, on Vercel",
          "Closed-beta chat interface with consent, citations and the explanation trace"],
         ["Authentication", "Clerk (JWT verified in the API)", "Sign-in and account sync for the beta"],
         ["Graph and vectors", "Neo4j 6.2 driver and neo4j-graphrag 1.18",
          "Hybrid Cypher retriever combining a vector index and a full-text index, with graph paths"],
         ["Relational store", "SQLAlchemy 2.0, asyncpg, PostgreSQL",
          "Sessions, messages, feedback, safety logs and full per-turn traces"],
         ["Emotion model", "RoBERTa fine-tuned on GoEmotions, ONNX, INT8, onnxruntime 1.29",
          "28-label emotion distribution collapsed to one intensity score"],
         ["Embeddings", "all-MiniLM-L6-v2, ONNX, 384 dimensions", "Vector half of retrieval"],
         ["Language model", "NVIDIA NIM: nemotron-3-super-120b-a12b, fallback nemotron-3.5-lightning-30b-a3b",
          "Answer generation, semantic intent and routing, under a 45 s wall-clock budget"],
         ["Testing", "pytest; ruff", "911 passing tests; 73 red-team safety tests are a required CI check"]],
        [3.0, 5.4, 6.2], size=9.5)

    H3("3.3.2", "Corpus and knowledge graph")
    J("The corpus has 3,836 units. There are 701 Bhagavad Gita verses with identifiers such as BG-2.47, "
      "taken from the open gita/gita dataset [20] using Shri Purohit Swami’s English translation. There "
      "are 3,135 Arthashastra sutras with identifiers such as AS-7.1.5, from R. Shamasastry’s 1915 "
      "translation [21], whose century-old wording is kept as it is. The Arthashastra’s sutra numbers are "
      "positional: the number is the paragraph’s index within its chapter. They are stable and citable "
      "but are not the numbering of a critical edition.")
    J("Each unit carries weak labels (themes, GoEmotions emotion labels [22] and concepts) assigned by "
      "keyword rules over the English text. The labels become Theme, Emotion and Concept nodes in the "
      "graph. Verses that share enough labels are joined by RELATED_TO edges. This step is where the "
      "graph nearly became too large for the free tier. Labels attached to more than 8% of the corpus "
      "(eight labels, such as relationships, leadership and strategy) are therefore excluded when the edges "
      "are derived.")
    J("Hub suppression removed 99.06% of the derived pairs, from 269,649 to 2,546, and the whole graph "
      "holds 14,709 relationships. The reason for doing it was capacity and not retrieval quality. Before "
      "suppression the graph reached 281,523 of the 400,000 relationships the free tier allows; after it "
      "uses 3.7% of that budget. The retrieval benefit is weak, and the project does not claim one.")

    H3("3.3.3", "Emotion detection and intensity")
    J("Emotion is predicted by a RoBERTa model [23] fine-tuned on GoEmotions [22], specifically the "
      "SamLowe/roberta-base-go_emotions checkpoint [24], exported to ONNX and quantised to INT8 for CPU "
      "inference. The 28-label output is collapsed into one arousal-weighted intensity:")
    EQ("I = min(1, 0.75 w(l_{1}) s_{1} + 0.15 Σ_{k>1} w(l_{k}) s_{k} + λ(x))", "3.1")
    J("Here s_{k} is the score of the k-th ranked label l_{k}, w is an arousal weight for that label, and "
      "λ(x) is a small bonus for explicit intensifier phrases in the text. Intensity is audit context. "
      "It never blocks a turn on its own, so grief guidance stays available unless a deterministic rule "
      "or the semantic layer fires independently.")

    H3("3.3.4", "Intent classification")
    J("Intent is one of six classes: seek guidance, emotional support, professional dilemma, scripture "
      "question, crisis, or other. It is produced by a zero-shot LLM classification combined with a "
      "keyword prior. If the model returns malformed output, the call is retried once. Intent is not the "
      "router. It feeds the safety gate and the scripture prior in re-ranking, and it answers the "
      "question of what the message is about, while the router decides what kind of reply is wanted.")

    H3("3.3.5", "The safety gate")
    J(f"The gate runs before retrieval and again after generation. {T('gate')} summarises its four layers. "
      "Five properties are fixed and cannot be changed.")
    NUM(1, "Order.", "Emotion, then intent, then the gate, then everything else. The gate cannot be moved later.")
    NUM(2, "A blocked turn carries no verses and makes no generation call.")
    NUM(3, "Emotion never blocks alone.")
    NUM(4, "A malformed classification means risk unknown, not risk low.")
    NUM(5, "The process refuses to start if the canonical crisis phrase does not block, if the canonical "
           "control does, or if the generation budget is below 15 seconds, because that same budget bounds "
           "the classifier.")
    TBL("gate", "Layers of the safety gate",
        ["Layer", "Mechanism", "Effect"],
        [["Rule", "14 deterministic patterns: suicidal ideation, self-harm, harm to others, abuse, medical "
                  "emergency, including Latin-script Hindi. Negation and third-party-past phrasing are suppressed",
          "Block"],
         ["Model", "LLM crisis score of at least 0.5. Malformed output is retried once; still malformed means risk unknown",
          "Block"],
         ["Soft cue", "10 crisis-adjacent cues, used only if no rule fired and the model layer failed twice",
          "Block, conservatively"],
         ["Output", "5 patterns on the generated answer: harm methods, discouraging treatment, moralising, "
                    "dismissal, and presenting a historical violence or torture method as permitted "
                    "(added after the red-team review in Section 4.6)",
          "Replace answer"]],
        [2.0, 9.2, 3.4], size=9.5)
    ALG("Algorithm 3.2: Input safety check",
        ["Run the 14 rule patterns on the message. Drop a match if it is negated or refers to a third party in the past.",
         "Read the semantic crisis score from the intent classifier. If the output was malformed, retry once.",
         "If the score is at least 0.5, add a model-layer trigger. If it is still malformed, record risk unknown.",
         "If no rule fired and the model layer failed twice, test the 10 soft cues and block on a match.",
         "If any trigger exists, return the fixed crisis reply and stop. No retrieval or generation call is made.",
         "Write every trigger and every near miss to the safety log."])
    J("The crisis reply is a fixed message that can be audited. It acknowledges the person, says why "
      "scripture is being set aside, lists five helplines (Tele-MANAS 14416, iCall, AASRA, KIRAN and 112), "
      "adds at most one extra line for abuse, harm to others or a medical emergency, and ends by saying "
      "the guide is still there. It contains no scripture. It was written to avoid an abrupt refusal [19], "
      "but it is still a single message and not a staged closure across several turns.")

    H3("3.3.6", "Conversational routing and reply tiers")
    J("After the gate and memory, a router chooses one of seven routes: greeting, meta, acknowledgement or "
      "close, follow-up, guidance, crisis, or out-of-scope. It works in two stages. A multilingual phrase "
      "lexicon matches whole messages and settles the obvious cases at no cost. Otherwise one cheap LLM "
      "call returns the route and, for a follow-up, a standalone English rewrite of the question. Routes "
      "that need no verse skip retrieval. If the router fails or times out after 6 seconds, the turn "
      "falls back to guidance, which is what every message received before routing existed. The "
      "router’s worst case is therefore the system without a router.")
    J(f"Each route has a reply tier that fixes its word budget and the number of verses it may cite "
      f"(see {T('len')}). The tiers exist because beta feedback complained that replies were too long, "
      "pasted whole verses and ended with an explanation nobody had asked for.")

    H3("3.3.7", "Hybrid retrieval and re-ranking")
    J("Retrieval uses the neo4j-graphrag hybrid Cypher retriever. A vector index over 384-dimensional "
      "all-MiniLM-L6-v2 embeddings [25] and a full-text index run together and return 20 candidates. "
      "Graph paths of one or two hops through Emotion, Theme and RELATED_TO nodes are then attached to "
      "each candidate as an annotation and a score. Candidates are re-ranked and the top five are kept:")
    EQ("s = 0.55 s_{vec} + 0.20 s_{ft} + 0.15 s_{graph} + 0.25 a(ι, σ)", "3.2")
    J("Here a(ι, σ) is a prior that scripture σ suits intent ι, and the graph score is 0.5 per "
      "path, capped at 1. The query is expanded with the top three emotions and the intent. When the "
      "router has produced an English rewrite, the vector search embeds the rewrite while the full-text "
      "search keeps the person’s own words. It matters that the graph annotates and re-scores "
      "candidates and does not add new ones. In production the candidates come only from vector and "
      "full-text fusion, and the graph clauses find nodes that have already been suggested. Chapter 4 "
      "tests what happens when the graph is allowed to add candidates.")

    H3("3.3.8", "Grounded generation")
    J("The language model may reason but may not source. The prompt gives it only the retrieved verses and "
      "tells it to cite at most two of them by identifier, in the form BG-2.47 or AS-1.2.3. Afterwards "
      "every identifier in the reply is checked against the retrieved set.")
    ALG("Algorithm 3.3: Citation-verified generation",
        ["Build the prompt from the route’s instructions, the retrieved verses and the memory context.",
         "Call the language model within the remaining wall-clock budget (45 seconds across the provider chain).",
         "Extract every verse identifier from the reply and compare it with the retrieved set.",
         "If any identifier is outside the set, or the reply cites nothing when it should, retry once with "
         "a corrective note that lists the allowed identifiers.",
         "If the retry still fails, replace the reply with a deterministic answer composed from the retrieved verses.",
         "Run the output sanity guard and the output safety gate. If the sanity guard fails twice, serve the overload reply.",
         "Attach the “why this verse” path for each cited verse and persist the turn."])
    J("Two further guards sit around this step. The wall-clock budget is shared as the remaining time "
      "divided by the number of providers left. When it runs out, the user receives an honest message that "
      "the guide is overloaded, and not a spinner or an error page. There is no template last resort in "
      "production, so a total outage never gets presented as guidance. The output sanity guard measures "
      "the length of the answer body, its repetition, and any word or word pair repeated back to back. "
      "It exists because a fallback model once produced “I see the the the / I the I the”, which passed "
      "every other check: the citations were valid, the grounded flag was true, and the safety verdict was clean.")

    H3("3.3.9", "Memory and persistence")
    J("Memory holds the last six turns word for word plus a rolling summary that is refreshed every eight "
      "messages and kept under 1,200 characters. The router reads it before deciding whether a turn needs "
      "retrieval. Every turn is stored in PostgreSQL in four tables: sessions, messages (with the full trace of each assistant turn), feedback and safety_logs.")
    H3("3.3.10", "The API layer")
    J("The research surface exposes the pipeline directly; the beta "
      "surface adds consent, accounts and feedback for the interface. Consent is recorded on the server "
      "against the session, under notice version beta-1, and the chat endpoint refuses a session that has "
      "not consented.")
    J("The main endpoints are POST /chat and GET /explain/{message_id} for the research surface, and "
      "POST /api/chat, /api/consent, /api/feedback and the /api/sessions routes for the interface, plus "
      "GET /health and GET /safety/logs for operations.")
    J("Rate limits use token buckets, which allow a short burst and then refill at a steady rate: 5 requests "
      "per minute per session, 20 per IP address and 16 globally. The global limit protects the "
      "language-model budget, because one turn spends two calls on the primary model.")

    H3("3.3.11", "Operation and testing")
    J("After every release a smoke test checks the production deployment: mock mode is off, the real "
      "backends answer, consent is enforced, a crisis turn runs no retrieval or generation, and the rate "
      "limiter works under a concurrent burst. A keep-alive script stops the free graph tier from pausing. "
      "The test suite has 911 passing tests, and a continuous-integration workflow runs the linter, the tests "
      "and the red-team suite on every push.")

    # ------------------------------------------------------------------ 3.4
    H2("3.4", "Implementation Screenshots", "s3_4")
    J("The following screenshots are of the deployed closed-beta interface. The first screen asks for "
      "consent to the beta notice, and nothing works until it is given.")
    FIG("shot_landing", "img/shot_landing_crop.png",
        "Closed-beta notice and consent screen of the deployed app (gitaguide-neon.vercel.app).", w=9.0)
    FIG("shot_answer", "img/shot_answer.png",
        "A guidance answer with the cited verses, the retrieval path behind each verse, and the stage trace "
        "and timings in the line below.", w=10.5)
    FIG("shot_crisis", "img/shot_crisis.png",
        "The crisis gate: a high-risk message returns the fixed helpline reply with no verse and no "
        "generation call.", w=10.5)

    # ------------------------------------------------------------------ 3.5
    H2("3.5", "Evaluation Method", "s3_5")
    J("Each part of the system was evaluated with its own instrument; the sizes are listed in Appendix B. "
      "There is a safety suite of 36 crisis prompts and 25 benign controls, run offline against the rules. "
      "Retrieval is measured on a 40-query gold set with 140 graded verses and on a pooled set of 100 "
      "queries judged by two raters, and component ablations use an exact McNemar test for paired "
      "comparisons. The emotion model is tested on 1,500 examples of the public GoEmotions test split, and "
      "intent classification on 100 live queries sent to the deployed API. Answer quality is rated by "
      "two people on 30 answers and checked by an automated judge on 69, and a red-team set has 25 "
      "harmful prompts and 8 controls. All intervals are 95% Wilson intervals [27], retrieval uses hit "
      "rate, Recall@5, MRR and nDCG@5 [26], and every figure states the backend, encoder, corpus and date "
      "that produced it.")
    J("The two raters agreed on the scales by discussing them before scoring. There was no written rubric "
      "and no inter-annotator agreement was computed, so the human results are a preliminary signal and not "
      "a validated instrument. Numbers from the offline retriever, which expands candidates through the "
      "graph, are never compared with numbers from the production retriever, which does not.")
    SUMMARY("The chapter presented the architecture, hardware, algorithms and software of each stage, the "
            "deployed interface and the evaluation method. The next chapter reports how well it performed.")
