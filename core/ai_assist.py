import re
from typing import Dict, Any, List, Optional

from openai import OpenAI
from pydantic import BaseModel
from typing import List as TList  # avoid shadowing above

from .models import Submission


# --- Anti-code sanitization (final safety net) ---
CODE_BLOCK_RE = re.compile(r"```.*?```", re.DOTALL)
PY_LINE_RE = re.compile(
    r"^\s*(def |class |for |while |if |elif |else:|print\(|import |from )",
    re.MULTILINE,
)


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


def build_prompt_snapshot(
    *,
    level: int,
    statement: str,
    constraints: str,
    visible_tests: List[Dict[str, str]],
    last_submission: Optional[Submission],
    last_submissions: List[Submission],
) -> str:
    """
    Собираем “снимок контекста” (задача + тесты + ошибки + 2–3 последние попытки)
    """
    parts: List[str] = []
    parts.append(f"LEVEL={level}")
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


# --- Structured outputs models (Pydantic) ---
class HintTextLevel1(BaseModel):
    text: str
    no_code_confirmed: bool


class HintTextLevel2(BaseModel):
    text: str
    no_code_confirmed: bool


def call_openai_hint(level: int, prompt_snapshot: str) -> dict:
    """
    Вызов OpenAI Responses API через responses.parse (structured output).
    Возвращаем: {"data": {"text": "...", "no_code_confirmed": True}, ...}
    """
    client = OpenAI()

    if level == 1:
        system_rules = (
            "You are a programming teacher speaking to a student in a natural, conversational tone.\n"
            "Your job: explain WHY the student's solution fails (errors or wrong logic), without solving the task.\n"
            "Rules:\n"
            "- Write a coherent teacher-like explanation as full sentences (no bullet lists, no numbered sections).\n"
            "- You MAY point to a specific line or a part of a line where the mistake is likely located, "
            "e.g. 'Around line 15 your condition is wrong...' or 'In the loop condition you...'.\n"
            "- Explain the reason clearly: what exactly breaks and why (type conversion, input format, loop condition, edge cases).\n"
            "- DO NOT provide code, pseudocode, or step-by-step full solution.\n"
            "- DO NOT show corrected code fragments.\n"
            "- If the code seems correct, say it and suggest what to verify (format, trailing spaces, reading input).\n"
            "Output must match schema: {text: string, no_code_confirmed: boolean}.\n"
            "Set no_code_confirmed=true only if you did not output any code-like content."
        )

        resp = client.responses.parse(
            model="gpt-4o-mini",
            input=[
                {"role": "system", "content": system_rules},
                {"role": "user", "content": prompt_snapshot},
            ],
            text_format=HintTextLevel1,
            max_output_tokens=380,
        )
        parsed = resp.output_parsed

        data = {
            "text": (parsed.text or "").strip(),
            "no_code_confirmed": bool(parsed.no_code_confirmed),
        }

    else:
        system_rules = (
            "You are a programming teacher speaking to a student in a natural, conversational tone.\n"
            "Your job: give a TEXT-ONLY solution path (guidance), not the final solution.\n"
            "Rules:\n"
            "- Write a coherent explanation as full sentences (no bullet lists, no numbered sections).\n"
            "- You MAY mention very short method/command names that are relevant, e.g. 'use split', 'use sort', "
            "'use a set', 'use a dictionary', 'use two pointers', 'use binary search' — but DO NOT write code or pseudocode.\n"
            "- Keep it as guidance: what to do conceptually and what to watch out for, without giving a ready-made algorithm.\n"
            "- DO NOT provide code, pseudocode, or near-code.\n"
            "- DO NOT provide exact final formulas if that fully solves it; explain the approach instead.\n"
            "Output must match schema: {text: string, no_code_confirmed: boolean}.\n"
            "Set no_code_confirmed=true only if you did not output any code-like content."
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

        data = {
            "text": (parsed.text or "").strip(),
            "no_code_confirmed": bool(parsed.no_code_confirmed),
        }
        usage = getattr(resp, "usage", None)
        tokens_in = getattr(usage, "input_tokens", None) if usage else None
        tokens_out = getattr(usage, "output_tokens", None) if usage else None
        return {"data": data, "tokens_in": tokens_in, "tokens_out": tokens_out, "model": "gpt-4o-mini"}