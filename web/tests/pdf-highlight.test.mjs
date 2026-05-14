// Standalone smoke test for src/lib/pdf-highlight.ts. Run with:
//   npx tsx tests/pdf-highlight.test.mjs
// We don't have a full unit-test runner wired up; this exists so the
// Day-18 quality gate can be re-checked after any matcher tweak.

import { buildPageText, findChunkInPage, normalize } from "../src/lib/pdf-highlight.ts"

function fakeSpans(text) {
  return text.split(/( )/).map(t => ({
    textContent: t,
    classList: { add() {}, remove() {} },
  }))
}

let pass = 0
let fail = 0
function check(name, cond, info) {
  if (cond) { console.log(`  PASS  ${name}`); pass++ }
  else      { console.log(`  FAIL  ${name}${info ? "  -- " + info : ""}`); fail++ }
}

console.log("pdf-highlight matcher tests")

{
  const page = "The result was extreme-\nly important to the study."
  const chunk = "extremely important to the study"
  const { fullText } = buildPageText(fakeSpans(page))
  const m = findChunkInPage(fullText, chunk)
  check("hyphenated line break", !!m, "expected match")
  // Whitespace gets doubled by fakeSpans' inter-span separator, so
  // collapse before substring-checking.
  const slice = m ? fullText.slice(m.start, m.end).replace(/\s+/g, " ") : ""
  check("hyphenated match covers 'important to the study'",
    slice.includes("important to the study"))
}
{
  const page = "The   quick brown\nfox jumps  over the lazy dog."
  const chunk = "quick brown fox jumps over"
  const { fullText } = buildPageText(fakeSpans(page))
  const m = findChunkInPage(fullText, chunk)
  check("collapsed whitespace", !!m)
}
{
  const page = "Section header. ... and this critical sentence about hybrid retrieval lives here. End of section."
  const chunk = "Different opening. Some preamble that does not match. This critical sentence about hybrid retrieval lives here. Trailing context."
  const { fullText } = buildPageText(fakeSpans(page))
  const m = findChunkInPage(fullText, chunk)
  check("middle-window fallback", !!m)
}
{
  const page = "Completely unrelated content about agriculture and trout farming."
  const chunk = "Hybrid retrieval combines dense and sparse signals."
  const { fullText } = buildPageText(fakeSpans(page))
  const m = findChunkInPage(fullText, chunk)
  check("no false positive on unrelated content", !m)
}
{
  const page = "The PROTOCOL specified by RFC 1234 is widely adopted."
  const chunk = "protocol specified by rfc 1234"
  const { fullText } = buildPageText(fakeSpans(page))
  const m = findChunkInPage(fullText, chunk)
  check("case insensitive", !!m)
}
{
  const n = normalize("extreme-\n  ly important").norm
  check("normalize de-hyphenates + collapses",
    n === "extremely important", `got ${JSON.stringify(n)}`)
}
{
  // The 85% target from the Day-18 gate: 6 of the above 7 must pass.
  console.log(`\n${pass}/${pass + fail} passed`)
  process.exit(fail > 0 ? 1 : 0)
}
