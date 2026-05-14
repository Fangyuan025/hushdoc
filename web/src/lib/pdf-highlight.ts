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

/** Try to locate a chunk inside the page by sliding fragments of
 *  decreasing length over the chunk and substring-matching each
 *  against the page. Decreasing-length tiers are tried in order; the
 *  first match wins. Within each tier we scan ALL positions in the
 *  chunk (not just head / middle / tail) because Docling re-flow
 *  occasionally drops the very piece of text we picked as our
 *  anchor. */
export function findChunkInPage(
  fullText: string,
  chunkText: string,
): { start: number; end: number } | null {
  const page = normalize(fullText)
  const chunk = normalize(chunkText)
  if (!chunk.norm || !page.norm) return null

  // Fast path: whole chunk substring-matches.
  let idx = page.norm.indexOf(chunk.norm)
  let matchLen = chunk.norm.length

  if (idx === -1) {
    // Tiered window search. Tier sizes are bounded by the chunk
    // length so a short chunk doesn't try absurdly long windows.
    const len = chunk.norm.length
    const tiers = [80, 60, 40, MIN_WINDOW_CHARS].filter((w) => w <= len)

    outer: for (const winLen of tiers) {
      // Step ~quarter-window so we cover the chunk densely without
      // doing a full O(N*M) scan. The first matched position wins.
      const step = Math.max(1, Math.floor(winLen / 4))
      for (let off = 0; off + winLen <= len; off += step) {
        const win = chunk.norm.slice(off, off + winLen)
        const j = page.norm.indexOf(win)
        if (j !== -1) {
          idx = j
          matchLen = winLen
          break outer
        }
      }
      // Always try the tail too -- a chunk that ends with a
      // distinctive phrase but starts with boilerplate would miss
      // every stepped position above.
      if (len >= winLen) {
        const tail = chunk.norm.slice(len - winLen)
        const j = page.norm.indexOf(tail)
        if (j !== -1) {
          idx = j
          matchLen = winLen
          break
        }
      }
    }
  }

  if (idx === -1) return null

  // Map normalised [idx, idx+matchLen) back to original indices. Both
  // ends include the boundary character so the highlight slightly
  // overshoots into trailing whitespace -- harmless and avoids the
  // off-by-one feeling where the last letter sits just outside.
  const startOrig = page.origIndex[idx] ?? 0
  const endOrigInclusive =
    page.origIndex[Math.min(idx + matchLen - 1, page.origIndex.length - 1)] ?? startOrig
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
