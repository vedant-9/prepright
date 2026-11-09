"""Curriculum tool: the model's window into the syllabus spine."""

from langchain_core.tools import tool

from app.data import curriculum


@tool
def get_curriculum(grade: float) -> dict:
    """List the maths chapters for a CBSE grade, with each chapter's unit
    marks (marks indicate exam importance). Use to help the student pick
    what to practice."""
    chapters = curriculum.get_chapters(int(grade))
    return {"grade": int(grade), "chapters": chapters}
