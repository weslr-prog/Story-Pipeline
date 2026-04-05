import json
from pathlib import Path

from dotenv import load_dotenv
from langchain_ollama import ChatOllama

from config import SETTINGS
from tts_engine import narrate_chapter

ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT / ".env")

NODE_WORD_MIN = 300
NODE_WORD_MAX = 600
TOTAL_NODES = 35
ENDING_NODES = 5
BRANCH_DEPTH = 4


def _validate_inputs() -> None:
    required = [
        ROOT / "story_bible.json",
        ROOT / "characters.json",
        ROOT / "style_guide.txt",
        ROOT / SETTINGS.voice_sample,
    ]
    missing = [str(p) for p in required if not p.exists()]
    if missing:
        raise FileNotFoundError("Missing required inputs:\n" + "\n".join(missing))


def _llm(temp: float, max_tokens: int) -> ChatOllama:
    return ChatOllama(
        model=SETTINGS.llm_model,
        base_url=SETTINGS.ollama_url,
        temperature=temp,
        num_ctx=8192,
        num_predict=max_tokens,
        repeat_penalty=1.1,
    )


def _invoke(llm: ChatOllama, prompt: str) -> str:
    resp = llm.invoke(prompt)
    return getattr(resp, "content", str(resp)).strip()


def _load_json(name: str):
    return json.loads((ROOT / name).read_text(encoding="utf-8"))


def generate_node_map() -> dict:
    bible = _load_json("story_bible.json")
    architect_prompt = f"""
Design a complete CYOA node map for this story:
{json.dumps(bible, indent=2)}

Requirements:
- {TOTAL_NODES} total nodes
- each non-ending node has exactly two choices: A and B
- {ENDING_NODES} ending nodes
- max depth {BRANCH_DEPTH}
- merged branches allowed

Return ONLY valid JSON with this shape:
{{
  "nodes": [
    {{
      "id": "node_001",
      "label": "...",
      "type": "story|ending",
      "word_target": 400,
      "leads_to": {{"A": "node_002", "B": "node_003"}},
      "choice_text": {{"A": "...", "B": "..."}}
    }}
  ]
}}
"""
    raw = _invoke(_llm(temp=0.5, max_tokens=3500), architect_prompt)
    node_map = json.loads(raw)
    (ROOT / "cyoa" / "node_map.json").write_text(json.dumps(node_map, indent=2), encoding="utf-8")
    return node_map


def write_node(node: dict, node_map: dict, characters: dict, style_guide: str) -> str:
    node_id = node["id"]
    node_type = node.get("type", "story")
    word_target = int(node.get("word_target", 450))
    word_target = max(NODE_WORD_MIN, min(NODE_WORD_MAX, word_target))

    writer_prompt = f"""
You are the Node Writer Agent for a CYOA story.
Write node {node_id} ({node_type}) in {word_target} words.
Keep continuity with global map and character voices.
At the end, include choice lines only if node type is story:
A) ...
B) ...

NODE:
{json.dumps(node, indent=2)}

NODE_MAP:
{json.dumps(node_map, indent=2)}

CHARACTERS:
{json.dumps(characters, indent=2)}

STYLE:
{style_guide}
"""

    editor_prompt = """
You are the Node Editor Agent.
Revise for clarity, continuity, and balanced choices.
Do not make one choice obviously correct.
Return only final node prose.

NODE DRAFT:
{draft}
"""

    draft = _invoke(_llm(temp=0.7, max_tokens=1200), writer_prompt)
    final = _invoke(_llm(temp=0.4, max_tokens=1200), editor_prompt.format(draft=draft))

    out = ROOT / "cyoa" / "nodes" / f"{node_id}.txt"
    out.write_text(final, encoding="utf-8")
    return final


def run_cyoa(max_nodes: int | None = None) -> None:
    (ROOT / "cyoa" / "nodes").mkdir(parents=True, exist_ok=True)
    _validate_inputs()

    node_map_path = ROOT / "cyoa" / "node_map.json"
    node_map = (
        json.loads(node_map_path.read_text(encoding="utf-8"))
        if node_map_path.exists()
        else generate_node_map()
    )

    characters = _load_json("characters.json")
    style_guide = (ROOT / "style_guide.txt").read_text(encoding="utf-8")

    nodes = node_map.get("nodes", [])
    if max_nodes is not None:
        nodes = nodes[:max_nodes]

    for idx, node in enumerate(nodes, start=1):
        node_id = node["id"]
        txt_path = ROOT / "cyoa" / "nodes" / f"{node_id}.txt"
        wav_path = ROOT / "cyoa" / "nodes" / f"{node_id}_narration.wav"

        if txt_path.exists() and wav_path.exists():
            print(f"[SKIP] {node_id} already rendered")
            continue

        final = write_node(node, node_map, characters, style_guide)
        narrate_chapter(
            text=final,
            voice_sample=SETTINGS.voice_sample,
            output_path=str(wav_path),
            chapter_num=1000 + idx,
        )
        print(f"[OK] {node_id} complete")


if __name__ == "__main__":
    run_cyoa(max_nodes=3)
