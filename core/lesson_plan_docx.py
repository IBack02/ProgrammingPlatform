"""Fill the supplied school lesson plan without rebuilding its Word layout."""
from io import BytesIO
from pathlib import Path
from zipfile import ZipFile

from docx import Document
from docx.shared import Pt

from .exam_work_docx import text as xml_text
from .lesson_plan_labels import DOCUMENT_LABELS, FORM_TRANSLATIONS
from .nis_values import NIS_VALUES


TEMPLATE = Path(__file__).with_name("lesson_plan_template.docx")


def _replace_label(paragraph, value):
    runs = paragraph.runs
    for run in runs:
        run.text = ""
    if isinstance(value, tuple):
        label, guidance = value
        runs[0].text = label
        next(run for run in runs if run.italic).text = guidance
    else:
        runs[0].text = value


def load_lesson_plan_template(language):
    document = Document(TEMPLATE)
    table = document.tables[0]
    if language == "kk":
        _replace_label(table.cell(7, 0).paragraphs[0], "NIS құндылықтары іс-әрекетте")
        _replace_label(table.cell(16, 4).paragraphs[3], "NIS құндылықтары іс-әрекетте (тәрбие элементі)")
        return document
    translation_index = {"ru": 0, "en": 1}[language]
    for (row, column, index), translations in FORM_TRANSLATIONS.items():
        _replace_label(table.cell(row, column).paragraphs[index], translations[translation_index])
    _replace_label(document.sections[0].header.paragraphs[0], DOCUMENT_LABELS[language]["title"])
    return document


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


def _activity_text(activity, language):
    return "\n".join([
        activity.description,
        *(f"{NIS_VALUES[link.value][language]} — {link.connection}" for link in activity.value_links),
    ])


def render_lesson_plan(document, *, language, teacher, date, classes, plan):
    labels = DOCUMENT_LABELS[language]
    table = document.tables[0]
    _add_text(table.cell(0, 0).paragraphs[1], plan.lesson_title)
    _add_text(table.cell(1, 2).paragraphs[1], teacher)
    _add_text(table.cell(2, 0).paragraphs[0], date)
    _add_text(table.cell(2, 2).paragraphs[0], classes)

    stages = (plan.beginning, plan.middle, plan.end)
    used_values = dict.fromkeys(
        link.value for stage in stages for activity in stage.activities for link in activity.value_links
    )
    values_summary = ", ".join(NIS_VALUES[value][language] for value in used_values)
    for row, value in enumerate((
        plan.learning_objective, plan.lesson_objective, plan.criteria, plan.language_objectives,
        values_summary, plan.interdisciplinary, plan.ict, plan.prior_knowledge,
    ), start=3):
        _fill(table.cell(row, 1), value)

    table.cell(14, 1).paragraphs[0].style = document.styles["Normal"]
    for row, stage in enumerate(stages, start=12):
        time = table.cell(row, 0).paragraphs[1]
        time.runs[0].text = f"({stage.minutes} {labels['minutes']})"
        _fill(table.cell(row, 1), "\n\n".join(_activity_text(activity, language) for activity in stage.activities))
        resources = [stage.resources] if stage.resources else []
        if stage.module_positions:
            resource_label = labels["module"] if len(stage.module_positions) == 1 else labels["modules"]
            resources.append(resource_label + " " + ", ".join(map(str, stage.module_positions)))
        _fill(table.cell(row, 6), "\n".join(resources))

    _fill(table.cell(17, 0), plan.differentiation)
    _fill(table.cell(17, 3), plan.assessment)
    _fill(table.cell(17, 4), plan.safety_and_connections)

    for paragraph in table.cell(18, 0).paragraphs:
        paragraph.paragraph_format.keep_with_next = True
    # Word requires a paragraph after the table. Keep it from adding a blank page.
    trailing = document.paragraphs[-1].paragraph_format
    trailing.space_before = trailing.space_after = Pt(0)
    trailing.line_spacing = Pt(1)

    result = BytesIO()
    updated_parts = {"word/document.xml": document.part.blob}
    if language != "kk":
        header = document.sections[0].header.part
        updated_parts[str(header.partname).lstrip("/")] = header.blob
    with ZipFile(TEMPLATE) as original, ZipFile(result, "w") as output:
        for part in original.infolist():
            content = updated_parts.get(part.filename)
            output.writestr(part, content if content is not None else original.read(part))
    result.seek(0)
    return result
