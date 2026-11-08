"""Convert common chatbot spoiler markup into native theory blocks."""
import re
from html.parser import HTMLParser


DETAILS_TAG = re.compile(r"</?details\b[^<>]*>", re.IGNORECASE)
SUMMARY_TAG = re.compile(r"</?summary\b[^<>]*>", re.IGNORECASE)
DISCLOSURE_START = re.compile(r"<details\b[^<>]*>\s*<summary\b[^<>]*>", re.IGNORECASE)
DETAILS = re.compile(
    r"<details\b[^<>]*>\s*(?:<summary\b[^<>]*>(.*?)</summary\s*>)?(.*?)</details\s*>",
    re.IGNORECASE | re.DOTALL,
)


class _PlainText(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts = []

    def handle_data(self, data):
        self.parts.append(data)

    def handle_starttag(self, tag, attrs):
        if tag == "br":
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in {"p", "li", "div", "pre"}:
            self.parts.append("\n")


def _plain_text(fragment):
    parser = _PlainText()
    parser.feed(fragment)
    parser.close()
    return "".join(parser.parts).strip()


def _split_disclosure(content):
    error = "Use hidden_text for a reveal. An HTML <details> block is incomplete or nested."
    offset = 0
    for match in DETAILS.finditer(content):
        before = content[offset:match.start()].strip()
        summary, answer = match.groups()
        if DETAILS_TAG.search(before) or DETAILS_TAG.search(answer) or SUMMARY_TAG.search(answer):
            raise ValueError(error)
        if before:
            yield "text", before
        if summary and (label := _plain_text(summary)):
            yield "text", label
        answer = _plain_text(answer)
        if not answer:
            raise ValueError("A reveal must contain answer text in its hidden_text block.")
        yield "hidden_text", answer
        offset = match.end()
    after = content[offset:].strip()
    if DETAILS_TAG.search(after):
        raise ValueError(error)
    if after:
        yield "text", after


def normalize_theory_disclosures(blocks):
    """Keep the original order while making room for any split blocks."""
    result = []
    next_ordinal = 1
    for block in sorted(blocks, key=lambda row: row["ordinal"]):
        next_ordinal = max(next_ordinal, block["ordinal"])
        content = block["content"]
        if block["block_type"] in {"text", "hidden_text"} and (
            DETAILS.search(content) or DISCLOSURE_START.search(content)
        ):
            parts = list(_split_disclosure(content))
            if block["block_type"] == "hidden_text":
                parts = [("hidden_text", "\n\n".join(text for _, text in parts))]
            for kind, text in parts:
                result.append({
                    "ordinal": next_ordinal,
                    "block_type": kind,
                    "heading_level": "",
                    "content": text,
                })
                next_ordinal += 1
        else:
            result.append({**block, "ordinal": next_ordinal})
            next_ordinal += 1
    return result
