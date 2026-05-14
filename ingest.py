"""
Step 1: Document Ingestion Module.

Parses PDF files locally using IBM Docling, preserving semantic layout
(headers, tables, code blocks, formulas-as-LaTeX), and converts the parsed
content into LangChain Document objects suitable for downstream embedding.

No network calls. No external APIs. 100% offline.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List, Optional

from docling.document_converter import DocumentConverter
from docling_core.transforms.chunker import HierarchicalChunker
from langchain_core.documents import Document

# HybridChunker is the production-recommended chunker: it builds on the
# hierarchical (layout-aware) split and then merges small adjacent chunks
# up to a token budget, so each chunk carries enough context for retrieval.
try:
    from docling_core.transforms.chunker import HybridChunker
    _HAS_HYBRID = True
except ImportError:  # pragma: no cover
    HybridChunker = None  # type: ignore
    _HAS_HYBRID = False

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(name)s | %(levelname)s | %(message)s",
)
logger = logging.getLogger("ingest")


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
DEFAULT_DATA_DIR = Path("./data")
# Docling auto-detects format from extension and routes to the right
# pipeline:
#   PDF    → standard layout pipeline (Heron model + TableFormer)
#   DOCX   → Word backend (parses XML directly, no OCR)
#   image  → image pipeline (RapidOCR for the text)
# The downstream chunker / embedder / RAG chain are all format-agnostic;
# they just see structured text with metadata.
PDF_SUFFIXES = {".pdf"}
DOCX_SUFFIXES = {".docx"}
IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".tif", ".tiff", ".bmp"}
# EPUB goes through Docling's native EpubDocumentBackend (no OCR needed,
# real semantic structure: chapters, headings, paragraphs). v0.5.0.
EPUB_SUFFIXES = {".epub"}
# Plain text + markdown skips Docling entirely (no layout/OCR model needed).
# Treated as a single "page 1" downstream because the citation chip
# already shows '[filename p.N]'; pinning to 1 keeps that surface intact
# without forcing the chain to handle page=None.
TEXT_SUFFIXES = {".txt", ".md", ".markdown"}
SUPPORTED_SUFFIXES = (
    PDF_SUFFIXES | DOCX_SUFFIXES | IMAGE_SUFFIXES
    | EPUB_SUFFIXES | TEXT_SUFFIXES
)


@dataclass
class IngestionResult:
    """Container for the output of an ingestion run."""

    source_path: Path
    markdown: str
    documents: List[Document]

    @property
    def chunk_count(self) -> int:
        return len(self.documents)


# ---------------------------------------------------------------------------
# Core ingestion
# ---------------------------------------------------------------------------
class PDFIngestor:
    """
    Wraps Docling's DocumentConverter and HierarchicalChunker to produce
    layout-aware LangChain Documents.

    The HierarchicalChunker splits on the document's semantic structure
    (sections, tables, code blocks, list groups) rather than fixed character
    counts, which preserves the meaning required for high-precision RAG.
    """

    def __init__(self, max_tokens: int = 512) -> None:
        """
        Parameters
        ----------
        max_tokens : int
            Token budget per chunk. The HybridChunker will merge small
            adjacent layout chunks up to this limit so each chunk carries
            enough context for high-precision retrieval. Falls back to the
            plain HierarchicalChunker if HybridChunker is unavailable.
        """
        try:
            self._converter = DocumentConverter()
            if _HAS_HYBRID:
                # tokenizer=None makes HybridChunker use a simple character
                # heuristic (~4 chars per token) instead of pulling a real
                # HF tokenizer. Avoids an extra model download for ingestion.
                self._chunker = HybridChunker(
                    tokenizer=None,
                    max_tokens=max_tokens,
                    merge_peers=True,
                )
                logger.info(
                    "Docling DocumentConverter + HybridChunker (max_tokens=%d) ready.",
                    max_tokens,
                )
            else:
                self._chunker = HierarchicalChunker()
                logger.info(
                    "Docling DocumentConverter + HierarchicalChunker (fallback) ready."
                )
        except Exception as exc:  # pragma: no cover - defensive
            logger.exception("Failed to initialize Docling converter.")
            raise RuntimeError(
                "Could not initialize Docling. Verify the docling install."
            ) from exc

    # ------------------------------------------------------------------ utils
    @staticmethod
    def _validate_path(src_path: Path) -> Path:
        src_path = Path(src_path).expanduser().resolve()
        if not src_path.exists():
            raise FileNotFoundError(f"File not found: {src_path}")
        if src_path.suffix.lower() not in SUPPORTED_SUFFIXES:
            raise ValueError(
                f"Unsupported file type '{src_path.suffix}'. "
                f"Expected one of {sorted(SUPPORTED_SUFFIXES)}."
            )
        return src_path

    @staticmethod
    def _is_image(path: Path) -> bool:
        return path.suffix.lower() in IMAGE_SUFFIXES

    @staticmethod
    def _is_docx(path: Path) -> bool:
        return path.suffix.lower() in DOCX_SUFFIXES

    @staticmethod
    def _is_epub(path: Path) -> bool:
        return path.suffix.lower() in EPUB_SUFFIXES

    @staticmethod
    def _is_plaintext(path: Path) -> bool:
        return path.suffix.lower() in TEXT_SUFFIXES

    @staticmethod
    def _clean_text(text: str) -> str:
        # Collapse excessive blank lines while keeping paragraph breaks.
        text = re.sub(r"\n{3,}", "\n\n", text).strip()
        return text

    # --------------------------------------------------------------- main API
    def ingest(self, src_path: str | Path) -> IngestionResult:
        """
        Convert a single document into Markdown + a list of LangChain
        Documents. Routes by extension:
            PDF   -> Docling layout pipeline
            DOCX  -> Docling Word backend (native XML)
            image -> Docling image pipeline (RapidOCR)
            txt   -> direct read + RecursiveCharacterTextSplitter
            md    -> same as txt but with markdown-aware separators

        Returns
        -------
        IngestionResult
            Populated with markdown and chunked Documents.
        """
        src_path = self._validate_path(Path(src_path))

        # Fast path: plain text and markdown skip Docling entirely. Saves
        # the ~770 MB layout model load + tens of seconds of cold start
        # for what is fundamentally already-structured text.
        if self._is_plaintext(src_path):
            return self._ingest_plaintext(src_path)

        # EPUB: Docling at our pin (2.x) doesn't include EPUB in its
        # allowed input formats. Rather than wait on upstream, we extract
        # the XHTML chapters ourselves and route them through the same
        # heading-aware plaintext splitter. Lossy on inline footnotes /
        # complex layouts; good enough for the typical 'chat with my
        # ebook' use case.
        if self._is_epub(src_path):
            return self._ingest_epub(src_path)

        if self._is_image(src_path):
            kind = "image (OCR)"
        elif self._is_docx(src_path):
            kind = "DOCX"
        elif self._is_epub(src_path):
            kind = "EPUB"
        else:
            kind = "PDF"
        logger.info("Parsing %s with Docling: %s", kind, src_path.name)

        try:
            conversion = self._converter.convert(str(src_path))
        except Exception as exc:
            logger.exception("Docling failed to parse %s", src_path)
            raise RuntimeError(f"Docling parse error for {src_path}") from exc

        docling_doc = conversion.document

        # Markdown preserves tables as pipe-tables and formulas as LaTeX,
        # which is ideal for the LLM to reason over downstream. For images
        # the markdown is the OCR text, structured by Docling's layout
        # detector (so single-column scans become paragraphs, multi-column
        # become tables, etc.).
        try:
            markdown = docling_doc.export_to_markdown()
        except Exception:
            logger.warning("Markdown export failed; falling back to text.")
            markdown = docling_doc.export_to_text()

        markdown = self._clean_text(markdown)

        # Layout-aware chunking using Docling's hierarchical chunker.
        documents = self._chunk_to_documents(docling_doc, source=src_path)

        logger.info(
            "Extracted %d chunks from %s (markdown length: %d chars)",
            len(documents),
            src_path.name,
            len(markdown),
        )

        return IngestionResult(
            source_path=src_path,
            markdown=markdown,
            documents=documents,
        )

    def ingest_text(
        self,
        text: str,
        filename: str,
    ) -> IngestionResult:
        """In-memory ingest for pasted text. Doesn't touch disk -- the
        caller decides whether to persist the source. Uses the same
        plaintext splitter / metadata shape as a .md/.txt file ingest
        so downstream the chain can't tell the difference.

        ``filename`` is what shows up in the UI's Library list and in
        citation chips, so callers should pick something descriptive
        (e.g. derived from the first line of the pasted content)."""
        logger.info("Ingesting pasted text as %s (%d chars).", filename, len(text))
        text = self._clean_text(text)
        if not text:
            return IngestionResult(
                source_path=Path(filename),
                markdown="",
                documents=[],
            )

        from langchain_text_splitters import RecursiveCharacterTextSplitter
        splitter = RecursiveCharacterTextSplitter(
            chunk_size=900,
            chunk_overlap=120,
            separators=[
                "\n# ", "\n## ", "\n### ", "\n#### ",
                "\n\n", "\n",
                ". ", "? ", "! ", "。", "？", "！",
                " ", "",
            ],
        )
        parts = splitter.split_text(text)
        heading_re = re.compile(r"^\s{0,3}(#{1,6})\s+(.+?)\s*$", re.MULTILINE)
        current_heading = ""

        documents: List[Document] = []
        for idx, chunk in enumerate(parts):
            chunk = chunk.strip()
            if not chunk:
                continue
            m = heading_re.search(chunk[:200])
            if m:
                current_heading = m.group(2).strip()
            documents.append(Document(
                page_content=chunk,
                metadata={
                    # 'source' is purely informational here -- there's no
                    # file on disk, so we use a virtual marker.
                    "source": f"<paste:{filename}>",
                    "filename": filename,
                    "chunk_index": idx,
                    "page": 1,
                    "headings": current_heading,
                },
            ))

        return IngestionResult(
            source_path=Path(filename),
            markdown=text,
            documents=documents,
        )

    def ingest_many(self, paths: Iterable[str | Path]) -> List[IngestionResult]:
        results: List[IngestionResult] = []
        for p in paths:
            try:
                results.append(self.ingest(p))
            except Exception as exc:
                logger.error("Skipping %s due to error: %s", p, exc)
        return results

    def ingest_directory(
        self,
        directory: str | Path = DEFAULT_DATA_DIR,
        recursive: bool = True,
    ) -> List[IngestionResult]:
        directory = Path(directory).expanduser().resolve()
        if not directory.is_dir():
            raise NotADirectoryError(f"Not a directory: {directory}")

        # Glob every supported extension (case-insensitive on Windows).
        files: List[Path] = []
        for suffix in SUPPORTED_SUFFIXES:
            pattern = f"**/*{suffix}" if recursive else f"*{suffix}"
            files.extend(directory.glob(pattern))
        files = sorted(set(files))
        logger.info("Found %d ingestible file(s) under %s", len(files), directory)
        return self.ingest_many(files)

    # ----------------------------------------------------------------- chunks
    def _chunk_to_documents(
        self, docling_doc, source: Path
    ) -> List[Document]:
        """
        Use Docling's HierarchicalChunker to split the document along its
        natural structure, then wrap each chunk as a LangChain Document with
        rich metadata (page numbers, headings, content type).
        """
        documents: List[Document] = []

        try:
            raw_chunks = list(self._chunker.chunk(docling_doc))
        except Exception as exc:
            logger.exception("Hierarchical chunking failed.")
            raise RuntimeError("Failed to chunk Docling document.") from exc

        for idx, chunk in enumerate(raw_chunks):
            text = getattr(chunk, "text", None) or str(chunk)
            text = self._clean_text(text)
            if not text:
                continue

            metadata = {
                "source": str(source),
                "filename": source.name,
                "chunk_index": idx,
            }

            # Pull headings and page references when available.
            meta = getattr(chunk, "meta", None)
            if meta is not None:
                headings = getattr(meta, "headings", None)
                if headings:
                    metadata["headings"] = " > ".join(map(str, headings))

                doc_items = getattr(meta, "doc_items", None) or []
                pages: list[int] = []
                labels: list[str] = []
                for item in doc_items:
                    label = getattr(item, "label", None)
                    if label and str(label) not in labels:
                        labels.append(str(label))
                    for prov in getattr(item, "prov", []) or []:
                        page = getattr(prov, "page_no", None)
                        if page is not None and page not in pages:
                            pages.append(page)
                if pages:
                    metadata["pages"] = sorted(pages)
                    metadata["page"] = pages[0]
                if labels:
                    metadata["content_types"] = ",".join(labels)

            documents.append(Document(page_content=text, metadata=metadata))

        return documents

    # -------------------------------------------------------- plaintext path
    def _ingest_plaintext(self, src_path: Path) -> IngestionResult:
        """Direct-read + recursive splitter for .txt / .md / .markdown.
        Avoids Docling entirely so a 5 KB note doesn't pay for the 770 MB
        layout model. Markdown headings are tracked so each chunk's
        nearest H1/H2/H3 ends up in metadata['headings'] -- same shape
        the Docling-derived chunks use, so the rest of the chain is
        format-agnostic."""
        logger.info("Reading plain text: %s", src_path.name)
        text = src_path.read_text(encoding="utf-8", errors="replace")
        text = self._clean_text(text)
        if not text:
            return IngestionResult(source_path=src_path, markdown="", documents=[])

        # Lazy import — only paid when a user actually drops txt/md in.
        # langchain_text_splitters arrives transitively via langchain_chroma.
        from langchain_text_splitters import RecursiveCharacterTextSplitter

        # Markdown-aware separators: prefer to break on heading boundaries
        # first, then paragraphs, then lines. chunk_size matches the
        # HybridChunker token budget order-of-magnitude (~512 tokens
        # ≈ ~900-1000 chars for English / Chinese mix).
        splitter = RecursiveCharacterTextSplitter(
            chunk_size=900,
            chunk_overlap=120,
            separators=[
                "\n# ", "\n## ", "\n### ", "\n#### ",  # markdown headings
                "\n\n",                                  # paragraphs
                "\n",
                ". ", "? ", "! ", "。", "？", "！",      # sentence boundaries (en + zh)
                " ", "",
            ],
        )
        parts = splitter.split_text(text)

        # Track the most recent heading we've seen in source order so each
        # chunk can carry a 'headings' field. The splitter doesn't expose
        # which separator it chose, so we just scan the chunk text.
        heading_re = re.compile(r"^\s{0,3}(#{1,6})\s+(.+?)\s*$", re.MULTILINE)
        current_heading = ""

        documents: List[Document] = []
        for idx, chunk in enumerate(parts):
            chunk = chunk.strip()
            if not chunk:
                continue
            # Update current_heading if this chunk starts with one.
            m = heading_re.search(chunk[:200])  # only scan top of chunk
            if m:
                current_heading = m.group(2).strip()
            documents.append(Document(
                page_content=chunk,
                metadata={
                    "source": str(src_path),
                    "filename": src_path.name,
                    "chunk_index": idx,
                    # Plaintext has no pages, but the citation chip
                    # expects [filename p.N]; pin to 1.
                    "page": 1,
                    "headings": current_heading,
                },
            ))

        logger.info(
            "Extracted %d plaintext chunks from %s (length: %d chars)",
            len(documents), src_path.name, len(text),
        )
        return IngestionResult(
            source_path=src_path,
            markdown=text,
            documents=documents,
        )

    # ------------------------------------------------------------- EPUB path
    def _ingest_epub(self, src_path: Path) -> IngestionResult:
        """Extract XHTML chapters from an EPUB and route them through the
        plaintext splitter. We don't go through Docling because its 2.x
        line doesn't list EPUB in InputFormat -- waiting on upstream
        rather than wiring this would block real users (research papers
        often come paired with an ebook of the same topic).

        EPUB structure:
          - It's a zip file with mimetype 'application/epub+zip'
          - META-INF/container.xml points at the package OPF
          - The OPF's <spine> orders the XHTML chapter files
          - Each chapter is XHTML; we strip tags via BeautifulSoup

        We preserve chapter order via spine, prefix each chapter's text
        with its first heading (if any), then concatenate with paragraph
        separators and feed to the same splitter as txt/md. Each chunk
        gets `page: chapter_index + 1` so the citation chip still shows
        a useful page-equivalent ('[book.epub p.3]' = chapter 3).
        """
        import zipfile
        from xml.etree import ElementTree as ET
        from bs4 import BeautifulSoup

        logger.info("Reading EPUB: %s", src_path.name)
        try:
            zf = zipfile.ZipFile(src_path)
        except Exception as exc:
            raise RuntimeError(f"Couldn't open EPUB {src_path}: {exc}") from exc

        try:
            # 1. Find the OPF path via META-INF/container.xml
            container_xml = zf.read("META-INF/container.xml").decode("utf-8", "replace")
            container = ET.fromstring(container_xml)
            # Namespace-agnostic find for <rootfile full-path="...">
            opf_path = None
            for el in container.iter():
                if el.tag.endswith("rootfile") and "full-path" in el.attrib:
                    opf_path = el.attrib["full-path"]
                    break
            if not opf_path:
                raise RuntimeError("EPUB container.xml had no <rootfile full-path=...>")

            # 2. Parse the OPF to get spine order + manifest id -> href map
            opf_dir = "/".join(opf_path.split("/")[:-1])
            opf_xml = zf.read(opf_path).decode("utf-8", "replace")
            opf = ET.fromstring(opf_xml)
            id_to_href: dict = {}
            for el in opf.iter():
                if el.tag.endswith("item"):
                    item_id = el.attrib.get("id")
                    href = el.attrib.get("href")
                    media = el.attrib.get("media-type", "")
                    if item_id and href and "html" in media:
                        id_to_href[item_id] = href
            spine_ids: list = []
            for el in opf.iter():
                if el.tag.endswith("itemref"):
                    rid = el.attrib.get("idref")
                    if rid:
                        spine_ids.append(rid)

            # 3. Read each chapter in spine order, strip to text
            chapter_texts: list[tuple[str, str]] = []  # (heading, body)
            for rid in spine_ids:
                href = id_to_href.get(rid)
                if not href:
                    continue
                chapter_path = f"{opf_dir}/{href}" if opf_dir else href
                try:
                    raw = zf.read(chapter_path).decode("utf-8", "replace")
                except KeyError:
                    continue
                soup = BeautifulSoup(raw, "html.parser")
                # First H1/H2/H3 = chapter heading
                heading_tag = soup.find(["h1", "h2", "h3"])
                heading = heading_tag.get_text(" ", strip=True) if heading_tag else ""
                # Drop script/style noise before extracting prose
                for bad in soup(["script", "style"]):
                    bad.decompose()
                body = soup.get_text("\n", strip=True)
                if body:
                    chapter_texts.append((heading, body))
        finally:
            zf.close()

        if not chapter_texts:
            logger.warning("EPUB %s yielded no extractable text.", src_path.name)
            return IngestionResult(source_path=src_path, markdown="", documents=[])

        # 4. Build chunks per chapter via the same splitter as txt/md
        from langchain_text_splitters import RecursiveCharacterTextSplitter
        splitter = RecursiveCharacterTextSplitter(
            chunk_size=900,
            chunk_overlap=120,
            separators=[
                "\n\n", "\n",
                ". ", "? ", "! ", "。", "？", "！",
                " ", "",
            ],
        )
        documents: List[Document] = []
        markdown_parts: list[str] = []
        global_idx = 0
        for chapter_idx, (heading, body) in enumerate(chapter_texts, start=1):
            body = self._clean_text(body)
            if heading:
                markdown_parts.append(f"## {heading}\n\n{body}")
            else:
                markdown_parts.append(body)
            for part in splitter.split_text(body):
                part = part.strip()
                if not part:
                    continue
                documents.append(Document(
                    page_content=part,
                    metadata={
                        "source": str(src_path),
                        "filename": src_path.name,
                        "chunk_index": global_idx,
                        # 'page' = chapter index so citations read
                        # '[book.epub p.3]' meaning chapter 3.
                        "page": chapter_idx,
                        "headings": heading,
                    },
                ))
                global_idx += 1

        logger.info(
            "Extracted %d EPUB chunks from %s across %d chapters.",
            len(documents), src_path.name, len(chapter_texts),
        )
        return IngestionResult(
            source_path=src_path,
            markdown="\n\n".join(markdown_parts),
            documents=documents,
        )


# ---------------------------------------------------------------------------
# CLI entry point — handy for ad-hoc testing
# ---------------------------------------------------------------------------
def _print_summary(results: List[IngestionResult]) -> None:
    total_chunks = sum(r.chunk_count for r in results)
    print(f"\nIngested {len(results)} document(s), {total_chunks} chunks total.")
    for r in results:
        print(f"  - {r.source_path.name}: {r.chunk_count} chunks")


def main(argv: Optional[List[str]] = None) -> None:
    import argparse

    parser = argparse.ArgumentParser(
        description="Parse PDFs with Docling into LangChain Documents."
    )
    parser.add_argument(
        "paths",
        nargs="*",
        help="One or more PDF files. If omitted, ingests ./data/*.pdf.",
    )
    args = parser.parse_args(argv)

    ingestor = PDFIngestor()

    if args.paths:
        results = ingestor.ingest_many(args.paths)
    else:
        results = ingestor.ingest_directory(DEFAULT_DATA_DIR)

    _print_summary(results)


if __name__ == "__main__":
    main()
