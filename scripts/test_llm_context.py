"""Quick test: verify the scene planner prompt fits in 20480 context and times properly."""
import json
import requests
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

bible = json.loads((ROOT / "story_bible.json").read_text())
characters = json.loads((ROOT / "characters.json").read_text())
style = (ROOT / "style_guide.txt").read_text()
briefs = json.loads((ROOT / "chapter_briefs.json").read_text())
brief = briefs[0]

context = (
    "STORY BIBLE:\n" + json.dumps(bible, indent=2)
    + "\n\nCHARACTERS:\n" + json.dumps(characters, indent=2)
    + "\n\nPRIOR CHAPTERS:\nThis is the first chapter."
    + "\n\nSTYLE GUIDE:\n" + style
    + "\n\nTHIS CHAPTER BRIEF:\n" + json.dumps(brief, indent=2)
)

plan_prompt = (
    "You are the Scene Planner Agent.\n"
    f"Build exactly three scenes for chapter 1.\n\n"
    f"CHAPTER BRIEF:\n{json.dumps(brief, indent=2)}\n\n"
    f"CONTEXT:\n{context}\n"
)

token_est = int(len(plan_prompt.split()) / 0.75)
print(f"Scene planner prompt estimate: {len(plan_prompt.split())} words, ~{token_est} tokens")
print(f"LLM_NUM_CTX=20480, output=1800 tokens, total ~{token_est + 1800} -> fits: {token_est + 1800 < 20480}")

start = time.time()
payload = {
    "model": "qwen2.5:7b-instruct-q5_K_M",
    "temperature": 0.3,
    "max_tokens": 50,
    "num_ctx": 20480,
    "messages": [{"role": "user", "content": plan_prompt}],
    "stream": False,
}
try:
    r = requests.post(
        "http://127.0.0.1:11434/v1/chat/completions", json=payload, timeout=120
    )
    elapsed = time.time() - start
    data = r.json()
    text = data["choices"][0]["message"]["content"]
    print(f"LLM responded in {elapsed:.1f}s: {text[:120]}")
except Exception as e:
    print(f"Failed after {time.time()-start:.1f}s: {e}")
