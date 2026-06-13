content = """# High-Level Architecture (HLD): The "Late Binding" Model

Welcome to the architecture of our personal wisdom system. 

If you are reading this for the first time, this document explains how we take raw, messy daily thoughts (like a voice note or journal entry) and turn them into a structured, highly searchable web of lifelong wisdom.

To make things easy to understand, we'll start with some quick definitions.

---

## 📖 Glossary of Terms

Before diving into the steps, here is what some of our technical words actually mean:

*   **Episode:** A single journal entry or recorded thought from one specific day. 
*   **Node:** A single piece of data in our database. An Episode is a node, a Pattern is a node, and a Belief is a node. 
*   **Knowledge Graph:** Instead of storing data in flat spreadsheets, we store it like a spider web. For example, the Episode node is connected to a Pattern node, which is connected to a Belief node.
*   **Late Binding:** Our core strategy. It means we wait as long as possible before trying to connect today's thought to your past history.
*   **Anchoring Bias:** A known AI flaw. If you give an AI your whole life story and ask it to analyze today's journal, it gets lazy and forces today's nuanced thought into an old category instead of looking at what is truly new today.
*   **Fragmentation:** The opposite problem. If the AI *never* sees your past, it will create 50 different categories for the exact same problem (e.g., "Sad about work", "Upset at job", "Office blues") making it impossible to see patterns.
*   **Embeddings & Vector Database:** A way to turn text into lists of numbers so the computer can find similar "meanings" or "vibes," even if you use completely different words. 

---

## ⚙️ The Data Journey (Step-by-Step)

Here is exactly what happens when you submit a new journal entry to the system.

### Step 1: Ingestion & "Blind" Microextraction
**Input:** The raw text of your thought (e.g., *"I felt really overwhelmed setting up my database today. I avoided it for 3 days."*)

We first let an AI read this text in totally isolated "blind" environment—it knows absolutely nothing about your past. 
*   **Why?** To prevent Anchoring Bias. We want absolute honesty about *today*.
*   **What it does:** The AI extracts the "Episode" details: what happened, your emotions, the triggers, the patterns, and the beliefs. 
*   **Tech Detail (Async Batching):** Because this step doesn't need to check history, it's very fast. If we want to process 500 old journal entries at once, we can do them all at the exact same time simultaneously (asynchronously).

### Step 2: Looking up the Past (Candidate Retrieval)
Now that we have today's fresh, unbiased extraction, we check the database to see if this has happened before.
*   **Why?** To prevent Fragmentation. We want to link today's entry to past trends if they exist.
*   **How we do it well:** Sometimes you say *"exhausted"* and sometimes you say *"burnt out"*. To make sure the AI recognizes these are the same, we use two tricks:
    1.  **HyDE (Hypothetical Document Embeddings):** The AI guesses what the "perfect" past pattern would look like before searching for it.
    2.  **Hybrid Search:** We search the database using *both* exact keyword matches (BM25) and "vibe/meaning" matches (Vectors).
*   **Result:** The system fetches the top 3-5 past patterns that look similar to today's thought.

### Step 3: Reconciliation (The Decision Maker)
Now we bring in a second AI step. We give this AI the pure extraction from Step 1, and the historical matches from Step 2. We ask it: *"How does today fit into the past?"*

The AI acts as a router and must choose one of three actions:
1.  **MERGE:** This is just another instance of a past pattern. Add to the count.
2.  **EVOLVE:** This is growth! The old belief has changed into a new realization.
3.  **BRANCH:** This is completely new. Create a brand new pattern in the database.

*   **Tech Detail (Strict JSON):** We don't let the AI write paragraphs here. We force it to act like a strict computer program that only spits out code (JSON format), e.g., `{"action": "MERGE", "confidence": 0.95}` so our database doesn't break.
*   **Tech Detail (Human-In-The-Loop / HITL):** AI isn't perfect. If the AI is less than 85% confident about its decision, it pauses. It puts the entry in a "Review Queue" for *you* (the human) to manually click a button to approve or reject the connection, keeping your database safe from errors.

### Step 4: Saving to the Knowledge Graph
We now save the data into our web-like database (Graph).
*   **Tech Detail (Append-Only Versioning):** What happens if your belief "evolves"? If we delete your old belief and overwrite it, we erase your history! Instead, we use "append-only versioning." We keep Belief Version 1 frozen in time attached to your old memories, and simply create Belief Version 2 for your new memories. You never lose a single phase of your life journey.

---

## 🔍 How You Use It (Output)

### Step 5: Querying (The RAG Retriever)
Let's say a year from now you ask the system: *"Why do I always get stuck on big tech projects?"*
1.  The system turns your question into numbers (Embeddings).
2.  It searches the Vector Database for your past connected Episodes, Patterns, and Beliefs.
3.  **RAG (Retrieval-Augmented Generation):** It sends this curated packet of your *own* history to an AI.
4.  The AI responds to you, acting as an advisor that intimately knows your exact life experiences.

### Step 6: The Killer Feature (Periodic Reflection)
Because we are saving "Episodes" properly linked to recurring "Patterns," the system can automatically generate reports for you, like a **Monthly Wisdom Report**.
*   It can tell you which negative patterns fired most frequently this month.
*   It can highlight exactly how your beliefs evolved compared to last month.
*   Instead of just answering questions, it acts as a feedback loop between "Present You" and "Past You."

---

## 💰 Cost Analysis

To build the Minimum Viable Product (MVP), this is incredibly cheap:
*   **Transcription:** Free (Using local tools like Whisper.cpp)
*   **Database (Graph & Vector):** Free (Using local tools like SQLite and ChromaDB)
*   **Extraction AI (The Brains):** Extremely cheap or Free (Using Google Gemini API or local/Claude APIs). Everything can be run locally on a laptop.
"""

with open('hld/HLDv1.md', 'w') as f:
    f.write(content)
