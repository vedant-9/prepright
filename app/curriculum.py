"""Curriculum data access — content/curriculum.json is the single source of truth.

DATA MODEL (why it looks like this):
- Marks weightage hangs on UNITS, not chapters — CBSE publishes "Algebra: 20
  marks", never per-chapter marks. Chapter-level weightage would be fabricated
  data; unit-level is sourced fact. PrepScore (P10) therefore computes over
  units (chapter mastery aggregates into its unit).
- The file is versioned (version / source / last_verified). A syllabus change
  = edit JSON + bump version. No code, no deploy.
- Grades 5–7 titles are flagged provisional_titles until re-verified against
  the current NCERT series (textbooks are mid-revamp under NCF-SE 2023).
"""

import json
from functools import lru_cache
from pathlib import Path

CURRICULUM_PATH = Path(__file__).resolve().parent.parent / "content" / "curriculum.json"


@lru_cache(maxsize=1)
def load() -> dict:
    """Load and cache the curriculum (file only changes between deploys)."""
    data = json.loads(CURRICULUM_PATH.read_text(encoding="utf-8"))
    _validate(data)
    return data


def _validate(data: dict) -> None:
    """Fail fast at startup on data typos (e.g. chapter pointing at a missing unit).

    Cheap insurance: the agent (P5+) will pass chapter ids to tools — a bad
    reference in data would surface as confusing runtime errors otherwise.
    """
    for grade, spec in data["grades"].items():
        unit_ids = {u["id"] for u in spec.get("units", [])}
        for ch in spec["chapters"]:
            if "unit" in ch and ch["unit"] not in unit_ids:
                raise ValueError(
                    f"curriculum.json: grade {grade} chapter {ch['id']!r} "
                    f"references unknown unit {ch['unit']!r}"
                )


def get_grade(grade: int) -> dict | None:
    return load()["grades"].get(str(grade))


def get_chapters(grade: int) -> list[dict]:
    """Chapters for a grade, each enriched with its unit's marks (null if none).

    This enriched view is what the API serves and the agent's
    get_curriculum tool (P5) will use to prioritize practice.
    """
    spec = get_grade(grade)
    if spec is None:
        return []
    units = {u["id"]: u for u in spec.get("units", [])}
    out = []
    for ch in spec["chapters"]:
        unit = units.get(ch.get("unit"))
        out.append({
            "id": ch["id"],
            "title": ch["title"],
            "unit": unit["title"] if unit else None,
            "unit_marks": unit["marks"] if unit else None,
            "styles": spec.get("styles", []),
        })
    return out


def find_chapter(chapter_id: str) -> dict | None:
    """Look up one chapter anywhere in the spine (agent tools pass ids around)."""
    for grade, spec in load()["grades"].items():
        for ch in spec["chapters"]:
            if ch["id"] == chapter_id:
                units = {u["id"]: u for u in spec.get("units", [])}
                unit = units.get(ch.get("unit"))
                return {
                    "grade": int(grade),
                    "id": ch["id"],
                    "title": ch["title"],
                    "unit": unit["title"] if unit else None,
                    "unit_marks": unit["marks"] if unit else None,
                    "styles": spec.get("styles", []),
                }
    return None
