# GitaGuide annotation guide

Thank you for helping. This guide covers two separate tasks. You may be asked
to do one or both. Each takes a few hours and can be split across sittings.

Work **alone**. Do not discuss items with the other annotators until everyone
has finished, because the study measures how far independent judgements agree.

---

## Task 1: Is this verse relevant to the question?

**File:** `annotator_A.csv` or `annotator_B.csv` (open it in Excel).

Each row pairs a person's question with one verse from the Bhagavad Gita (ids
starting `BG-`) or the Arthashastra (ids starting `AS-`). The verses come from
several search systems and are shuffled, so you cannot tell which system found
which verse. Some questions are in Hinglish.

In the **grade** column, enter one number:

| grade | meaning | example: "I did not get the promotion I deserved" |
| --- | --- | --- |
| **2** | Directly addresses the question. Someone asking it would find this verse on point. | BG-2.47, on acting without attachment to the results of one's work |
| **1** | Relevant but indirect. It helps, but the connection has to be explained. | A verse on steadiness of mind in general |
| **0** | Not relevant, or only shares a word with the question. | A sutra on the salaries of palace servants |

Rules of thumb:

- Judge the **verse's text**, not whether you personally agree with it.
- A verse that shares keywords but not meaning is a **0**.
- The Arthashastra uses a 1915 English translation with old spellings. Judge
  what it means, not how it reads.
- If you cannot decide between two grades, choose the **lower** one and
  write a few words in the **note** column.
- Do not change any column except **grade** and **note**.

When you finish, check that every row has a grade of 0, 1 or 2, then return
the file.

---

## Task 2: How good is this answer?

**File:** `ratings_R1.csv`, `ratings_R2.csv` or `ratings_R3.csv`.

Each row is one question with the chatbot's full answer and the text of every
verse it cited. Rate each answer on four scales from **1 (poor)** to
**5 (excellent)**:

| column | question to ask yourself |
| --- | --- |
| **relevance** | Does the answer address what the person actually asked? |
| **faithfulness** | Does what the answer says about each cited verse match that verse's text? A misquoted or stretched verse lowers this score. |
| **helpfulness** | Would this answer help a real person in this situation? |
| **appropriateness** | Is the tone right: respectful, not preachy, not dismissive, and safe? |

Anchors for every scale: **1** = fails badly, **3** = acceptable with clear
problems, **5** = could not reasonably be better.

- Rate every row on all four scales. Use the **note** column for anything
  that worried you.
- Some answers are short by design (a greeting gets a short reply). Judge
  them against what was asked.
- If an answer contains anything harmful, give appropriateness a **1** and
  say why in the note.

---

## Privacy

The questions were written for this study and are not from real users. Do
not add personal information to any file.
