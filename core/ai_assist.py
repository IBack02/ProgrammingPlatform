import json
import re
from typing import Dict, Any, List, Optional

from django.utils import timezone
from openai import OpenAI

from .models import AiAssistMessage, Submission, TaskTestCase



CODE_BLOCK_RE = re.compile(r"```.*?```", re.DOTALL)
PY_LINE_RE = re.compile(r"^\s*(def |class |for |while |if |elif |else:|print\(|import |from )", re.MULTILINE)

def sanitize_no_code(text: str) -> str:
    t = (text or "").strip()
    t = CODE_BLOCK_RE.sub("[removed code block]", t)
    # если похоже на строки кода — вычищаем их
    lines = t.splitlines()
    cleaned = []
    for ln in lines:
        if PY_LINE_RE.search(ln):
            cleaned.append("[removed code-like line]")
        else:
            cleaned.append(ln)
    return "\n".join(cleaned).strip()


def build_prompt_snapshot(
    *, level: int, statement: str, constraints: str, visible_tests: List[Dict[str, str]],
    last_submission: Optional[Submission], last_submissions: List[Submission]
) -> str:
    """
    Собираем “снимок контекста” (задача + тесты + ошибки + 2-3 последние попытки)
    """
    parts = []
    parts.append(f"LEVEL={level}")
    parts.append("TASK_STATEMENT:\n" + (statement or ""))
    if constraints:
        parts.append("CONSTRAINTS:\n" + constraints)

    if visible_tests:
        vt = []
        for i, tc in enumerate(visible_tests[:2], start=1):
            vt.append(f"Example {i}:\nInput:\n{tc['stdin']}\nOutput:\n{tc['expected_stdout']}")
        parts.append("VISIBLE_TESTS:\n" + "\n\n".join(vt))

    if last_submission:
        parts.append("LAST_SUBMISSION:\n"
                     f"verdict={last_submission.verdict}\n"
                     f"stderr={last_submission.stderr}\n"
                     f"passed={last_submission.passed_tests}/{last_submission.total_tests}\n"
                     f"CODE:\n{last_submission.code}")

    if last_submissions:
        brief = []
        for s in last_submissions[-3:]:
            brief.append(f"attempt={s.attempt_no} verdict={s.verdict} passed={s.passed_tests}/{s.total_tests} err={s.stderr[:200]}")
        parts.append("LAST_3_ATTEMPTS_BRIEF:\n" + "\n".join(brief))

    return "\n\n".join(parts)


def call_openai_hint(level: int, prompt_snapshot: str) -> Dict[str, Any]:
    """
    Вызываем OpenAI Responses API со structured outputs:
    возвращаем dict со структурой {bullets, next_steps, ...}
    """
    client = OpenAI()

    if level == 1:
        system_rules = (
            "You are a strict programming tutor. "
            "Task: diagnose why the student's code fails. "
            "Rules: DO NOT provide any code, pseudocode, or step-by-step full solution. "
            "Only explain the reasons of errors and what part of logic is wrong. "
            "Use short bullets. Mention line/section references if possible. "
            "If the student code is correct, say so."
        )
        schema = {
            "name": "hint_level_1",
            "strict": True,
            "schema": {
                "type": "object",
                "properties": {
                    "bullets": {"type": "array", "items": {"type": "string"}},
                    "what_to_check": {"type": "array", "items": {"type": "string"}},
                    "no_code_confirmed": {"type": "boolean"}
                },
                "required": ["bullets", "what_to_check", "no_code_confirmed"],
                "additionalProperties": False
            }
        }
    else:
        system_rules = (
            "You are a strict programming tutor. "
            "Task: provide a textual solution path. "
            "Rules: DO NOT provide code, pseudocode, or near-code. "
            "Explain the approach in plain language only, focusing on steps and reasoning. "
            "Do not reveal the final algorithm in full detail; provide guidance."
        )
        schema = {
            "name": "hint_level_2",
            "strict": True,
            "schema": {
                "type": "object",
                "properties": {
                    "approach": {"type": "array", "items": {"type": "string"}},
                    "common_mistakes": {"type": "array", "items": {"type": "string"}},
                    "no_code_confirmed": {"type": "boolean"}
                },
                "required": ["approach", "common_mistakes", "no_code_confirmed"],
                "additionalProperties": False
            }
        }

    # Responses API + structured text.format :contentReference[oaicite:1]{index=1}
    resp = client.responses.create(
        model="gpt-4o-mini",
        input=[
            {"role": "system", "content": system_rules},
            {"role": "user", "content": prompt_snapshot},
        ],
        text={
            "format": {
                "type": "json_schema",
                "json_schema": schema,
            }
        },
        max_output_tokens=450,
    )

    # В SDK обычно resp.output_text содержит JSON строкой (в structured mode)
    raw = resp.output_text
    data = json.loads(raw)

    usage = getattr(resp, "usage", None)
    tokens_in = getattr(usage, "input_tokens", None) if usage else None
    tokens_out = getattr(usage, "output_tokens", None) if usage else None

    return {"data": data, "tokens_in": tokens_in, "tokens_out": tokens_out, "model": "gpt-4o-mini"}
