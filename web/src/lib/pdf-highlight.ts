/**
 * v0.5.0 — chunk-text fuzzy matching for the PDF citation viewer.
 *
 * The chunk's `snippet` arrives from the server as a cleaned-up string:
 * Docling normalised whitespace, joined hyphenated line-breaks, dropped
 * page-header/footer noise, etc. The text-layer pdf.js builds, on the
 * other hand, follows whatever glyph order the PDF actually contains
 * -- soft hyphens included, lines split mid-word at column breaks,
 * occasional kerning splits inside a single token.
 *
 * To highlight the chunk inside the rendered page we have to:
 *   1. Concatenate every text-layer span into a single page string
 *      while remembering which span each character came from.
 *   2. Normalise both the page string and the chunk snippet to a
 *      common form: lowercase + collapsed whitespace + de-hyphenated
 *      line breaks. We keep an index map from each normalised char
 *      back to its origin character in the un-normalised page string.
 *   3. Substring-search the normalised chunk inside the normalised
 *      page text. If that misses (Docling re-flowed too aggressively,
 *      or the chunk crosses a page boundary), fall back to matching
 *      a long token bigram from the chunk's leading/middle/trailing
 *      window.
 *   4. Translate the matched normalised range back to spans via the
 *      index map and tag each touched span with a class.
 *
 * The whole thing runs synchronously on a single page's worth of text
 * (a few KB at most), so we don't worry about perf -- correctness is
 * what matters, since users will spot a mis-highlighted chunk
 * instantly.
 */

/** Build a single concatenated text-layer string plus a per-span
 *  offset table. ``offsets[i]`` is the start position of span ``i``'s
 *  text inside ``fullText``; ``offsets[spans.length]`` is the total
 *  length, so a half-open range ``[a, b)`` falls into span ``i`` iff
 *  ``offsets[i] <= a < offsets[i+1]``. We insert a single space
 *  between spans so two glyph-groups joined by pdf.js don't visually
 *  bleed into one another when we substring-match. */
export function buildPageText(spans: HTMLElement[]): {
  fullText: string
  offsets: number[]
} {
  const parts: string[] = []
  const offsets: number[] = []
  let cursor = 0
  for (const s of spans) {
    offsets.push(cursor)
    const t = s.textContent ?? ""
    parts.push(t)
    cursor += t.length
    // Inter-span separator (pdf.js doesn't always emit whitespace
    // between spans even when they're visually distinct words).
    parts.push(" ")
    cursor += 1
  }
  offsets.push(cursor)
  return { fullText: parts.join(""), offsets }
}

/** Lowercase + collapse runs of whitespace + drop hyphen-before-
 *  whitespace (common at column / page breaks in PDFs). Returns the
 *  normalised string plus a map ``origIndex[i]`` = the index in the
 *  original string that produced normalised character ``i``. The
 *  inverse map is what lets a match range hop back to spans without
 *  losing positional accuracy. */
export function normalize(text: string): {
  norm: string
  origIndex: number[]
} {
  const out: string[] = []
  const map: number[] = []
  let prevWasSpace = false
  for (let i = 0; i < text.length; i++) {
    const ch = text[i]
    // Hyphen immediately followed by whitespace = line-break
    // hyphenation -- drop the hyphen AND consume the whitespace run
    // that follows so 'extreme-\n ly' fuses into 'extremely', not
    // 'extreme ly'.
    if (ch === "-" || ch === "­" /* soft hyphen */) {
      const next = text[i + 1]
      if (next && /\s/.test(next)) {
        let j = i + 1
        while (j < text.length && /\s/.test(text[j])) j++
        i = j - 1 // -1 because the for-loop's i++ moves us past `j-1`
        prevWasSpace = false
        continue
      }
    }
    if (/\s/.test(ch)) {
      if (prevWasSpace) continue
      out.push(" ")
      map.push(i)
      prevWasSpace = true
    } else {
      out.push(ch.toLowerCase())
      map.push(i)
      prevWasSpace = false
    }
  }
  return { norm: out.join(""), origIndex: map }
}

/** Minimum window length we'll try when sliding short fragments of
 *  the chunk across the page text. Going much shorter than this
 *  produces false positives on common phrases ("at the end of", "as
 *  shown in", etc.) -- 24 is a sweet spot in practice. */
const MIN_WINDOW_CHARS = 24

/** How far we'll tolerate page text drifting from chunk text during
 *  greedy bidirectional extension. We count mismatches in a sliding
 *  window; once `EXTEND_MISMATCH_RATIO` of the recent
 *  `EXTEND_WINDOW_LEN` characters disagree, we stop growing in that
 *  direction. The numbers are picked so a reflow that drops a comma
 *  or rejoins a hyphenated word doesn't abort the extension, but a
 *  jump to an unrelated paragraph does. */
const EXTEND_WINDOW_LEN = 24
const EXTEND_MISMATCH_RATIO = 0.4

/** Hard cap on how far the bidirectional extension can grow past the
 *  anchor seed: the chunk's length scaled by this factor. PDF text
 *  layers in multi-column layouts are flattened into linear text by
 *  pdf.js, so a naive greedy walk easily strays into the adjacent
 *  column once the chunk's end is past. Capping at ~1.25x chunk
 *  length keeps the highlight tight without truncating chunks that
 *  Docling lightly re-flowed (a bit shorter than the page rendering). */
const MAX_EXTENSION_RATIO = 1.25

/** Expand a confirmed anchor match outwards in both directions so the
 *  highlight covers the full chunk paragraph, not just the seed
 *  window. We step character-by-character and tolerate a moderate
 *  amount of drift -- Docling reflow drops whitespace, joins
 *  hyphenated breaks, and occasionally re-orders punctuation, all of
 *  which should NOT cut the highlight short. Hard mismatch (the page
 *  has wandered into a different paragraph) terminates extension. A
 *  hard length cap prevents the walk from straying past the chunk
 *  text into adjacent columns / paragraphs that pdf.js flattens into
 *  the linear text-layer stream. */
function extendMatch(
  pageNorm: string,
  chunkNorm: string,
  pageStart: number, // first matched char in page
  pageEnd: number,   // one past last matched char in page
  chunkStart: number,
  chunkEnd: number,
): { pageStart: number; pageEnd: number } {
  const maxTotalLen = Math.ceil(chunkNorm.length * MAX_EXTENSION_RATIO)
  // ----- extend forward
  {
    let pi = pageEnd
    let ci = chunkEnd
    const window: number[] = [] // 1 = mismatch, 0 = match
    let mismatches = 0
    while (pi < pageNorm.length && ci < chunkNorm.length) {
      if (pi - pageStart >= maxTotalLen) break
      const m = pageNorm[pi] === chunkNorm[ci] ? 0 : 1
      window.push(m)
      mismatches += m
      if (window.length > EXTEND_WINDOW_LEN) {
        mismatches -= window.shift()!
      }
      if (
        window.length === EXTEND_WINDOW_LEN &&
        mismatches / EXTEND_WINDOW_LEN > EXTEND_MISMATCH_RATIO
      ) {
        break
      }
      pi++
      ci++
    }
    // Walk back past any trailing mismatched chars so the highlight
    // ends on a real match boundary rather than mid-noise.
    while (pi > pageEnd && window.length > 0 && window[window.length - 1] === 1) {
      pi--
      window.pop()
    }
    pageEnd = pi
  }
  // ----- extend backward
  {
    let pi = pageStart - 1
    let ci = chunkStart - 1
    const window: number[] = []
    let mismatches = 0
    while (pi >= 0 && ci >= 0) {
      if (pageEnd - pi >= maxTotalLen) break
      const m = pageNorm[pi] === chunkNorm[ci] ? 0 : 1
      window.unshift(m)
      mismatches += m
      if (window.length > EXTEND_WINDOW_LEN) {
        mismatches -= window.pop()!
      }
      if (
        window.length === EXTEND_WINDOW_LEN &&
        mismatches / EXTEND_WINDOW_LEN > EXTEND_MISMATCH_RATIO
      ) {
        break
      }
      pi--
      ci--
    }
    while (pi < pageStart - 1 && window.length > 0 && window[0] === 1) {
      pi++
      window.shift()
    }
    pageStart = pi + 1
  }
  return { pageStart, pageEnd }
}

/** Try to locate a chunk inside the page by sliding fragments of
 *  decreasing length over the chunk and substring-matching each
 *  against the page. The first matched window becomes an anchor;
 *  ``extendMatch`` then grows it outwards in both directions so the
 *  full chunk paragraph (not just the 80-char anchor) gets highlighted.
 *  Decreasing-length tiers are tried in order; within each tier we
 *  scan all positions in the chunk because Docling re-flow can drop
 *  the head or tail. */
export function findChunkInPage(
  fullText: string,
  chunkText: string,
): { start: number; end: number } | null {
  const page = normalize(fullText)
  const chunk = normalize(chunkText)
  if (!chunk.norm || !page.norm) return null

  // Anchor coordinates (in NORMALISED space).
  let pageOff = -1
  let chunkOff = 0
  let matchLen = 0

  // Fast path: whole chunk substring-matches. Anchor + match cover
  // everything, no extension needed.
  const wholeIdx = page.norm.indexOf(chunk.norm)
  if (wholeIdx !== -1) {
    pageOff = wholeIdx
    chunkOff = 0
    matchLen = chunk.norm.length
  } else {
    // Tiered window search. Tier sizes are bounded by the chunk
    // length so a short chunk doesn't try absurdly long windows.
    const len = chunk.norm.length
    const tiers = [80, 60, 40, MIN_WINDOW_CHARS].filter((w) => w <= len)

    outer: for (const winLen of tiers) {
      const step = Math.max(1, Math.floor(winLen / 4))
      for (let off = 0; off + winLen <= len; off += step) {
        const win = chunk.norm.slice(off, off + winLen)
        const j = page.norm.indexOf(win)
        if (j !== -1) {
          pageOff = j
          chunkOff = off
          matchLen = winLen
          break outer
        }
      }
      if (len >= winLen) {
        const tail = chunk.norm.slice(len - winLen)
        const j = page.norm.indexOf(tail)
        if (j !== -1) {
          pageOff = j
          chunkOff = len - winLen
          matchLen = winLen
          break
        }
      }
    }
  }

  if (pageOff === -1) return null

  // Bidirectional greedy extension. Stops when the page and chunk
  // texts visibly diverge (different paragraph / outside the chunk's
  // reach in the rendered text-layer).
  const expanded = extendMatch(
    page.norm,
    chunk.norm,
    pageOff,
    pageOff + matchLen,
    chunkOff,
    chunkOff + matchLen,
  )

  // Map normalised [pageStart, pageEnd) back to original indices.
  const startOrig = page.origIndex[expanded.pageStart] ?? 0
  const endOrigInclusive =
    page.origIndex[
      Math.min(expanded.pageEnd - 1, page.origIndex.length - 1)
    ] ?? startOrig
  return { start: startOrig, end: endOrigInclusive + 1 }
}

/** Tag every span whose character range intersects ``[start, end)``
 *  with the given CSS class. Idempotent -- clears any previous match
 *  class on the same spans first so re-highlighting after a page flip
 *  doesn't leave ghosts behind. */
export function highlightSpansInRange(
  spans: HTMLElement[],
  offsets: number[],
  start: number,
  end: number,
  className: string,
): number {
  // Clear previous highlight first.
  for (const s of spans) s.classList.remove(className)
  let tagged = 0
  for (let i = 0; i < spans.length; i++) {
    const spanStart = offsets[i]
    const spanEnd = offsets[i + 1]
    if (spanEnd <= start) continue
    if (spanStart >= end) break
    spans[i].classList.add(className)
    tagged++
  }
  return tagged
}

/** One-call helper: build the text map for the current text layer,
 *  fuzzy-match the chunk, and apply the highlight class. Returns the
 *  number of spans we tagged (0 = no match). Mostly here so the React
 *  effect can stay focused on lifecycle. */
export function highlightChunkInTextLayer(
  textLayer: HTMLElement,
  chunkText: string,
  className = "chunkMatch",
): number {
  const spans = Array.from(
    textLayer.querySelectorAll<HTMLElement>("span"),
  ).filter((el) => {
    // pdf.js inserts an `.endOfContent` sentinel span we don't want
    // to highlight (it carries no real text).
    return !el.classList.contains("endOfContent")
  })
  if (spans.length === 0 || !chunkText) return 0
  const { fullText, offsets } = buildPageText(spans)
  const match = findChunkInPage(fullText, chunkText)
  if (!match) {
    // Clear leftover highlight from a previous page even on miss.
    for (const s of spans) s.classList.remove(className)
    return 0
  }
  return highlightSpansInRange(
    spans,
    offsets,
    match.start,
    match.end,
    className,
  )
}
