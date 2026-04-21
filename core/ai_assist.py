import os
import re
from typing import Dict, Any, List, Optional, Literal

from openai import OpenAI
from pydantic import BaseModel

from .models import Submission


CODE_BLOCK_RE = re.compile(r"```.*?```", re.DOTALL)
PY_LINE_RE = re.compile(
    r"^\s*(def |class |for |while |if |elif |else:|print\(|import |from )",
    re.MULTILINE,
)
FENCED_CODE_STRIP_RE = re.compile(r"^```(?:[a-zA-Z0-9_+\-#]*)?\s*|\s*```$", re.IGNORECASE | re.DOTALL)


def sanitize_no_code(text: str) -> str:
    t = (text or "").strip()
    t = CODE_BLOCK_RE.sub("[removed code block]", t)

    lines = t.splitlines()
    cleaned = []
    for ln in lines:
        if PY_LINE_RE.search(ln):
            cleaned.append("[removed code-like line]")
        else:
            cleaned.append(ln)
    return "\n".join(cleaned).strip()


def strip_code_fences(text: str) -> str:
    t = (text or "").strip()
    if t.startswith("```"):
        t = FENCED_CODE_STRIP_RE.sub("", t).strip()
    return t


def build_prompt_snapshot(
    *,
    level: int,
    programming_language: str,
    interface_language: str,
    statement: str,
    constraints: str,
    visible_tests: List[Dict[str, str]],
    last_submission: Optional[Submission],
    last_submissions: List[Submission],
) -> str:
    parts: List[str] = []
    parts.append(f"LEVEL={level}")
    parts.append(f"PROGRAMMING_LANGUAGE={programming_language}")
    parts.append(f"INTERFACE_LANGUAGE={interface_language}")
    parts.append("TASK_STATEMENT:\n" + (statement or ""))

    if constraints:
        parts.append("CONSTRAINTS:\n" + constraints)

    if visible_tests:
        vt: List[str] = []
        for i, tc in enumerate(visible_tests[:2], start=1):
            vt.append(
                f"Example {i}:\n"
                f"Input:\n{tc.get('stdin', '')}\n"
                f"Output:\n{tc.get('expected_stdout', '')}"
            )
        parts.append("VISIBLE_TESTS:\n" + "\n\n".join(vt))

    if last_submission:
        parts.append(
            "LAST_SUBMISSION:\n"
            f"verdict={last_submission.verdict}\n"
            f"stderr={last_submission.stderr}\n"
            f"passed={last_submission.passed_tests}/{last_submission.total_tests}\n"
            f"CODE:\n{last_submission.code}"
        )

    if last_submissions:
        brief: List[str] = []
        for s in last_submissions[-3:]:
            err = (s.stderr or "")[:200]
            brief.append(
                f"attempt={s.attempt_no} verdict={s.verdict} "
                f"passed={s.passed_tests}/{s.total_tests} err={err}"
            )
        parts.append("LAST_3_ATTEMPTS_BRIEF:\n" + "\n".join(brief))

    return "\n\n".join(parts)


def build_solution_prompt_snapshot(
    *,
    session_title: str,
    session_description: str,
    programming_language: str,
    interface_language: str,
    statement: str,
    constraints: str,
    visible_tests: List[Dict[str, str]],
    last_submission: Optional[Submission],
    last_submissions: List[Submission],
    top_fragment: str = "",
    bottom_fragment: str = "",
) -> str:
    parts: List[str] = []

    parts.append("SESSION_THEME_TITLE:\n" + (session_title or ""))
    if session_description:
        parts.append("SESSION_THEME_DESCRIPTION:\n" + session_description)
    parts.append(f"PROGRAMMING_LANGUAGE={programming_language}")
    parts.append(f"INTERFACE_LANGUAGE={interface_language}")

    parts.append("TASK_STATEMENT:\n" + (statement or ""))

    if constraints:
        parts.append("CONSTRAINTS:\n" + constraints)

    if top_fragment.strip():
        parts.append(
            "MANDATORY_TOP_CODE_FRAGMENT:\n"
            + top_fragment
            + "\n\n"
            + "This is required code that will be prepended before the student's code. "
              "Your solution must work with it, must not repeat its logic, and must complement it."
        )

    if bottom_fragment.strip():
        parts.append(
            "MANDATORY_BOTTOM_CODE_FRAGMENT:\n"
            + bottom_fragment
            + "\n\n"
            + "This is required code that will be appended after the student's code. "
              "Your solution must work with it, must not repeat its logic, and must complement it."
        )

    if visible_tests:
        vt: List[str] = []
        for i, tc in enumerate(visible_tests[:5], start=1):
            vt.append(
                f"Visible test {i}:\n"
                f"Input:\n{tc.get('stdin', '')}\n"
                f"Output:\n{tc.get('expected_stdout', '')}"
            )
        parts.append("VISIBLE_TESTS:\n" + "\n\n".join(vt))

    if last_submission:
        parts.append(
            "LAST_SUBMISSION:\n"
            f"verdict={last_submission.verdict}\n"
            f"stderr={last_submission.stderr}\n"
            f"passed={last_submission.passed_tests}/{last_submission.total_tests}\n"
            f"CODE:\n{last_submission.code}"
        )

    if last_submissions:
        attempts: List[str] = []
        for s in last_submissions[-5:]:
            err = (s.stderr or "")[:400]
            out = (s.stdout or "")[:400]
            attempts.append(
                f"attempt={s.attempt_no}\n"
                f"verdict={s.verdict}\n"
                f"passed={s.passed_tests}/{s.total_tests}\n"
                f"stdout={out}\n"
                f"stderr={err}\n"
                f"code:\n{s.code}"
            )
        parts.append("RECENT_ATTEMPTS:\n" + "\n\n".join(attempts))

    return "\n\n".join(parts)


class HintTextLevel1(BaseModel):
    text: str
    no_code_confirmed: bool


class HintTextLevel2(BaseModel):
    text: str
    no_code_confirmed: bool


class FullSolutionLevel3(BaseModel):
    code: str
class TheoryMaterialBlockSchema(BaseModel):
    ordinal: int
    block_type: Literal["heading", "text", "code", "image", "video"]
    heading_level: Optional[Literal["h1", "h2"]] = None
    content: str


class TheoryMaterialSchema(BaseModel):
    title: str
    blocks: List[TheoryMaterialBlockSchema]


class TheoryOpenAnswerEvaluation(BaseModel):
    is_correct: bool
    score: int
    feedback: str

def build_theory_material_prompt_snapshot(
    *,
    session_title: str,
    session_description: str,
    module_title: str,
    topic: str,
    teacher_prompt: str,
) -> str:
    parts: List[str] = []

    parts.append("SESSION_TITLE:\n" + (session_title or ""))
    if session_description:
        parts.append("SESSION_DESCRIPTION:\n" + session_description)

    parts.append("MODULE_TITLE:\n" + (module_title or ""))
    parts.append("MODULE_TOPIC:\n" + (topic or ""))

    if teacher_prompt:
        parts.append("TEACHER_PROMPT:\n" + teacher_prompt)

    return "\n\n".join(parts)


def build_theory_open_answer_prompt_snapshot(
    *,
    session_title: str,
    session_description: str,
    module_title: str,
    question_prompt: str,
    model_answer: str,
    student_answer: str,
    accept_suitable_answer: bool,
) -> str:
    parts: List[str] = []

    parts.append("SESSION_TITLE:\n" + (session_title or ""))
    if session_description:
        parts.append("SESSION_DESCRIPTION:\n" + session_description)
    if module_title:
        parts.append("MODULE_TITLE:\n" + module_title)

    parts.append("QUESTION:\n" + (question_prompt or ""))
    parts.append("MODEL_ANSWER:\n" + (model_answer or ""))
    parts.append("STUDENT_ANSWER:\n" + (student_answer or ""))
    parts.append(f"ACCEPT_SUITABLE_ANSWER={'yes' if accept_suitable_answer else 'no'}")

    return "\n\n".join(parts)

def _get_openai_client() -> OpenAI:
    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is not set in environment variables")
    return OpenAI(api_key=api_key)


def _extract_usage(resp) -> tuple[Optional[int], Optional[int]]:
    usage = getattr(resp, "usage", None)
    tokens_in = getattr(usage, "input_tokens", None) if usage else None
    tokens_out = getattr(usage, "output_tokens", None) if usage else None
    return tokens_in, tokens_out


def call_openai_hint(level: int, prompt_snapshot: str) -> dict:
    if level not in (1, 2):
        raise ValueError("call_openai_hint only supports levels 1 and 2")

    client = _get_openai_client()

    if level == 1:
        system_rules = (
            "You are a precise programming tutor.\n"
            "Read INTERFACE_LANGUAGE from user context and answer strictly in that language.\n"
            "Read PROGRAMMING_LANGUAGE from user context and reason only about that language.\n"
            "Level 1 objective: identify the exact place of the main error and explain the root cause.\n"
            "Reference a concrete code area from LAST_SUBMISSION (function, loop, condition, variable, expression, or output formatting).\n"
            "Do not provide a full solution and do not provide code.\n"
            "Do not output pseudocode.\n"
            "Use 2-4 concise sentences.\n"
            "Output schema: {text: string, no_code_confirmed: boolean}."
        )
        resp = client.responses.parse(
            model="gpt-4o-mini",
            input=[
                {"role": "system", "content": system_rules},
                {"role": "user", "content": prompt_snapshot},
            ],
            text_format=HintTextLevel1,
            max_output_tokens=460,
        )
        parsed = resp.output_parsed
        if parsed is None:
            raise RuntimeError("OpenAI returned no parsed output for hint level 1")

        data = {
            "text": (parsed.text or "").strip(),
            "no_code_confirmed": bool(parsed.no_code_confirmed),
        }

    else:
        system_rules = (
            "You are a precise programming tutor.\n"
            "Write a SHORT guidance: exactly 2–3 sentences, no bullets, no lists.\n"
            "Read PROGRAMMING_LANGUAGE from user context and reason only about that language.\n"
            "Level 2 objective: give concrete next actions to make the solution pass.\n"
            "State exactly what should be added or changed and where (input parsing, data structure, loop logic, condition, function, output format).\n"
            "Do not provide code and do not provide pseudocode.\n"
            "Use 2-5 concise sentences, no bullets.\n"
            "Output schema: {text: string, no_code_confirmed: boolean}.\n"
            "Set no_code_confirmed=true only if no code-like fragments were produced."
        )
        resp = client.responses.parse(
            model="gpt-4o-mini",
            input=[
                {"role": "system", "content": system_rules},
                {"role": "user", "content": prompt_snapshot},
            ],
            text_format=HintTextLevel2,
            max_output_tokens=420,
        )
        parsed = resp.output_parsed
        if parsed is None:
            raise RuntimeError("OpenAI returned no parsed output for hint level 2")

        data = {
            "text": (parsed.text or "").strip(),
            "no_code_confirmed": bool(parsed.no_code_confirmed),
        }

    tokens_in, tokens_out = _extract_usage(resp)

    return {
        "data": data,
        "tokens_in": tokens_in,
        "tokens_out": tokens_out,
        "model": "gpt-4o-mini",
    }


def call_openai_solution(prompt_snapshot: str) -> dict:
    client = _get_openai_client()

    system_rules = (
        "You are a programming teacher.\n"
        "Read PROGRAMMING_LANGUAGE from user context and return solution code only in that language.\n"
        "Read INTERFACE_LANGUAGE from user context, but still return only code without explanations.\n"
        "Main priority: simplicity and readability for students.\n"
        "Do not use comments.\n"
        "If mandatory code fragments are provided, treat them as required and complement them without duplicating their logic.\n"
        "Return only the student's writable code.\n"
        "Output schema: {code: string}."
    )

    resp = client.responses.parse(
        model="gpt-4o-mini",
        input=[
            {"role": "system", "content": system_rules},
            {"role": "user", "content": prompt_snapshot},
        ],
        text_format=FullSolutionLevel3,
        max_output_tokens=1400,
    )

    parsed = resp.output_parsed
    if parsed is None:
        raise RuntimeError("OpenAI returned no parsed output for full solution")

    code = strip_code_fences(parsed.code or "").strip()
    if not code:
        raise RuntimeError("OpenAI returned empty solution code")

    tokens_in, tokens_out = _extract_usage(resp)

    return {
        "data": {"code": code},
        "tokens_in": tokens_in,
        "tokens_out": tokens_out,
        "model": "gpt-4o-mini",
    }
def call_openai_theory_material(prompt_snapshot: str) -> dict:
    client = _get_openai_client()

    system_rules = (
        "You are a Python teacher creating a theory learning module for students.\n"
        "Return a structured lesson using blocks only.\n"
        "Allowed block types: heading, text, code, image, video.\n"
        "For heading blocks, heading_level must be h1 or h2.\n"
        "For text, code, image and video blocks, heading_level must be null or omitted.\n"
        "For image blocks, content must be a direct image URL.\n"
        "For video blocks, content must be a YouTube URL.\n"
        "The lesson must be simple, clear, beginner-friendly, and aligned with the session theme.\n"
        "Code examples must be valid Python.\n"
        "Do not include markdown fences.\n"
        "Do not include explanations outside the schema.\n"
        "Output must match schema: {title: string, blocks: TheoryMaterialBlockSchema[] }."
    )

    resp = client.responses.parse(
        model="gpt-4o-mini",
        input=[
            {"role": "system", "content": system_rules},
            {"role": "user", "content": prompt_snapshot},
        ],
        text_format=TheoryMaterialSchema,
        max_output_tokens=2200,
    )

    parsed = resp.output_parsed
    if parsed is None:
        raise RuntimeError("OpenAI returned no parsed output for theory material")

    blocks = []
    for block in parsed.blocks:
        content = (block.content or "").strip()
        if not content:
            continue

        blocks.append({
            "ordinal": int(block.ordinal),
            "block_type": block.block_type,
            "heading_level": (block.heading_level or ""),
            "content": content,
        })

    if not blocks:
        raise RuntimeError("OpenAI returned empty theory material blocks")

    tokens_in, tokens_out = _extract_usage(resp)

    return {
        "data": {
            "title": (parsed.title or "").strip(),
            "blocks": blocks,
        },
        "tokens_in": tokens_in,
        "tokens_out": tokens_out,
        "model": "gpt-4o-mini",
    }


def call_openai_theory_open_answer(prompt_snapshot: str) -> dict:
    client = _get_openai_client()

    system_rules = (
        "You are a fair teacher grading a student's answer to a theory question in a Python learning platform.\n"
        "Compare the student's answer with the model answer.\n"
        "If ACCEPT_SUITABLE_ANSWER=yes, accept meaningfully correct paraphrases or equivalent correct explanations.\n"
        "If ACCEPT_SUITABLE_ANSWER=no, be stricter and require close alignment with the model answer.\n"
        "Return concise feedback for the student in 1-2 sentences.\n"
        "Output must match schema: {is_correct: boolean, score: integer, feedback: string}.\n"
        "score must be 0 to 100."
    )

    resp = client.responses.parse(
        model="gpt-4o-mini",
        input=[
            {"role": "system", "content": system_rules},
            {"role": "user", "content": prompt_snapshot},
        ],
        text_format=TheoryOpenAnswerEvaluation,
        max_output_tokens=250,
    )

    parsed = resp.output_parsed
    if parsed is None:
        raise RuntimeError("OpenAI returned no parsed output for theory open answer")

    tokens_in, tokens_out = _extract_usage(resp)

    return {
        "data": {
            "is_correct": bool(parsed.is_correct),
            "score": max(0, min(100, int(parsed.score))),
            "feedback": (parsed.feedback or "").strip(),
        },
        "tokens_in": tokens_in,
        "tokens_out": tokens_out,
        "model": "gpt-4o-mini",
    }
