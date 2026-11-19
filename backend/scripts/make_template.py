"""Gera templates/peticao_regressiva.docx (modelo docxtpl) com python-docx.

Uso: uv run python scripts/make_template.py
"""
from __future__ import annotations

from pathlib import Path

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.shared import Cm, Pt

OUT = Path(__file__).resolve().parent.parent / "templates" / "peticao_regressiva.docx"


def build(out: Path = OUT) -> Path:
    doc = Document()
    for s in doc.sections:
        s.top_margin = Cm(3)
        s.left_margin = Cm(3)
        s.bottom_margin = Cm(2)
        s.right_margin = Cm(2)

    normal = doc.styles["Normal"]
    normal.font.name = "Times New Roman"
    normal.font.size = Pt(12)
    pf = normal.paragraph_format
    pf.line_spacing = 1.5
    pf.space_after = Pt(6)

    def para(text: str, *, bold: bool = False, center: bool = False, indent: bool = False) -> None:
        p = doc.add_paragraph()
        if center:
            p.alignment = WD_ALIGN_PARAGRAPH.CENTER
        else:
            p.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
        if indent:
            p.paragraph_format.first_line_indent = Cm(2.5)
        run = p.add_run(text)
        run.bold = bold

    def title(text: str) -> None:
        doc.add_paragraph()
        para(text, bold=True, center=True)

    def loop(var: str, items: str, *, indent: bool = True) -> None:
        para(f"{{%p for {var} in {items} %}}")
        para(f"{{{{ {var} }}}}", indent=indent)
        para("{%p endfor %}")

    para("{{ enderecamento }}", bold=True)
    for _ in range(4):
        doc.add_paragraph()
    para("{{ qualificacao_autora }}", indent=True)
    para("{{ qualificacao_reu }}", indent=True)

    title("AÇÃO REGRESSIVA DE RESSARCIMENTO DE DANOS")
    title("I – DOS FATOS")
    loop("p", "fatos")
    title("II – DO DIREITO")
    loop("p", "direito")
    title("III – DOS PEDIDOS")
    para("Diante do exposto, requer:", indent=True)
    loop("p", "pedidos")
    title("IV – DO VALOR DA CAUSA")
    para("Dá-se à causa o valor de {{ valor_causa }}.", indent=True)
    title("V – DOCUMENTOS QUE INSTRUEM A INICIAL")
    loop("d", "rol", indent=False)
    doc.add_paragraph()
    para("Termos em que pede deferimento.", indent=True)
    para("{{ local_data }}", center=True)
    doc.add_paragraph()
    para("______________________________", center=True)
    para("{{ advogado }}", center=True)

    out.parent.mkdir(parents=True, exist_ok=True)
    doc.save(str(out))
    return out


if __name__ == "__main__":
    print(build())
