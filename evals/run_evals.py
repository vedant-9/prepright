"""PrepRight eval harness (P9).

Two runnable evals + one parked benchmark:

  1. RETRIEVAL GOLDEN SET  - 15 queries, one per concept card; PASS if the
     expected card appears in the top-3 (recall@3 >= 0.7). Runs in whatever
     mode is available (semantic if the embedding API answers, lexical
     otherwise) - and REPORTS the mode, never silently faking quality.
  2. QUIZ INTEGRITY       - every cached quiz must satisfy the schema
     contract: 4 unique options, valid answer_index, verified flag. This is
     the "did we ever serve a broken quiz" audit.
  3. GSM8K (parked)       - 20 grade-school problems stored in
     gsm8k_sample.json as the future question-quality benchmark (compare
     generated question difficulty/answerability against a known-good set).

Run:  python -m evals.run_evals
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.data.cache import CACHE_DIR
from app.rag.retriever import retrieve

GOLDEN = Path(__file__).resolve().parent / "golden_retrieval.json"
PASS_BAR = 0.70


def eval_retrieval() -> tuple[float, str]:
    cases = json.loads(GOLDEN.read_text(encoding="utf-8"))["cases"]
    hits, mode = 0, "n/a"
    for case in cases:
        out = retrieve(case["query"], k=3)
        mode = out["mode"]
        ids = [r["id"] for r in out["results"]]
        ok = case["expected"] in ids
        hits += ok
        flag = "ok " if ok else "MISS"
        print(f"  [{flag}] {case['query'][:42]:42s} -> {ids[0]}")
    return hits / len(cases), mode


def eval_quiz_cache() -> tuple[int, int]:
    if not CACHE_DIR.exists():
        return 0, 0
    total = bad = 0
    for path in CACHE_DIR.glob("*.json"):
        blob = json.loads(path.read_text(encoding="utf-8"))
        quiz = blob.get("value") if "value" in blob else blob
        if not isinstance(quiz, dict) or "questions" not in quiz:
            continue  # not a quiz cache entry (embeddings etc.)
        total += 1
        for q in quiz["questions"]:
            opts = q.get("options", [])
            if (len(opts) != 4 or len(set(opts)) != 4
                    or not 0 <= q.get("answer_index", -1) < 4
                    or not q.get("verified")):
                bad += 1
                print(f"  [BAD] {quiz.get('chapter_id')}: {q.get('question_text', '?')[:50]}")
    return total, bad


def main() -> int:
    print("== EVAL 1: retrieval golden set (recall@3) ==")
    score, mode = eval_retrieval()
    print(f"  recall@3 = {score:.2f}  (mode: {mode}, pass bar {PASS_BAR})")

    print("== EVAL 2: cached quiz integrity ==")
    total, bad = eval_quiz_cache()
    print(f"  {total} cached quizzes checked, {bad} bad questions")

    gsm8k = Path(__file__).resolve().parent / "gsm8k_sample.json"
    print(f"== GSM8K benchmark: {'present (' + str(json.loads(gsm8k.read_text(encoding='utf-8'))['n']) + ' problems, parked for question-quality eval)' if gsm8k.exists() else 'not downloaded'} ==")

    ok = score >= PASS_BAR and bad == 0
    print(f"\nRESULT: {'PASS' if ok else 'FAIL'}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
