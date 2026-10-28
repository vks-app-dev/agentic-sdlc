"""Open questions the agents leave for people, answered from the dashboard during a review.

Agents write them in the stage's main document under `## Open questions`:

    - [ ] Q3: How is the user identified (login, API key, token)?
      **Answer:**

A person's answer is written back in place (`- [x] Q3: …` / `**Answer:** …`), so the next agents read it. Options the
agents offer as "(a) … (b) …" are picked up so the dashboard can show them as choices.
"""

import json
import os
import re
import subprocess
from pathlib import Path
from typing import Any

from . import events, state

_ITEM = re.compile(r"^- \[( |x|X)\] (Q\d+)\b[:\s]*(.*)$")
_ANSWER = re.compile(r"^\s*\*\*Answer:\*\*\s*(.*)$")


def document(root: Path, item: dict[str, Any], stage: str | None = None) -> Path | None:
    """The document whose open questions a stage's review is about."""
    stage = stage or item["current_stage"]
    req = item["id"]
    p = {"design": root / "docs" / "design" / req / "README.md"}.get(stage, root / "specs" / f"{req}.md")
    return p if p.exists() else None


def _section(lines: list[str]) -> tuple[int, int] | None:
    """The Open questions section that holds questions (a document can have more than one such heading)."""
    found = []
    for start, ln in enumerate(lines):
        if re.match(r"^## .*open questions", ln, re.I):
            end = next((i for i in range(start + 1, len(lines)) if lines[i].startswith("## ")), len(lines))
            found.append((sum(bool(_ITEM.match(x)) for x in lines[start:end]), start, end))
    if not found:
        return None
    _, start, end = max(found)
    return start, end


def _options(text: str) -> list[dict[str, str]]:
    marks = list(re.finditer(r"\(([a-e])\)\s+", text))
    if len(marks) < 2:
        return []
    out = []
    for i, m in enumerate(marks):
        end = marks[i + 1].start() if i + 1 < len(marks) else len(text)
        label = re.split(r"(?<=[.;])\s+(?=[A-Z])", text[m.end():end].strip())[0].rstrip(";.,")
        out.append({"key": m.group(1), "label": label[:140]})
    return out


def parse(text: str) -> list[dict[str, Any]]:
    lines = text.splitlines()
    sec = _section(lines)
    if not sec:
        return []
    out: list[dict[str, Any]] = []
    for i in range(*sec):
        m = _ITEM.match(lines[i])
        if not m:
            continue
        body = m.group(3).strip()
        answer = ""
        j = i + 1
        while j < sec[1] and not _ITEM.match(lines[j]) and not lines[j].startswith("## "):
            a = _ANSWER.match(lines[j])
            if a:
                answer = a.group(1).strip()
            j += 1
        out.append({"id": m.group(2), "text": body, "answered": bool(answer) or m.group(1) != " ",
                    "answer": answer, "blocking": bool(re.search(r"\bblocking\b", body, re.I)),
                    "options": _options(body)})
    return out


def for_item(root: Path, item: dict[str, Any], stage: str | None = None) -> dict[str, Any]:
    p = document(root, item, stage)
    qs = parse(p.read_text()) if p else []
    return {"file": str(p.relative_to(root)) if p else None, "questions": qs,
            "open": sum(not q["answered"] for q in qs),
            "blocking_open": sum(q["blocking"] and not q["answered"] for q in qs)}


def answer(root: Path, item: dict[str, Any], answers: dict[str, str], by: str) -> dict[str, Any]:
    """Write answers in place: the box is ticked and the answer goes on its `**Answer:**` line."""
    p = document(root, item)
    if not p:
        raise state.StateError("This stage has no document with open questions")
    lines = p.read_text().splitlines()
    sec = _section(lines)
    if not sec:
        raise state.StateError(f"{p.name} has no Open questions section")
    answers = {k: " ".join(v.split()) for k, v in answers.items() if v.strip()}
    done, i = [], sec[0]
    while i < len(lines):
        m = _ITEM.match(lines[i])
        if m and m.group(2) in answers:
            qid = m.group(2)
            lines[i] = re.sub(r"^- \[ \]", "- [x]", lines[i])  # tick it, keep the question as the agents wrote it
            j = i + 1
            while j < len(lines) and not _ITEM.match(lines[j]) and not lines[j].startswith("## "):
                if _ANSWER.match(lines[j]):
                    break
                j += 1
            text = f"  **Answer:** {answers[qid]} _({by})_"
            if j < len(lines) and _ANSWER.match(lines[j]):
                lines[j] = text
            else:
                lines.insert(i + 1, text)
            done.append(qid)
        i += 1
    p.write_text("\n".join(lines) + "\n")
    if done:
        events.emit(root, "questions_answered", req=item["id"], by=by, detail=f"{', '.join(done)} in {p.name}")
    return {**for_item(root, state.load(root, item["id"])), "saved": done}


def suggest(root: Path, item: dict[str, Any], timeout: int = 240) -> dict[str, Any]:
    """Draft answers for the open questions with one read-only Claude Code call, for a person to review."""
    info = for_item(root, item)
    todo = [q for q in info["questions"] if not q["answered"]]
    if not todo:
        return {"answers": {}, "cost_usd": 0}
    doc = (root / info["file"]).read_text()
    listing = "\n".join(f"{q['id']}: {q['text']}" for q in todo)
    prompt = ("Agents writing the requirements for a software work item left open questions for a person. Draft a short, "
              "concrete recommended answer for each, based on the document and the repository (read-only). When the "
              "question offers options (a), (b)…, pick one and say why in one sentence. If it truly needs a business "
              "decision you cannot infer, give the safest sensible default and say it is an assumption.\n"
              "Reply with ONLY a JSON object mapping question id to answer text, e.g. {\"Q1\": \"...\"}.\n\n"
              f"Questions:\n{listing}\n\nDocument ({info['file']}):\n{doc[:30000]}")
    env = {**os.environ}
    if os.environ.get("SDLC_RUN_AUTH") == "login":
        env.pop("ANTHROPIC_API_KEY", None)
    try:
        r = subprocess.run([os.environ.get("SDLC_CLAUDE_BIN", "claude"), "-p", prompt, "--output-format", "json",
                            "--model", os.environ.get("SDLC_SUGGEST_MODEL") or "sonnet",
                            "--allowedTools", "Read", "Grep", "Glob"],
                           cwd=root, capture_output=True, text=True, timeout=timeout, env=env, stdin=subprocess.DEVNULL)
        out = json.loads(r.stdout)
    except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError) as e:
        raise state.StateError(f"Could not get suggestions from Claude Code: {e}") from e
    if out.get("is_error"):
        raise state.StateError(f"Claude Code: {out.get('result')}")
    m = re.search(r"\{.*\}", str(out.get("result") or ""), re.S)
    try:
        drafted = {k: str(v) for k, v in (json.loads(m.group(0)) if m else {}).items()
                   if k in {q["id"] for q in todo}}
    except json.JSONDecodeError:
        drafted = {}
    if not drafted:
        raise state.StateError("The suggestions could not be read; answer the questions or try again")
    return {"answers": drafted, "cost_usd": out.get("total_cost_usd")}
