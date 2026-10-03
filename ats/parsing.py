"""Turn .docx / .pdf / .txt files into plain text."""

import io
import re
import unicodedata
import zipfile
from collections.abc import Iterator
from pathlib import PurePath

import docx
import pdfplumber
from docx.document import Document as DocxDocument
from docx.opc.exceptions import PackageNotFoundError
from docx.oxml.xmlchemy import BaseOxmlElement
from docx.section import _Footer, _Header
from docx.table import Table
from docx.text.paragraph import Paragraph
from pdfplumber.utils.exceptions import PdfminerException

from ats.config import MAX_DOCUMENT_CHARS, MAX_PDF_PAGES
from ats.models import ParsedDocument

SUPPORTED_JD_EXTENSIONS = (".pdf", ".docx", ".txt")

TEXT_BOX_WARNING = (
    "This document contains text boxes. Their text was NOT read, because many ATS "
    "systems also skip text boxes. Move important content (skills, job titles, "
    "dates) out of text boxes and into normal paragraphs."
)

# Matches any run of spaces, tabs, or newlines (including non-breaking spaces).
_WHITESPACE = re.compile(r"\s+")


class ParsingError(Exception):
    """Raised when a file cannot be read. The message is safe to show to the user."""


def normalize_text(text: str) -> str:
    """Return a lowercase, single-spaced copy of `text` for keyword matching.

    NFKC normalization turns look-alike characters into plain ones, e.g. the
    "fi" ligature that PDFs often contain becomes the two letters "f" and "i".
    """
    plain = unicodedata.normalize("NFKC", text)
    return _WHITESPACE.sub(" ", plain).strip().lower()


def read_cv_docx(file_bytes: bytes) -> ParsedDocument:
    """Extract text from a CV in .docx format (paragraphs, tables, headers, footers)."""
    return _parse_docx(file_bytes, label="CV")


def read_jd_file(file_bytes: bytes, file_name: str) -> ParsedDocument:
    """Extract text from a job description file, chosen by its extension."""
    extension = PurePath(file_name).suffix.lower()
    if extension == ".pdf":
        return _parse_pdf(file_bytes)
    if extension == ".docx":
        return _parse_docx(file_bytes, label="job description")
    if extension == ".txt":
        return _parse_txt(file_bytes)
    supported = ", ".join(SUPPORTED_JD_EXTENSIONS)
    raise ParsingError(
        f"Unsupported job description file type '{extension or file_name}'. "
        f"Please upload one of: {supported}."
    )


def read_jd_text(pasted_text: str) -> ParsedDocument:
    """Wrap job description text pasted into the app."""
    return _build_parsed_document(pasted_text, label="job description")


def _build_parsed_document(
    text: str, label: str, warnings: list[str] | None = None
) -> ParsedDocument:
    """Create a ParsedDocument, refusing documents that are empty or too long."""
    original_text = text.strip()
    if not original_text:
        raise ParsingError(f"No readable text was found in the {label}.")
    if len(original_text) > MAX_DOCUMENT_CHARS:
        raise ParsingError(
            f"The {label} is too long ({len(original_text):,} characters; the "
            f"limit is {MAX_DOCUMENT_CHARS:,}). Check that it is the right file, "
            "or paste only the relevant part."
        )
    return ParsedDocument(
        original_text=original_text,
        normalized_text=normalize_text(original_text),
        warnings=warnings or [],
    )


def _parse_txt(file_bytes: bytes) -> ParsedDocument:
    """Decode a .txt file as UTF-8."""
    try:
        # "utf-8-sig" is UTF-8 that also drops the invisible marker (BOM)
        # that Windows Notepad sometimes adds at the start of a file.
        text = file_bytes.decode("utf-8-sig")
    except UnicodeDecodeError as error:
        raise ParsingError(
            "The .txt file is not UTF-8 encoded. In Notepad, use "
            "File > Save As and choose Encoding: UTF-8, then upload it again."
        ) from error
    return _build_parsed_document(text, label="job description")


def _parse_pdf(file_bytes: bytes) -> ParsedDocument:
    """Extract text from every page of a PDF."""
    try:
        with pdfplumber.open(io.BytesIO(file_bytes)) as pdf:
            if len(pdf.pages) > MAX_PDF_PAGES:
                raise ParsingError(
                    f"The PDF has {len(pdf.pages)} pages; the limit is "
                    f"{MAX_PDF_PAGES}. Check that it is the right file, or paste "
                    "the job description text instead."
                )
            page_texts = [page.extract_text() or "" for page in pdf.pages]
    except PdfminerException as error:
        raise ParsingError(
            "The PDF could not be opened. It may be damaged or password-protected."
        ) from error

    text = "\n\n".join(page_text for page_text in page_texts if page_text.strip())
    if not text.strip():
        # A PDF with pages but no text layer is usually a scanned image.
        raise ParsingError(
            "No text was found in the PDF. It may be a scanned image. "
            "Try pasting the job description text instead."
        )
    return _build_parsed_document(text, label="job description")


def _parse_docx(file_bytes: bytes, label: str) -> ParsedDocument:
    """Extract text from a .docx file and flag text boxes we could not read."""
    try:
        document = docx.Document(io.BytesIO(file_bytes))
    # SyntaxError covers lxml's XMLSyntaxError: a valid zip with damaged XML inside.
    except (
        zipfile.BadZipFile,
        PackageNotFoundError,
        KeyError,
        ValueError,
        SyntaxError,
    ) as error:
        raise ParsingError(
            f"The {label} could not be opened as a Word (.docx) file. "
            "If it is an old .doc file, open it in Word and save it as .docx."
        ) from error

    headers, footers = _collect_headers_and_footers(document)
    blocks = (
        [_container_text(header) for header in headers]
        + [_container_text(document)]
        + [_container_text(footer) for footer in footers]
    )
    text = "\n".join(block for block in blocks if block)

    warnings: list[str] = []
    if _has_text_boxes(document, headers + footers):
        warnings.append(TEXT_BOX_WARNING)
        if not text.strip():
            raise ParsingError(
                f"All the text in the {label} seems to be inside text boxes, "
                "which this app (and many ATS systems) cannot read."
            )
    return _build_parsed_document(text, label=label, warnings=warnings)


def _collect_headers_and_footers(
    document: DocxDocument,
) -> tuple[list[_Header], list[_Footer]]:
    """Return every header and footer that has its own content.

    A header "linked to previous" just reuses the previous section's header,
    so skipping those avoids reading the same text twice.
    """
    headers: list[_Header] = []
    footers: list[_Footer] = []
    for section in document.sections:
        for header in (
            section.header,
            section.first_page_header,
            section.even_page_header,
        ):
            if not header.is_linked_to_previous:
                headers.append(header)
        for footer in (
            section.footer,
            section.first_page_footer,
            section.even_page_footer,
        ):
            if not footer.is_linked_to_previous:
                footers.append(footer)
    return headers, footers


def _container_text(container: DocxDocument | _Header | _Footer) -> str:
    """Join the text of all paragraphs and tables in a document part, in order."""
    lines = list(_iter_block_lines(container.iter_inner_content()))
    return "\n".join(line for line in lines if line.strip())


def _iter_block_lines(blocks: Iterator[Paragraph | Table]) -> Iterator[str]:
    """Yield one line per paragraph, and one line per table row."""
    for block in blocks:
        if isinstance(block, Paragraph):
            yield block.text
        else:
            yield from _iter_table_lines(block)


def _iter_table_lines(table: Table) -> Iterator[str]:
    """Yield each table row as one line, with cells separated by " | "."""
    for row in table.rows:
        cell_texts: list[str] = []
        for cell in row.cells:
            # Recurse so tables nested inside a cell are read too.
            cell_text = " ".join(_iter_block_lines(cell.iter_inner_content())).strip()
            # Merged cells are returned once per grid column they span,
            # so skip a cell that repeats the one just before it.
            if cell_text and (not cell_texts or cell_texts[-1] != cell_text):
                cell_texts.append(cell_text)
        if cell_texts:
            yield " | ".join(cell_texts)


def _has_text_boxes(
    document: DocxDocument, headers_and_footers: list[_Header | _Footer]
) -> bool:
    """Return True if the body, a header, or a footer contains a text box."""
    elements: list[BaseOxmlElement] = [document.element.body]
    elements += [item.part.element for item in headers_and_footers]
    return any(element.xpath(".//w:txbxContent") for element in elements)
