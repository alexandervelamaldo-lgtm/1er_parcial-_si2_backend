from __future__ import annotations

from datetime import datetime, timezone
import unicodedata


def format_bs(amount: float | None) -> str:
    value = round(float(amount or 0), 2)
    integer, decimals = f"{value:.2f}".split(".")
    groups: list[str] = []
    while integer:
        groups.append(integer[-3:])
        integer = integer[:-3]
    return f"Bs {' '.join(reversed(groups))}.{decimals}"


def _sanitize_pdf_text(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode("ascii")
    return normalized.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def build_invoice_pdf(*, title: str, lines: list[str]) -> bytes:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    text_lines = [_sanitize_pdf_text(title), ""] + [_sanitize_pdf_text(line) for line in lines]

    content_parts = ["BT", "/F1 16 Tf", "50 790 Td", "18 TL"]
    for index, line in enumerate(text_lines):
        if index == 0:
            content_parts.append(f"({line}) Tj")
        else:
            content_parts.append("T*")
            content_parts.append(f"({line}) Tj")
    content_parts.append("ET")
    content_stream = "\n".join(content_parts).encode("latin-1")

    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 842] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        f"<< /Length {len(content_stream)} >>\nstream\n".encode("latin-1") + content_stream + b"\nendstream",
    ]

    pdf = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets: list[int] = []
    for index, obj in enumerate(objects, start=1):
        offsets.append(len(pdf))
        pdf.extend(f"{index} 0 obj\n".encode("latin-1"))
        pdf.extend(obj)
        pdf.extend(b"\nendobj\n")

    xref_offset = len(pdf)
    pdf.extend(f"xref\n0 {len(objects) + 1}\n".encode("latin-1"))
    pdf.extend(b"0000000000 65535 f \n")
    for offset in offsets:
        pdf.extend(f"{offset:010d} 00000 n \n".encode("latin-1"))
    pdf.extend(
        (
            f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R /Info << /Producer (Emergency SI2) /CreationDate ({timestamp}) >> >>\n"
            f"startxref\n{xref_offset}\n%%EOF"
        ).encode("latin-1")
    )
    return bytes(pdf)
