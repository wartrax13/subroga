"""Normalização de PDFs: hashes, contagem de páginas, deduplicação e miniaturas."""
from __future__ import annotations

import hashlib
import io
import threading
from pathlib import Path

import pypdfium2 as pdfium
from pypdf import PdfReader


def file_sha256(path: str | Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def page_count(path: str | Path) -> int:
    return len(PdfReader(str(path)).pages)


def page_hashes(path: str | Path) -> list[str]:
    """SHA-256 do conteúdo de cada página (stream de conteúdo + tamanho + rotação).

    Independe dos metadados do arquivo, então a mesma página em PDFs diferentes
    gera o mesmo hash.
    """
    reader = PdfReader(str(path))
    out: list[str] = []
    for page in reader.pages:
        h = hashlib.sha256()
        contents = page.get_contents()
        if contents is not None:
            h.update(contents.get_data())
        _hash_xobjects(page, h)
        box = page.mediabox
        h.update(f"{float(box.width):.2f}x{float(box.height):.2f}r{page.rotation}".encode())
        out.append(h.hexdigest())
    return out


def _hash_xobjects(page: object, h: "hashlib._Hash") -> None:
    """Inclui os bytes das imagens/forms da página: páginas escaneadas têm o mesmo
    content stream ("/Im0 Do") e só diferem pela imagem."""
    try:
        resources = page.get("/Resources")  # type: ignore[attr-defined]
        xobjects = resources.get_object().get("/XObject") if resources is not None else None
        if xobjects is None:
            return
        xobjects = xobjects.get_object()
        for name in sorted(xobjects):
            obj = xobjects[name].get_object()
            h.update(str(name).encode())
            data = getattr(obj, "_data", None)
            if isinstance(data, bytes):
                h.update(data)
    except Exception:  # noqa: BLE001 - PDF malformado: fica só com o content stream
        return


def dedupe_files(paths: list[str | Path]) -> tuple[list[Path], list[Path]]:
    """Separa arquivos únicos de duplicados (mesmo SHA-256). Mantém o primeiro visto."""
    seen: set[str] = set()
    unique: list[Path] = []
    dups: list[Path] = []
    for p in paths:
        path = Path(p)
        digest = file_sha256(path)
        if digest in seen:
            dups.append(path)
        else:
            seen.add(digest)
            unique.append(path)
    return unique, dups


# PDFium não é thread-safe: chamadas simultâneas (várias miniaturas pedidas ao
# mesmo tempo pelo threadpool do FastAPI) corrompem o heap e derrubam o processo.
_PDFIUM_LOCK = threading.Lock()


def render_page_png(path: str | Path, page_index: int, scale: float = 1.0) -> bytes:
    """Renderiza uma página (0-based) como PNG."""
    scale = min(max(scale, 0.05), 3.0)
    with _PDFIUM_LOCK:
        pdf = pdfium.PdfDocument(str(path))
        try:
            page = pdf[page_index]
            try:
                bitmap = page.render(scale=scale)
                image = bitmap.to_pil()
                bitmap.close()
            finally:
                page.close()
        finally:
            pdf.close()
    buf = io.BytesIO()
    image.save(buf, format="PNG")
    return buf.getvalue()


def write_page_subset(path: str | Path, pages: list[int], out_path: str | Path) -> Path:
    """Grava um PDF só com as páginas indicadas (0-based), na ordem dada."""
    from pypdf import PdfWriter

    reader = PdfReader(str(path))
    writer = PdfWriter()
    for i in pages:
        writer.add_page(reader.pages[i])
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "wb") as f:
        writer.write(f)
    return out
