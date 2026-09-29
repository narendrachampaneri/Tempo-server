"""Tool calling, strict JSON and an image, with the official OpenAI client.

Works the same whichever free model answers: models without native tool support get the tools
described in their prompt, and every reply is validated (and retried on another model if it
doesn't fit). Run a server first: TEMPO_ENABLE_MOCK=1 tempo-server serve
"""

import json

from openai import OpenAI

client = OpenAI(base_url="http://127.0.0.1:8000/v1", api_key="unused-in-local-mode")

weather = {
    "type": "function",
    "function": {
        "name": "get_weather",
        "description": "Current weather for a city",
        "parameters": {
            "type": "object",
            "properties": {"city": {"type": "string"}},
            "required": ["city"],
        },
    },
}
ask = [{"role": "user", "content": "What's the weather in Paris?"}]
reply = client.chat.completions.create(model="tempo/auto", messages=ask, tools=[weather])
call = reply.choices[0].message.tool_calls[0]
print("tool call:", call.function.name, call.function.arguments)

# Send the tool's result back; the model answers in text.
ask += [
    reply.choices[0].message.model_dump(exclude_none=True),
    {"role": "tool", "tool_call_id": call.id, "content": json.dumps({"temp_c": 21})},
]
print(
    "answer:",
    client.chat.completions.create(model="tempo/auto", messages=ask, tools=[weather])
    .choices[0]
    .message.content,
)

person = {
    "type": "json_schema",
    "json_schema": {
        "name": "person",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {"name": {"type": "string"}, "age": {"type": "integer"}},
            "required": ["name", "age"],
        },
    },
}
reply = client.chat.completions.create(
    model="tempo/auto",
    messages=[{"role": "user", "content": "Asha is 34. Return the person."}],
    response_format=person,
)
print("json:", json.loads(reply.choices[0].message.content))

image = {
    "role": "user",
    "content": [
        {"type": "text", "text": "What is in this picture?"},
        {
            "type": "image_url",
            "image_url": {"url": "https://upload.wikimedia.org/wikipedia/commons/3/3a/Cat03.jpg"},
        },
    ],
}
reply = client.chat.completions.create(model="tempo/auto", messages=[image])
print("image, answered by", reply.model, ":", reply.choices[0].message.content[:80])
