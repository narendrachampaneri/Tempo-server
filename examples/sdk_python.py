"""The Python SDK: stream the thinking window, then use the answer.

pip install ./sdk/python            # `pip install tempo-server-client` once published
TEMPO_ENABLE_MOCK=1 tempo-server serve &
python examples/sdk_python.py
"""

from tempo_server_client import TempoClient

tempo = TempoClient()  # $TEMPO_URL (default http://127.0.0.1:8000) and $TEMPO_API_KEY

for event in tempo.stream("What is 17% of 2,340?", mode="fast"):
    if event.text:
        print("▸", event.text)

answer = tempo.ask("Explain the difference between TCP and UDP in two lines")
print(f"\n{answer.text}\n\n— {answer.model}, {answer.stages} stages, {answer.stop_reason}")
tempo.feedback(answer.question_id, "up")
left = {q["label"]: q["left_today"] for q in tempo.quota()["providers"]}
print("Free requests left today:", left or "no hosted provider set up")
