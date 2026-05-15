"""End-to-end v0.5.0 smoke test. Hits the running uvicorn (default :8765)
and exercises every new feature:
- upload PDF
- has_raw stamping
- /raw endpoint serves the right bytes
- chat with hybrid retrieval (verify trace + source channel tagging)
- multi-variant regenerate (variant_done event + active_variant flip)
- session-memory persistence (via conv JSON inspection)

Run after `.venv/Scripts/python.exe -m uvicorn server.main:app --port 8765`
is up. Cleans up after itself (deletes uploaded test PDF + conv).
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from typing import Iterator

import requests

BASE = "http://localhost:8200/api"
ROOT = Path(__file__).resolve().parent
TEST_PDF = ROOT / "Lin-Jiang-AIchatbotPrivacy.pdf"
PASS = 0
FAIL = 0


def check(name: str, cond: bool, info: str = "") -> None:
    global PASS, FAIL
    mark = "PASS" if cond else "FAIL"
    extra = f"  -- {info}" if info else ""
    print(f"  {mark}  {name}{extra}", flush=True)
    if cond:
        PASS += 1
    else:
        FAIL += 1


def sse_lines(resp: requests.Response) -> Iterator[tuple[str, dict]]:
    """Tiny SSE parser. Yields (event_name, json_payload) tuples."""
    event = "message"
    data: list[str] = []
    for raw_b in resp.iter_lines(decode_unicode=False):
        if raw_b is None:
            continue
        raw = raw_b.decode("utf-8", errors="replace") if isinstance(raw_b, bytes) else raw_b
        if raw == "":
            if data:
                payload_text = "\n".join(data)
                try:
                    payload = json.loads(payload_text)
                except Exception:
                    payload = payload_text
                yield event, payload
            event = "message"
            data = []
            continue
        if raw.startswith(":"):
            continue
        if raw.startswith("event:"):
            event = raw[6:].strip()
        elif raw.startswith("data:"):
            data.append(raw[5:].lstrip())


def main() -> int:
    if not TEST_PDF.exists():
        print(f"FATAL: {TEST_PDF} not found")
        return 2

    print(f"== v0.5.0 smoke test ==")
    print(f"   PDF: {TEST_PDF.name}")
    print()

    # ---------------------------------------------------------------- 1. upload
    print("1. Upload PDF")
    with open(TEST_PDF, "rb") as f:
        # `replace=true` keeps the test isolated from any pre-existing
        # index state (none in our case, but defensive).
        r = requests.post(
            f"{BASE}/documents/upload",
            files=[("files", (TEST_PDF.name, f, "application/pdf"))],
            data={"replace": "true"},
            stream=True,
            timeout=600,
        )
    if r.status_code != 200:
        check("upload returned 200", False, f"got {r.status_code}: {r.text[:200]}")
        return 1
    file_done_seen = False
    all_done_seen = False
    chunks_indexed = 0
    for event, payload in sse_lines(r):
        if event == "file_done":
            file_done_seen = True
            chunks_indexed = payload.get("chunks", 0)
        elif event == "all_done":
            all_done_seen = True
    check("upload SSE produced file_done", file_done_seen)
    check("upload SSE produced all_done", all_done_seen)
    check("at least one chunk indexed", chunks_indexed > 0, f"chunks={chunks_indexed}")

    # --------------------------------------------------------- 2. list documents
    print("\n2. List documents -- expect has_raw=true on uploaded PDF")
    r = requests.get(f"{BASE}/documents", timeout=10)
    check("/api/documents -> 200", r.status_code == 200)
    j = r.json()
    found = next((f for f in j.get("files", []) if f["filename"] == TEST_PDF.name), None)
    check("uploaded file appears in listing", found is not None)
    if found:
        check("has_raw=true on uploaded PDF", bool(found.get("has_raw")),
              f"got has_raw={found.get('has_raw')}")
        check("source_kind=uploaded", found.get("source_kind") == "uploaded")

    # ------------------------------------------------------ 3. raw bytes match
    print("\n3. /raw streams identical bytes")
    r = requests.get(f"{BASE}/documents/{TEST_PDF.name}/raw", timeout=30)
    check("/raw -> 200", r.status_code == 200)
    check("/raw content-type application/pdf",
          r.headers.get("content-type") == "application/pdf")
    on_disk = TEST_PDF.read_bytes()
    check("/raw bytes match original", r.content == on_disk,
          f"sizes raw={len(r.content)} disk={len(on_disk)}")

    # ------------------------------------------------------ 4. raw path hardening
    print("\n4. /raw rejects path traversal")
    for bad in ["..\\..\\windows", "../etc/passwd"]:
        # FastAPI's path matcher catches some of these as 404; explicit
        # traversal patterns we sanitize ourselves.
        r = requests.get(f"{BASE}/documents/{bad}/raw", timeout=5)
        check(f"/raw rejects {bad!r}", r.status_code in (400, 404),
              f"got {r.status_code}")
    r = requests.get(f"{BASE}/documents/nonexistent.pdf/raw", timeout=5)
    check("/raw returns 404 for missing file", r.status_code == 404)

    # ------------------------------------------------------ 5. create conv
    print("\n5. Create conversation + first chat turn (hybrid retrieval)")
    # NOTE: we deliberately leave `title` empty so the auto-title
    # generator fires on the first chat turn (that's what the SSE
    # `title` event in step 6 exercises).
    r = requests.post(f"{BASE}/conversations", json={}, timeout=10)
    check("create conv -> 200", r.status_code == 200)
    conv_id = r.json()["id"]
    print(f"   conv_id = {conv_id}")

    # -------------------------------------- 6. first chat turn + verify hybrid
    print("\n6. POST /api/chat (hybrid retrieval + trace tagging)")
    r = requests.post(
        f"{BASE}/chat",
        json={
            "question": "What does this paper say about student privacy?",
            "conversation_id": conv_id,
        },
        stream=True,
        timeout=600,
    )
    check("/chat returned 200", r.status_code == 200, f"got {r.status_code}")
    done_payload = None
    title_emitted = False
    sources_event_seen = False
    for event, payload in sse_lines(r):
        if event == "done":
            done_payload = payload
        elif event == "sources":
            sources_event_seen = True
        elif event == "title":
            title_emitted = True
    check("sources SSE event seen", sources_event_seen)
    check("done SSE event seen", done_payload is not None)
    check("title event emitted on first turn", title_emitted)
    if done_payload:
        answer = (done_payload.get("answer") or "")
        check("answer non-empty", len(answer.strip()) > 0,
              f"len={len(answer)}")
        check("retrieval_mode includes 'hybrid'",
              "hybrid" in (done_payload.get("retrieval_mode", "") or ""),
              f"got {done_payload.get('retrieval_mode')!r}")
        sources = done_payload.get("source_documents") or []
        check("at least one source document cited",
              len(sources) > 0, f"n={len(sources)}")
        trace = done_payload.get("retrieval_trace") or []
        check("retrieval_trace populated",
              len(trace) > 0, f"n={len(trace)}")
        if trace:
            sources_set = {(t.get("source") or "") for t in trace}
            print(f"   trace source channels seen: {sorted(sources_set)}")
            # In hybrid mode we expect at least one of dense/bm25/both
            # to show up across the candidate set.
            ok = bool(sources_set & {"dense", "bm25", "both", "memory"})
            check("trace entries tagged with channel source", ok,
                  f"got {sources_set}")

    # ----------------------------------- 7. regenerate -> append variant
    print("\n7. Regenerate -> new variant on tail assistant message")
    r = requests.post(
        f"{BASE}/chat",
        json={
            "question": "",  # ignored in regenerate mode
            "conversation_id": conv_id,
            "regenerate": True,
        },
        stream=True,
        timeout=600,
    )
    check("/chat (regenerate) -> 200", r.status_code == 200)
    variant_done = None
    regen_answer = ""
    for event, payload in sse_lines(r):
        if event == "done":
            regen_answer = payload.get("answer", "")
        elif event == "variant_done":
            variant_done = payload
    check("variant_done SSE event seen", variant_done is not None)
    if variant_done:
        check("variant_done reports message_index >= 1",
              variant_done.get("message_index", 0) >= 1)
        check("variant_done variant_count == 2",
              variant_done.get("variant_count") == 2,
              f"got {variant_done}")
        check("regenerated answer non-empty",
              len(regen_answer.strip()) > 0)

    # ------------------------------- 8. GET conv -> variants persisted
    print("\n8. GET conv -> 2 variants on assistant message")
    r = requests.get(f"{BASE}/conversations/{conv_id}", timeout=10)
    check("GET conv -> 200", r.status_code == 200)
    msgs = r.json().get("messages", [])
    check("conversation has 2 messages", len(msgs) == 2,
          f"got {len(msgs)}")
    if len(msgs) >= 2:
        asst = msgs[1]
        variants = asst.get("variants") or []
        check("assistant msg has 2 variants", len(variants) == 2,
              f"got {len(variants)}")
        check("active_variant points at the new one",
              asst.get("active_variant") == 1,
              f"got {asst.get('active_variant')}")
        if variants and len(variants) >= 1:
            v0 = variants[0]
            check("variant 0 carries sources",
                  isinstance(v0.get("sources"), list) and len(v0["sources"]) > 0)
            check("variant 0 carries retrieval_trace",
                  isinstance(v0.get("retrieval_trace"), list))

    # ------------------------------ 9. PATCH active_variant -> flip back
    print("\n9. PATCH active_variant -> 0 (flip back)")
    r = requests.patch(
        f"{BASE}/conversations/{conv_id}/messages/1/active_variant",
        json={"variant_index": 0},
        timeout=10,
    )
    check("PATCH active_variant -> 200", r.status_code == 200)
    if r.status_code == 200:
        asst = r.json()["messages"][1]
        check("active_variant flipped to 0",
              asst.get("active_variant") == 0)

    # ------------------------------- 10. session memory written to disk
    print("\n10. Session memory persisted on disk")
    p = ROOT / "chat_history" / f"{conv_id}.json"
    check("chat_history JSON exists", p.exists())
    if p.exists():
        data = json.loads(p.read_text(encoding="utf-8"))
        check("recent_chunks recorded",
              isinstance(data.get("recent_chunks"), list)
              and len(data["recent_chunks"]) > 0,
              f"got {len(data.get('recent_chunks', []) or [])}")

    # ------------------------------------------------------ 11. cleanup
    print("\n11. Cleanup")
    r = requests.delete(f"{BASE}/conversations/{conv_id}", timeout=5)
    check("delete conv -> 200", r.status_code == 200)
    r = requests.delete(f"{BASE}/documents/{TEST_PDF.name}", timeout=5)
    check("delete document -> 200", r.status_code == 200)
    raw_after = ROOT / "data" / "uploads" / TEST_PDF.name
    check("raw PDF unlinked from disk", not raw_after.exists())

    print()
    print(f"== {PASS}/{PASS + FAIL} passed ==")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
