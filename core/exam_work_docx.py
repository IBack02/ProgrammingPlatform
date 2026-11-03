"""Word presentation for an exam attempt, using saved teacher grades only."""
import io
import re

from docx import Document
from docx.enum.table import WD_CELL_VERTICAL_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.opc.constants import RELATIONSHIP_TYPE
from docx.shared import Inches, Pt, RGBColor


LABELS = {
    "ru": {
        "title": "Экзаменационная работа", "student": "Ученик", "class": "Класс",
        "submitted": "Дата сдачи", "not_submitted": "Работа ещё не сдана",
        "total": "Набрано баллов по оценке учителя", "maximum": "Максимальный балл",
        "ungraded": "Не оценено", "progress": "Оценено заданий",
        "partial": "Оценивание не завершено. Сумма включает только выставленные баллы.",
        "instructions": "Инструкция", "work": "Задания и ответы", "question": "Задание",
        "answer": "Ответ ученика", "score": "Баллы учителя", "feedback": "Комментарий учителя",
        "empty": "Нет ответа", "options": "Варианты ответа", "left": "Элемент",
        "image": "Иллюстрация к заданию", "diagram": "Схема ученика",
        "scheme": "Марк схема экзамена", "expected": "Ожидаемый ответ и критерии оценивания",
        "no_scheme": "Не указаны", "row": "Строка", "out_of": "из",
    },
    "kk": {
        "title": "Емтихан жұмысы", "student": "Оқушы", "class": "Сынып",
        "submitted": "Тапсырған күні", "not_submitted": "Жұмыс әлі тапсырылған жоқ",
        "total": "Мұғалім бағасы бойынша жиналған балл", "maximum": "Ең жоғары балл",
        "ungraded": "Бағаланбаған", "progress": "Бағаланған тапсырмалар",
        "partial": "Бағалау аяқталмаған. Қосындыға тек қойылған балдар енгізілген.",
        "instructions": "Нұсқаулық", "work": "Тапсырмалар мен жауаптар", "question": "Тапсырма",
        "answer": "Оқушы жауабы", "score": "Мұғалім балы", "feedback": "Мұғалім пікірі",
        "empty": "Жауап жоқ", "options": "Жауап нұсқалары", "left": "Элемент",
        "image": "Тапсырма суреті", "diagram": "Оқушы сызбасы",
        "scheme": "Емтиханды бағалау сызбасы", "expected": "Күтілетін жауап және бағалау критерийлері",
        "no_scheme": "Көрсетілмеген", "row": "Жол", "out_of": "/",
    },
    "en": {
        "title": "Exam work", "student": "Student", "class": "Class",
        "submitted": "Submission date", "not_submitted": "Not submitted yet",
        "total": "Points awarded by the teacher", "maximum": "Maximum score",
        "ungraded": "Not graded", "progress": "Questions graded",
        "partial": "Grading is incomplete. The total includes only saved grades.",
        "instructions": "Instructions", "work": "Questions and answers", "question": "Question",
        "answer": "Student answer", "score": "Teacher score", "feedback": "Teacher feedback",
        "empty": "No answer", "options": "Answer options", "left": "Item",
        "image": "Question illustration", "diagram": "Student diagram",
        "scheme": "Exam mark scheme", "expected": "Expected answer and marking criteria",
        "no_scheme": "Not specified", "row": "Row", "out_of": "out of",
    },
}
_INVALID_XML = re.compile(r"[^\x09\x0a\x0d\x20-\ud7ff\ue000-\ufffd\U00010000-\U0010ffff]")


def text(value):
    return _INVALID_XML.sub("", str(value if value is not None else ""))


def number(value, labels):
    return labels["ungraded"] if value is None else format(value, ".2f").rstrip("0").rstrip(".")


def _field(doc, label, value):
    paragraph = doc.add_paragraph()
    paragraph.add_run(label + ": ").bold = True
    paragraph.add_run(text(value))


def _link(doc, label, url):
    paragraph = doc.add_paragraph()
    hyperlink = OxmlElement("w:hyperlink")
    hyperlink.set(qn("r:id"), paragraph.part.relate_to(url, RELATIONSHIP_TYPE.HYPERLINK, is_external=True))
    run = OxmlElement("w:r")
    properties = OxmlElement("w:rPr")
    color = OxmlElement("w:color")
    color.set(qn("w:val"), "075985")
    properties.append(color)
    underline = OxmlElement("w:u")
    underline.set(qn("w:val"), "single")
    properties.append(underline)
    run.append(properties)
    content = OxmlElement("w:t")
    content.text = text(label)
    run.append(content)
    hyperlink.append(run)
    paragraph._p.append(hyperlink)


def _table(doc, headers, rows, widths):
    table = doc.add_table(rows=1, cols=len(headers))
    table.autofit = False
    for column, width in zip(table.columns, widths):
        column.width = Inches(width)
    props = table._tbl.tblPr
    borders = OxmlElement("w:tblBorders")
    for edge in ("top", "left", "bottom", "right", "insideH", "insideV"):
        border = OxmlElement(f"w:{edge}")
        for key, value in (("val", "single"), ("sz", "4"), ("color", "D9D9D9")):
            border.set(qn(f"w:{key}"), value)
        borders.append(border)
    props.append(borders)
    header = OxmlElement("w:tblHeader")
    table.rows[0]._tr.get_or_add_trPr().append(header)
    for index, values in enumerate([headers, *rows]):
        cells = table.rows[0].cells if index == 0 else table.add_row().cells
        for column_index, (cell, value, width) in enumerate(zip(cells, values, widths)):
            cell.width = Inches(width)
            cell.vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.CENTER
            cell.text = text(value)
            margins = OxmlElement("w:tcMar")
            for edge in ("top", "left", "bottom", "right"):
                margin = OxmlElement(f"w:{edge}")
                margin.set(qn("w:w"), "110")
                margin.set(qn("w:type"), "dxa")
                margins.append(margin)
            cell._tc.get_or_add_tcPr().append(margins)
            if index == 0:
                shading = OxmlElement("w:shd")
                shading.set(qn("w:fill"), "E8EFF5")
                cell._tc.get_or_add_tcPr().append(shading)
            for paragraph in cell.paragraphs:
                paragraph.paragraph_format.space_after = Pt(3)
                paragraph.paragraph_format.space_before = Pt(3)
                paragraph.paragraph_format.keep_with_next = False
                if len(headers) == 3 and column_index in (0, 2):
                    paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
                for run in paragraph.runs:
                    run.font.size = Pt(10.5)
                    run.bold = index == 0
    doc.add_paragraph().paragraph_format.space_after = Pt(2)
    return table


def render_exam_work(data, language="ru"):
    labels = LABELS.get(language, LABELS["ru"])
    doc = Document()
    section = doc.sections[0]
    section.page_width, section.page_height = Inches(8.5), Inches(11)
    section.top_margin = section.bottom_margin = Inches(.7)
    section.left_margin = section.right_margin = Inches(.75)
    for name, size in (("Normal", 11), ("Title", 24), ("Heading 1", 16), ("Heading 2", 13)):
        style = doc.styles[name]
        style.font.name = "Arial"
        style.font.size = Pt(size)
        style.font.color.rgb = RGBColor(0, 0, 0)
        style.paragraph_format.space_after = Pt(8)
    doc.styles["Normal"].paragraph_format.line_spacing = 1.12
    doc.core_properties.title = text(data["exam_title"])
    doc.core_properties.author = "ProgaPlatform"
    doc.add_paragraph(labels["title"], style="Title")
    doc.add_paragraph(text(data["exam_title"])).runs[0].bold = True
    if data["topic"]:
        doc.add_paragraph(text(data["topic"]))
    _field(doc, labels["student"], data["student_name"])
    _field(doc, labels["class"], data["class_name"])
    _field(doc, labels["submitted"], data["submitted_at"] or labels["not_submitted"])
    _field(doc, labels["total"], number(data["teacher_score"], labels))
    _field(doc, labels["maximum"], number(data["maximum"], labels))
    _field(doc, labels["progress"], f'{data["graded_count"]} / {len(data["questions"])}')
    if data["graded_count"] < len(data["questions"]):
        doc.add_paragraph(labels["partial"])
    if data["instructions"]:
        doc.add_heading(labels["instructions"], level=1)
        doc.add_paragraph(text(data["instructions"]))
    doc.add_heading(labels["work"], level=1)
    for row in data["questions"]:
        doc.add_heading(f'{labels["question"]} {row["position"]}', level=2)
        doc.add_paragraph(text(row["prompt"]))
        if row["image_url"]:
            _link(doc, labels["image"], row["image_url"])
        if row["choices"]:
            doc.add_paragraph(labels["options"]).runs[0].bold = True
            for choice in row["choices"]:
                doc.add_paragraph(text(f'{choice["key"]}) {choice["text"]}'))
        if row["matching_options"]:
            _field(doc, labels["options"], "; ".join(row["matching_options"]))
        doc.add_paragraph(labels["answer"]).runs[0].bold = True
        if row["type"] == "matching":
            _table(doc, [labels["left"], labels["answer"]],
                   [[left, right or labels["empty"]] for left, right in row["matching_rows"]], [3.5, 3.5])
        elif row["type"] == "table" and row["table_columns"]:
            count = len(row["table_columns"])
            _table(doc, row["table_columns"],
                   [[value if value != "" else labels["empty"] for value in values] for values in row["table_rows"]],
                   [7 / count] * count)
        elif row["type"] == "diagram" and row["diagram_url"]:
            _link(doc, labels["diagram"], row["diagram_url"])
        else:
            doc.add_paragraph(text(row["answer_text"]) or labels["empty"])
        _field(doc, labels["score"], f'{number(row["score"], labels)} {labels["out_of"]} {number(row["maximum"], labels)}')
        if row["feedback"]:
            _field(doc, labels["feedback"], row["feedback"])
    heading = doc.add_heading(labels["scheme"], level=1)
    heading.paragraph_format.page_break_before = True
    scheme_rows = []
    for row in data["questions"]:
        expected = row["expected_text"]
        if row["expected_table_rows"]:
            expected = "\n".join(
                f'{labels["row"]} {index}: ' + "; ".join(f"{column}: {value}" for column, value in zip(row["table_columns"], values))
                for index, values in enumerate(row["expected_table_rows"], 1)
            )
        scheme_rows.append([str(row["position"]), expected or labels["no_scheme"], number(row["maximum"], labels)])
    _table(doc, [labels["question"], labels["expected"], labels["maximum"]], scheme_rows, [.65, 5.15, 1.2])
    output = io.BytesIO()
    doc.save(output)
    return output.getvalue()
