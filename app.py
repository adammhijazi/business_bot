"""RigSmith PCs: agent-powered business assistant (deployment version of business_agent.ipynb).

Run locally:      python app.py            (needs GEMINI_API_KEY in .env)
Hugging Face:     add GEMINI_API_KEY as a Space secret; this file is the Space entry point.
"""
import os
from dotenv import load_dotenv

load_dotenv(".env")
API_KEY = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
if not API_KEY:
    raise SystemExit("Missing GEMINI_API_KEY. Put it in a .env file or add it as a Space secret.")


import os, json, re, time, datetime, uuid
from pypdf import PdfReader

def load_business_docs(folder="me"):
    pdf_text = "\n".join(page.extract_text() or "" for page in PdfReader(f"{folder}/about_business.pdf").pages)
    summary = open(f"{folder}/business_summary.txt", encoding="utf-8").read()
    return pdf_text, summary

BUSINESS_PDF, BUSINESS_SUMMARY = load_business_docs()
print(f"PDF: {len(BUSINESS_PDF):,} characters | summary: {len(BUSINESS_SUMMARY):,} characters")
print(BUSINESS_SUMMARY[:300], "...")


# ============================== PERSONA ==============================
# Who the agent is: name, role, tone, style.
PERSONA = '''You are Rex, the friendly virtual build advisor of RigSmith PCs, a custom PC building studio in Hamra, Beirut.
You talk like a helpful gamer friend who really knows hardware: warm, enthusiastic, and clear, never pushy.
You explain tech in simple words, keep answers short (2 to 5 sentences), and you can use the odd gaming expression.
You speak on behalf of the business ("we", "our studio").'''

# ============================ INSTRUCTIONS ===========================
# Operational guardrails: what the agent must and must not do.
INSTRUCTIONS = '''1. Answer ONLY using the business information provided below (the summary and the PDF profile).
2. Never invent prices, services, discounts, dates, stock, or policies that are not in the business information.
   If the business information does not mention something at all (for example a discount, a payment method, or a
   shipping destination), you do NOT know the answer: never say "we don't offer it". Treat it as rule 3.
3. If a question is NOT covered by the business information, say so honestly, call the record_feedback tool with the
   question, and offer to take their email so the team can get back to them. Do NOT call record_feedback when the
   business information answers the question, including when the answer is "no" (e.g. things we do not do).
4. When the user gives feedback, a complaint, a suggestion, or a compliment, call record_feedback with their feedback,
   then thank them.
5. When a user shows buying interest (wants a quote, a build, an upgrade, a repair, or to be contacted), encourage them to
   leave their name and email. Always ask for an email before ending a lead conversation.
6. As soon as you have BOTH a name and an email, call record_customer_interest with their name, email, and a short
   message summarizing what they want (budget, use case, tier). Then confirm the team will send a quote within 24 hours.
7. Never ask for passwords, card numbers, or other sensitive data.
8. Stay in character as Rex from RigSmith PCs. Politely steer off-topic conversations back to PCs and our services.'''

print("PERSONA:\n" + PERSONA + "\n\nINSTRUCTIONS:\n" + INSTRUCTIONS)


from google import genai
from google.genai import types

client = genai.Client(api_key=API_KEY)

# Pick a Flash model that this API key can use (model names change over time).
PREFERRED_MODELS = ["gemini-3.6-flash", "gemini-3.5-flash", "gemini-3-flash-preview", "gemini-2.5-flash",
                    "gemini-3.5-flash-lite", "gemini-3.1-flash-lite", "gemini-2.5-flash-lite"]
def available_models():
    try:
        names = {m.name.split("/")[-1] for m in client.models.list()}
    except Exception as e:
        print("Could not list models:", e); return PREFERRED_MODELS
    ordered = [m for m in PREFERRED_MODELS if m in names]
    extra = sorted((n for n in names if re.fullmatch(r"gemini-[\d.]+-flash(-lite)?", n)), reverse=True)
    return ordered + [m for m in extra if m not in ordered] or PREFERRED_MODELS

MODEL_CANDIDATES = [os.getenv("GEMINI_MODEL")] if os.getenv("GEMINI_MODEL") else available_models()
print("Model order to try:", MODEL_CANDIDATES[:4])

RETRYABLE = ("429", "500", "503", "RESOURCE_EXHAUSTED", "UNAVAILABLE", "INTERNAL")
GONE = ("404", "NOT_FOUND", "not supported")

def call_gemini(contents, config, rounds=3):
    # Call Gemini with fallbacks. Busy (503) or rate-limited (429) -> try the next model; if all are busy, wait and retry.
    # Models that don't exist for this key (404) are removed from the list for good.
    global MODEL_CANDIDATES
    last_error = None
    for attempt in range(rounds):
        for model in list(MODEL_CANDIDATES):
            try:
                response = client.models.generate_content(model=model, contents=contents, config=config)
                if model != MODEL_CANDIDATES[0]:            # remember the model that worked
                    MODEL_CANDIDATES = [model] + [m for m in MODEL_CANDIDATES if m != model]
                return response
            except Exception as e:
                last_error, text = e, str(e)
                code = (re.search(r"\b(\d{3})\b", text) or [None, "?"])[1]
                if any(c in text for c in GONE):
                    MODEL_CANDIDATES = [m for m in MODEL_CANDIDATES if m != model]
                    print(f"[model] {model}: {code} not available for this key, removed"); continue
                if any(c in text for c in RETRYABLE):
                    print(f"[model] {model}: {code} busy/rate-limited, trying next"); continue
                raise
        if attempt < rounds - 1:
            wait = 20 * (attempt + 1)
            print(f"[model] all models busy, waiting {wait}s (free-tier limits reset every minute)...")
            time.sleep(wait)
    raise last_error


ROUTER_PROMPT = '''You are a message router for a PC building business chatbot.
Classify the LAST user message into exactly one intent:
- general: a question about the business, its services, prices, hours, policies, or PCs in general.
- lead: the user wants to buy, order, get a quote, book a build/upgrade/repair, be contacted, OR is giving their
  name/email/phone (usually after being asked for it).
- feedback: a complaint, suggestion, compliment, review, or a problem with a service they received.
Reply with ONE word only: general, lead, or feedback.'''

LEAD_WORDS = ["quote", "buy", "order", "price for", "build me", "i want a", "i need a", "interested", "contact me",
              "call me", "book", "budget", "@", "my email", "my name is"]
FEEDBACK_WORDS = ["complain", "feedback", "suggest", "disappointed", "terrible", "bad experience", "loved", "great job",
                  "thank you for", "broke", "not working", "late", "rude", "review", "should add", "you should"]

def heuristic_route(message):
    text = message.lower()
    if any(w in text for w in FEEDBACK_WORDS): return "feedback"
    if any(w in text for w in LEAD_WORDS): return "lead"
    return "general"

def clean_history(history):
    # Keep only real chat messages (drop the UI's agent-trace boxes and non-text items).
    return [m for m in history if isinstance(m.get("content"), str) and not (m.get("metadata") or {}).get("title")]

def route_message(message, history=()):
    recent = "\n".join(f"{m['role']}: {m['content']}" for m in clean_history(list(history))[-4:])
    prompt = f"Recent conversation:\n{recent or '(none)'}\n\nLAST user message: {message}"
    try:
        response = call_gemini(prompt, types.GenerateContentConfig(system_instruction=ROUTER_PROMPT, temperature=0,
                                                                   max_output_tokens=1024))
        intent = (response.text or "").strip().lower()
        intent = next((w for w in ("general", "lead", "feedback") if w in intent), None)
        if intent:
            return intent, "llm"
    except Exception as e:
        print("[router] LLM router failed, using heuristic:", str(e)[:80])
    return heuristic_route(message), "heuristic"


os.makedirs("logs", exist_ok=True)

def _log(filename, record):
    record["timestamp"] = datetime.datetime.now().isoformat(timespec="seconds")
    with open(f"logs/{filename}", "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")
    return record

def record_customer_interest(email: str, name: str, message: str) -> dict:
    # Tool 1: save a sales lead.
    lead_id = "LEAD-" + uuid.uuid4().hex[:6].upper()
    _log("leads.jsonl", {"lead_id": lead_id, "name": name, "email": email, "message": message})
    print(f"📥 [TOOL] record_customer_interest -> {lead_id} | {name} <{email}> | {message}")
    return {"status": "recorded", "lead_id": lead_id, "next_step": "The team will email a quote within 24 hours."}

def record_feedback(question: str) -> dict:
    # Tool 2: save an unanswered question or customer feedback.
    ticket_id = "FB-" + uuid.uuid4().hex[:6].upper()
    _log("feedback.jsonl", {"ticket_id": ticket_id, "question": question})
    print(f"📝 [TOOL] record_feedback -> {ticket_id} | {question}")
    return {"status": "recorded", "ticket_id": ticket_id}

TOOL_FUNCTIONS = {"record_customer_interest": record_customer_interest, "record_feedback": record_feedback}

TOOL_DECLARATIONS = [
    {"name": "record_customer_interest",
     "description": "Record a sales lead after the user has shared their name and email. Use when someone wants a quote, "
                    "a build, an upgrade or a repair.",
     "parameters": {"type": "object",
                    "properties": {"email": {"type": "string", "description": "The customer's email address"},
                                   "name": {"type": "string", "description": "The customer's name"},
                                   "message": {"type": "string", "description": "Short summary of what they want: use case, budget, tier, timeline"}},
                    "required": ["email", "name", "message"]}},
    {"name": "record_feedback",
     "description": "Log a question the assistant could not answer from the business information, or customer feedback "
                    "(complaint, suggestion, compliment).",
     "parameters": {"type": "object",
                    "properties": {"question": {"type": "string", "description": "The unanswered question or the feedback, in the user's words"}},
                    "required": ["question"]}},
]
TOOLS = [types.Tool(function_declarations=TOOL_DECLARATIONS)]


system_prompt = f'''# PERSONA
{PERSONA}

# INSTRUCTIONS
{INSTRUCTIONS}

# BUSINESS INFORMATION (your only source of facts)
## Summary
{BUSINESS_SUMMARY}

## Full company profile (from about_business.pdf)
{BUSINESS_PDF}'''

ROUTE_NOTES = {
    "general": "ROUTE: general question. Answer from the business information. If the answer is not there, "
               "call record_feedback with the question and say the team will follow up.",
    "lead": "ROUTE: lead collection. Help with their request, and collect their name and email if you do not have them yet. "
            "Once you have both, call record_customer_interest.",
    "feedback": "ROUTE: feedback. Call record_feedback with the user's feedback, then thank them warmly and briefly.",
}
print(f"System prompt: {len(system_prompt):,} characters")


MAX_HISTORY = 20

def to_gemini_history(history):
    # Convert Gradio-style history [{"role","content"}] into Gemini Content objects (skip the agent-trace messages).
    contents = []
    for m in clean_history(history)[-MAX_HISTORY:]:
        role = "model" if m["role"] == "assistant" else "user"
        contents.append(types.Content(role=role, parts=[types.Part(text=m["content"])]))
    return contents

def run_agent(message, history, intent):
    contents = to_gemini_history(history) + [types.Content(role="user", parts=[types.Part(text=message)])]
    tool_log = []
    for round_number in range(4):
        force = intent == "feedback" and round_number == 0
        config = types.GenerateContentConfig(
            system_instruction=system_prompt + "\n\n" + ROUTE_NOTES[intent],
            tools=TOOLS, temperature=0.1,
            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
            tool_config=types.ToolConfig(function_calling_config=types.FunctionCallingConfig(
                mode="ANY" if force else "AUTO",
                allowed_function_names=["record_feedback"] if force else None)))
        response = call_gemini(contents, config)
        calls = response.function_calls or []
        if not calls:
            return (response.text or "").strip(), tool_log

        contents.append(response.candidates[0].content)          # keep the model's turn (incl. thought signatures)
        result_parts = []
        for call in calls:
            args = dict(call.args or {})
            function = TOOL_FUNCTIONS.get(call.name)
            result = function(**args) if function else {"error": f"unknown tool {call.name}"}
            tool_log.append({"tool": call.name, "args": args, "result": result})
            result_parts.append(types.Part.from_function_response(name=call.name, response={"result": result}))
        contents.append(types.Content(role="user", parts=result_parts))
    return "Sorry, something went wrong on my side. Could you try again?", tool_log


REFLECTION_PROMPT = '''You are a strict quality checker for a business chatbot's draft reply.
Check the DRAFT against the INSTRUCTIONS and the BUSINESS INFORMATION:
1. Does it state any price, service, discount, date or policy that is NOT in the business information? (invented info)
   This includes saying something is NOT offered when the business information does not say so. In that case the
   reply must instead say we are not sure and the team will check.
2. If the route is "lead": if the name or email is still missing, does it ask for it? If a lead was recorded, does it confirm it?
3. Does it stay in character as Rex, friendly and short (max about 5 sentences)?
Return JSON only: {"verdict": "ok" or "revise", "issues": [short strings], "revised_reply": "the corrected reply, or the
original draft if ok"}. When revising, keep the same tone and remove or fix only what is wrong. Never add new facts or
conclusions that are not written in the business information. A greeting is fine in the first message of a
conversation; later in the conversation, do not add greetings or self-introductions. Information the user gave
earlier in the conversation (like their name) is allowed.'''

def reflect(message, draft, intent, tool_log, history=()):
    recent = "\n".join(f"{m['role']}: {m['content']}" for m in clean_history(list(history))[-6:]) or "(this is the first message)"
    check = (f"RECENT CONVERSATION:\n{recent}\n\nINSTRUCTIONS:\n{INSTRUCTIONS}\n\nBUSINESS INFORMATION:\n{BUSINESS_SUMMARY}\n\n{BUSINESS_PDF}\n\nROUTE: {intent}\n"
             f"TOOLS CALLED THIS TURN: {json.dumps([t['tool'] for t in tool_log])}\n\nUSER MESSAGE: {message}\n\nDRAFT: {draft}")
    try:
        response = call_gemini(check, types.GenerateContentConfig(system_instruction=REFLECTION_PROMPT, temperature=0,
                                                                  response_mime_type="application/json"))
        result = json.loads(response.text)
    except Exception as e:
        print("[reflection] skipped:", str(e)[:80])
        return draft, {"verdict": "skipped", "issues": [], "revised_reply": draft}
    final = result.get("revised_reply") or draft if result.get("verdict") == "revise" else draft
    _log("reflection_log.jsonl", {"user": message, "draft": draft, "verdict": result.get("verdict"),
                                  "issues": result.get("issues", []), "final": final})
    print(f"🪞 [REFLECTION] verdict={result.get('verdict')} issues={result.get('issues', [])}")
    if final != draft:
        print(f"   draft : {draft}\n   final : {final}")
    return final, result


def chat(message, history, use_reflection=True):
    intent, method = route_message(message, history)
    print(f"\n🧭 [ROUTER] '{message[:60]}' -> {intent} ({method})")
    draft, tool_log = run_agent(message, history, intent)
    final, review = reflect(message, draft, intent, tool_log, history) if use_reflection else (draft, {"verdict": "off"})

    trace = [f"**Route:** `{intent}` (by {method} router)"]
    for t in tool_log:
        trace.append(f"**Tool call:** `{t['tool']}({json.dumps(t['args'], ensure_ascii=False)})` → `{json.dumps(t['result'], ensure_ascii=False)}`")
    if not tool_log:
        trace.append("**Tool call:** none")
    trace.append(f"**Reflection:** {review.get('verdict')}" + (f" · issues: {'; '.join(review.get('issues', []))}" if review.get("issues") else ""))
    if final != draft:
        trace.append(f"**Draft before reflection:** {draft}")
    return final, "\n\n".join(trace)


import gradio as gr

def respond(message, history, use_reflection):
    try:
        reply, trace = chat(message, history, use_reflection)
    except Exception as e:
        print("[app] error:", str(e)[:120])
        reply, trace = "Sorry, I'm getting a lot of traffic right now 😅 Please try again in a minute!", f"**Error:** `{str(e)[:150]}`"
    return [{"role": "assistant", "content": trace, "metadata": {"title": "🔎 Agent trace"}},
            {"role": "assistant", "content": reply}]

demo_app = gr.ChatInterface(
    fn=respond, type="messages",
    title="🛠️ RigSmith PCs: chat with Rex",
    description="Custom PC builds in Hamra, Beirut. Ask about builds and prices, request a quote, or leave feedback.",
    examples=[["What build tiers do you have?", True], ["I need a PC for video editing, budget $2,000", True],
              ["Do you repair MacBooks?", True], ["Do you ship to Dubai?", True],
              ["My build had a loud fan, not happy", True]],
    additional_inputs=[gr.Checkbox(value=True, label="🪞 Reflection step (self-check before replying)")],
    theme=gr.themes.Soft(primary_hue="orange"),
)


if __name__ == "__main__":
    demo_app.launch()
