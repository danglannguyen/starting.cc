"""Test helpers that build SYNTHETIC .docx and .pdf files in memory."""

import io
from pathlib import Path

from docx.document import Document as DocxDocument
from docx.oxml import parse_xml

FIXTURES_DIR = Path(__file__).parent / "fixtures"

# A legacy (VML) Word text box. Word saves text boxes as XML like this.
TEXT_BOX_RUN_XML = (
    '<w:r xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" '
    'xmlns:v="urn:schemas-microsoft-com:vml">'
    '<w:pict><v:shape style="width:200pt;height:40pt"><v:textbox><w:txbxContent>'
    "<w:p><w:r><w:t>Hidden skill: Kubernetes</w:t></w:r></w:p>"
    "</w:txbxContent></v:textbox></v:shape></w:pict></w:r>"
)


def docx_to_bytes(document: DocxDocument) -> bytes:
    """Save a python-docx Document to bytes, the same form an upload arrives in."""
    buffer = io.BytesIO()
    document.save(buffer)
    return buffer.getvalue()


def add_text_box(document: DocxDocument) -> None:
    """Append a paragraph containing a text box to the document body."""
    paragraph = document.add_paragraph()
    # python-docx has no public API for text boxes, so insert the raw XML.
    paragraph._p.append(parse_xml(TEXT_BOX_RUN_XML))


def build_pdf(lines: list[str]) -> bytes:
    """Build a minimal one-page PDF with each string on its own line.

    Pass an empty list to get a page with no text, like a scanned image.
    """
    operations = ["BT", "/F1 12 Tf", "72 720 Td", "14 TL"]
    for line in lines:
        escaped = line.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
        operations.append(f"({escaped}) Tj T*")
    operations.append("ET")
    stream = "\n".join(operations).encode("latin-1")

    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        (
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            b"/Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>"
        ),
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream",
    ]

    # A PDF ends with a table of byte offsets for each object, so track them.
    output = bytearray(b"%PDF-1.4\n")
    offsets: list[int] = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(output))
        output += b"%d 0 obj\n" % number + body + b"\nendobj\n"
    xref_position = len(output)
    output += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    for offset in offsets:
        output += b"%010d 00000 n \n" % offset
    output += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%EOF\n" % (
        len(objects) + 1,
        xref_position,
    )
    return bytes(output)
