"""Privacy options: local models only, or never a free tier that may log or train on prompts.

In demo mode there is no local model, so `local_only` answers 503 with the reason; with Ollama
running (OLLAMA_API_BASE) it answers from your computer.
"""

import openai
from openai import OpenAI

client = OpenAI(base_url="http://127.0.0.1:8000/v1", api_key="unused-in-local-mode")

for privacy in ("local_only", "no_logging"):
    try:
        reply = client.chat.completions.create(
            model="tempo/auto",
            messages=[{"role": "user", "content": "Summarise: the meeting moved to Friday."}],
            extra_body={"tempo": {"privacy": privacy}},
        )
        print(f"{privacy}: {reply.model}: {reply.choices[0].message.content[:80]}")
    except openai.APIStatusError as error:
        print(f"{privacy}: no model allowed ({error.status_code}): {error.message[:120]}")
