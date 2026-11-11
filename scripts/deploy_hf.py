"""Deploy to Hugging Face Spaces (free tier, no credit card).

Usage: HF_TOKEN=<write token> python scripts/deploy_hf.py

Reads API keys from .env and sets them as Space secrets - they never
appear in the repo or the Space's file listing.
"""

import os
import sys
from pathlib import Path

from huggingface_hub import HfApi

ROOT = Path(__file__).resolve().parent.parent
SPACE_NAME = "prepright"

IGNORE = [".venv", ".git", ".env", "docs", "data", "__pycache__",
          ".pytest_cache", "*.db", ".zcode", "assets/frames"]


def read_env(key: str) -> str:
    for line in (ROOT / ".env").read_text(encoding="utf-8").splitlines():
        if line.startswith(f"{key}="):
            return line.split("=", 1)[1].strip()
    return ""


def main() -> None:
    token = os.environ.get("HF_TOKEN", "")
    if not token:
        sys.exit("HF_TOKEN env var missing (a WRITE token from hf.co/settings/tokens)")

    api = HfApi(token=token)
    user = api.whoami()["name"]
    repo_id = f"{user}/{SPACE_NAME}"
    print(f"deploying as {repo_id}")

    api.create_repo(repo_id, repo_type="space", space_sdk="docker",
                    private=False, exist_ok=True)
    api.upload_folder(folder_path=str(ROOT), repo_id=repo_id,
                      repo_type="space", ignore_patterns=IGNORE + ["README.md"])

    # Space config lives in its README frontmatter - injected here so the
    # public GitHub README stays clean
    frontmatter = ("---\ntitle: PrepRight\nemoji: \U0001F4D0\ncolorFrom: blue\n"
                   "colorTo: indigo\nsdk: docker\napp_port: 7860\n---\n\n")
    space_readme = frontmatter + (ROOT / "README.md").read_text(encoding="utf-8")
    tmp = ROOT / "data" / "space_README.md"
    tmp.write_text(space_readme, encoding="utf-8")
    api.upload_file(path_or_fileobj=str(tmp), path_in_repo="README.md",
                    repo_id=repo_id, repo_type="space")
    tmp.unlink()

    for key in ("GROQ_API_KEY", "GEMINI_API_KEY", "LLM_PROVIDER",
                "GROQ_MODEL", "GROQ_FALLBACK_MODEL", "GEMINI_MODEL"):
        val = read_env(key)
        if val:
            api.add_space_secret(repo_id, key, val)
            print(f"secret set: {key}")

    print(f"\nSpace: https://huggingface.co/spaces/{repo_id}")
    print("First build takes a few minutes (Docker image pull + pip install).")


if __name__ == "__main__":
    main()
