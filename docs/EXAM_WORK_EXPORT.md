# Individual exam work download

The teacher grading page has a **Download work as Word** link. It downloads a
`.docx` containing the exam title, student name and class, local submission date
with UTC offset, maximum score, teacher score and grading progress. An unsubmitted
attempt has an explicit status instead of an invented submission date. Missing
grades remain distinct from zero; partially graded work shows a provisional total.

Each question includes its prompt, answer options where applicable, the student's
saved answer, awarded score, maximum score and saved teacher feedback. Matching
answers use the attempt's shuffled presentation keys. Tables include both given
cells and student-entered cells. A final table contains the full mark scheme for
every question, including expected matching pairs and expected table contents.

Scores are calculated directly from `ExamAnswer.awarded_score` for the exam's
questions. The exporter never reads peer-assessment assignments, review scores,
review comments or the cached `ExamAttempt.total_score`. This is the same grade
field shown in the teacher's grading screen, including existing automatic grades
and later teacher overrides. Unsaved UI edits are not exported; the button's hint
explains that saved grades are used.

Illustrations and diagrams are clickable links rather than embedded images. A
diagram saved only as editor XML links to an authenticated `.drawio` download,
so an upload to Google Drive is not required. Those internal links require access
to the portal as the exam owner. The exporter does not fetch remote images, call
OpenAI or upload student data to another service.

Both downloads require an active teacher session and exact exam ownership.
Shared-template access and student access do not grant download permission.
Responses use attachment filenames and `no-store` cache headers. Normal exam
expiry logic runs before export, preserving teacher overrides.

## Deployment

`requirements.txt` adds `python-docx==1.2.0`. Redeploy on Render so dependencies
are installed. No migrations or environment variables are required. No static
assets were added for this feature.

## Verification

27 tests pass across `core.tests_exam_work_export`, `core.tests_exam_autograding`
and `core.tests_lesson_reports`. They cover all answer types, missing and zero
grades, teacher-only totals, mark schemes, localization, owner authorization,
diagram downloads, expiry, unsafe URLs and long Unicode answers. Django checks
and the no-migration check pass. A browser click downloaded a valid 38 KB DOCX
with all six sample questions and their mark-scheme rows.

The sample opens in Word 2013 as a three-page document. The packaged LibreOffice
renderer is unavailable on this Windows host; native Word inspection confirmed
readable question content. The Word window was subsequently closed, so a complete
rendered page-image review was not completed.
