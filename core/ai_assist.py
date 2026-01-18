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
class HintLevel1(BaseModel):
    bullets: TList[str]
    what_to_check: TList[str]
    no_code_confirmed: bool


class HintLevel2(BaseModel):
    approach: TList[str]
    common_mistakes: TList[str]
    no_code_confirmed: bool


def call_openai_hint(level: int, prompt_snapshot: str) -> Dict[str, Any]:
    """
    Вариант A: используем responses.parse + Pydantic.
    Это автоматически формирует правильный text.format и избавляет от ошибок вида:
    "Missing required parameter: text.format.name".
    """
    client = OpenAI()

    if level == 1:
        system_rules = (
            "You are a strict programming tutor.\n"
            "Task: diagnose why the student's code fails.\n"
            "Rules:\n"
            "- DO NOT provide any code, pseudocode, or step-by-step full solution.\n"
            "- Only explain the reasons of errors and what part of logic is wrong.\n"
            "- Use short bullet points.\n"
            "- If possible, refer to line/section of the student's code.\n"
            "- If the student code is correct, say so.\n"
            "Output MUST follow the given JSON schema."
        )

        resp = client.responses.parse(
            model="gpt-4o-mini",
            input=[
                {"role": "system", "content": system_rules},
                {"role": "user", "content": prompt_snapshot},
            ],
            text_format=HintLevel1,
            max_output_tokens=450,
        )

        parsed = resp.output_parsed  # HintLevel1 instance

        data = {
            "bullets": parsed.bullets,
            "what_to_check": parsed.what_to_check,
            "no_code_confirmed": parsed.no_code_confirmed,
        }

    else:
        system_rules = (
            "You are a strict programming tutor.\n"
            "Task: provide a textual solution path.\n"
            "Rules:\n"
            "- DO NOT provide code, pseudocode, or near-code.\n"
            "- Explain the approach in plain language only.\n"
            "- Provide guidance, not a full ready-made algorithm.\n"
            "Output MUST follow the given JSON schema."
        )

        resp = client.responses.parse(
            model="gpt-4o-mini",
            input=[
                {"role": "system", "content": system_rules},
                {"role": "user", "content": prompt_snapshot},
            ],
            text_format=HintLevel2,
            max_output_tokens=450,
        )

        parsed = resp.output_parsed  # HintLevel2 instance

        data = {
            "approach": parsed.approach,
            "common_mistakes": parsed.common_mistakes,
            "no_code_confirmed": parsed.no_code_confirmed,
        }

    usage = getattr(resp, "usage", None)
    tokens_in = getattr(usage, "input_tokens", None) if usage else None
    tokens_out = getattr(usage, "output_tokens", None) if usage else None

    return {
        "data": data,
        "tokens_in": tokens_in,
        "tokens_out": tokens_out,
        "model": "gpt-4o-mini",
    }
