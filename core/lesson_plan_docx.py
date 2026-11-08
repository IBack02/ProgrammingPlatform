"""Fill the supplied school lesson plan without rebuilding its Word layout."""
from io import BytesIO
from pathlib import Path
from zipfile import ZipFile

from docx.shared import Pt
from lxml import etree

from .exam_work_docx import text as xml_text


TEMPLATE = Path(__file__).with_name("lesson_plan_template.docx")


def template_text(document):
    rows = []
    for row in document.tables[0].rows:
        # The underlying row lists each physical cell once, including merged cells.
        rows.append(" | ".join(" ".join(p.text for p in cell.p_lst).strip() for cell in row._tr.tc_lst))
    header = document.sections[0].header.paragraphs[0].text
    return header + "\n" + "\n".join(rows)


def _add_text(paragraph, value):
    run = paragraph.add_run(xml_text(value))
    run.bold = False
    run.font.name = "Times New Roman"
    run.font.size = Pt(12)


def _fill(cell, value):
    paragraph = cell.paragraphs[0]
    paragraph.clear()
    _add_text(paragraph, value)


def render_lesson_plan(document, *, title, teacher, date, classes, plan):
    table = document.tables[0]
    _add_text(table.cell(0, 0).paragraphs[1], title)
    _add_text(table.cell(1, 2).paragraphs[1], teacher)
    _add_text(table.cell(2, 0).paragraphs[0], date)
    _add_text(table.cell(2, 2).paragraphs[0], classes)

    for row, value in enumerate((
        plan.learning_objective, plan.lesson_objective, plan.criteria, plan.language_objectives,
        plan.nis_values, plan.interdisciplinary, plan.ict, plan.prior_knowledge,
    ), start=3):
        _fill(table.cell(row, 1), value)

    table.cell(14, 1).paragraphs[0].style = document.styles["Normal"]
    for row, stage in enumerate((plan.beginning, plan.middle, plan.end), start=12):
        time = table.cell(row, 0).paragraphs[1]
        time.runs[0].text = f"({stage.minutes} мин)"
        _fill(table.cell(row, 1), stage.activities)
        resources = [stage.resources] if stage.resources else []
        if stage.module_positions:
            resources.append("Модульдер " + ", ".join(map(str, stage.module_positions)))
        _fill(table.cell(row, 6), "\n".join(resources))

    _fill(table.cell(17, 0), plan.differentiation)
    _fill(table.cell(17, 3), plan.assessment)
    _fill(table.cell(17, 4), plan.safety_and_connections)

    result = BytesIO()
    body = etree.tostring(document.element, encoding="UTF-8", xml_declaration=True, standalone=True)
    with ZipFile(TEMPLATE) as original, ZipFile(result, "w") as output:
        for part in original.infolist():
            output.writestr(part, body if part.filename == "word/document.xml" else original.read(part))
    result.seek(0)
    return result
