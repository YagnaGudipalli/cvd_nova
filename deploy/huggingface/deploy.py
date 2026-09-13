"""Deploy the Intelligence Studio to a Hugging Face Space (Docker SDK).

    .venv/bin/hf auth login                                  # once, interactively
    .venv/bin/python deploy/huggingface/deploy.py            # public Space <you>/cardio4cities
    .venv/bin/python deploy/huggingface/deploy.py --space you/name --private

What it does, in order:
1. Creates the Space if it does not exist.
2. Uploads only what the container needs. .env, the local ledger, tests and the
   virtualenv are never sent.
3. Stores credentials from .env as encrypted Space *secrets* and non-sensitive
   settings as Space *variables*. No credential value is ever printed.
4. Follows the build and reports when the app is reachable.
"""

import argparse
import sys
import time
from pathlib import Path

from dotenv import dotenv_values
from huggingface_hub import HfApi

ROOT = Path(__file__).resolve().parents[2]

UPLOAD = ["Dockerfile", ".dockerignore", "requirements.txt", "backend/**", "frontend/**", "docs/examples/**"]
NEVER = ["**/__pycache__/**", "**/*.pyc", ".env", ".env.*", "data/**", ".venv/**", "tests/**"]

#: Sent as encrypted secrets. OPENAI_API_KEY is deliberately absent: with
#: LLM_BASE_URL pointing at Groq it is never used, so it has no reason to leave
#: this machine.
SECRETS = ["LLM_API_KEY", "QDRANT_URL", "QDRANT_API_KEY", "NEO4J_URI", "NEO4J_USERNAME", "NEO4J_PASSWORD",
           "TAVILY_API_KEY", "BRAVE_API_KEY", "SERPER_API_KEY"]

#: Sent as plain variables: configuration, not credentials.
VARIABLES = ["LLM_BASE_URL", "LLM_MODEL", "LLM_FAST_MODEL", "LLM_EXTRACTION_MODELS", "LLM_MAX_CONCURRENCY",
             "LLM_DOC_CHARS", "GRAPH_MODEL", "GRAPH_FAST_MODEL", "GRAPH_MAX_EPISODES", "GRAPHITI_ENABLED",
             "NEO4J_DATABASE", "QDRANT_COLLECTION", "EMBEDDING_PROVIDER", "EMBEDDING_MODEL", "CHECKER_REASONING_EFFORT"]


def app_url(repo_id: str) -> str:
    return "https://" + repo_id.replace("/", "-").replace("_", "-").replace(".", "-").lower() + ".hf.space"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--space", help="owner/name; defaults to <your username>/cardio4cities")
    parser.add_argument("--private", action="store_true", help="private Space (evaluators would need a Hugging Face login)")
    parser.add_argument("--no-wait", action="store_true", help="do not follow the build")
    args = parser.parse_args()

    api = HfApi()
    try:
        user = api.whoami()["name"]
    except Exception:
        print("Not logged in to Hugging Face. Run:  .venv/bin/hf auth login   (use a token with write access)")
        return 1
    repo_id = args.space or f"{user}/cardio4cities"

    env = dotenv_values(ROOT / ".env")
    missing = [key for key in ("LLM_API_KEY", "LLM_BASE_URL", "QDRANT_URL", "QDRANT_API_KEY", "NEO4J_URI", "NEO4J_PASSWORD") if not env.get(key)]
    if missing:
        print(f"Warning: .env is missing {', '.join(missing)}; those capabilities will be degraded on the Space.")

    print(f"1/4 Creating Space {repo_id} ({'private' if args.private else 'public'})")
    api.create_repo(repo_id, repo_type="space", space_sdk="docker", private=args.private, exist_ok=True)

    print("2/4 Uploading application files")
    api.upload_folder(repo_id=repo_id, repo_type="space", folder_path=ROOT, allow_patterns=UPLOAD,
                      ignore_patterns=NEVER, commit_message="Deploy CARDIO4Cities Intelligence Studio")
    api.upload_file(repo_id=repo_id, repo_type="space", path_or_fileobj=ROOT / "deploy/huggingface/README.md",
                    path_in_repo="README.md", commit_message="Space configuration")

    print("3/4 Setting secrets and variables")
    sent_secrets = [key for key in SECRETS if env.get(key)]
    for key in sent_secrets:
        api.add_space_secret(repo_id, key, env[key])
    sent_variables = [key for key in VARIABLES if env.get(key)]
    for key in sent_variables:
        api.add_space_variable(repo_id, key, env[key])
    print(f"    secrets:   {', '.join(sent_secrets)}")
    print(f"    variables: {', '.join(sent_variables)}")

    print(f"4/4 Space: https://huggingface.co/spaces/{repo_id}")
    print(f"    App:   {app_url(repo_id)}")
    if args.no_wait:
        return 0

    print("    Following the build (the first build takes several minutes)...")
    last = None
    deadline = time.time() + 30 * 60
    while time.time() < deadline:
        stage = api.get_space_runtime(repo_id).stage
        if stage != last:
            print(f"    {time.strftime('%H:%M:%S')} {stage}")
            last = stage
        if stage == "RUNNING":
            print(f"    Live at {app_url(repo_id)}")
            return 0
        if stage in {"BUILD_ERROR", "RUNTIME_ERROR", "CONFIG_ERROR", "NO_APP_FILE"}:
            print(f"    Build failed; open https://huggingface.co/spaces/{repo_id}?logs=build for the log.")
            return 2
        time.sleep(15)
    print("    Still building after 30 minutes; check the Space page.")
    return 3


if __name__ == "__main__":
    sys.exit(main())
