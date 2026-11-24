# RigSmith PCs: Agent-Powered Business Assistant

**EECE 503P/798S: Agentic Systems** · C3 – Introduction to Agents · Adam Hijazi

**RigSmith PCs** is a fictional custom-PC building studio in Hamra, Beirut. **Rex**, its virtual build advisor, is an agent built on the **Gemini API**. It answers questions from the company documents, routes every message to the right handling path, collects sales leads, logs feedback and unanswered questions through tool calls, and checks its own replies before sending them.

🎥 **Demo video:** *add your link here*
🤗 **Live demo (Hugging Face Spaces):** *add your link here*

---

## Features

| Requirement | How it is done |
|---|---|
| Fictional business identity | `me/about_business.pdf` (2-page company profile) and `me/business_summary.txt` |
| Persona vs instructions | Two clearly labeled blocks, `PERSONA` and `INSTRUCTIONS` (notebook Section 2) |
| Router | LLM classifier into `general` / `lead` / `feedback`, with a keyword fallback (Section 3) |
| Tools | `record_customer_interest(email, name, message)` and `record_feedback(question)`, with MCP-style schemas (Section 4) |
| Chat with memory + tool calling | Gemini with chat history (last 20 messages) and a manual tool-calling loop (Sections 5–6) |
| Gradio UI | `gr.ChatInterface` with a collapsible **🔎 Agent trace** for every reply (Section 8) |
| **Bonus:** reflection | A second model call checks the draft and revises it if needed; draft and final are logged (Section 7) |
| **Bonus:** Hugging Face Spaces | `app.py` is deploy-ready (see below) |

## Project structure

```text
business_bot/
├── me/
│   ├── about_business.pdf      # company profile (services, prices, team, policies)
│   └── business_summary.txt    # short summary
├── business_agent.ipynb        # main notebook: explained step by step
├── app.py                      # same agent as a script, for deployment
├── requirements.txt
├── .env.example                # template: copy to .env and add your key
├── .gitignore                  # keeps .env and logs/ out of GitHub
└── logs/                       # created at runtime: leads.jsonl, feedback.jsonl, reflection_log.jsonl
```

---

## Setup

### 1. Get a free Gemini API key

1. Go to <https://aistudio.google.com/apikey> and sign in with a Google account.
2. Click **Create API key** and copy it. It's free; no credit card needed.

### 2a. Run in Google Colab (easiest)

1. Open <https://colab.research.google.com> → **File → Upload notebook** → `business_agent.ipynb`.
2. Click the **🔑 (Secrets)** icon in the left sidebar → **Add new secret** → name `GEMINI_API_KEY`, paste your key as the value, and switch on **Notebook access**.
3. **Runtime → Run all.** When asked, upload `about_business.pdf` and `business_summary.txt`.
4. The last cell prints a `https://....gradio.live` link. Open it to chat with Rex.

No GPU is needed, because the model runs on Google's servers.

### 2b. Run locally (Jupyter or `app.py`)

```bash
git clone <your-repo-url> && cd business_bot
pip install -r requirements.txt
cp .env.example .env          # then open .env and paste your key after GEMINI_API_KEY=
jupyter notebook business_agent.ipynb      # or:  python app.py  → open http://127.0.0.1:7860
```

### 3. Deploy to Hugging Face Spaces (bonus)

1. Create an account at <https://huggingface.co>, then **New → Space**. Choose the **Gradio** SDK and the free CPU hardware.
2. Upload `app.py`, `requirements.txt` and the `me/` folder (**Files → Add file → Upload files**). Do **not** upload `.env`.
3. **Settings → Variables and secrets → New secret**: name `GEMINI_API_KEY`, value = your key.
4. In the Space's own `README.md`, set `sdk_version: 5.50.0`, so it matches the `gradio<6` pin in `requirements.txt`.
5. The Space builds automatically (1–3 minutes). Your public link is `https://huggingface.co/spaces/<you>/<space-name>`.

---

## How it works

```text
user message
     │
     ▼
 ┌────────┐   general / lead / feedback    ┌──────────────────────────────────────────┐
 │ ROUTER │ ─────────────────────────────▶ │ AGENT (Gemini)                            │
 └────────┘   (LLM call, keyword fallback)  │ system prompt = persona + instructions     │
                                            │   + business PDF + summary + route note    │
                                            │ + chat history (short-term memory)         │
                                            │ + tools ─────► record_customer_interest    │
                                            │          ─────► record_feedback            │
                                            └──────────────────────────────────────────┘
                                                            │ draft reply
                                                            ▼
                                                  ┌────────────────────┐
                                                  │ REFLECTION (bonus) │ ok → send draft
                                                  │ check vs rules     │ revise → send revised
                                                  └────────────────────┘    (both logged)
```

### Persona and instructions

- **Persona:** *who* the agent is. Rex is RigSmith's friendly build advisor and talks like a helpful gamer friend who really knows hardware.
- **Instructions:** the operational guardrails. Answer only from the business documents. Never invent prices or discounts. Log unknown questions and feedback with `record_feedback`. Always ask for an email in a lead conversation, and call `record_customer_interest` once both name and email are known. Never ask for sensitive data. Stay in character.

Both are separate, labeled variables in the notebook and are combined into `system_prompt` in Section 5.

### Router

| Intent | Example | Handling |
|---|---|---|
| `general` | "What are your opening hours?" | Answer from the documents. If the answer is unknown, call `record_feedback`. |
| `lead` | "I want a 1440p gaming PC, budget $1,300" / "I'm Adam, adam@mail.com" | Collect name and email, then call `record_customer_interest`. |
| `feedback` | "My PC arrived late" / "Great cable management!" | `record_feedback` is **forced** (`mode="ANY"`) on the first model call. |

The router is one short LLM call that answers with a single word. It also sees the last few messages, so "I'm Adam, adam@mail.com" is recognized as a lead when it answers a request for contact details. If the call fails, a keyword heuristic takes over.

### Tools (MCP-style schemas)

| Tool | Parameters | Returns | Logs to |
|---|---|---|---|
| `record_customer_interest` | `email` (string, required), `name` (string, required), `message` (string, required: what they want) | `{"status": "recorded", "lead_id": "LEAD-…", "next_step": "…"}` | console + `logs/leads.jsonl` |
| `record_feedback` | `question` (string, required: the unanswered question or the feedback) | `{"status": "recorded", "ticket_id": "FB-…"}` | console + `logs/feedback.jsonl` |

The full JSON schemas (`name`, `description`, `inputSchema`, `output`) are in notebook Section 4.

### Agent loop

The SDK's automatic function calling is turned off on purpose, so every step is visible:

1. Send the system prompt, history, message and tools to Gemini.
2. If the reply contains function calls, run the Python functions and send the results back (up to 4 rounds).
3. Otherwise, return the text.

### Reflection (bonus)

A second model call checks the draft against the instructions:

- Did it invent prices or policies?
- On a lead path, did it ask for the missing name or email?
- Did it stay in character and keep the reply short?

It returns JSON (`verdict`, `issues`, `revised_reply`). The draft, the verdict and the final reply are printed and saved to `logs/reflection_log.jsonl`. In the UI, the agent trace shows the original draft whenever it was revised.

### Model choice

The agent uses a **Gemini Flash** model, which is fast and has a free tier. Gemini model names change often, so the notebook lists the models your key can access and picks the newest Flash model from a preference list. If a model is unavailable or out of free quota, it automatically falls back to the next one. To force a specific model, set `GEMINI_MODEL` in `.env`.

> The assignment mentions `google.generativeai`. That SDK has been deprecated by Google, so this project uses its official replacement, **`google-genai`** (`from google import genai`).

---

## Try it: example conversations

| Type this | Expected behavior |
|---|---|
| "What build tiers do you have?" | `general`: lists the 4 tiers and starting prices from the PDF |
| "Do you ship to Dubai?" | `general` → not in the documents → `record_feedback` is called, and Rex offers to take your email so the team can reply |
| "Do you offer a student discount?" | Same as above. Reflection catches any invented discount. |
| "I need a PC for video editing, budget $2,000" → "I'm Adam, adam@example.com" | `lead`: suggests the Creator Workstation and asks for contact details, then calls `record_customer_interest` |
| "My build had a loud fan, not happy" | `feedback`: `record_feedback` is forced, and Rex apologizes and thanks the user |
| "Do you repair MacBooks?" | `general`: answers "no" from the PDF's "what we do not do" list (no tool needed) |

## Demo video checklist (2–4 minutes)

1. Show the repo structure and the labeled **PERSONA** and **INSTRUCTIONS** blocks.
2. Run the **router test** cell (Section 3) to show the three intents.
3. In the Gradio app, open the **🔎 Agent trace** for each of these:
   - a general question (no tool);
   - an unknown question, which calls `record_feedback`;
   - a lead conversation, which calls `record_customer_interest` once the email is given;
   - a complaint, which forces the feedback route.
4. Show `logs/leads.jsonl`, `logs/feedback.jsonl` and `logs/reflection_log.jsonl`, pointing out any draft the reflection revised.
5. (Bonus) Open the Hugging Face Space link.

## Security

- The API key lives only in `.env` (local), in Colab secrets, or in Hugging Face secrets. `.env` is listed in `.gitignore`, so it is never uploaded.
- The bot never asks for passwords or payment details (instruction 7).
