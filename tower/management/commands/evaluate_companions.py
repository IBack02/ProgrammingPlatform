import json
from pathlib import Path
from types import SimpleNamespace

from django.core.management.base import BaseCommand, CommandError

from tower.character_contracts import load_behavior_profiles
from tower.companion_ai import generate_companion_reply
from tower.companion_personalities import behavior_instructions


CASES = [
    ("unfinished", {"title":"Two numbers", "statement":"Read two integers and output their sum.", "status":"in_progress"}),
    ("teacher_feedback", {"title":"Loops", "question":"Describe when the repetition stops.",
        "comment":"Check the final value included in your loop.", "score":"1", "max_score":"2"}),
    ("lesson_fact", {"title":"Variables", "topic":"Names and values",
        "text":"A variable associates a name with a value. Assigning a new value to that name changes which value is used later."}),
]


class Command(BaseCommand):
    help = "Validate all companion prompts; --live runs bounded artificial examples against the configured model."

    def add_arguments(self, parser):
        parser.add_argument("--live", action="store_true")
        parser.add_argument("--limit", type=int, default=36)
        parser.add_argument("--output", default="artifacts/tower/character-sets/companion-evaluation.json")

    def handle(self, *args, **options):
        profiles = load_behavior_profiles().profiles
        results = []
        limit = max(1, min(108, options["limit"]))
        for index, profile in enumerate(profiles):
            for language_index, language in enumerate(("ru", "kk", "en")):
                if len(results) >= limit:
                    break
                kind, context = CASES[(index + language_index) % len(CASES)]
                row = {"hero":profile.hero_id, "language":language, "source_kind":kind}
                behavior_instructions(profile.hero_id, language, profile_data=profile.model_dump(mode="json"))
                if options["live"]:
                    message = SimpleNamespace(hero_id=profile.hero_id, language=language,
                        behavior_snapshot=profile.model_dump(mode="json"), source_kind=kind, source_snapshot={"context":context})
                    try:
                        generated = generate_companion_reply(message)
                        row.update(ok=True, text=generated.text, tokens_in=generated.tokens_in,
                                   tokens_out=generated.tokens_out, duration_ms=generated.duration_ms, model=generated.model)
                    except Exception as exc:
                        row.update(ok=False, error_type=type(exc).__name__)
                else:
                    row.update(ok=True, check="prompt_only")
                results.append(row)
                if len(results) % 6 == 0:
                    self.stdout.write(f"Validated {len(results)} examples.")
        report = {"live":options["live"], "results":results,
            "manual_rubric":["faithful_to_source", "recognizable_character", "kind_tone", "no_solution_disclosure"],
            "manual_review_required":options["live"]}
        output = Path(options["output"])
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
        self.stdout.write(f"Results: {len(results)} examples; {sum(not row['ok'] for row in results)} failures. Report: {output}")
        if any(not row["ok"] for row in results):
            raise CommandError("Some evaluation cases failed; see the report (no credentials recorded).")
