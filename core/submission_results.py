"""Preserve every judge result, including missing results and original test inputs."""


def summarize_results(testcases, results, tokens, judge_failed=False):
    by_token = {r.token: r for r in results if getattr(r, "token", None)}
    rows = []
    verdicts = {3: "accepted", 4: "wrong_answer", 5: "time_limit", 6: "compilation_error"}
    for index, case in enumerate(testcases):
        token = tokens[index] if index < len(tokens) else None
        result = by_token.get(token) if token else None
        status = getattr(result, "status_id", None)
        completed = status is not None and status >= 3
        rows.append({
            "ordinal": case["ordinal"], "stdin": case["stdin"],
            "expected_stdout": case["expected_stdout"],
            "status_id": status, "verdict": verdicts.get(status, "runtime_error") if completed else "unavailable",
            "stdout": getattr(result, "stdout", "") or "",
            "stderr": (getattr(result, "stderr", "") or getattr(result, "compile_output", "")
                       or getattr(result, "message", "") or ("Execution result unavailable." if not completed else "")),
        })
    passed = sum(row["verdict"] == "accepted" for row in rows)
    failed = next((row for row in rows if row["verdict"] != "accepted"), None)
    verdict = "runtime_error" if judge_failed or any(row["verdict"] == "unavailable" for row in rows) else (failed["verdict"] if failed else "accepted")
    output = failed or (rows[-1] if rows else {})
    return rows, passed, verdict, output.get("stdout", ""), output.get("stderr", "")
