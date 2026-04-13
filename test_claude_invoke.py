"""Smoke test: invoke all models defined in agents/llms/all.yaml."""

import os
import yaml
from pathlib import Path
from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()

LLM_DIR = Path("agents/llms")


def load_all_models():
    """Load all model configs from all.yaml (with includes)."""
    with open(LLM_DIR / "all.yaml") as f:
        root = yaml.safe_load(f)

    models = []
    for inc in root.get("include", []):
        with open(LLM_DIR / inc) as f:
            data = yaml.safe_load(f)
        for entry in data.get("llm", []):
            entry["_source"] = inc
            models.append(entry)
    return models


def get_client(entry):
    """Build an OpenAI-compatible client for the given model config."""
    model_type = entry.get("model_type", "openai")
    api_key_env = entry.get("api_key_env", "")
    base_url_env = entry.get("base_url_env", "")

    api_key = os.environ.get(api_key_env, "")
    if not api_key:
        return None, f"env var {api_key_env} not set"

    if model_type == "dashscope":
        base_url = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    elif base_url_env:
        base_url = os.environ.get(base_url_env, "")
        if not base_url:
            return None, f"env var {base_url_env} not set"
    else:
        base_url = None  # default OpenAI endpoint

    return OpenAI(api_key=api_key, base_url=base_url, timeout=120), None


def test_model(entry):
    """Send a single request and return (success, detail)."""
    model_name = entry["model_name"]
    client, err = get_client(entry)
    if client is None:
        return False, err

    response = client.chat.completions.create(
        model=model_name,
        messages=[{"role": "user", "content": "Say 'hello' and nothing else."}],
        max_tokens=16,
    )
    content = response.choices[0].message.content
    return True, content


def main():
    models = load_all_models()
    print(f"Found {len(models)} model(s) in all.yaml\n")
    print(f"{'Model':<45} {'Type':<12} {'Source':<18} {'Result'}")
    print("-" * 110)

    passed, failed, skipped = 0, 0, 0
    for entry in models:
        model_name = entry["model_name"]
        model_type = entry.get("model_type", "openai")
        source = entry.get("_source", "?")
        display = entry.get("model_display_name", "")

        try:
            ok, detail = test_model(entry)
            if ok:
                passed += 1
                print(f"{model_name:<45} {model_type:<12} {source:<18} OK  -> {detail!r}")
            else:
                skipped += 1
                print(f"{model_name:<45} {model_type:<12} {source:<18} SKIP: {detail}")
        except Exception as e:
            failed += 1
            err_msg = str(e).split("\n")[0]
            print(f"{model_name:<45} {model_type:<12} {source:<18} FAIL: {err_msg}")

    print("-" * 110)
    print(f"\nTotal: {len(models)}  |  Passed: {passed}  |  Failed: {failed}  |  Skipped: {skipped}")


if __name__ == "__main__":
    main()
