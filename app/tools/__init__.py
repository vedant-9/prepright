"""All agent tools, one import site for graph binding.

The @tool docstrings are the model's instruction manual - they are part of
the product surface, not just code comments.
"""

from app.tools.calculator import calculate
from app.tools.curriculum_tool import get_curriculum
from app.tools.progress import get_student_profile, record_progress
from app.tools.quiz import generate_quiz, grade_answer, grade_quiz
from app.tools.retrieval_tool import lookup_concept

TOOLS = [generate_quiz, grade_quiz, grade_answer, record_progress,
         get_student_profile, get_curriculum, lookup_concept, calculate]

__all__ = ["TOOLS", "generate_quiz", "grade_quiz", "grade_answer",
           "record_progress", "get_student_profile", "get_curriculum",
           "lookup_concept", "calculate"]
