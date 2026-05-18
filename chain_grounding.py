"""
v0.6.0 — answer-sentence ↔ chunk-paragraph binding.

After the answer streams in with strict numeric [N] citations, we walk
each answer sentence and resolve its [N] tags to a specific paragraph
inside the cited chunk. The frontend renders these paragraphs as
hover-popover excerpts on the [N] chips — so the user gets the
NotebookLM-style "hover citation → see exact source paragraph" loop
without ever leaving the chat view.

Pipeline (per request, on the done-event hot path):
    answer text  →  split into sentences (en + zh)
                 →  for each sentence, extract its [N] tags
                 →  for each [N], look up the cited Document
                 →  split that chunk into paragraphs
                 →  score each paragraph vs the sentence (jaccard +
                    longest-common-substring), pick the best
                 →  emit (sentence, [{prompt_id, filename, page,
                                       paragraph, score}, ...])

All pure-Python. No embedding model calls — we already have the
chunks in memory and we're doing per-sentence string scoring on a
small candidate set (≤ 6 chunks, ≤ ~6 paragraphs each), so the
algorithmic version is plenty fast (single-digit milliseconds for a
1000-char answer).
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from langchain_core.documents import Document

logger = logging.getLogger("chain_grounding")


_INLINE_CITATION_RE = re.compile(r"\[(\d{1,3})\]")

# Sentence-end punctuation we recognise. Cantonese-period「。」, full-
# width !? included. We also accept the regular ascii enders. Soft
# enders (commas, semicolons) intentionally excluded -- they don't
# end a sentence semantically.
_SENT_END_CHARS = "。！？.!?"

# Tokens for jaccard scoring. We keep CJK characters as individual
# tokens (Chinese has no whitespace), and split latin tokens on
# non-word boundaries. Stop-word style filtering happens implicitly
# via the BM25/dense pipeline upstream -- here we just want to know
# how many surface tokens overlap between the answer sentence and a
# paragraph candidate.
_TOKEN_RE = re.compile(r"[A-Za-z0-9_]+|[一-鿿]", re.UNICODE)


@dataclass
class ParagraphBinding:
    """One sentence ↔ one cited chunk paragraph."""
    prompt_id: int
    filename: str
    page: Optional[int]
    paragraph: str
    score: float  # 0..1, higher is better


@dataclass
class SentenceBinding:
    """One answer sentence + the paragraphs each of its [N] tags
    resolved to. ``citations`` keeps the ids in encounter order for
    the frontend's chip-rendering pass."""
    text: str
    start: int          # char offset in answer
    end: int            # exclusive
    citations: List[int]
    paragraphs: List[ParagraphBinding]


# ---------------------------------------------------------------------------
# Sentence splitter
# ---------------------------------------------------------------------------
def split_sentences(text: str) -> List[Tuple[int, int, str]]:
    """Split ``text`` into ``(start, end, sentence)`` triples. ``end``
    is exclusive in ``text``. Sentences keep their trailing
    punctuation AND any trailing [N] tags (the model usually writes
    "...claim [1]." with the [N] before the period; the splitter walks
    past closing brackets attached to the punctuation).

    Bilingual: handles English-style "Cap. Then" and Chinese-style
    "...。下一句" alike. Short fragments (< 4 chars) get glued into the
    previous sentence so we don't emit "Yes." / "OK." as standalone
    rows (those have no useful binding anyway)."""
    if not text:
        return []

    out: List[Tuple[int, int, str]] = []
    n = len(text)
    i = 0
    start = 0
    # Step through every char; flush a sentence when we hit a
    # terminator that isn't part of a common abbreviation.
    while i < n:
        ch = text[i]
        if ch in _SENT_END_CHARS:
            # Abbreviation guard for English: "Mr." / "Dr." / "fig." /
            # "p.3", plus the trickier compounds "e.g." / "i.e." which
            # have TWO periods.
            if ch == ".":
                back = text[max(0, i - 6):i + 1].lower()
                # 0) Decimal numbers like "3.4" or "ρ = 0.303": digit
                # both sides of the period -- never a sentence end.
                if (
                    i > 0
                    and i + 1 < n
                    and text[i - 1].isdigit()
                    and text[i + 1].isdigit()
                ):
                    i += 1
                    continue
                # 1) Single-period abbrevs ending here.
                if re.search(
                    r"(?:^|\W)(?:e\.g|i\.e|mr|mrs|ms|dr|jr|sr|st|fig|p|pp)\.$",
                    back,
                ):
                    i += 1
                    continue
                # 2) First period of a compound abbrev (the "e." in
                # "e.g.", the "i." in "i.e."): peek ahead one char +
                # one period; if the previous char is a lone letter,
                # we're inside an abbrev. Avoids splitting "e.g. 7B"
                # at the first period.
                if i + 2 < n and text[i + 2] == ".":
                    nxt = text[i + 1]
                    prev_char = text[i - 1] if i > 0 else ""
                    prev_before = text[i - 2] if i > 1 else ""
                    if (
                        nxt.isalpha()
                        and len(nxt) == 1
                        and prev_char.isalpha()
                        and not prev_before.isalpha()
                    ):
                        i += 1
                        continue
            # Advance past closing punctuation that conventionally
            # sticks to the period: quotes, parens, brackets.
            j = i + 1
            while j < n and text[j] in '"\')]”’':
                j += 1
            # Trim leading whitespace from the snippet.
            sent = text[start:j]
            stripped = sent.strip()
            if stripped:
                # Glue tiny fragments into the previous sentence so
                # the binding loop has substance to score.
                if len(stripped) < 4 and out:
                    p_start, p_end, p_text = out[-1]
                    out[-1] = (p_start, j, text[p_start:j].strip())
                else:
                    out.append((start + (len(sent) - len(sent.lstrip())),
                                j, stripped))
            # Skip following whitespace before the next sentence.
            while j < n and text[j].isspace():
                j += 1
            start = j
            i = j
            continue
        i += 1
    # Trailing text without a terminator -- e.g. a final paragraph
    # that ended on a token instead of a period.
    if start < n:
        tail = text[start:n].strip()
        if tail:
            out.append((start + (len(text[start:n]) - len(text[start:n].lstrip())),
                        n, tail))
    return out


# ---------------------------------------------------------------------------
# Paragraph splitter (within a chunk)
# ---------------------------------------------------------------------------
def split_paragraphs(chunk_text: str) -> List[str]:
    """Slice a chunk into ``paragraph-ish`` units the popover can show
    one at a time. We prefer blank-line splits (the Docling-derived
    chunks tend to have them); when the chunk is dense / single-block,
    we fall back to splitting on Chinese-period / sentence boundaries
    and re-grouping into 200-400-char windows.

    Short fragments (< 40 chars) are merged forwards so we never emit
    a paragraph that's just a heading number or a stray symbol."""
    if not chunk_text:
        return []
    # Blank-line split first.
    rough = [p.strip() for p in re.split(r"\n\s*\n+", chunk_text) if p.strip()]
    if len(rough) >= 2:
        return _merge_short(rough, min_len=40)
    # Single-block fallback: re-group sentences into ~300-char windows.
    single = rough[0] if rough else chunk_text.strip()
    pieces: List[str] = []
    cur: List[str] = []
    cur_len = 0
    for _start, _end, sent in split_sentences(single):
        cur.append(sent)
        cur_len += len(sent)
        if cur_len >= 300:
            pieces.append(" ".join(cur))
            cur = []
            cur_len = 0
    if cur:
        pieces.append(" ".join(cur))
    return _merge_short(pieces or [single], min_len=40)


def _merge_short(paragraphs: List[str], *, min_len: int) -> List[str]:
    """Glue paragraphs shorter than ``min_len`` into their successor
    (or predecessor when they're the last item) so the popover never
    shows a one-word entry."""
    out: List[str] = []
    for p in paragraphs:
        if not p:
            continue
        if out and len(out[-1]) < min_len:
            out[-1] = (out[-1] + " " + p).strip()
        else:
            out.append(p)
    # Final-tail merge: if the last is too short and there's a
    # previous, merge backwards.
    if len(out) >= 2 and len(out[-1]) < min_len:
        tail = out.pop()
        out[-1] = (out[-1] + " " + tail).strip()
    return out


# ---------------------------------------------------------------------------
# Similarity scoring
# ---------------------------------------------------------------------------
def _tokens(text: str) -> List[str]:
    return [t.lower() for t in _TOKEN_RE.findall(text)]


def _jaccard(a_tokens: List[str], b_tokens: List[str]) -> float:
    """Standard Jaccard over token sets. Empty sets → 0."""
    if not a_tokens or not b_tokens:
        return 0.0
    sa, sb = set(a_tokens), set(b_tokens)
    inter = len(sa & sb)
    union = len(sa | sb)
    return inter / union if union else 0.0


def _longest_run_overlap(a: str, b: str) -> int:
    """Length of the longest 8+ char substring of ``a`` that appears
    verbatim in ``b``. Cheap proxy for "they actually share a phrase",
    catches exact-quote sentences that the token-set Jaccard washes
    out (because the sentence has too many discourse tokens)."""
    if len(a) < 8 or not b:
        return 0
    a_low = a.lower()
    b_low = b.lower()
    best = 0
    # Slide a window of decreasing size; first hit wins. Bounded at 64
    # because we're not trying to match a whole paragraph -- a short
    # phrase is sufficient evidence.
    for size in (64, 48, 32, 24, 16, 12, 8):
        if size > len(a_low):
            continue
        # Step quarter-window for speed.
        step = max(1, size // 4)
        for off in range(0, len(a_low) - size + 1, step):
            piece = a_low[off:off + size]
            if piece in b_low:
                best = size
                break
        if best:
            break
    return best


# Tokens that show up everywhere in academic prose and shouldn't
# dominate the Jaccard score. Tiny static list — full stop-words
# would slow things down without helping much, since the longest-
# run-overlap branch already handles the "real phrase match" signal.
_STOP_TOKENS = {
    "the", "a", "an", "and", "or", "but", "of", "in", "on", "for", "with",
    "to", "is", "are", "was", "were", "be", "been", "by", "that", "this",
    "these", "those", "it", "its", "as", "at", "from", "their", "they",
    "we", "our", "us", "you", "your", "i", "he", "she", "have", "has",
    "had", "do", "does", "did", "will", "would", "could", "should",
}

_ENTITY_RE = re.compile(
    # Capitalised word runs ("Privacy Paradox", "P3", "ChatGPT", "Lin")
    r"\b[A-Z][A-Za-z0-9]*\b"
    # Numbers / decimals / percentages ("0.303", "47", "20-30")
    r"|\b\d+(?:[.,-]\d+)?%?\b"
    # Chinese 2+ char runs — likely names / domain terms
    r"|[一-鿿]{2,}"
)

# v0.7.4: ``The``, ``A``, ``This`` etc. are commonly sentence-initial
# and slip through the cap-word regex as "entities". The entity_bonus
# in _score_paragraph then over-rewards their overlap (any two
# English paragraphs share "the") so genuinely unrelated sentences
# scored as moderate matches. Drop these from the entity set after
# extraction. Anything still ALL-CAPS or multi-cap-word ("ChatGPT",
# "WMT", "Lin Jiang") survives because we lowercase AFTER the
# regex pass.
_ENTITY_BLOCKLIST = frozenset({
    "the", "a", "an", "this", "that", "these", "those", "it",
    "its", "their", "they", "we", "our", "you", "your", "i",
    "he", "she", "him", "her",
    # Bare verbs that begin a sentence and get capitalised too:
    "is", "are", "was", "were", "be", "been", "being",
    "do", "does", "did", "doing",
    "have", "has", "had", "having",
    "in", "on", "at", "of", "for", "to", "with", "by",
})


def _entities(text: str) -> set:
    """Distinctive surface tokens that should carry more weight than
    common dictionary words when scoring sentence-vs-paragraph match.
    Picks proper-noun-looking runs (capitalised), numbers, percentages,
    and Chinese 2+ char sequences. Lowercased so 'P3' matches 'p3'.
    Common sentence-initial words ("The", "A", "This"…) are filtered
    via ``_ENTITY_BLOCKLIST`` so their cross-paragraph "match" doesn't
    poison the entity_bonus.
    """
    if not text:
        return set()
    out: set = set()
    for m in _ENTITY_RE.finditer(text):
        tok = m.group(0).lower()
        if tok in _ENTITY_BLOCKLIST:
            continue
        out.add(tok)
    return out


def _score_paragraph(sentence: str, paragraph: str) -> float:
    """0..1 score: weighted blend of Jaccard (sans stop-tokens),
    longest-run substring, and entity overlap. The shift from
    v0.6.0's pure-Jaccard-plus-tiny-run-bonus is that runs and
    entities now lead — they're the signals that actually correspond
    to "this sentence quotes / paraphrases this paragraph", whereas
    Jaccard noise from generic words ("the", "is", "and") used to
    let nearly-empty paragraphs win the auto-citation lottery."""
    s_tokens = [t for t in _tokens(sentence) if t not in _STOP_TOKENS]
    p_tokens = [t for t in _tokens(paragraph) if t not in _STOP_TOKENS]
    jaccard = _jaccard(s_tokens, p_tokens)
    # Exact-phrase match — a 16-char run is a strong signal, a 64-
    # char run is near-quote. Boost up to +0.5.
    run = _longest_run_overlap(sentence, paragraph)
    run_bonus = min(0.5, run / 50.0)
    # Entity overlap (numbers / proper nouns / CJK terms): if the
    # sentence mentions "P3" or "0.303" and the paragraph contains
    # them too, that's almost-conclusive evidence -- boost up to
    # +0.35.
    s_ent = _entities(sentence)
    p_ent = _entities(paragraph)
    entity_overlap = 0.0
    if s_ent:
        entity_overlap = len(s_ent & p_ent) / len(s_ent)
    entity_bonus = entity_overlap * 0.35
    return min(1.0, 0.4 * jaccard + run_bonus + entity_bonus)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
def bind_answer_to_sources(
    answer_text: str,
    cited_docs: List[Document],
    *,
    min_score: float = 0.05,
) -> List[SentenceBinding]:
    """Produce per-sentence bindings for the frontend popover layer.

    Skips sentences whose [N] tags don't resolve to anything in
    ``cited_docs`` (shouldn't happen after v0.6.0 sanitization, but
    defensive). Paragraphs with very low scores fall back to the
    first paragraph of the chunk so the popover always has SOMETHING
    -- showing the chunk's intro is far less misleading than a blank
    excerpt."""
    if not answer_text or not cited_docs:
        return []
    # Resolve [N] → doc once up front.
    by_id: Dict[int, Document] = {}
    for d in cited_docs:
        pid = (d.metadata or {}).get("prompt_id")
        if isinstance(pid, int):
            by_id[pid] = d
    # Cache per-chunk paragraph splits -- a chunk cited from multiple
    # sentences only needs to be split once.
    para_cache: Dict[int, List[str]] = {}

    out: List[SentenceBinding] = []
    for start, end, sent in split_sentences(answer_text):
        ids = []
        seen = set()
        for m in _INLINE_CITATION_RE.finditer(sent):
            try:
                n = int(m.group(1))
            except ValueError:
                continue
            if n in seen or n not in by_id:
                continue
            seen.add(n)
            ids.append(n)
        if not ids:
            out.append(SentenceBinding(
                text=sent, start=start, end=end, citations=[], paragraphs=[],
            ))
            continue
        bindings: List[ParagraphBinding] = []
        for pid in ids:
            doc = by_id[pid]
            if pid not in para_cache:
                para_cache[pid] = split_paragraphs(doc.page_content)
            paragraphs = para_cache[pid]
            if not paragraphs:
                continue
            scored = [
                (p, _score_paragraph(sent, p)) for p in paragraphs
            ]
            best_para, best_score = max(scored, key=lambda t: t[1])
            if best_score < min_score:
                # Fall back to the chunk's leading paragraph so the
                # popover isn't blank -- but tag the score honestly so
                # the UI can render it as "weak match" later.
                best_para = paragraphs[0]
            meta = doc.metadata or {}
            bindings.append(ParagraphBinding(
                prompt_id=pid,
                filename=str(meta.get("filename", "")),
                page=meta.get("page"),
                paragraph=best_para,
                score=round(best_score, 4),
            ))
        out.append(SentenceBinding(
            text=sent,
            start=start,
            end=end,
            citations=ids,
            paragraphs=bindings,
        ))
    return out


def bindings_to_payload(bindings: List[SentenceBinding]) -> List[dict]:
    """Serialise SentenceBinding list for the SSE done event. Flat
    dict shape the frontend can consume without any TS class plumbing."""
    return [
        {
            "text": b.text,
            "start": b.start,
            "end": b.end,
            "citations": b.citations,
            "paragraphs": [
                {
                    "prompt_id": p.prompt_id,
                    "filename": p.filename,
                    "page": p.page,
                    "paragraph": p.paragraph,
                    "score": p.score,
                }
                for p in b.paragraphs
            ],
        }
        for b in bindings
    ]


# ---------------------------------------------------------------------------
# v0.6.0: post-hoc citation injection
# ---------------------------------------------------------------------------
# Heuristic: discourse-only sentences ("In summary,", "It is important
# to note that...") should never get an auto-citation injected. These
# regex fragments catch the most common low-content openers in en + zh.
_DISCOURSE_PATTERNS = (
    r"^\s*(in summary|in conclusion|overall|to summarize|moreover|"
    r"furthermore|additionally|in addition|importantly|it is important|"
    r"however|on the other hand|in contrast|for example|for instance|"
    r"as mentioned|as noted|finally|first|second|third|lastly)\b",
    r"^\s*(总之|总而言之|此外|另外|同时|然而|相反|例如|比如|最后|首先|其次|再者|更重要的是)\b",
)

import re as _re_aux
_DISCOURSE_RE = _re_aux.compile(
    "|".join(_DISCOURSE_PATTERNS), _re_aux.IGNORECASE
)


def _is_factual_sentence(text: str) -> bool:
    """Heuristic gate before we attach an auto-citation to a sentence.
    Must be a reasonable length and not a pure discourse marker.

    v0.7.4: length floor dropped 30 → 20 so short factoids like
    ``"The model has 6 layers."`` (24 chars) become eligible. They
    were the most common "no citation at all" complaint."""
    s = (text or "").strip()
    if len(s) < 20:
        return False
    if _DISCOURSE_RE.match(s):
        return False
    return True


def _strip_trailing_period(s: str) -> tuple[str, str]:
    """Split a sentence into ``(body_without_trailing_punct, punct)``.
    Punct is the run of trailing terminators / quotes we'd want to put
    BACK after appending the [N] tag. Matches the splitter's
    end-of-sentence treatment so the inserted ``[N]`` ends up
    immediately before the period, like "...claim [1]."."""
    n = len(s)
    j = n
    while j > 0 and s[j - 1] in '"\')]”’':
        j -= 1
    end_chars = _SENT_END_CHARS  # local alias
    if j > 0 and s[j - 1] in end_chars:
        j -= 1
    return s[:j].rstrip(), s[j:]


def resolve_citations(
    answer_text: str,
    kept_docs: List[Document],
    *,
    auto_min_score: float = 0.15,
    override_margin: float = 1.15,
    model_cite_floor: float = 0.05,
    secondary_min_score: float = 0.18,
    hard_strip_score: float = 0.01,
) -> tuple[str, List[SentenceBinding]]:
    """v0.6.1 unified citation resolution.

    Earlier (v0.6.0) we ran two disjoint paths: "model cited [N] → trust
    everything blindly" or "model didn't cite → auto-inject". The blind
    trust meant a lazy model that stamped ``[1]`` after every sentence
    pinned the entire answer to chunk 1 even when chunks 2-5 were the
    real source. This routine merges both paths into a per-sentence
    verify-and-reroute:

    For every factual sentence:
      1. Score it against every kept chunk's best paragraph.
      2. Identify the chunk with the BEST score (best_pid).
      3. If the model wrote [N] for this sentence:
         - Score sentence against chunk N's best paragraph (model_score).
         - If best_score > auto_min_score AND
              best_pid != N AND
              best_score >= override_margin * model_score
           → REPLACE [N] with [best_pid].
           Else → keep the model's [N], bind to chunk N's best paragraph.
      4. If the model didn't cite this sentence and best_score >=
         auto_min_score: inject [best_pid] before the terminator.
      5. Otherwise: pass through, mark ungrounded.

    Net effect: the model's "[1] everywhere" laziness gets diversified
    automatically (sentences where chunk 3 is materially better than
    chunk 1 get rewritten), and the popover always shows the paragraph
    the binding actually scored highest on -- not just the chunk's
    leading paragraph.

    v0.7.4: thresholds loosened across the board because v0.7.3 users
    reported "often no citation at all" or "wrong citation".
    - ``auto_min_score``  0.22 → 0.15: paraphrases with low entity
      overlap were falling below the gate. 0.15 still excludes
      generic discourse hits.
    - ``override_margin`` 1.4 → 1.15: model "[1] everywhere" laziness
      now gets corrected when a different chunk scores even slightly
      better, not only when it's >40% better.
    - ``model_cite_floor`` (new, 0.05): if the model wrote [N] but
      [N] barely matches at all, treat it as no citation -- the
      model probably guessed. Falls back to auto-inject the best
      chunk if it's above ``auto_min_score``, else drops the chip.
      Catches the "completely wrong [N]" failure mode.
    """
    if not answer_text or not kept_docs:
        return answer_text, []

    # Resolve prompt_id -> doc up front.
    by_id: Dict[int, Document] = {}
    for d in kept_docs:
        pid = (d.metadata or {}).get("prompt_id")
        if isinstance(pid, int) and pid not in by_id:
            by_id[pid] = d
    if not by_id:
        return answer_text, []
    para_cache: Dict[int, List[str]] = {}

    def _best_para_for(pid: int, sent: str) -> tuple[Optional[str], float]:
        """Find the highest-scoring paragraph inside chunk ``pid`` for
        ``sent``. Returns (paragraph, score)."""
        doc = by_id.get(pid)
        if doc is None:
            return None, 0.0
        if pid not in para_cache:
            para_cache[pid] = split_paragraphs(doc.page_content)
        paragraphs = para_cache[pid]
        if not paragraphs:
            return None, 0.0
        best_p, best_s = paragraphs[0], -1.0
        for p in paragraphs:
            s = _score_paragraph(sent, p)
            if s > best_s:
                best_p, best_s = p, s
        return best_p, max(0.0, best_s)

    def _best_chunk_for(sent: str) -> tuple[Optional[int], Optional[str], float]:
        """Across all kept chunks, find the (pid, paragraph, score)
        triple that maximises the paragraph score for ``sent``."""
        best_pid: Optional[int] = None
        best_para: Optional[str] = None
        best_score: float = 0.0
        for pid in by_id:
            para, score = _best_para_for(pid, sent)
            if para is not None and score > best_score:
                best_pid, best_para, best_score = pid, para, score
        return best_pid, best_para, best_score

    sentences = split_sentences(answer_text)
    if not sentences:
        return answer_text, []

    # v0.7.3: SentenceBinding.start/end MUST point into the FINAL
    # ``answer_text`` we return, not the original. The previous code
    # used the input-text offsets, so any auto-injected [N] earlier in
    # the answer pushed every later sentence's actual position past
    # its recorded ``start``/``end``. The frontend's same-citation
    # dedup pass (``displayContent`` in ChatMessage.tsx) ``slice(start,
    # end)``s into the final text, so stale offsets meant it stripped
    # chips from the wrong sentences -- often leaving the duplicate
    # ``[N] [N] [N]`` run the dedup was supposed to collapse.
    #
    # ``out_offset`` tracks where the next ``new_parts`` push will land
    # in the final string. We snapshot it for every binding.
    new_parts: List[str] = []
    cursor = 0
    out_offset = 0
    out_bindings: List[SentenceBinding] = []

    def _emit(s: str) -> int:
        """Append ``s`` to ``new_parts``, return the start offset it
        lands at in the final string. Updates ``out_offset``."""
        nonlocal out_offset
        start_offset = out_offset
        new_parts.append(s)
        out_offset += len(s)
        return start_offset

    for start, end, sent in sentences:
        if start > cursor:
            _emit(answer_text[cursor:start])
        cursor = end

        # Parse [N] tags the model wrote inside this sentence.
        model_ids: List[int] = []
        seen: set = set()
        for m in _INLINE_CITATION_RE.finditer(sent):
            try:
                n = int(m.group(1))
            except ValueError:
                continue
            if n in seen or n not in by_id:
                continue
            seen.add(n)
            model_ids.append(n)

        # v0.7.5: bug spotted by the smoke-test grid — the literal
        # "[4]" tag in a sentence like "The sky is sunny [4]." was
        # being passed to ``_score_paragraph`` verbatim, which then
        # parsed "4" as an entity. Any chunk containing the digit 4
        # (a percentage, a year, a section number...) scored an
        # entity_bonus of 0.35 and the model_cite_floor never tripped,
        # so the wrong [N] survived. Score on a stripped copy of the
        # sentence so the citation tag itself never contributes to
        # the match. The original ``sent`` is still what gets emitted.
        scoring_sent = _INLINE_CITATION_RE.sub("", sent).strip() or sent

        # v0.7.5: bindings_for_ids builds paragraph bindings for every
        # in-scope prompt_id, used by every branch below + the
        # post-process orphan sweep. Centralises the "always have a
        # binding per surviving [N]" invariant the frontend needs to
        # avoid rendering disabled chips.
        def _bindings_for_ids(
            ids: List[int],
            scoring_sent: str,
        ) -> List[ParagraphBinding]:
            out_b: List[ParagraphBinding] = []
            for pid in ids:
                if pid not in by_id:
                    continue
                para, sc = _best_para_for(pid, scoring_sent)
                meta = (by_id[pid].metadata or {})
                out_b.append(ParagraphBinding(
                    prompt_id=pid,
                    filename=str(meta.get("filename", "")),
                    page=meta.get("page"),
                    paragraph=para or (
                        para_cache.get(pid, [""])[0] if pid in para_cache else ""
                    ),
                    score=round(sc, 4),
                ))
            return out_b

        # v0.7.6: weak-secondary filter. When the model writes
        # ``[1][2][3]`` the primary [1] is the one it committed to;
        # the secondaries are usually "this also applies" padding.
        # When a secondary's paragraph score is below
        # ``secondary_min_score`` the chip's popover would show a
        # paragraph that doesn't really back the claim — the user
        # clicks expecting evidence and gets a weakly-related blurb.
        # Probe results on the Attention paper showed exactly this:
        # ``[1][2]`` reroute kept a 0.177-score secondary [2] whose
        # paragraph mentioned attention generically but not the
        # specific O(1) claim. We strip those secondaries (the
        # primary always survives — we never leave the sentence
        # un-cited). Returns ``(stripped_text, kept_ids)``.
        def _filter_weak_secondaries(
            text: str,
            ids: List[int],
            scoring_text: str,
        ) -> tuple[str, List[int]]:
            if len(ids) <= 1:
                return text, ids
            keep = [ids[0]]
            dropped: List[int] = []
            for pid in ids[1:]:
                _, sc = _best_para_for(pid, scoring_text)
                if sc >= secondary_min_score:
                    keep.append(pid)
                else:
                    dropped.append(pid)
            if dropped:
                # Strip dropped [pid] tags (and any leading space)
                # from the text. Use a word-boundary look-ahead so
                # "[1]" doesn't accidentally strip the "[" of "[12]".
                for pid in dropped:
                    text = re.sub(
                        rf" ?\[{pid}\](?!\d)", "", text,
                    )
            return text, keep

        if not _is_factual_sentence(sent):
            # v0.7.5: a non-factual sentence may still carry a model-
            # written [N] (e.g. a section header line). Build paragraph
            # bindings for any valid model_ids so the frontend's hover
            # popover works on those chips too. We DON'T auto-inject
            # for non-factual sentences — that'd add chips to greetings
            # and section headers.
            sent_start = _emit(sent)
            out_bindings.append(SentenceBinding(
                text=sent, start=sent_start, end=sent_start + len(sent),
                citations=list(model_ids),
                paragraphs=_bindings_for_ids(model_ids, scoring_sent),
            ))
            continue

        best_pid, best_para, best_score = _best_chunk_for(scoring_sent)

        # v0.7.4 / v0.7.5 / v0.7.6 — three-tier model_cite_floor.
        # The goal is to strip citations the model clearly got wrong
        # while keeping legitimate paraphrases + cross-language pairs.
        #
        # Tier A (hard_strip): primary_score < 0.01 — literally no
        #   token, run, or entity overlap between sentence and cited
        #   chunk. ``capital of France [4]`` style. Always strip; no
        #   linguistic case (paraphrase / cross-language) produces a
        #   zero — a Chinese sentence vs an English chunk still
        #   shares ``AI`` / ``GDPR`` / ``2018`` / digits.
        # Tier B (model_cite_floor + best_score check, 0.05): primary
        #   was a poor match BUT some other chunk also matches
        #   poorly. Trust the model — typical "no chunk really
        #   matches because the user paraphrased heavily" case.
        # Tier C (model_cite_floor + a better alternative): primary
        #   was poor AND another chunk is materially better — model
        #   picked the wrong neighbour. Strip + let the auto-inject
        #   pick the better chunk.
        if model_ids:
            _, _primary_score = _best_para_for(model_ids[0], scoring_sent)
            if _primary_score < hard_strip_score:
                # Tier A — definitely wrong, no signal at all.
                sent = re.sub(r" ?\[\d+\]", "", sent)
                model_ids = []
            elif (
                _primary_score < model_cite_floor
                and best_score >= model_cite_floor
            ):
                # Tier C — wrong; auto-inject will pick the right one.
                sent = re.sub(r" ?\[\d+\]", "", sent)
                model_ids = []

        if model_ids:
            # Verify: how well does sentence actually match the model's
            # FIRST cited chunk? (We resolve to first-cited only -- if
            # the model also wrote [2][3] we'll keep them as additional
            # citations attached to their own best paragraphs.)
            primary = model_ids[0]
            model_para, model_score = _best_para_for(primary, scoring_sent)
            # v0.7.5 sub-fix: if the model already cited best_pid as a
            # secondary id ([primary][best_pid][...]) the override would
            # duplicate best_pid (text becomes "[best_pid][best_pid]
            # [...]"), giving the reader two visually-identical adjacent
            # chips. Trust the model in that case — it knew best_pid
            # was relevant; the primary chip stays in case the model's
            # ordering was deliberate.
            best_already_cited = (
                best_pid is not None and best_pid in model_ids
            )
            # Reroute if a different chunk is materially better.
            if (
                best_pid is not None
                and best_pid != primary
                and not best_already_cited
                and best_score >= auto_min_score
                and best_score >= override_margin * max(model_score, 0.01)
            ):
                # Rewrite the sentence's [primary] → [best_pid] (only
                # the first [N] gets rewritten; secondary citations
                # stay in the text AND keep their own bindings so
                # they render as live chips, not disabled).
                body_re = re.compile(r"\[(\d{1,3})\]")
                replaced = False

                def _swap(match: re.Match) -> str:
                    nonlocal replaced
                    if replaced:
                        return match.group(0)
                    try:
                        if int(match.group(1)) == primary:
                            replaced = True
                            return f"[{best_pid}]"
                    except ValueError:
                        pass
                    return match.group(0)

                new_sent = body_re.sub(_swap, sent)
                # v0.7.5: bindings now cover BOTH best_pid (the
                # rerouted primary) AND any secondary model_ids that
                # survived in new_sent — those chips would otherwise
                # render as disabled because the binding list omitted
                # them. citations stays in encounter order in the
                # rewritten text.
                rewritten_ids: List[int] = [best_pid]
                for sid in model_ids[1:]:
                    if sid in by_id and sid != best_pid:
                        rewritten_ids.append(sid)
                # v0.7.6: drop weak secondaries from the rewritten
                # sentence so a multi-cite "[best][weak]" doesn't
                # render a chip whose popover content doesn't back
                # the sentence.
                clean_scoring_sent = (
                    _INLINE_CITATION_RE.sub("", new_sent).strip()
                    or new_sent
                )
                new_sent, rewritten_ids = _filter_weak_secondaries(
                    new_sent, rewritten_ids, clean_scoring_sent,
                )
                sent_start = _emit(new_sent)
                out_bindings.append(SentenceBinding(
                    text=new_sent,
                    start=sent_start, end=sent_start + len(new_sent),
                    citations=rewritten_ids,
                    paragraphs=_bindings_for_ids(
                        rewritten_ids, clean_scoring_sent,
                    ),
                ))
            else:
                # Keep model's citations. v0.7.6: drop weak
                # secondaries first so the rendered text only carries
                # chips whose paragraph actually backs the sentence.
                sent, model_ids = _filter_weak_secondaries(
                    sent, model_ids, scoring_sent,
                )
                sent_start = _emit(sent)
                bindings: List[ParagraphBinding] = []
                for pid in model_ids:
                    para, sc = _best_para_for(pid, scoring_sent)
                    meta = (by_id[pid].metadata or {})
                    bindings.append(ParagraphBinding(
                        prompt_id=pid,
                        filename=str(meta.get("filename", "")),
                        page=meta.get("page"),
                        paragraph=para or (
                            para_cache.get(pid, [""])[0] if pid in para_cache else ""
                        ),
                        score=round(sc, 4),
                    ))
                out_bindings.append(SentenceBinding(
                    text=sent, start=sent_start, end=sent_start + len(sent),
                    citations=model_ids,
                    paragraphs=bindings,
                ))
        else:
            # No model citation -- auto-inject if the best chunk is a
            # convincing match. v0.6.1 raises the threshold from 0.18
            # to 0.22 so weakly-matched chunks stop sneaking onto
            # discourse / topic-changer sentences.
            if (
                best_pid is None
                or best_para is None
                or best_score < auto_min_score
            ):
                sent_start = _emit(sent)
                out_bindings.append(SentenceBinding(
                    text=sent, start=sent_start, end=sent_start + len(sent),
                    citations=[], paragraphs=[],
                ))
                continue
            body, tail = _strip_trailing_period(sent)
            injected = f"{body} [{best_pid}]{tail}"
            sent_start = _emit(injected)
            meta = (by_id[best_pid].metadata or {})
            out_bindings.append(SentenceBinding(
                text=injected,
                start=sent_start, end=sent_start + len(injected),
                citations=[best_pid],
                paragraphs=[ParagraphBinding(
                    prompt_id=best_pid,
                    filename=str(meta.get("filename", "")),
                    page=meta.get("page"),
                    paragraph=best_para,
                    score=round(best_score, 4),
                )],
            ))

    if cursor < len(answer_text):
        _emit(answer_text[cursor:])
    final_text = "".join(new_parts)

    # v0.7.5: orphan-chip sweep. The frontend's CitationChip renders
    # ``binding === undefined`` as a disabled (dashed-border, muted)
    # chip — exactly the "all citations show up as 失效" symptom the
    # user reported in v0.7.4. We can hit that state through subtle
    # paths: e.g. the model wrote ``[2]`` inside a non-factual
    # sentence where we previously emitted ``paragraphs=[]``, or the
    # override branch left a secondary id in the rewritten text
    # without a binding. The branches above were patched, but the
    # invariant deserves a belt-and-braces final sweep — walk the
    # FINAL text, find every surviving ``[N]``, and ensure each one
    # has at least one binding in ``out_bindings``. Anything still
    # orphaned gets a synthetic zero-length SentenceBinding at the
    # tail carrying the missing ParagraphBindings.
    # First, strip any ``[N]`` whose id is not in ``by_id`` at all.
    # ``sanitize_answer_citations`` upstream gates on ``len(all_docs)``,
    # but if a caller passes a kept_docs subset whose prompt_ids don't
    # cover the full sanitised range we'd otherwise leave dangling
    # chips that render as "disabled" placeholders. Belt and braces.
    if by_id:
        def _drop_unknown(m: re.Match) -> str:
            try:
                n = int(m.group(1))
            except ValueError:
                return ""
            return m.group(0) if n in by_id else ""

        stripped = _INLINE_CITATION_RE.sub(_drop_unknown, final_text)
        if stripped != final_text:
            # Tidy "claim ." -> "claim." and squeezed double spaces.
            stripped = re.sub(r" +([.,;:!?。！？])", r"\1", stripped)
            stripped = re.sub(r"  +", " ", stripped)
            final_text = stripped

    bound_ids: set = set()
    for sb in out_bindings:
        for pb in sb.paragraphs:
            bound_ids.add(pb.prompt_id)
    orphans: List[int] = []
    seen_orphan: set = set()
    for m in _INLINE_CITATION_RE.finditer(final_text):
        try:
            n = int(m.group(1))
        except ValueError:
            continue
        if n in by_id and n not in bound_ids and n not in seen_orphan:
            seen_orphan.add(n)
            orphans.append(n)
    if orphans:
        tail_para_bindings: List[ParagraphBinding] = []
        for pid in orphans:
            # Score against the whole answer for a "best paragraph"
            # guess — better than a blank popover. The chip itself
            # doesn't use the score, only the paragraph text +
            # filename + page, so a coarse pick is fine here.
            para, sc = _best_para_for(pid, final_text)
            meta = (by_id[pid].metadata or {})
            tail_para_bindings.append(ParagraphBinding(
                prompt_id=pid,
                filename=str(meta.get("filename", "")),
                page=meta.get("page"),
                paragraph=para or (
                    para_cache.get(pid, [""])[0] if pid in para_cache else ""
                ),
                score=round(sc, 4),
            ))
        out_bindings.append(SentenceBinding(
            text="",
            start=len(final_text),
            end=len(final_text),
            citations=[],
            paragraphs=tail_para_bindings,
        ))

    return final_text, out_bindings


def filter_kept_docs_to_cited(
    kept_docs: List[Document],
    answer_text: str,
) -> List[Document]:
    """Final source-list contract: a doc shows up as a source iff its
    prompt_id appears in the (possibly auto-rewritten) answer's
    [N] tags. Deduped and ordered by first appearance."""
    if not answer_text:
        return []
    try:
        from llm_chain import parse_inline_citations as _pic  # type: ignore
        ids = _pic(answer_text)
    except Exception:
        ids = []
    by_id: Dict[int, Document] = {}
    for d in kept_docs:
        pid = (d.metadata or {}).get("prompt_id")
        if isinstance(pid, int) and pid not in by_id:
            by_id[pid] = d
    out: List[Document] = []
    seen: set = set()
    for n in ids:
        if n in by_id and n not in seen:
            out.append(by_id[n])
            seen.add(n)
    return out
