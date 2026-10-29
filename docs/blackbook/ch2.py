# -*- coding: utf-8 -*-
from lib import B, CH, H2, H3, J, LEAD, SUMMARY, T, F, TBL


def build():
    CH(2, "Literature Survey", "ch2")
    J("This chapter reviews the work that GitaGuide builds on and the work it is compared against. It "
      "first gives the background needed to read the rest of the survey, covering language models in "
      "mental health, the documented risks of religious chatbots, crisis detection, graph-based retrieval "
      "and the models used in this project. It then surveys the closest existing systems one group at a "
      "time and compares them in a single table. The chapter ends by stating the gap that the project "
      "is designed to fill. A longer critical review of 34 sources, with a second search designed to "
      "challenge the gap, was written separately as the project’s literature review [12]; this chapter "
      "summarises it.")

    H2("2.1", "Introduction to the Overall Topic", "s2_1")
    H3("2.1.1", "Language models in mental health and emotional support")
    J("Language models are now being tried for behavioural health, and the literature is cautious. Stade "
      "et al. argue that such systems could change behavioural healthcare, but only if they are developed "
      "and evaluated responsibly [1]. A systematic review of LLM applications in mental health by Guo et "
      "al. found that the evidence base is thin and that evaluation methods vary widely between studies "
      "[2]. A later review of chatbots in mental health, tracing their growth from rule-based systems to "
      "LLMs, reached a similar view and pointed to gaps in evaluation and clinical validation [7]. Both "
      "reviews describe safety as something added after the system is built.")
    H3("2.1.2", "Religious chatbots and their documented risks")
    J("The risks of putting scripture behind a chatbot have been reported in three ways. Journalism "
      "provided the clearest case. A 2023 investigation reported that several deployed Gita chatbots "
      "condoned violence when the question was framed as duty, and quoted real verses to support the "
      "answer [3]. Biana reviewed religion-based chatbots from a feminist perspective and found "
      "inaccurate answers and bias, including gender bias, when the systems were asked about women [4]. "
      "Wright reported the theological view that scripture chatbots carry the hallucination risk of any "
      "language model, so that a user can mistake invented material for genuine scripture [5].")
    H3("2.1.3", "Emotional dependency and refusal harm")
    J("Two further risks concern the relationship between the user and the system. Zhu, Coifman and Jin "
      "analysed user discourse about AI chatbots and reported emotional dependency among the principal "
      "risks users describe [6]. Ni and Yang described Abrupt Refusal Secondary Harm (ARSH): when a safety "
      "protocol ends an emotionally charged conversation without a transition, the refusal can itself feel "
      "like rejection to a user who has come to rely on the system, so a staged closure is preferable to "
      "a hard stop [19]. These two works shape the requirements in Section 2.2.4.")
    H3("2.1.4", "Crisis detection and how it is evaluated")
    J("Detecting crisis in text is a task with real disagreement. Weber et al. tested LLM-based suicide- "
      "and crisis-risk detection on conversation segments annotated by clinicians, and reported "
      "substantial disagreement among the clinicians themselves [14]. Open evaluation suites for "
      "chatbot suicide-risk detection and response now exist; Bentley et al. describe a human validation "
      "study of one of them, VERA-MH [15]. These works matter for this project in a specific way. They show "
      "that a self-written safety suite, as used here, can test for regressions but cannot establish "
      "clinical validity.")
    H3("2.1.5", "Graph-based retrieval")
    J("GraphRAG, from Microsoft Research, uses a graph built over a corpus to answer questions about the "
      "corpus as a whole, which plain vector retrieval handles poorly. It does this at a much higher "
      "indexing cost and with two to three times the latency of vector-only retrieval [16]. Zhang et "
      "al. propose an entity-event knowledge graph for retrieval-augmented generation at EACL 2026 [17]. "
      "A review of how knowledge graphs and LLMs relate to each other identified explainability as the "
      "weakest aspect of the systems examined [18]. That last finding is the reason GitaGuide stores the "
      "graph path behind every retrieved verse instead of using the graph only to improve ranking.")
    H3("2.1.6", "Emotion and embedding models")
    J("The emotion model in this project is built on RoBERTa [23], a robustly optimised variant of BERT, "
      "fine-tuned on GoEmotions [22], a dataset of 28 fine-grained emotion labels. The specific "
      "checkpoint used is a public model card by Lowe [24]. For retrieval, verses and queries are "
      "embedded with a Sentence-BERT model [25], specifically the all-MiniLM-L6-v2 variant, which gives "
      "384-dimensional vectors and is small enough to run on a CPU instance.")

    H2("2.2", "Survey of Existing Systems", "s2_2")
    H3("2.2.1", "Scripture-grounded systems")
    J("Dhokane et al. built the most complete academic Gita system found in the review [8]. It combines a "
      "FAISS index over Sentence-BERT embeddings with fine-tuned BERT and GPT-2 models. The authors report "
      "91.4% semantic accuracy against a keyword baseline of about 60%, with validation by domain experts "
      "on 2,000 question–answer pairs and a user study with 150 participants. The paper does not state "
      "how semantic accuracy is computed. The system has no emotion, intent or safety layer and does not "
      "include the Arthashastra, which it lists as future work.")
    J("SpiritualLLM, by Kapuriya et al., fine-tunes and benchmarks 12 LLMs for emotional support on a "
      "dataset derived from the Gita, using automatic metrics, an LLM jury and a small human evaluation "
      "[9]. It has no knowledge graph, no memory across turns, no explanation module and no safety "
      "filter. Chandra et al. analysed the Sanskrit text of the Gita and its English and Tamil "
      "translations with topic modelling and sentiment analysis, but they did not build a conversational "
      "system [13]. Outside academic work, Quran Chat answers questions from the Quran, tafsir and Sunnah "
      "with citations and reports about 86% accuracy on an internal, non-adversarial measure. It has no "
      "emotion detection and no safety gate beyond a general disclaimer [11].")
    H3("2.2.2", "Emotion-aware and safety-conscious systems")
    J("SAGE, by Aharon et al., is the closest system to GitaGuide in design [10]. It combines a "
      "heterogeneous knowledge graph with classification of crisis-aware counselling strategies and "
      "was evaluated by experts. It is not grounded in any scripture, does not use two texts, and "
      "has no explicit emotion-intensity score. The clinician-annotated crisis-detection study [14] and "
      "the open evaluation suite [15] described in Section 2.1.4 round out what is available for "
      "judging safety, and both are at the level of the conversation and not of a scripture-grounded answer.")
    H3("2.2.3", "Comparison of the closest systems")
    J(f"{T('cmp')} compares the closest systems on the capabilities this project cares about. The entries "
      "are taken from the statements in the cited works; “n/r” means not reported and a dash means "
      "not applicable.")
    TBL("cmp", "Capability comparison with the closest systems",
        ["System", "Text", "Emotion", "Knowledge graph", "Safety", "Two texts"],
        [["Dhokane et al. [8]", "Gita", "No", "No", "No", "No"],
         ["SpiritualLLM [9]", "Gita-inspired", "Yes", "No", "No", "No"],
         ["SAGE [10]", "Counselling", "No intensity", "Yes", "Yes", "—"],
         ["Quran Chat [11]", "Quran, tafsir, Sunnah", "No", "n/r", "Disclaimer only", "—"],
         ["GitaGuide (this project)", "Gita + Arthashastra", "Yes, with intensity", "Yes", "Four layers", "Yes"]],
        [3.4, 2.9, 2.4, 2.1, 2.1, 1.7], center_cols=(2, 3, 4, 5))
    H3("2.2.4", "Research gap and problem definition")
    J("Five requirements follow from the survey. Safety should come before retrieval and generation, and "
      "should be testable as such. Every citation should be verifiable against what was retrieved. Every "
      "answer should have a stored explanation. The system should measure the intensity of an emotion "
      "and not only its category. And the deployment should be cheap enough for a small research beta.")
    J("Within the 34 sources reviewed, and with a second search designed to find a counter-example [12], "
      "no system combined emotion-intensity modelling, retrieval across two scriptures, knowledge-graph "
      "explanations, a layered safety gate and conversational memory in one pipeline. That is the gap "
      "GitaGuide targets. The review was done by one reviewer and was limited to English-language "
      "sources, so the absence of such a system is a finding of this review and not a proof.")
    SUMMARY("The survey showed that language models in mental health are poorly validated, that Gita "
            "chatbots have produced harmful answers while citing real verses, and that no reviewed system "
            "combines emotion intensity, two scriptures, graph explanations and a safety gate in one "
            "pipeline. These findings define the five requirements the next chapter designs against.")
