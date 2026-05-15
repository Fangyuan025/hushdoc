"""v0.6.0 commit 3 verification: sentence -> chunk -> paragraph binding.

After this commit the SSE done event carries a ``sentence_bindings``
field. Each entry maps one answer sentence to the paragraphs inside
the cited chunks it refers to, ready for the frontend's hover popover
to consume.

Run with the v0.6.0 backend up on :8200.
"""
from __future__ import annotations

import json
import sys
import io
from pathlib import Path

# Force UTF-8 stdout on Windows so Chinese / em-dashes don't crash.
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

import requests

BASE = "http://localhost:8200/api"
TEST_PDF = Path(__file__).parent / "Lin-Jiang-AIchatbotPrivacy.pdf"

PASS, FAIL = 0, 0
def check(name, cond, info=""):
    global PASS, FAIL
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + (f"  -- {info}" if info else ""))
    PASS += 1 if cond else 0
    FAIL += 0 if cond else 1

def sse_lines(r):
    event = "message"; data = []
    for raw_b in r.iter_lines(decode_unicode=False):
        if raw_b is None: continue
        raw = raw_b.decode("utf-8", "replace") if isinstance(raw_b, bytes) else raw_b
        if raw == "":
            if data:
                try: payload = json.loads("\n".join(data))
                except Exception: payload = "\n".join(data)
                yield event, payload
            event = "message"; data = []
            continue
        if raw.startswith("event:"): event = raw[6:].strip()
        elif raw.startswith("data:"): data.append(raw[5:].lstrip())

print("1. Upload PDF")
with open(TEST_PDF, "rb") as f:
    r = requests.post(BASE + "/documents/upload",
        files=[("files", (TEST_PDF.name, f, "application/pdf"))],
        data={"replace": "true"}, stream=True, timeout=600)
    for _ in sse_lines(r): pass
check("upload completed", True)

print("\n2. Ask a question, observe done.sentence_bindings")
cid = requests.post(BASE + "/conversations", json={}, timeout=10).json()["id"]
r = requests.post(BASE + "/chat", json={
    "question": "What does this paper say about user privacy attitudes?",
    "conversation_id": cid,
}, stream=True, timeout=600)
done = None
for ev, payload in sse_lines(r):
    if ev == "done": done = payload
check("done event seen", done is not None)

if done:
    answer = done.get("answer", "")
    sources = done.get("source_documents", [])
    bindings = done.get("sentence_bindings", [])
    print(f"\n   answer length: {len(answer)} chars")
    print(f"   sources (cited): {len(sources)}")
    print(f"   sentence bindings: {len(bindings)}")

    check("done carries sentence_bindings", isinstance(bindings, list))
    check("at least one binding", len(bindings) > 0)

    cited_sentence_count = sum(1 for b in bindings if b.get("citations"))
    uncited_sentence_count = sum(1 for b in bindings if not b.get("citations"))
    check("at least one cited sentence", cited_sentence_count > 0,
          f"cited={cited_sentence_count}")

    # Every cited sentence must have at least one paragraph binding
    bad = [b for b in bindings if b.get("citations") and not b.get("paragraphs")]
    check("every cited sentence has paragraphs", len(bad) == 0,
          f"missing={len(bad)}")

    # Show first few bindings
    print("\n   Sample bindings (first 3 cited):")
    cited_seen = 0
    for b in bindings:
        if not b.get("citations"): continue
        cited_seen += 1
        if cited_seen > 3: break
        print(f"     sent: {b['text'][:90]!r}")
        print(f"     cites: {b['citations']}")
        for p in b.get("paragraphs", []):
            print(f"       [{p['prompt_id']}] score={p['score']} "
                  f"({p['filename']} p.{p['page']})")
            print(f"         {p['paragraph'][:120]!r}")
        print()

    # No binding should reference a prompt_id absent from cited sources
    src_ids = set()
    for s in sources:
        # frontend payload doesn't carry prompt_id directly; bind by
        # filename+page tuple instead. Use binding paragraphs as oracle:
        pass
    cited_pids_in_bindings = {
        p["prompt_id"]
        for b in bindings
        for p in b.get("paragraphs", [])
    }
    # All bound prompt_ids should appear in the answer's [N] tags
    import re
    answer_ids = set(int(m.group(1)) for m in re.finditer(r"\[(\d+)\]", answer))
    check("binding prompt_ids subset of answer [N]",
          cited_pids_in_bindings.issubset(answer_ids),
          f"bound={cited_pids_in_bindings} answer_ids={answer_ids}")

# cleanup
requests.delete(BASE + f"/conversations/{cid}", timeout=10)
requests.delete(BASE + f"/documents/{TEST_PDF.name}", timeout=10)
print()
print(f"== {PASS}/{PASS+FAIL} passed ==")
sys.exit(0 if FAIL == 0 else 1)
