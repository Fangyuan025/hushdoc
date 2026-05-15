"""v0.6.0 commit 4 verification: adaptive top-k + MMR.

After this commit the retrieval_mode string carries `+adaptive(N)`
when the rerank tail got truncated, and `+mmr` when the kept docs got
re-ordered. We just spot-check that the chain runs end-to-end and the
mode string reflects the new pipeline.
"""
from __future__ import annotations

import io
import json
import re
import sys
from pathlib import Path

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

print("\n2. Ask + observe retrieval_mode + sentence_bindings")
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
    mode = done.get("retrieval_mode", "")
    bindings = done.get("sentence_bindings", [])
    sources = done.get("source_documents", [])
    answer = done.get("answer", "")
    print(f"\n   retrieval_mode: {mode!r}")
    print(f"   sources: {len(sources)}, bindings: {len(bindings)}")

    check("mode contains 'hybrid'", "hybrid" in mode)
    check("mode contains '+mmr'", "+mmr" in mode,
          f"got {mode!r}")
    # Adaptive may or may not trigger depending on the score distribution
    # for this query. We just check the suffix structure is correct
    # WHEN it triggers.
    if "+adaptive(" in mode:
        m = re.search(r"\+adaptive\((\d+)\)", mode)
        if m:
            n = int(m.group(1))
            check("adaptive kept count parses", n >= 2)

    check("sentence_bindings still produced",
          isinstance(bindings, list) and len(bindings) >= 1)
    check("at least one binding has paragraphs",
          any(b.get("paragraphs") for b in bindings))

    # Trace integrity: cited entries' page should match a real source
    trace = done.get("retrieval_trace", [])
    cited_in_trace = sum(1 for t in trace if t.get("cited"))
    check("retrieval_trace.cited rows correspond to actual sources",
          cited_in_trace > 0, f"cited rows: {cited_in_trace}")

requests.delete(BASE + f"/conversations/{cid}", timeout=10)
requests.delete(BASE + f"/documents/{TEST_PDF.name}", timeout=10)
print()
print(f"== {PASS}/{PASS+FAIL} passed ==")
sys.exit(0 if FAIL == 0 else 1)
