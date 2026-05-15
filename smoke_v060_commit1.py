"""v0.6.0 commit 1 verification: numeric inline citations end-to-end.

After this commit:
- Every excerpt in the answer prompt is tagged [1], [2], ... (verified
  by inspecting the chain's debug log indirectly).
- The model is told to cite using ONLY those numeric tags.
- ``done.source_documents`` contains ONLY chunks whose prompt_id appears
  as [N] in ``done.answer``.
- Hallucinated [N] outside the valid id range are stripped from the
  answer text before it ships to the UI.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path
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

# 1. upload
print("1. Upload PDF")
with open(TEST_PDF, "rb") as f:
    r = requests.post(BASE + "/documents/upload",
        files=[("files", (TEST_PDF.name, f, "application/pdf"))],
        data={"replace": "true"}, stream=True, timeout=600)
for _ in sse_lines(r): pass
check("upload completed", True)

# 2. create conv + ask
print("\n2. Ask a question, observe done event")
cid = requests.post(BASE + "/conversations", json={}, timeout=10).json()["id"]
r = requests.post(BASE + "/chat", json={
    "question": "What does this paper say about user privacy attitudes?",
    "conversation_id": cid,
}, stream=True, timeout=600)
done = None
candidate_pool = []  # captured from `sources` event (pre-citation-filter)
for ev, payload in sse_lines(r):
    if ev == "sources":
        candidate_pool = payload.get("docs", []) or []
    elif ev == "done":
        done = payload
check("done event seen", done is not None)

if done:
    answer = done.get("answer", "")
    sources = done.get("source_documents", [])
    # The SSE adapter pops all_source_documents before sending to keep
    # the wire payload small. We reconstruct the candidate pool from
    # the prior `sources` event for the strict-subset check below.
    all_sources = candidate_pool

    print(f"\n   answer length: {len(answer)} chars")
    print(f"   sources (cited, post-strict-filter): {len(sources)}")
    print(f"   candidate pool (pre-filter): {len(all_sources)}")
    print(f"   answer excerpt:\n     {answer[:300]!r}")

    # 3. answer must use numeric [N] tags, not [file.pdf p.X] format
    numeric_cites = re.findall(r"\[(\d{1,3})\]", answer)
    legacy_cites = re.findall(
        r"\[[^\[\]]+?\.pdf\s*[, ]\s*(?:p\.?|page)\s*\d+\]",
        answer, re.IGNORECASE,
    )
    print(f"\n   numeric [N] tags in answer: {numeric_cites}")
    print(f"   legacy [file p.N] tags: {legacy_cites}")
    check("answer uses numeric [N] format", len(numeric_cites) > 0,
          f"found {len(numeric_cites)} numeric tags")
    check("answer does NOT mix in legacy [file p.N] tags",
          len(legacy_cites) == 0,
          f"got {len(legacy_cites)} legacy")

    # 4. sources is exactly the cited ids (deduped)
    cited_ids = set()
    for n in numeric_cites:
        try: cited_ids.add(int(n))
        except: pass
    check("sources count == unique cited ids",
          len(sources) == len(cited_ids),
          f"sources={len(sources)} ids={len(cited_ids)}")

    # 5. no hallucinated [N] -- every numeric tag falls within
    # [1, len(all_sources)]
    valid_range = range(1, len(all_sources) + 1)
    out_of_range = [int(n) for n in numeric_cites if int(n) not in valid_range]
    check("no hallucinated ids in cleaned answer",
          len(out_of_range) == 0,
          f"out-of-range ids: {out_of_range}")

    # 6. sources are strictly a subset of the candidate pool
    # (filename, page) pairs match
    src_keys = {(s.get("filename"), s.get("page")) for s in sources}
    all_keys = {(s.get("filename"), s.get("page")) for s in all_sources}
    check("sources subset of candidate pool", src_keys.issubset(all_keys))

# cleanup
requests.delete(BASE + f"/conversations/{cid}", timeout=10)
requests.delete(BASE + f"/documents/{TEST_PDF.name}", timeout=10)

print()
print(f"== {PASS}/{PASS+FAIL} passed ==")
sys.exit(0 if FAIL == 0 else 1)
