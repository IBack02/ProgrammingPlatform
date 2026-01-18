import base64
import os
import time
from dataclasses import dataclass
from typing import List, Dict, Any, Optional

import requests


# Лучше потом подтянуть /languages и выбрать python3 по имени,
# но для MVP оставим один ID. Если не сработает — быстро поменяем.
PYTHON_LANGUAGE_ID = 71


@dataclass
class Judge0Item:
    token: str
    status_id: int
    status_desc: str
    stdout: str
    stderr: str
    compile_output: str
    message: str


def _b64(s: str) -> str:
    return base64.b64encode((s or "").encode("utf-8")).decode("ascii")


def _b64decode(s: Optional[str]) -> str:
    if not s:
        return ""
    try:
        return base64.b64decode(s).decode("utf-8", errors="replace")
    except Exception:
        return s


def _headers() -> Dict[str, str]:
    key = os.getenv("JUDGE0_RAPIDAPI_KEY", "").strip()
    host = os.getenv("JUDGE0_RAPIDAPI_HOST", "").strip()
    if key and host:
        return {"X-RapidAPI-Key": key, "X-RapidAPI-Host": host}
    return {}


def _base_url() -> str:
    return os.getenv("JUDGE0_BASE_URL", "https://judge0-ce.p.rapidapi.com").rstrip("/")


def create_batch_submissions(code: str, testcases: List[Dict[str, str]]) -> List[str]:
    """
    testcases: [{"stdin": "...", "expected_stdout": "..."}, ...]
    Возвращает список tokens.
    Batch endpoint: POST /submissions/batch :contentReference[oaicite:2]{index=2}
    """
    url = f"{_base_url()}/submissions/batch?base64_encoded=true"

    submissions = []
    for tc in testcases:
        submissions.append({
            "language_id": PYTHON_LANGUAGE_ID,
            "source_code": _b64(code),
            "stdin": _b64(tc.get("stdin", "")),
            # Важно: Judge0 умеет сравнивать expected_output и ставить статус Wrong Answer. :contentReference[oaicite:3]{index=3}
            "expected_output": _b64(tc.get("expected_stdout", "")),
        })

    payload = {"submissions": submissions}

    r = requests.post(url, json=payload, headers=_headers(), timeout=25)
    r.raise_for_status()
    data = r.json()

    # ожидаем: [{"token":"..."}, {"token":"..."}, ...]
    tokens = [item.get("token") for item in data if isinstance(item, dict)]
    if not tokens or any(t is None for t in tokens):
        raise RuntimeError(f"Judge0 batch did not return tokens: {data}")
    return tokens


def get_batch_results(tokens: List[str]) -> List[Judge0Item]:
    """
    GET /submissions/batch?tokens=... :contentReference[oaicite:1]{index=1}
    Judge0 возвращает объект: {"submissions":[...]}
    """
    tok = ",".join(tokens)

    # Оптимизация: берем status_id вместо status (меньше данных)
    fields = "token,stdout,stderr,compile_output,message,status_id"
    url = f"{_base_url()}/submissions/batch?base64_encoded=true&tokens={tok}&fields={fields}"

    r = requests.get(url, headers=_headers(), timeout=25)
    r.raise_for_status()
    data = r.json()

    # ВАЖНО: response может быть dict с ключом "submissions"
    rows = data.get("submissions") if isinstance(data, dict) else data
    if not isinstance(rows, list):
        raise RuntimeError(f"Unexpected Judge0 batch response: {data}")

    items: List[Judge0Item] = []
    for row in rows:
        if not isinstance(row, dict):
            # на всякий случай
            items.append(Judge0Item(
                token="",
                status_id=0,
                status_desc="Invalid response row",
                stdout="",
                stderr="",
                compile_output="",
                message=str(row),
            ))
            continue

        status_id = int(row.get("status_id") or 0)

        items.append(Judge0Item(
            token=row.get("token", "") or "",
            status_id=status_id,
            status_desc="",  # мы не запрашиваем description ради экономии
            stdout=_b64decode(row.get("stdout")),
            stderr=_b64decode(row.get("stderr")),
            compile_output=_b64decode(row.get("compile_output")),
            message=_b64decode(row.get("message")),
        ))

    return items



def wait_batch(tokens: List[str], timeout_sec: int = 25, poll_interval: float = 0.8) -> List[Judge0Item]:
    """
    Пуллим до тех пор, пока все не выйдут из In Queue/Processing.
    Статусы: IN_QUEUE=1, PROCESSING=2, ACCEPTED=3, WRONG_ANSWER=4, TLE=5, CE=6, RUNTIME_ERROR=7+ :contentReference[oaicite:5]{index=5}
    """
    start = time.time()
    while True:
        items = get_batch_results(tokens)
        pending = [it for it in items if it.status_id in (1, 2)]
        if not pending:
            return items
        if time.time() - start > timeout_sec:
            return items
        time.sleep(poll_interval)
