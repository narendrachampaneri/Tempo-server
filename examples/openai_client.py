"""Use Tempo as a model in the official OpenAI Python client."""

from openai import OpenAI

client = OpenAI(base_url="http://127.0.0.1:8000/v1", api_key="unused-in-local-mode")

reply = client.chat.completions.create(
    model="tempo/auto",  # or tempo/fast, tempo/best, tempo/private
    messages=[{"role": "user", "content": "Explain recursion in one paragraph."}],
    extra_body={"tempo": {"max_stages": 3, "trace": True}},
)
print("Answered by:", reply.model)
print(reply.choices[0].message.content)
for event in reply.model_extra.get("tempo", {}).get("trace", []):
    print("  ▸", event.get("text"))
