window.TEMPO_RECORDING = {
 "recorded_at": "2026-09-28",
 "version": "0.1.0",
 "demo_mode": true,
 "sessions": [
  {
   "question": "Write a Python function that checks if a string is a palindrome, with tests",
   "events": [
    {
     "type": "received",
     "t": 0.0,
     "mode": "auto",
     "text": "Received · mode auto"
    },
    {
     "type": "analyze",
     "t": 0.001,
     "task": "code",
     "complexity": 0.45,
     "script": "latin",
     "needs": [],
     "input_tokens": 18,
     "est_output_tokens": 700,
     "source": "rules",
     "text": "Understanding: code · complexity 0.45 · ~18 input tokens"
    },
    {
     "type": "plan",
     "t": 0.002,
     "strategy": "cascade",
     "max_stages": 5,
     "drafts": 1,
     "parts": 0,
     "time_budget_s": 60.0,
     "quota_budget": 12,
     "reason": "draft, check, then fix if needed",
     "laya": "Laya off",
     "text": "Plan: cascade · up to 5 stages · 60s · 12 free requests · draft, check, then fix if needed · Laya off"
    },
    {
     "type": "stage_start",
     "t": 0.002,
     "stage": 1,
     "max_stages": 5,
     "job": "draft",
     "models": [
      "mock/flaky"
     ],
     "reason": "highest predicted quality for code",
     "requests_left": 12,
     "time_left_s": 60.0,
     "text": "Stage 1/5 · draft · mock/flaky · highest predicted quality for code"
    },
    {
     "type": "call_start",
     "t": 0.002,
     "stage": 1,
     "job": "draft",
     "model": "mock/flaky",
     "attempt": 1,
     "text": "Calling mock/flaky"
    },
    {
     "type": "call_error",
     "t": 0.078,
     "stage": 1,
     "model": "mock/flaky",
     "kind": "rate_limit",
     "message": "429: demo rate limit",
     "text": "✗ mock/flaky: rate limited"
    },
    {
     "type": "fallback",
     "t": 0.078,
     "stage": 1,
     "from": "mock/flaky",
     "to": "mock/smart",
     "text": "Falling back → mock/smart"
    },
    {
     "type": "call_start",
     "t": 0.078,
     "stage": 1,
     "job": "draft",
     "model": "mock/smart",
     "attempt": 2,
     "text": "Calling mock/smart (attempt 2)"
    },
    {
     "type": "reasoning_delta",
     "t": 0.169,
     "stage": 1,
     "model": "mock/smart",
     "delta": "Checking "
    },
    {
     "type": "reasoning_delta",
     "t": 0.185,
     "stage": 1,
     "model": "mock/smart",
     "delta": "what "
    },
    {
     "type": "reasoning_delta",
     "t": 0.2,
     "stage": 1,
     "model": "mock/smart",
     "delta": "the "
    },
    {
     "type": "reasoning_delta",
     "t": 0.216,
     "stage": 1,
     "model": "mock/smart",
     "delta": "question "
    },
    {
     "type": "reasoning_delta",
     "t": 0.231,
     "stage": 1,
     "model": "mock/smart",
     "delta": "asks "
    },
    {
     "type": "reasoning_delta",
     "t": 0.247,
     "stage": 1,
     "model": "mock/smart",
     "delta": "for "
    },
    {
     "type": "reasoning_delta",
     "t": 0.262,
     "stage": 1,
     "model": "mock/smart",
     "delta": "before "
    },
    {
     "type": "reasoning_delta",
     "t": 0.278,
     "stage": 1,
     "model": "mock/smart",
     "delta": "answering. "
    },
    {
     "type": "answer_delta",
     "t": 0.293,
     "stage": 1,
     "model": "mock/smart",
     "delta": "This"
    },
    {
     "type": "answer_delta",
     "t": 0.309,
     "stage": 1,
     "model": "mock/smart",
     "delta": " is"
    },
    {
     "type": "answer_delta",
     "t": 0.324,
     "stage": 1,
     "model": "mock/smart",
     "delta": " an"
    },
    {
     "type": "answer_delta",
     "t": 0.34,
     "stage": 1,
     "model": "mock/smart",
     "delta": " offline"
    },
    {
     "type": "answer_delta",
     "t": 0.355,
     "stage": 1,
     "model": "mock/smart",
     "delta": " demo"
    },
    {
     "type": "answer_delta",
     "t": 0.37,
     "stage": 1,
     "model": "mock/smart",
     "delta": " answer"
    },
    {
     "type": "answer_delta",
     "t": 0.386,
     "stage": 1,
     "model": "mock/smart",
     "delta": " from"
    },
    {
     "type": "answer_delta",
     "t": 0.401,
     "stage": 1,
     "model": "mock/smart",
     "delta": " **Smart"
    },
    {
     "type": "answer_delta",
     "t": 0.417,
     "stage": 1,
     "model": "mock/smart",
     "delta": " Demo"
    },
    {
     "type": "answer_delta",
     "t": 0.432,
     "stage": 1,
     "model": "mock/smart",
     "delta": " Model**."
    },
    {
     "type": "answer_delta",
     "t": 0.447,
     "stage": 1,
     "model": "mock/smart",
     "delta": " Tempo"
    },
    {
     "type": "answer_delta",
     "t": 0.463,
     "stage": 1,
     "model": "mock/smart",
     "delta": " analyzed"
    },
    {
     "type": "answer_delta",
     "t": 0.478,
     "stage": 1,
     "model": "mock/smart",
     "delta": " your"
    },
    {
     "type": "answer_delta",
     "t": 0.493,
     "stage": 1,
     "model": "mock/smart",
     "delta": " question,"
    },
    {
     "type": "answer_delta",
     "t": 0.509,
     "stage": 1,
     "model": "mock/smart",
     "delta": " ranked"
    },
    {
     "type": "answer_delta",
     "t": 0.525,
     "stage": 1,
     "model": "mock/smart",
     "delta": " the"
    },
    {
     "type": "answer_delta",
     "t": 0.54,
     "stage": 1,
     "model": "mock/smart",
     "delta": " available"
    },
    {
     "type": "answer_delta",
     "t": 0.555,
     "stage": 1,
     "model": "mock/smart",
     "delta": " models,"
    },
    {
     "type": "answer_delta",
     "t": 0.571,
     "stage": 1,
     "model": "mock/smart",
     "delta": " and"
    },
    {
     "type": "answer_delta",
     "t": 0.586,
     "stage": 1,
     "model": "mock/smart",
     "delta": " routed"
    },
    {
     "type": "answer_delta",
     "t": 0.602,
     "stage": 1,
     "model": "mock/smart",
     "delta": " it"
    },
    {
     "type": "answer_delta",
     "t": 0.617,
     "stage": 1,
     "model": "mock/smart",
     "delta": " here.\n\nYou"
    },
    {
     "type": "answer_delta",
     "t": 0.632,
     "stage": 1,
     "model": "mock/smart",
     "delta": " asked:"
    },
    {
     "type": "answer_delta",
     "t": 0.648,
     "stage": 1,
     "model": "mock/smart",
     "delta": " “Write"
    },
    {
     "type": "answer_delta",
     "t": 0.663,
     "stage": 1,
     "model": "mock/smart",
     "delta": " a"
    },
    {
     "type": "answer_delta",
     "t": 0.679,
     "stage": 1,
     "model": "mock/smart",
     "delta": " Python"
    },
    {
     "type": "answer_delta",
     "t": 0.694,
     "stage": 1,
     "model": "mock/smart",
     "delta": " function"
    },
    {
     "type": "answer_delta",
     "t": 0.71,
     "stage": 1,
     "model": "mock/smart",
     "delta": " that"
    },
    {
     "type": "answer_delta",
     "t": 0.725,
     "stage": 1,
     "model": "mock/smart",
     "delta": " checks"
    },
    {
     "type": "answer_delta",
     "t": 0.74,
     "stage": 1,
     "model": "mock/smart",
     "delta": " if"
    },
    {
     "type": "answer_delta",
     "t": 0.756,
     "stage": 1,
     "model": "mock/smart",
     "delta": " a"
    },
    {
     "type": "answer_delta",
     "t": 0.771,
     "stage": 1,
     "model": "mock/smart",
     "delta": " string"
    },
    {
     "type": "answer_delta",
     "t": 0.786,
     "stage": 1,
     "model": "mock/smart",
     "delta": " is"
    },
    {
     "type": "answer_delta",
     "t": 0.802,
     "stage": 1,
     "model": "mock/smart",
     "delta": " a"
    },
    {
     "type": "answer_delta",
     "t": 0.817,
     "stage": 1,
     "model": "mock/smart",
     "delta": " palindrome,"
    },
    {
     "type": "answer_delta",
     "t": 0.832,
     "stage": 1,
     "model": "mock/smart",
     "delta": " with"
    },
    {
     "type": "answer_delta",
     "t": 0.848,
     "stage": 1,
     "model": "mock/smart",
     "delta": " tests”\n\nAdd"
    },
    {
     "type": "answer_delta",
     "t": 0.863,
     "stage": 1,
     "model": "mock/smart",
     "delta": " a"
    },
    {
     "type": "answer_delta",
     "t": 0.879,
     "stage": 1,
     "model": "mock/smart",
     "delta": " free"
    },
    {
     "type": "answer_delta",
     "t": 0.894,
     "stage": 1,
     "model": "mock/smart",
     "delta": " API"
    },
    {
     "type": "answer_delta",
     "t": 0.909,
     "stage": 1,
     "model": "mock/smart",
     "delta": " key"
    },
    {
     "type": "answer_delta",
     "t": 0.925,
     "stage": 1,
     "model": "mock/smart",
     "delta": " (for"
    },
    {
     "type": "answer_delta",
     "t": 0.94,
     "stage": 1,
     "model": "mock/smart",
     "delta": " example"
    },
    {
     "type": "answer_delta",
     "t": 0.956,
     "stage": 1,
     "model": "mock/smart",
     "delta": " `GROQ_API_KEY`)"
    },
    {
     "type": "answer_delta",
     "t": 0.971,
     "stage": 1,
     "model": "mock/smart",
     "delta": " or"
    },
    {
     "type": "answer_delta",
     "t": 0.986,
     "stage": 1,
     "model": "mock/smart",
     "delta": " point"
    },
    {
     "type": "answer_delta",
     "t": 1.002,
     "stage": 1,
     "model": "mock/smart",
     "delta": " `OLLAMA_API_BASE`"
    },
    {
     "type": "answer_delta",
     "t": 1.017,
     "stage": 1,
     "model": "mock/smart",
     "delta": " at"
    },
    {
     "type": "answer_delta",
     "t": 1.033,
     "stage": 1,
     "model": "mock/smart",
     "delta": " a"
    },
    {
     "type": "answer_delta",
     "t": 1.048,
     "stage": 1,
     "model": "mock/smart",
     "delta": " local"
    },
    {
     "type": "answer_delta",
     "t": 1.063,
     "stage": 1,
     "model": "mock/smart",
     "delta": " Ollama"
    },
    {
     "type": "answer_delta",
     "t": 1.079,
     "stage": 1,
     "model": "mock/smart",
     "delta": " server"
    },
    {
     "type": "answer_delta",
     "t": 1.094,
     "stage": 1,
     "model": "mock/smart",
     "delta": " to"
    },
    {
     "type": "answer_delta",
     "t": 1.11,
     "stage": 1,
     "model": "mock/smart",
     "delta": " get"
    },
    {
     "type": "answer_delta",
     "t": 1.125,
     "stage": 1,
     "model": "mock/smart",
     "delta": " real"
    },
    {
     "type": "answer_delta",
     "t": 1.141,
     "stage": 1,
     "model": "mock/smart",
     "delta": " answers.\n\n```python\ndef"
    },
    {
     "type": "answer_delta",
     "t": 1.156,
     "stage": 1,
     "model": "mock/smart",
     "delta": " demo():\n"
    },
    {
     "type": "answer_delta",
     "t": 1.171,
     "stage": 1,
     "model": "mock/smart",
     "delta": " "
    },
    {
     "type": "answer_delta",
     "t": 1.187,
     "stage": 1,
     "model": "mock/smart",
     "delta": " "
    },
    {
     "type": "answer_delta",
     "t": 1.202,
     "stage": 1,
     "model": "mock/smart",
     "delta": " "
    },
    {
     "type": "answer_delta",
     "t": 1.218,
     "stage": 1,
     "model": "mock/smart",
     "delta": " return"
    },
    {
     "type": "answer_delta",
     "t": 1.233,
     "stage": 1,
     "model": "mock/smart",
     "delta": " 'offline"
    },
    {
     "type": "answer_delta",
     "t": 1.249,
     "stage": 1,
     "model": "mock/smart",
     "delta": " demo'\n```"
    },
    {
     "type": "call_end",
     "t": 1.249,
     "stage": 1,
     "job": "draft",
     "model": "mock/smart",
     "ms": 1171,
     "ttft_ms": 91,
     "text": "Answer from mock/smart in 1.2s (first token 0.1s)"
    },
    {
     "type": "stage_end",
     "t": 1.249,
     "stage": 1,
     "job": "draft",
     "ms": 1247,
     "answers": 1,
     "requests_used": 2,
     "requests_left": 10,
     "time_left_s": 58.8,
     "text": "Stage 1 done in 1.2s · 10 free requests left · 58.8s left"
    },
    {
     "type": "stage_start",
     "t": 1.25,
     "stage": 2,
     "max_stages": 5,
     "job": "check",
     "models": [
      "mock/fast"
     ],
     "reason": "heuristics + judge mock/fast",
     "requests_left": 10,
     "time_left_s": 58.7,
     "text": "Stage 2/5 · check · mock/fast · heuristics + judge mock/fast"
    },
    {
     "type": "call_start",
     "t": 1.25,
     "stage": 2,
     "job": "check",
     "model": "mock/fast",
     "attempt": 1,
     "text": "Calling mock/fast"
    },
    {
     "type": "call_end",
     "t": 1.326,
     "stage": 2,
     "job": "check",
     "model": "mock/fast",
     "ms": 76,
     "ttft_ms": 76,
     "text": "Answer from mock/fast in 0.1s (first token 0.1s)"
    },
    {
     "type": "check",
     "t": 1.326,
     "stage": 2,
     "results": [
      {
       "model": "mock/smart",
       "stage": 1,
       "score": 0.6,
       "passed": false,
       "issues": [
        "could be more specific"
       ],
       "hard_fail": false,
       "judge_score": 6.0,
       "judge_model": "mock/fast",
       "heuristic_score": 0.8
      }
     ],
     "judge_model": "mock/fast",
     "best_score": 0.6,
     "passed": false,
     "text": "Check: best score 0.60 ✗ not good enough yet (judge mock/fast) · could be more specific"
    },
    {
     "type": "stage_end",
     "t": 1.327,
     "stage": 2,
     "job": "check",
     "ms": 76,
     "answers": 0,
     "requests_used": 3,
     "requests_left": 9,
     "time_left_s": 58.7,
     "text": "Stage 2 done in 0.1s · 9 free requests left · 58.7s left"
    },
    {
     "type": "stage_start",
     "t": 1.327,
     "stage": 3,
     "max_stages": 5,
     "job": "fix",
     "models": [
      "mock/fast"
     ],
     "reason": "fix: could be more specific",
     "requests_left": 9,
     "time_left_s": 58.7,
     "text": "Stage 3/5 · fix · mock/fast · fix: could be more specific"
    },
    {
     "type": "call_start",
     "t": 1.327,
     "stage": 3,
     "job": "fix",
     "model": "mock/fast",
     "attempt": 1,
     "text": "Calling mock/fast"
    },
    {
     "type": "answer_reset",
     "t": 1.418,
     "stage": 3,
     "reason": "replaced by stage 3",
     "text": "Replacing the shown answer with stage 3"
    },
    {
     "type": "answer_delta",
     "t": 1.418,
     "stage": 3,
     "model": "mock/fast",
     "delta": "This"
    },
    {
     "type": "answer_delta",
     "t": 1.433,
     "stage": 3,
     "model": "mock/fast",
     "delta": " is"
    },
    {
     "type": "answer_delta",
     "t": 1.449,
     "stage": 3,
     "model": "mock/fast",
     "delta": " an"
    },
    {
     "type": "answer_delta",
     "t": 1.464,
     "stage": 3,
     "model": "mock/fast",
     "delta": " improved"
    },
    {
     "type": "answer_delta",
     "t": 1.48,
     "stage": 3,
     "model": "mock/fast",
     "delta": " offline"
    },
    {
     "type": "answer_delta",
     "t": 1.495,
     "stage": 3,
     "model": "mock/fast",
     "delta": " demo"
    },
    {
     "type": "answer_delta",
     "t": 1.511,
     "stage": 3,
     "model": "mock/fast",
     "delta": " answer"
    },
    {
     "type": "answer_delta",
     "t": 1.526,
     "stage": 3,
     "model": "mock/fast",
     "delta": " from"
    },
    {
     "type": "answer_delta",
     "t": 1.542,
     "stage": 3,
     "model": "mock/fast",
     "delta": " **Fast"
    },
    {
     "type": "answer_delta",
     "t": 1.557,
     "stage": 3,
     "model": "mock/fast",
     "delta": " Demo"
    },
    {
     "type": "answer_delta",
     "t": 1.573,
     "stage": 3,
     "model": "mock/fast",
     "delta": " Model**"
    },
    {
     "type": "answer_delta",
     "t": 1.588,
     "stage": 3,
     "model": "mock/fast",
     "delta": " (stage"
    },
    {
     "type": "answer_delta",
     "t": 1.604,
     "stage": 3,
     "model": "mock/fast",
     "delta": " job:"
    },
    {
     "type": "answer_delta",
     "t": 1.619,
     "stage": 3,
     "model": "mock/fast",
     "delta": " fix)."
    },
    {
     "type": "answer_delta",
     "t": 1.634,
     "stage": 3,
     "model": "mock/fast",
     "delta": " It"
    },
    {
     "type": "answer_delta",
     "t": 1.65,
     "stage": 3,
     "model": "mock/fast",
     "delta": " addresses"
    },
    {
     "type": "answer_delta",
     "t": 1.665,
     "stage": 3,
     "model": "mock/fast",
     "delta": " the"
    },
    {
     "type": "answer_delta",
     "t": 1.681,
     "stage": 3,
     "model": "mock/fast",
     "delta": " issues"
    },
    {
     "type": "answer_delta",
     "t": 1.696,
     "stage": 3,
     "model": "mock/fast",
     "delta": " the"
    },
    {
     "type": "answer_delta",
     "t": 1.711,
     "stage": 3,
     "model": "mock/fast",
     "delta": " checker"
    },
    {
     "type": "answer_delta",
     "t": 1.727,
     "stage": 3,
     "model": "mock/fast",
     "delta": " found"
    },
    {
     "type": "answer_delta",
     "t": 1.742,
     "stage": 3,
     "model": "mock/fast",
     "delta": " in"
    },
    {
     "type": "answer_delta",
     "t": 1.758,
     "stage": 3,
     "model": "mock/fast",
     "delta": " the"
    },
    {
     "type": "answer_delta",
     "t": 1.773,
     "stage": 3,
     "model": "mock/fast",
     "delta": " earlier"
    },
    {
     "type": "answer_delta",
     "t": 1.788,
     "stage": 3,
     "model": "mock/fast",
     "delta": " draft.\n\n```python\ndef"
    },
    {
     "type": "answer_delta",
     "t": 1.804,
     "stage": 3,
     "model": "mock/fast",
     "delta": " demo():\n"
    },
    {
     "type": "answer_delta",
     "t": 1.819,
     "stage": 3,
     "model": "mock/fast",
     "delta": " "
    },
    {
     "type": "answer_delta",
     "t": 1.835,
     "stage": 3,
     "model": "mock/fast",
     "delta": " "
    },
    {
     "type": "answer_delta",
     "t": 1.85,
     "stage": 3,
     "model": "mock/fast",
     "delta": " "
    },
    {
     "type": "answer_delta",
     "t": 1.865,
     "stage": 3,
     "model": "mock/fast",
     "delta": " return"
    },
    {
     "type": "answer_delta",
     "t": 1.881,
     "stage": 3,
     "model": "mock/fast",
     "delta": " 'offline"
    },
    {
     "type": "answer_delta",
     "t": 1.896,
     "stage": 3,
     "model": "mock/fast",
     "delta": " demo'\n```\n\nAdd"
    },
    {
     "type": "answer_delta",
     "t": 1.912,
     "stage": 3,
     "model": "mock/fast",
     "delta": " a"
    },
    {
     "type": "answer_delta",
     "t": 1.927,
     "stage": 3,
     "model": "mock/fast",
     "delta": " free"
    },
    {
     "type": "answer_delta",
     "t": 1.942,
     "stage": 3,
     "model": "mock/fast",
     "delta": " API"
    },
    {
     "type": "answer_delta",
     "t": 1.958,
     "stage": 3,
     "model": "mock/fast",
     "delta": " key"
    },
    {
     "type": "answer_delta",
     "t": 1.973,
     "stage": 3,
     "model": "mock/fast",
     "delta": " (for"
    },
    {
     "type": "answer_delta",
     "t": 1.989,
     "stage": 3,
     "model": "mock/fast",
     "delta": " example"
    },
    {
     "type": "answer_delta",
     "t": 2.004,
     "stage": 3,
     "model": "mock/fast",
     "delta": " `GROQ_API_KEY`)"
    },
    {
     "type": "answer_delta",
     "t": 2.019,
     "stage": 3,
     "model": "mock/fast",
     "delta": " or"
    },
    {
     "type": "answer_delta",
     "t": 2.035,
     "stage": 3,
     "model": "mock/fast",
     "delta": " point"
    },
    {
     "type": "answer_delta",
     "t": 2.05,
     "stage": 3,
     "model": "mock/fast",
     "delta": " `OLLAMA_API_BASE`"
    },
    {
     "type": "answer_delta",
     "t": 2.066,
     "stage": 3,
     "model": "mock/fast",
     "delta": " at"
    },
    {
     "type": "answer_delta",
     "t": 2.081,
     "stage": 3,
     "model": "mock/fast",
     "delta": " a"
    },
    {
     "type": "answer_delta",
     "t": 2.097,
     "stage": 3,
     "model": "mock/fast",
     "delta": " local"
    },
    {
     "type": "answer_delta",
     "t": 2.112,
     "stage": 3,
     "model": "mock/fast",
     "delta": " Ollama"
    },
    {
     "type": "answer_delta",
     "t": 2.128,
     "stage": 3,
     "model": "mock/fast",
     "delta": " server"
    },
    {
     "type": "answer_delta",
     "t": 2.143,
     "stage": 3,
     "model": "mock/fast",
     "delta": " to"
    },
    {
     "type": "answer_delta",
     "t": 2.159,
     "stage": 3,
     "model": "mock/fast",
     "delta": " get"
    },
    {
     "type": "answer_delta",
     "t": 2.174,
     "stage": 3,
     "model": "mock/fast",
     "delta": " real"
    },
    {
     "type": "answer_delta",
     "t": 2.19,
     "stage": 3,
     "model": "mock/fast",
     "delta": " answers."
    },
    {
     "type": "call_end",
     "t": 2.19,
     "stage": 3,
     "job": "fix",
     "model": "mock/fast",
     "ms": 863,
     "ttft_ms": 91,
     "text": "Answer from mock/fast in 0.9s (first token 0.1s)"
    },
    {
     "type": "stage_end",
     "t": 2.19,
     "stage": 3,
     "job": "fix",
     "ms": 863,
     "answers": 1,
     "requests_used": 4,
     "requests_left": 8,
     "time_left_s": 57.8,
     "text": "Stage 3 done in 0.9s · 8 free requests left · 57.8s left"
    },
    {
     "type": "stage_start",
     "t": 2.191,
     "stage": 4,
     "max_stages": 5,
     "job": "check",
     "models": [
      "mock/smart"
     ],
     "reason": "heuristics + judge mock/smart",
     "requests_left": 8,
     "time_left_s": 57.8,
     "text": "Stage 4/5 · check · mock/smart · heuristics + judge mock/smart"
    },
    {
     "type": "call_start",
     "t": 2.191,
     "stage": 4,
     "job": "check",
     "model": "mock/smart",
     "attempt": 1,
     "text": "Calling mock/smart"
    },
    {
     "type": "call_end",
     "t": 2.267,
     "stage": 4,
     "job": "check",
     "model": "mock/smart",
     "ms": 76,
     "ttft_ms": 76,
     "text": "Answer from mock/smart in 0.1s (first token 0.1s)"
    },
    {
     "type": "check",
     "t": 2.267,
     "stage": 4,
     "results": [
      {
       "model": "mock/fast",
       "stage": 3,
       "score": 0.9,
       "passed": true,
       "issues": [],
       "hard_fail": false,
       "judge_score": 9.0,
       "judge_model": "mock/smart",
       "heuristic_score": 0.8
      }
     ],
     "judge_model": "mock/smart",
     "best_score": 0.9,
     "passed": true,
     "text": "Check: best score 0.90 ✓ passed (judge mock/smart)"
    },
    {
     "type": "stage_end",
     "t": 2.267,
     "stage": 4,
     "job": "check",
     "ms": 76,
     "answers": 0,
     "requests_used": 5,
     "requests_left": 7,
     "time_left_s": 57.7,
     "text": "Stage 4 done in 0.1s · 7 free requests left · 57.7s left"
    },
    {
     "type": "answer_final",
     "t": 2.268,
     "answer": "This is an improved offline demo answer from **Fast Demo Model** (stage job: fix). It addresses the issues the checker found in the earlier draft.\n\n```python\ndef demo():\n    return 'offline demo'\n```\n\nAdd a free API key (for example `GROQ_API_KEY`) or point `OLLAMA_API_BASE` at a local Ollama server to get real answers.",
     "model": "mock/fast",
     "stage": 3,
     "score": 0.9,
     "passed": true,
     "reasoning": null,
     "tool_calls": null,
     "text": "Final answer from mock/fast (stage 3, score 0.90)"
    },
    {
     "type": "done",
     "t": 2.268,
     "model": "mock/fast",
     "stages": 4,
     "attempts": 5,
     "requests": 5,
     "total_ms": 2268,
     "stop_reason": "passed",
     "strategy": "cascade",
     "text": "Done in 2.3s · 4 stages · 5 model calls · 5 free used · answer passed its check"
    }
   ]
  },
  {
   "question": "What is 17% of 2,340?",
   "events": [
    {
     "type": "received",
     "t": 0.0,
     "mode": "auto",
     "text": "Received · mode auto"
    },
    {
     "type": "analyze",
     "t": 0.0,
     "task": "math",
     "complexity": 0.45,
     "script": "latin",
     "needs": [
      "reasoning"
     ],
     "input_tokens": 5,
     "est_output_tokens": 400,
     "source": "rules",
     "text": "Understanding: math · complexity 0.45 · needs reasoning · ~5 input tokens"
    },
    {
     "type": "plan",
     "t": 0.001,
     "strategy": "cascade",
     "max_stages": 5,
     "drafts": 1,
     "parts": 0,
     "time_budget_s": 60.0,
     "quota_budget": 12,
     "reason": "draft, check, then fix if needed",
     "laya": "Laya off",
     "text": "Plan: cascade · up to 5 stages · 60s · 12 free requests · draft, check, then fix if needed · Laya off"
    },
    {
     "type": "stage_start",
     "t": 0.001,
     "stage": 1,
     "max_stages": 5,
     "job": "draft",
     "models": [
      "mock/smart"
     ],
     "reason": "highest predicted quality for math",
     "requests_left": 12,
     "time_left_s": 60.0,
     "text": "Stage 1/5 · draft · mock/smart · highest predicted quality for math"
    },
    {
     "type": "call_start",
     "t": 0.001,
     "stage": 1,
     "job": "draft",
     "model": "mock/smart",
     "attempt": 1,
     "text": "Calling mock/smart"
    },
    {
     "type": "reasoning_delta",
     "t": 0.092,
     "stage": 1,
     "model": "mock/smart",
     "delta": "Checking "
    },
    {
     "type": "reasoning_delta",
     "t": 0.108,
     "stage": 1,
     "model": "mock/smart",
     "delta": "what "
    },
    {
     "type": "reasoning_delta",
     "t": 0.123,
     "stage": 1,
     "model": "mock/smart",
     "delta": "the "
    },
    {
     "type": "reasoning_delta",
     "t": 0.139,
     "stage": 1,
     "model": "mock/smart",
     "delta": "question "
    },
    {
     "type": "reasoning_delta",
     "t": 0.154,
     "stage": 1,
     "model": "mock/smart",
     "delta": "asks "
    },
    {
     "type": "reasoning_delta",
     "t": 0.169,
     "stage": 1,
     "model": "mock/smart",
     "delta": "for "
    },
    {
     "type": "reasoning_delta",
     "t": 0.185,
     "stage": 1,
     "model": "mock/smart",
     "delta": "before "
    },
    {
     "type": "reasoning_delta",
     "t": 0.2,
     "stage": 1,
     "model": "mock/smart",
     "delta": "answering. "
    },
    {
     "type": "answer_delta",
     "t": 0.216,
     "stage": 1,
     "model": "mock/smart",
     "delta": "This"
    },
    {
     "type": "answer_delta",
     "t": 0.231,
     "stage": 1,
     "model": "mock/smart",
     "delta": " is"
    },
    {
     "type": "answer_delta",
     "t": 0.247,
     "stage": 1,
     "model": "mock/smart",
     "delta": " an"
    },
    {
     "type": "answer_delta",
     "t": 0.262,
     "stage": 1,
     "model": "mock/smart",
     "delta": " offline"
    },
    {
     "type": "answer_delta",
     "t": 0.277,
     "stage": 1,
     "model": "mock/smart",
     "delta": " demo"
    },
    {
     "type": "answer_delta",
     "t": 0.293,
     "stage": 1,
     "model": "mock/smart",
     "delta": " answer"
    },
    {
     "type": "answer_delta",
     "t": 0.308,
     "stage": 1,
     "model": "mock/smart",
     "delta": " from"
    },
    {
     "type": "answer_delta",
     "t": 0.324,
     "stage": 1,
     "model": "mock/smart",
     "delta": " **Smart"
    },
    {
     "type": "answer_delta",
     "t": 0.339,
     "stage": 1,
     "model": "mock/smart",
     "delta": " Demo"
    },
    {
     "type": "answer_delta",
     "t": 0.355,
     "stage": 1,
     "model": "mock/smart",
     "delta": " Model**."
    },
    {
     "type": "answer_delta",
     "t": 0.37,
     "stage": 1,
     "model": "mock/smart",
     "delta": " Tempo"
    },
    {
     "type": "answer_delta",
     "t": 0.385,
     "stage": 1,
     "model": "mock/smart",
     "delta": " analyzed"
    },
    {
     "type": "answer_delta",
     "t": 0.401,
     "stage": 1,
     "model": "mock/smart",
     "delta": " your"
    },
    {
     "type": "answer_delta",
     "t": 0.416,
     "stage": 1,
     "model": "mock/smart",
     "delta": " question,"
    },
    {
     "type": "answer_delta",
     "t": 0.432,
     "stage": 1,
     "model": "mock/smart",
     "delta": " ranked"
    },
    {
     "type": "answer_delta",
     "t": 0.447,
     "stage": 1,
     "model": "mock/smart",
     "delta": " the"
    },
    {
     "type": "answer_delta",
     "t": 0.463,
     "stage": 1,
     "model": "mock/smart",
     "delta": " available"
    },
    {
     "type": "answer_delta",
     "t": 0.478,
     "stage": 1,
     "model": "mock/smart",
     "delta": " models,"
    },
    {
     "type": "answer_delta",
     "t": 0.493,
     "stage": 1,
     "model": "mock/smart",
     "delta": " and"
    },
    {
     "type": "answer_delta",
     "t": 0.509,
     "stage": 1,
     "model": "mock/smart",
     "delta": " routed"
    },
    {
     "type": "answer_delta",
     "t": 0.525,
     "stage": 1,
     "model": "mock/smart",
     "delta": " it"
    },
    {
     "type": "answer_delta",
     "t": 0.54,
     "stage": 1,
     "model": "mock/smart",
     "delta": " here.\n\nYou"
    },
    {
     "type": "answer_delta",
     "t": 0.555,
     "stage": 1,
     "model": "mock/smart",
     "delta": " asked:"
    },
    {
     "type": "answer_delta",
     "t": 0.571,
     "stage": 1,
     "model": "mock/smart",
     "delta": " “What"
    },
    {
     "type": "answer_delta",
     "t": 0.586,
     "stage": 1,
     "model": "mock/smart",
     "delta": " is"
    },
    {
     "type": "answer_delta",
     "t": 0.601,
     "stage": 1,
     "model": "mock/smart",
     "delta": " 17%"
    },
    {
     "type": "answer_delta",
     "t": 0.617,
     "stage": 1,
     "model": "mock/smart",
     "delta": " of"
    },
    {
     "type": "answer_delta",
     "t": 0.632,
     "stage": 1,
     "model": "mock/smart",
     "delta": " 2,340?”\n\nAdd"
    },
    {
     "type": "answer_delta",
     "t": 0.648,
     "stage": 1,
     "model": "mock/smart",
     "delta": " a"
    },
    {
     "type": "answer_delta",
     "t": 0.663,
     "stage": 1,
     "model": "mock/smart",
     "delta": " free"
    },
    {
     "type": "answer_delta",
     "t": 0.679,
     "stage": 1,
     "model": "mock/smart",
     "delta": " API"
    },
    {
     "type": "answer_delta",
     "t": 0.694,
     "stage": 1,
     "model": "mock/smart",
     "delta": " key"
    },
    {
     "type": "answer_delta",
     "t": 0.709,
     "stage": 1,
     "model": "mock/smart",
     "delta": " (for"
    },
    {
     "type": "answer_delta",
     "t": 0.725,
     "stage": 1,
     "model": "mock/smart",
     "delta": " example"
    },
    {
     "type": "answer_delta",
     "t": 0.74,
     "stage": 1,
     "model": "mock/smart",
     "delta": " `GROQ_API_KEY`)"
    },
    {
     "type": "answer_delta",
     "t": 0.756,
     "stage": 1,
     "model": "mock/smart",
     "delta": " or"
    },
    {
     "type": "answer_delta",
     "t": 0.771,
     "stage": 1,
     "model": "mock/smart",
     "delta": " point"
    },
    {
     "type": "answer_delta",
     "t": 0.786,
     "stage": 1,
     "model": "mock/smart",
     "delta": " `OLLAMA_API_BASE`"
    },
    {
     "type": "answer_delta",
     "t": 0.802,
     "stage": 1,
     "model": "mock/smart",
     "delta": " at"
    },
    {
     "type": "answer_delta",
     "t": 0.817,
     "stage": 1,
     "model": "mock/smart",
     "delta": " a"
    },
    {
     "type": "answer_delta",
     "t": 0.833,
     "stage": 1,
     "model": "mock/smart",
     "delta": " local"
    },
    {
     "type": "answer_delta",
     "t": 0.848,
     "stage": 1,
     "model": "mock/smart",
     "delta": " Ollama"
    },
    {
     "type": "answer_delta",
     "t": 0.864,
     "stage": 1,
     "model": "mock/smart",
     "delta": " server"
    },
    {
     "type": "answer_delta",
     "t": 0.879,
     "stage": 1,
     "model": "mock/smart",
     "delta": " to"
    },
    {
     "type": "answer_delta",
     "t": 0.894,
     "stage": 1,
     "model": "mock/smart",
     "delta": " get"
    },
    {
     "type": "answer_delta",
     "t": 0.91,
     "stage": 1,
     "model": "mock/smart",
     "delta": " real"
    },
    {
     "type": "answer_delta",
     "t": 0.925,
     "stage": 1,
     "model": "mock/smart",
     "delta": " answers."
    },
    {
     "type": "call_end",
     "t": 0.926,
     "stage": 1,
     "job": "draft",
     "model": "mock/smart",
     "ms": 924,
     "ttft_ms": 91,
     "text": "Answer from mock/smart in 0.9s (first token 0.1s)"
    },
    {
     "type": "stage_end",
     "t": 0.926,
     "stage": 1,
     "job": "draft",
     "ms": 924,
     "answers": 1,
     "requests_used": 1,
     "requests_left": 11,
     "time_left_s": 59.1,
     "text": "Stage 1 done in 0.9s · 11 free requests left · 59.1s left"
    },
    {
     "type": "stage_start",
     "t": 0.927,
     "stage": 2,
     "max_stages": 5,
     "job": "check",
     "models": [
      "mock/fast"
     ],
     "reason": "heuristics + judge mock/fast",
     "requests_left": 11,
     "time_left_s": 59.1,
     "text": "Stage 2/5 · check · mock/fast · heuristics + judge mock/fast"
    },
    {
     "type": "call_start",
     "t": 0.927,
     "stage": 2,
     "job": "check",
     "model": "mock/fast",
     "attempt": 1,
     "text": "Calling mock/fast"
    },
    {
     "type": "call_end",
     "t": 1.003,
     "stage": 2,
     "job": "check",
     "model": "mock/fast",
     "ms": 76,
     "ttft_ms": 76,
     "text": "Answer from mock/fast in 0.1s (first token 0.1s)"
    },
    {
     "type": "check",
     "t": 1.003,
     "stage": 2,
     "results": [
      {
       "model": "mock/smart",
       "stage": 1,
       "score": 0.6,
       "passed": false,
       "issues": [
        "could be more specific"
       ],
       "hard_fail": false,
       "judge_score": 6.0,
       "judge_model": "mock/fast",
       "heuristic_score": 0.8
      }
     ],
     "judge_model": "mock/fast",
     "best_score": 0.6,
     "passed": false,
     "text": "Check: best score 0.60 ✗ not good enough yet (judge mock/fast) · could be more specific"
    },
    {
     "type": "stage_end",
     "t": 1.003,
     "stage": 2,
     "job": "check",
     "ms": 76,
     "answers": 0,
     "requests_used": 2,
     "requests_left": 10,
     "time_left_s": 59.0,
     "text": "Stage 2 done in 0.1s · 10 free requests left · 59s left"
    },
    {
     "type": "stage_start",
     "t": 1.004,
     "stage": 3,
     "max_stages": 5,
     "job": "fix",
     "models": [
      "mock/fast"
     ],
     "reason": "fix: could be more specific",
     "requests_left": 10,
     "time_left_s": 59.0,
     "text": "Stage 3/5 · fix · mock/fast · fix: could be more specific"
    },
    {
     "type": "call_start",
     "t": 1.004,
     "stage": 3,
     "job": "fix",
     "model": "mock/fast",
     "attempt": 1,
     "text": "Calling mock/fast"
    },
    {
     "type": "answer_reset",
     "t": 1.095,
     "stage": 3,
     "reason": "replaced by stage 3",
     "text": "Replacing the shown answer with stage 3"
    },
    {
     "type": "answer_delta",
     "t": 1.095,
     "stage": 3,
     "model": "mock/fast",
     "delta": "This"
    },
    {
     "type": "answer_delta",
     "t": 1.11,
     "stage": 3,
     "model": "mock/fast",
     "delta": " is"
    },
    {
     "type": "answer_delta",
     "t": 1.125,
     "stage": 3,
     "model": "mock/fast",
     "delta": " an"
    },
    {
     "type": "answer_delta",
     "t": 1.141,
     "stage": 3,
     "model": "mock/fast",
     "delta": " improved"
    },
    {
     "type": "answer_delta",
     "t": 1.156,
     "stage": 3,
     "model": "mock/fast",
     "delta": " offline"
    },
    {
     "type": "answer_delta",
     "t": 1.172,
     "stage": 3,
     "model": "mock/fast",
     "delta": " demo"
    },
    {
     "type": "answer_delta",
     "t": 1.187,
     "stage": 3,
     "model": "mock/fast",
     "delta": " answer"
    },
    {
     "type": "answer_delta",
     "t": 1.203,
     "stage": 3,
     "model": "mock/fast",
     "delta": " from"
    },
    {
     "type": "answer_delta",
     "t": 1.218,
     "stage": 3,
     "model": "mock/fast",
     "delta": " **Fast"
    },
    {
     "type": "answer_delta",
     "t": 1.233,
     "stage": 3,
     "model": "mock/fast",
     "delta": " Demo"
    },
    {
     "type": "answer_delta",
     "t": 1.249,
     "stage": 3,
     "model": "mock/fast",
     "delta": " Model**"
    },
    {
     "type": "answer_delta",
     "t": 1.264,
     "stage": 3,
     "model": "mock/fast",
     "delta": " (stage"
    },
    {
     "type": "answer_delta",
     "t": 1.28,
     "stage": 3,
     "model": "mock/fast",
     "delta": " job:"
    },
    {
     "type": "answer_delta",
     "t": 1.295,
     "stage": 3,
     "model": "mock/fast",
     "delta": " fix)."
    },
    {
     "type": "answer_delta",
     "t": 1.311,
     "stage": 3,
     "model": "mock/fast",
     "delta": " It"
    },
    {
     "type": "answer_delta",
     "t": 1.326,
     "stage": 3,
     "model": "mock/fast",
     "delta": " addresses"
    },
    {
     "type": "answer_delta",
     "t": 1.341,
     "stage": 3,
     "model": "mock/fast",
     "delta": " the"
    },
    {
     "type": "answer_delta",
     "t": 1.357,
     "stage": 3,
     "model": "mock/fast",
     "delta": " issues"
    },
    {
     "type": "answer_delta",
     "t": 1.372,
     "stage": 3,
     "model": "mock/fast",
     "delta": " the"
    },
    {
     "type": "answer_delta",
     "t": 1.388,
     "stage": 3,
     "model": "mock/fast",
     "delta": " checker"
    },
    {
     "type": "answer_delta",
     "t": 1.403,
     "stage": 3,
     "model": "mock/fast",
     "delta": " found"
    },
    {
     "type": "answer_delta",
     "t": 1.419,
     "stage": 3,
     "model": "mock/fast",
     "delta": " in"
    },
    {
     "type": "answer_delta",
     "t": 1.434,
     "stage": 3,
     "model": "mock/fast",
     "delta": " the"
    },
    {
     "type": "answer_delta",
     "t": 1.449,
     "stage": 3,
     "model": "mock/fast",
     "delta": " earlier"
    },
    {
     "type": "answer_delta",
     "t": 1.465,
     "stage": 3,
     "model": "mock/fast",
     "delta": " draft.\n\nAdd"
    },
    {
     "type": "answer_delta",
     "t": 1.48,
     "stage": 3,
     "model": "mock/fast",
     "delta": " a"
    },
    {
     "type": "answer_delta",
     "t": 1.495,
     "stage": 3,
     "model": "mock/fast",
     "delta": " free"
    },
    {
     "type": "answer_delta",
     "t": 1.511,
     "stage": 3,
     "model": "mock/fast",
     "delta": " API"
    },
    {
     "type": "answer_delta",
     "t": 1.526,
     "stage": 3,
     "model": "mock/fast",
     "delta": " key"
    },
    {
     "type": "answer_delta",
     "t": 1.541,
     "stage": 3,
     "model": "mock/fast",
     "delta": " (for"
    },
    {
     "type": "answer_delta",
     "t": 1.557,
     "stage": 3,
     "model": "mock/fast",
     "delta": " example"
    },
    {
     "type": "answer_delta",
     "t": 1.572,
     "stage": 3,
     "model": "mock/fast",
     "delta": " `GROQ_API_KEY`)"
    },
    {
     "type": "answer_delta",
     "t": 1.588,
     "stage": 3,
     "model": "mock/fast",
     "delta": " or"
    },
    {
     "type": "answer_delta",
     "t": 1.603,
     "stage": 3,
     "model": "mock/fast",
     "delta": " point"
    },
    {
     "type": "answer_delta",
     "t": 1.619,
     "stage": 3,
     "model": "mock/fast",
     "delta": " `OLLAMA_API_BASE`"
    },
    {
     "type": "answer_delta",
     "t": 1.634,
     "stage": 3,
     "model": "mock/fast",
     "delta": " at"
    },
    {
     "type": "answer_delta",
     "t": 1.649,
     "stage": 3,
     "model": "mock/fast",
     "delta": " a"
    },
    {
     "type": "answer_delta",
     "t": 1.665,
     "stage": 3,
     "model": "mock/fast",
     "delta": " local"
    },
    {
     "type": "answer_delta",
     "t": 1.68,
     "stage": 3,
     "model": "mock/fast",
     "delta": " Ollama"
    },
    {
     "type": "answer_delta",
     "t": 1.695,
     "stage": 3,
     "model": "mock/fast",
     "delta": " server"
    },
    {
     "type": "answer_delta",
     "t": 1.711,
     "stage": 3,
     "model": "mock/fast",
     "delta": " to"
    },
    {
     "type": "answer_delta",
     "t": 1.726,
     "stage": 3,
     "model": "mock/fast",
     "delta": " get"
    },
    {
     "type": "answer_delta",
     "t": 1.742,
     "stage": 3,
     "model": "mock/fast",
     "delta": " real"
    },
    {
     "type": "answer_delta",
     "t": 1.757,
     "stage": 3,
     "model": "mock/fast",
     "delta": " answers."
    },
    {
     "type": "call_end",
     "t": 1.757,
     "stage": 3,
     "job": "fix",
     "model": "mock/fast",
     "ms": 754,
     "ttft_ms": 91,
     "text": "Answer from mock/fast in 0.8s (first token 0.1s)"
    },
    {
     "type": "stage_end",
     "t": 1.758,
     "stage": 3,
     "job": "fix",
     "ms": 754,
     "answers": 1,
     "requests_used": 3,
     "requests_left": 9,
     "time_left_s": 58.2,
     "text": "Stage 3 done in 0.8s · 9 free requests left · 58.2s left"
    },
    {
     "type": "stage_start",
     "t": 1.758,
     "stage": 4,
     "max_stages": 5,
     "job": "check",
     "models": [
      "mock/smart"
     ],
     "reason": "heuristics + judge mock/smart",
     "requests_left": 9,
     "time_left_s": 58.2,
     "text": "Stage 4/5 · check · mock/smart · heuristics + judge mock/smart"
    },
    {
     "type": "call_start",
     "t": 1.759,
     "stage": 4,
     "job": "check",
     "model": "mock/smart",
     "attempt": 1,
     "text": "Calling mock/smart"
    },
    {
     "type": "call_end",
     "t": 1.834,
     "stage": 4,
     "job": "check",
     "model": "mock/smart",
     "ms": 76,
     "ttft_ms": 75,
     "text": "Answer from mock/smart in 0.1s (first token 0.1s)"
    },
    {
     "type": "check",
     "t": 1.835,
     "stage": 4,
     "results": [
      {
       "model": "mock/fast",
       "stage": 3,
       "score": 0.9,
       "passed": true,
       "issues": [],
       "hard_fail": false,
       "judge_score": 9.0,
       "judge_model": "mock/smart",
       "heuristic_score": 0.8
      }
     ],
     "judge_model": "mock/smart",
     "best_score": 0.9,
     "passed": true,
     "text": "Check: best score 0.90 ✓ passed (judge mock/smart)"
    },
    {
     "type": "stage_end",
     "t": 1.835,
     "stage": 4,
     "job": "check",
     "ms": 76,
     "answers": 0,
     "requests_used": 4,
     "requests_left": 8,
     "time_left_s": 58.2,
     "text": "Stage 4 done in 0.1s · 8 free requests left · 58.2s left"
    },
    {
     "type": "answer_final",
     "t": 1.835,
     "answer": "This is an improved offline demo answer from **Fast Demo Model** (stage job: fix). It addresses the issues the checker found in the earlier draft.\n\nAdd a free API key (for example `GROQ_API_KEY`) or point `OLLAMA_API_BASE` at a local Ollama server to get real answers.",
     "model": "mock/fast",
     "stage": 3,
     "score": 0.9,
     "passed": true,
     "reasoning": null,
     "tool_calls": null,
     "text": "Final answer from mock/fast (stage 3, score 0.90)"
    },
    {
     "type": "done",
     "t": 1.835,
     "model": "mock/fast",
     "stages": 4,
     "attempts": 4,
     "requests": 4,
     "total_ms": 1835,
     "stop_reason": "passed",
     "strategy": "cascade",
     "text": "Done in 1.8s · 4 stages · 4 model calls · 4 free used · answer passed its check"
    }
   ]
  },
  {
   "question": "Translate \"Where is the railway station?\" into Hindi and Gujarati",
   "events": [
    {
     "type": "received",
     "t": 0.0,
     "mode": "auto",
     "text": "Received · mode auto"
    },
    {
     "type": "analyze",
     "t": 0.0,
     "task": "translate",
     "complexity": 0.2,
     "script": "latin",
     "needs": [],
     "input_tokens": 16,
     "est_output_tokens": 200,
     "source": "rules",
     "text": "Understanding: translate · complexity 0.20 · ~16 input tokens"
    },
    {
     "type": "plan",
     "t": 0.001,
     "strategy": "single",
     "max_stages": 3,
     "drafts": 1,
     "parts": 0,
     "time_budget_s": 60.0,
     "quota_budget": 12,
     "reason": "simple question: one draft and a quick check",
     "laya": "Laya off",
     "text": "Plan: single · up to 3 stages · 60s · 12 free requests · simple question: one draft and a quick check · Laya off"
    },
    {
     "type": "stage_start",
     "t": 0.001,
     "stage": 1,
     "max_stages": 3,
     "job": "draft",
     "models": [
      "mock/smart"
     ],
     "reason": "highest predicted quality for translate",
     "requests_left": 12,
     "time_left_s": 60.0,
     "text": "Stage 1/3 · draft · mock/smart · highest predicted quality for translate"
    },
    {
     "type": "call_start",
     "t": 0.001,
     "stage": 1,
     "job": "draft",
     "model": "mock/smart",
     "attempt": 1,
     "text": "Calling mock/smart"
    },
    {
     "type": "reasoning_delta",
     "t": 0.092,
     "stage": 1,
     "model": "mock/smart",
     "delta": "Checking "
    },
    {
     "type": "reasoning_delta",
     "t": 0.108,
     "stage": 1,
     "model": "mock/smart",
     "delta": "what "
    },
    {
     "type": "reasoning_delta",
     "t": 0.123,
     "stage": 1,
     "model": "mock/smart",
     "delta": "the "
    },
    {
     "type": "reasoning_delta",
     "t": 0.138,
     "stage": 1,
     "model": "mock/smart",
     "delta": "question "
    },
    {
     "type": "reasoning_delta",
     "t": 0.154,
     "stage": 1,
     "model": "mock/smart",
     "delta": "asks "
    },
    {
     "type": "reasoning_delta",
     "t": 0.169,
     "stage": 1,
     "model": "mock/smart",
     "delta": "for "
    },
    {
     "type": "reasoning_delta",
     "t": 0.185,
     "stage": 1,
     "model": "mock/smart",
     "delta": "before "
    },
    {
     "type": "reasoning_delta",
     "t": 0.201,
     "stage": 1,
     "model": "mock/smart",
     "delta": "answering. "
    },
    {
     "type": "answer_delta",
     "t": 0.216,
     "stage": 1,
     "model": "mock/smart",
     "delta": "This"
    },
    {
     "type": "answer_delta",
     "t": 0.232,
     "stage": 1,
     "model": "mock/smart",
     "delta": " is"
    },
    {
     "type": "answer_delta",
     "t": 0.248,
     "stage": 1,
     "model": "mock/smart",
     "delta": " an"
    },
    {
     "type": "answer_delta",
     "t": 0.263,
     "stage": 1,
     "model": "mock/smart",
     "delta": " offline"
    },
    {
     "type": "answer_delta",
     "t": 0.279,
     "stage": 1,
     "model": "mock/smart",
     "delta": " demo"
    },
    {
     "type": "answer_delta",
     "t": 0.294,
     "stage": 1,
     "model": "mock/smart",
     "delta": " answer"
    },
    {
     "type": "answer_delta",
     "t": 0.31,
     "stage": 1,
     "model": "mock/smart",
     "delta": " from"
    },
    {
     "type": "answer_delta",
     "t": 0.325,
     "stage": 1,
     "model": "mock/smart",
     "delta": " **Smart"
    },
    {
     "type": "answer_delta",
     "t": 0.341,
     "stage": 1,
     "model": "mock/smart",
     "delta": " Demo"
    },
    {
     "type": "answer_delta",
     "t": 0.356,
     "stage": 1,
     "model": "mock/smart",
     "delta": " Model**."
    },
    {
     "type": "answer_delta",
     "t": 0.373,
     "stage": 1,
     "model": "mock/smart",
     "delta": " Tempo"
    },
    {
     "type": "answer_delta",
     "t": 0.388,
     "stage": 1,
     "model": "mock/smart",
     "delta": " analyzed"
    },
    {
     "type": "answer_delta",
     "t": 0.403,
     "stage": 1,
     "model": "mock/smart",
     "delta": " your"
    },
    {
     "type": "answer_delta",
     "t": 0.419,
     "stage": 1,
     "model": "mock/smart",
     "delta": " question,"
    },
    {
     "type": "answer_delta",
     "t": 0.434,
     "stage": 1,
     "model": "mock/smart",
     "delta": " ranked"
    },
    {
     "type": "answer_delta",
     "t": 0.45,
     "stage": 1,
     "model": "mock/smart",
     "delta": " the"
    },
    {
     "type": "answer_delta",
     "t": 0.465,
     "stage": 1,
     "model": "mock/smart",
     "delta": " available"
    },
    {
     "type": "answer_delta",
     "t": 0.481,
     "stage": 1,
     "model": "mock/smart",
     "delta": " models,"
    },
    {
     "type": "answer_delta",
     "t": 0.496,
     "stage": 1,
     "model": "mock/smart",
     "delta": " and"
    },
    {
     "type": "answer_delta",
     "t": 0.512,
     "stage": 1,
     "model": "mock/smart",
     "delta": " routed"
    },
    {
     "type": "answer_delta",
     "t": 0.527,
     "stage": 1,
     "model": "mock/smart",
     "delta": " it"
    },
    {
     "type": "answer_delta",
     "t": 0.543,
     "stage": 1,
     "model": "mock/smart",
     "delta": " here.\n\nYou"
    },
    {
     "type": "answer_delta",
     "t": 0.558,
     "stage": 1,
     "model": "mock/smart",
     "delta": " asked:"
    },
    {
     "type": "answer_delta",
     "t": 0.574,
     "stage": 1,
     "model": "mock/smart",
     "delta": " “Translate"
    },
    {
     "type": "answer_delta",
     "t": 0.589,
     "stage": 1,
     "model": "mock/smart",
     "delta": " \"Where"
    },
    {
     "type": "answer_delta",
     "t": 0.605,
     "stage": 1,
     "model": "mock/smart",
     "delta": " is"
    },
    {
     "type": "answer_delta",
     "t": 0.62,
     "stage": 1,
     "model": "mock/smart",
     "delta": " the"
    },
    {
     "type": "answer_delta",
     "t": 0.636,
     "stage": 1,
     "model": "mock/smart",
     "delta": " railway"
    },
    {
     "type": "answer_delta",
     "t": 0.651,
     "stage": 1,
     "model": "mock/smart",
     "delta": " station?\""
    },
    {
     "type": "answer_delta",
     "t": 0.666,
     "stage": 1,
     "model": "mock/smart",
     "delta": " into"
    },
    {
     "type": "answer_delta",
     "t": 0.682,
     "stage": 1,
     "model": "mock/smart",
     "delta": " Hindi"
    },
    {
     "type": "answer_delta",
     "t": 0.697,
     "stage": 1,
     "model": "mock/smart",
     "delta": " and"
    },
    {
     "type": "answer_delta",
     "t": 0.713,
     "stage": 1,
     "model": "mock/smart",
     "delta": " Gujarati”\n\nAdd"
    },
    {
     "type": "answer_delta",
     "t": 0.728,
     "stage": 1,
     "model": "mock/smart",
     "delta": " a"
    },
    {
     "type": "answer_delta",
     "t": 0.744,
     "stage": 1,
     "model": "mock/smart",
     "delta": " free"
    },
    {
     "type": "answer_delta",
     "t": 0.76,
     "stage": 1,
     "model": "mock/smart",
     "delta": " API"
    },
    {
     "type": "answer_delta",
     "t": 0.775,
     "stage": 1,
     "model": "mock/smart",
     "delta": " key"
    },
    {
     "type": "answer_delta",
     "t": 0.79,
     "stage": 1,
     "model": "mock/smart",
     "delta": " (for"
    },
    {
     "type": "answer_delta",
     "t": 0.806,
     "stage": 1,
     "model": "mock/smart",
     "delta": " example"
    },
    {
     "type": "answer_delta",
     "t": 0.821,
     "stage": 1,
     "model": "mock/smart",
     "delta": " `GROQ_API_KEY`)"
    },
    {
     "type": "answer_delta",
     "t": 0.837,
     "stage": 1,
     "model": "mock/smart",
     "delta": " or"
    },
    {
     "type": "answer_delta",
     "t": 0.852,
     "stage": 1,
     "model": "mock/smart",
     "delta": " point"
    },
    {
     "type": "answer_delta",
     "t": 0.868,
     "stage": 1,
     "model": "mock/smart",
     "delta": " `OLLAMA_API_BASE`"
    },
    {
     "type": "answer_delta",
     "t": 0.883,
     "stage": 1,
     "model": "mock/smart",
     "delta": " at"
    },
    {
     "type": "answer_delta",
     "t": 0.899,
     "stage": 1,
     "model": "mock/smart",
     "delta": " a"
    },
    {
     "type": "answer_delta",
     "t": 0.914,
     "stage": 1,
     "model": "mock/smart",
     "delta": " local"
    },
    {
     "type": "answer_delta",
     "t": 0.93,
     "stage": 1,
     "model": "mock/smart",
     "delta": " Ollama"
    },
    {
     "type": "answer_delta",
     "t": 0.945,
     "stage": 1,
     "model": "mock/smart",
     "delta": " server"
    },
    {
     "type": "answer_delta",
     "t": 0.961,
     "stage": 1,
     "model": "mock/smart",
     "delta": " to"
    },
    {
     "type": "answer_delta",
     "t": 0.976,
     "stage": 1,
     "model": "mock/smart",
     "delta": " get"
    },
    {
     "type": "answer_delta",
     "t": 0.992,
     "stage": 1,
     "model": "mock/smart",
     "delta": " real"
    },
    {
     "type": "answer_delta",
     "t": 1.007,
     "stage": 1,
     "model": "mock/smart",
     "delta": " answers."
    },
    {
     "type": "call_end",
     "t": 1.007,
     "stage": 1,
     "job": "draft",
     "model": "mock/smart",
     "ms": 1006,
     "ttft_ms": 91,
     "text": "Answer from mock/smart in 1.0s (first token 0.1s)"
    },
    {
     "type": "stage_end",
     "t": 1.007,
     "stage": 1,
     "job": "draft",
     "ms": 1006,
     "answers": 1,
     "requests_used": 1,
     "requests_left": 11,
     "time_left_s": 59.0,
     "text": "Stage 1 done in 1.0s · 11 free requests left · 59s left"
    },
    {
     "type": "stage_start",
     "t": 1.008,
     "stage": 2,
     "max_stages": 3,
     "job": "check",
     "models": [],
     "reason": "heuristics only",
     "requests_left": 11,
     "time_left_s": 59.0,
     "text": "Stage 2/3 · check · no model · heuristics only"
    },
    {
     "type": "check",
     "t": 1.008,
     "stage": 2,
     "results": [
      {
       "model": "mock/smart",
       "stage": 1,
       "score": 0.8,
       "passed": true,
       "issues": [],
       "hard_fail": false,
       "judge_score": null,
       "judge_model": null,
       "heuristic_score": 0.8
      }
     ],
     "judge_model": null,
     "best_score": 0.8,
     "passed": true,
     "text": "Check: best score 0.80 ✓ passed"
    },
    {
     "type": "stage_end",
     "t": 1.008,
     "stage": 2,
     "job": "check",
     "ms": 0,
     "answers": 0,
     "requests_used": 1,
     "requests_left": 11,
     "time_left_s": 59.0,
     "text": "Stage 2 done in 0.0s · 11 free requests left · 59s left"
    },
    {
     "type": "answer_final",
     "t": 1.008,
     "answer": "This is an offline demo answer from **Smart Demo Model**. Tempo analyzed your question, ranked the available models, and routed it here.\n\nYou asked: “Translate \"Where is the railway station?\" into Hindi and Gujarati”\n\nAdd a free API key (for example `GROQ_API_KEY`) or point `OLLAMA_API_BASE` at a local Ollama server to get real answers.",
     "model": "mock/smart",
     "stage": 1,
     "score": 0.8,
     "passed": true,
     "reasoning": "Checking what the question asks for before answering. ",
     "tool_calls": null,
     "text": "Final answer from mock/smart (stage 1, score 0.80)"
    },
    {
     "type": "done",
     "t": 1.008,
     "model": "mock/smart",
     "stages": 2,
     "attempts": 1,
     "requests": 1,
     "total_ms": 1008,
     "stop_reason": "passed",
     "strategy": "single",
     "text": "Done in 1.0s · 2 stages · 1 model call · 1 free used · answer passed its check"
    }
   ]
  },
  {
   "question": "Explain the difference between TCP and UDP",
   "events": [
    {
     "type": "received",
     "t": 0.0,
     "mode": "auto",
     "text": "Received · mode auto"
    },
    {
     "type": "analyze",
     "t": 0.0,
     "task": "reasoning",
     "complexity": 0.4,
     "script": "latin",
     "needs": [],
     "input_tokens": 10,
     "est_output_tokens": 500,
     "source": "rules",
     "text": "Understanding: reasoning · complexity 0.40 · ~10 input tokens"
    },
    {
     "type": "plan",
     "t": 0.001,
     "strategy": "cascade",
     "max_stages": 5,
     "drafts": 1,
     "parts": 0,
     "time_budget_s": 60.0,
     "quota_budget": 12,
     "reason": "draft, check, then fix if needed",
     "laya": "Laya off",
     "text": "Plan: cascade · up to 5 stages · 60s · 12 free requests · draft, check, then fix if needed · Laya off"
    },
    {
     "type": "stage_start",
     "t": 0.002,
     "stage": 1,
     "max_stages": 5,
     "job": "draft",
     "models": [
      "mock/smart"
     ],
     "reason": "highest predicted quality for reasoning",
     "requests_left": 12,
     "time_left_s": 60.0,
     "text": "Stage 1/5 · draft · mock/smart · highest predicted quality for reasoning"
    },
    {
     "type": "call_start",
     "t": 0.002,
     "stage": 1,
     "job": "draft",
     "model": "mock/smart",
     "attempt": 1,
     "text": "Calling mock/smart"
    },
    {
     "type": "reasoning_delta",
     "t": 0.093,
     "stage": 1,
     "model": "mock/smart",
     "delta": "Checking "
    },
    {
     "type": "reasoning_delta",
     "t": 0.108,
     "stage": 1,
     "model": "mock/smart",
     "delta": "what "
    },
    {
     "type": "reasoning_delta",
     "t": 0.124,
     "stage": 1,
     "model": "mock/smart",
     "delta": "the "
    },
    {
     "type": "reasoning_delta",
     "t": 0.139,
     "stage": 1,
     "model": "mock/smart",
     "delta": "question "
    },
    {
     "type": "reasoning_delta",
     "t": 0.154,
     "stage": 1,
     "model": "mock/smart",
     "delta": "asks "
    },
    {
     "type": "reasoning_delta",
     "t": 0.17,
     "stage": 1,
     "model": "mock/smart",
     "delta": "for "
    },
    {
     "type": "reasoning_delta",
     "t": 0.185,
     "stage": 1,
     "model": "mock/smart",
     "delta": "before "
    },
    {
     "type": "reasoning_delta",
     "t": 0.201,
     "stage": 1,
     "model": "mock/smart",
     "delta": "answering. "
    },
    {
     "type": "answer_delta",
     "t": 0.216,
     "stage": 1,
     "model": "mock/smart",
     "delta": "This"
    },
    {
     "type": "answer_delta",
     "t": 0.232,
     "stage": 1,
     "model": "mock/smart",
     "delta": " is"
    },
    {
     "type": "answer_delta",
     "t": 0.247,
     "stage": 1,
     "model": "mock/smart",
     "delta": " an"
    },
    {
     "type": "answer_delta",
     "t": 0.263,
     "stage": 1,
     "model": "mock/smart",
     "delta": " offline"
    },
    {
     "type": "answer_delta",
     "t": 0.278,
     "stage": 1,
     "model": "mock/smart",
     "delta": " demo"
    },
    {
     "type": "answer_delta",
     "t": 0.294,
     "stage": 1,
     "model": "mock/smart",
     "delta": " answer"
    },
    {
     "type": "answer_delta",
     "t": 0.309,
     "stage": 1,
     "model": "mock/smart",
     "delta": " from"
    },
    {
     "type": "answer_delta",
     "t": 0.324,
     "stage": 1,
     "model": "mock/smart",
     "delta": " **Smart"
    },
    {
     "type": "answer_delta",
     "t": 0.34,
     "stage": 1,
     "model": "mock/smart",
     "delta": " Demo"
    },
    {
     "type": "answer_delta",
     "t": 0.355,
     "stage": 1,
     "model": "mock/smart",
     "delta": " Model**."
    },
    {
     "type": "answer_delta",
     "t": 0.371,
     "stage": 1,
     "model": "mock/smart",
     "delta": " Tempo"
    },
    {
     "type": "answer_delta",
     "t": 0.386,
     "stage": 1,
     "model": "mock/smart",
     "delta": " analyzed"
    },
    {
     "type": "answer_delta",
     "t": 0.402,
     "stage": 1,
     "model": "mock/smart",
     "delta": " your"
    },
    {
     "type": "answer_delta",
     "t": 0.417,
     "stage": 1,
     "model": "mock/smart",
     "delta": " question,"
    },
    {
     "type": "answer_delta",
     "t": 0.433,
     "stage": 1,
     "model": "mock/smart",
     "delta": " ranked"
    },
    {
     "type": "answer_delta",
     "t": 0.448,
     "stage": 1,
     "model": "mock/smart",
     "delta": " the"
    },
    {
     "type": "answer_delta",
     "t": 0.463,
     "stage": 1,
     "model": "mock/smart",
     "delta": " available"
    },
    {
     "type": "answer_delta",
     "t": 0.479,
     "stage": 1,
     "model": "mock/smart",
     "delta": " models,"
    },
    {
     "type": "answer_delta",
     "t": 0.494,
     "stage": 1,
     "model": "mock/smart",
     "delta": " and"
    },
    {
     "type": "answer_delta",
     "t": 0.51,
     "stage": 1,
     "model": "mock/smart",
     "delta": " routed"
    },
    {
     "type": "answer_delta",
     "t": 0.526,
     "stage": 1,
     "model": "mock/smart",
     "delta": " it"
    },
    {
     "type": "answer_delta",
     "t": 0.541,
     "stage": 1,
     "model": "mock/smart",
     "delta": " here.\n\nYou"
    },
    {
     "type": "answer_delta",
     "t": 0.557,
     "stage": 1,
     "model": "mock/smart",
     "delta": " asked:"
    },
    {
     "type": "answer_delta",
     "t": 0.572,
     "stage": 1,
     "model": "mock/smart",
     "delta": " “Explain"
    },
    {
     "type": "answer_delta",
     "t": 0.587,
     "stage": 1,
     "model": "mock/smart",
     "delta": " the"
    },
    {
     "type": "answer_delta",
     "t": 0.603,
     "stage": 1,
     "model": "mock/smart",
     "delta": " difference"
    },
    {
     "type": "answer_delta",
     "t": 0.619,
     "stage": 1,
     "model": "mock/smart",
     "delta": " between"
    },
    {
     "type": "answer_delta",
     "t": 0.634,
     "stage": 1,
     "model": "mock/smart",
     "delta": " TCP"
    },
    {
     "type": "answer_delta",
     "t": 0.649,
     "stage": 1,
     "model": "mock/smart",
     "delta": " and"
    },
    {
     "type": "answer_delta",
     "t": 0.665,
     "stage": 1,
     "model": "mock/smart",
     "delta": " UDP”\n\nAdd"
    },
    {
     "type": "answer_delta",
     "t": 0.68,
     "stage": 1,
     "model": "mock/smart",
     "delta": " a"
    },
    {
     "type": "answer_delta",
     "t": 0.696,
     "stage": 1,
     "model": "mock/smart",
     "delta": " free"
    },
    {
     "type": "answer_delta",
     "t": 0.711,
     "stage": 1,
     "model": "mock/smart",
     "delta": " API"
    },
    {
     "type": "answer_delta",
     "t": 0.727,
     "stage": 1,
     "model": "mock/smart",
     "delta": " key"
    },
    {
     "type": "answer_delta",
     "t": 0.742,
     "stage": 1,
     "model": "mock/smart",
     "delta": " (for"
    },
    {
     "type": "answer_delta",
     "t": 0.757,
     "stage": 1,
     "model": "mock/smart",
     "delta": " example"
    },
    {
     "type": "answer_delta",
     "t": 0.773,
     "stage": 1,
     "model": "mock/smart",
     "delta": " `GROQ_API_KEY`)"
    },
    {
     "type": "answer_delta",
     "t": 0.789,
     "stage": 1,
     "model": "mock/smart",
     "delta": " or"
    },
    {
     "type": "answer_delta",
     "t": 0.805,
     "stage": 1,
     "model": "mock/smart",
     "delta": " point"
    },
    {
     "type": "answer_delta",
     "t": 0.821,
     "stage": 1,
     "model": "mock/smart",
     "delta": " `OLLAMA_API_BASE`"
    },
    {
     "type": "answer_delta",
     "t": 0.836,
     "stage": 1,
     "model": "mock/smart",
     "delta": " at"
    },
    {
     "type": "answer_delta",
     "t": 0.852,
     "stage": 1,
     "model": "mock/smart",
     "delta": " a"
    },
    {
     "type": "answer_delta",
     "t": 0.867,
     "stage": 1,
     "model": "mock/smart",
     "delta": " local"
    },
    {
     "type": "answer_delta",
     "t": 0.883,
     "stage": 1,
     "model": "mock/smart",
     "delta": " Ollama"
    },
    {
     "type": "answer_delta",
     "t": 0.898,
     "stage": 1,
     "model": "mock/smart",
     "delta": " server"
    },
    {
     "type": "answer_delta",
     "t": 0.914,
     "stage": 1,
     "model": "mock/smart",
     "delta": " to"
    },
    {
     "type": "answer_delta",
     "t": 0.929,
     "stage": 1,
     "model": "mock/smart",
     "delta": " get"
    },
    {
     "type": "answer_delta",
     "t": 0.945,
     "stage": 1,
     "model": "mock/smart",
     "delta": " real"
    },
    {
     "type": "answer_delta",
     "t": 0.96,
     "stage": 1,
     "model": "mock/smart",
     "delta": " answers."
    },
    {
     "type": "call_end",
     "t": 0.961,
     "stage": 1,
     "job": "draft",
     "model": "mock/smart",
     "ms": 959,
     "ttft_ms": 91,
     "text": "Answer from mock/smart in 1.0s (first token 0.1s)"
    },
    {
     "type": "stage_end",
     "t": 0.961,
     "stage": 1,
     "job": "draft",
     "ms": 959,
     "answers": 1,
     "requests_used": 1,
     "requests_left": 11,
     "time_left_s": 59.0,
     "text": "Stage 1 done in 1.0s · 11 free requests left · 59s left"
    },
    {
     "type": "stage_start",
     "t": 0.962,
     "stage": 2,
     "max_stages": 5,
     "job": "check",
     "models": [
      "mock/fast"
     ],
     "reason": "heuristics + judge mock/fast",
     "requests_left": 11,
     "time_left_s": 59.0,
     "text": "Stage 2/5 · check · mock/fast · heuristics + judge mock/fast"
    },
    {
     "type": "call_start",
     "t": 0.962,
     "stage": 2,
     "job": "check",
     "model": "mock/fast",
     "attempt": 1,
     "text": "Calling mock/fast"
    },
    {
     "type": "call_end",
     "t": 1.038,
     "stage": 2,
     "job": "check",
     "model": "mock/fast",
     "ms": 76,
     "ttft_ms": 75,
     "text": "Answer from mock/fast in 0.1s (first token 0.1s)"
    },
    {
     "type": "check",
     "t": 1.038,
     "stage": 2,
     "results": [
      {
       "model": "mock/smart",
       "stage": 1,
       "score": 0.6,
       "passed": false,
       "issues": [
        "could be more specific"
       ],
       "hard_fail": false,
       "judge_score": 6.0,
       "judge_model": "mock/fast",
       "heuristic_score": 0.8
      }
     ],
     "judge_model": "mock/fast",
     "best_score": 0.6,
     "passed": false,
     "text": "Check: best score 0.60 ✗ not good enough yet (judge mock/fast) · could be more specific"
    },
    {
     "type": "stage_end",
     "t": 1.038,
     "stage": 2,
     "job": "check",
     "ms": 76,
     "answers": 0,
     "requests_used": 2,
     "requests_left": 10,
     "time_left_s": 59.0,
     "text": "Stage 2 done in 0.1s · 10 free requests left · 59s left"
    },
    {
     "type": "stage_start",
     "t": 1.039,
     "stage": 3,
     "max_stages": 5,
     "job": "fix",
     "models": [
      "mock/fast"
     ],
     "reason": "fix: could be more specific",
     "requests_left": 10,
     "time_left_s": 59.0,
     "text": "Stage 3/5 · fix · mock/fast · fix: could be more specific"
    },
    {
     "type": "call_start",
     "t": 1.039,
     "stage": 3,
     "job": "fix",
     "model": "mock/fast",
     "attempt": 1,
     "text": "Calling mock/fast"
    },
    {
     "type": "answer_reset",
     "t": 1.13,
     "stage": 3,
     "reason": "replaced by stage 3",
     "text": "Replacing the shown answer with stage 3"
    },
    {
     "type": "answer_delta",
     "t": 1.13,
     "stage": 3,
     "model": "mock/fast",
     "delta": "This"
    },
    {
     "type": "answer_delta",
     "t": 1.145,
     "stage": 3,
     "model": "mock/fast",
     "delta": " is"
    },
    {
     "type": "answer_delta",
     "t": 1.161,
     "stage": 3,
     "model": "mock/fast",
     "delta": " an"
    },
    {
     "type": "answer_delta",
     "t": 1.176,
     "stage": 3,
     "model": "mock/fast",
     "delta": " improved"
    },
    {
     "type": "answer_delta",
     "t": 1.192,
     "stage": 3,
     "model": "mock/fast",
     "delta": " offline"
    },
    {
     "type": "answer_delta",
     "t": 1.207,
     "stage": 3,
     "model": "mock/fast",
     "delta": " demo"
    },
    {
     "type": "answer_delta",
     "t": 1.222,
     "stage": 3,
     "model": "mock/fast",
     "delta": " answer"
    },
    {
     "type": "answer_delta",
     "t": 1.238,
     "stage": 3,
     "model": "mock/fast",
     "delta": " from"
    },
    {
     "type": "answer_delta",
     "t": 1.253,
     "stage": 3,
     "model": "mock/fast",
     "delta": " **Fast"
    },
    {
     "type": "answer_delta",
     "t": 1.269,
     "stage": 3,
     "model": "mock/fast",
     "delta": " Demo"
    },
    {
     "type": "answer_delta",
     "t": 1.284,
     "stage": 3,
     "model": "mock/fast",
     "delta": " Model**"
    },
    {
     "type": "answer_delta",
     "t": 1.3,
     "stage": 3,
     "model": "mock/fast",
     "delta": " (stage"
    },
    {
     "type": "answer_delta",
     "t": 1.315,
     "stage": 3,
     "model": "mock/fast",
     "delta": " job:"
    },
    {
     "type": "answer_delta",
     "t": 1.331,
     "stage": 3,
     "model": "mock/fast",
     "delta": " fix)."
    },
    {
     "type": "answer_delta",
     "t": 1.346,
     "stage": 3,
     "model": "mock/fast",
     "delta": " It"
    },
    {
     "type": "answer_delta",
     "t": 1.362,
     "stage": 3,
     "model": "mock/fast",
     "delta": " addresses"
    },
    {
     "type": "answer_delta",
     "t": 1.377,
     "stage": 3,
     "model": "mock/fast",
     "delta": " the"
    },
    {
     "type": "answer_delta",
     "t": 1.393,
     "stage": 3,
     "model": "mock/fast",
     "delta": " issues"
    },
    {
     "type": "answer_delta",
     "t": 1.408,
     "stage": 3,
     "model": "mock/fast",
     "delta": " the"
    },
    {
     "type": "answer_delta",
     "t": 1.424,
     "stage": 3,
     "model": "mock/fast",
     "delta": " checker"
    },
    {
     "type": "answer_delta",
     "t": 1.439,
     "stage": 3,
     "model": "mock/fast",
     "delta": " found"
    },
    {
     "type": "answer_delta",
     "t": 1.455,
     "stage": 3,
     "model": "mock/fast",
     "delta": " in"
    },
    {
     "type": "answer_delta",
     "t": 1.47,
     "stage": 3,
     "model": "mock/fast",
     "delta": " the"
    },
    {
     "type": "answer_delta",
     "t": 1.486,
     "stage": 3,
     "model": "mock/fast",
     "delta": " earlier"
    },
    {
     "type": "answer_delta",
     "t": 1.501,
     "stage": 3,
     "model": "mock/fast",
     "delta": " draft.\n\nAdd"
    },
    {
     "type": "answer_delta",
     "t": 1.517,
     "stage": 3,
     "model": "mock/fast",
     "delta": " a"
    },
    {
     "type": "answer_delta",
     "t": 1.532,
     "stage": 3,
     "model": "mock/fast",
     "delta": " free"
    },
    {
     "type": "answer_delta",
     "t": 1.548,
     "stage": 3,
     "model": "mock/fast",
     "delta": " API"
    },
    {
     "type": "answer_delta",
     "t": 1.563,
     "stage": 3,
     "model": "mock/fast",
     "delta": " key"
    },
    {
     "type": "answer_delta",
     "t": 1.579,
     "stage": 3,
     "model": "mock/fast",
     "delta": " (for"
    },
    {
     "type": "answer_delta",
     "t": 1.594,
     "stage": 3,
     "model": "mock/fast",
     "delta": " example"
    },
    {
     "type": "answer_delta",
     "t": 1.61,
     "stage": 3,
     "model": "mock/fast",
     "delta": " `GROQ_API_KEY`)"
    },
    {
     "type": "answer_delta",
     "t": 1.625,
     "stage": 3,
     "model": "mock/fast",
     "delta": " or"
    },
    {
     "type": "answer_delta",
     "t": 1.641,
     "stage": 3,
     "model": "mock/fast",
     "delta": " point"
    },
    {
     "type": "answer_delta",
     "t": 1.656,
     "stage": 3,
     "model": "mock/fast",
     "delta": " `OLLAMA_API_BASE`"
    },
    {
     "type": "answer_delta",
     "t": 1.672,
     "stage": 3,
     "model": "mock/fast",
     "delta": " at"
    },
    {
     "type": "answer_delta",
     "t": 1.687,
     "stage": 3,
     "model": "mock/fast",
     "delta": " a"
    },
    {
     "type": "answer_delta",
     "t": 1.702,
     "stage": 3,
     "model": "mock/fast",
     "delta": " local"
    },
    {
     "type": "answer_delta",
     "t": 1.718,
     "stage": 3,
     "model": "mock/fast",
     "delta": " Ollama"
    },
    {
     "type": "answer_delta",
     "t": 1.733,
     "stage": 3,
     "model": "mock/fast",
     "delta": " server"
    },
    {
     "type": "answer_delta",
     "t": 1.749,
     "stage": 3,
     "model": "mock/fast",
     "delta": " to"
    },
    {
     "type": "answer_delta",
     "t": 1.764,
     "stage": 3,
     "model": "mock/fast",
     "delta": " get"
    },
    {
     "type": "answer_delta",
     "t": 1.78,
     "stage": 3,
     "model": "mock/fast",
     "delta": " real"
    },
    {
     "type": "answer_delta",
     "t": 1.795,
     "stage": 3,
     "model": "mock/fast",
     "delta": " answers."
    },
    {
     "type": "call_end",
     "t": 1.795,
     "stage": 3,
     "job": "fix",
     "model": "mock/fast",
     "ms": 756,
     "ttft_ms": 91,
     "text": "Answer from mock/fast in 0.8s (first token 0.1s)"
    },
    {
     "type": "stage_end",
     "t": 1.795,
     "stage": 3,
     "job": "fix",
     "ms": 756,
     "answers": 1,
     "requests_used": 3,
     "requests_left": 9,
     "time_left_s": 58.2,
     "text": "Stage 3 done in 0.8s · 9 free requests left · 58.2s left"
    },
    {
     "type": "stage_start",
     "t": 1.796,
     "stage": 4,
     "max_stages": 5,
     "job": "check",
     "models": [
      "mock/smart"
     ],
     "reason": "heuristics + judge mock/smart",
     "requests_left": 9,
     "time_left_s": 58.2,
     "text": "Stage 4/5 · check · mock/smart · heuristics + judge mock/smart"
    },
    {
     "type": "call_start",
     "t": 1.796,
     "stage": 4,
     "job": "check",
     "model": "mock/smart",
     "attempt": 1,
     "text": "Calling mock/smart"
    },
    {
     "type": "call_end",
     "t": 1.872,
     "stage": 4,
     "job": "check",
     "model": "mock/smart",
     "ms": 76,
     "ttft_ms": 75,
     "text": "Answer from mock/smart in 0.1s (first token 0.1s)"
    },
    {
     "type": "check",
     "t": 1.872,
     "stage": 4,
     "results": [
      {
       "model": "mock/fast",
       "stage": 3,
       "score": 0.9,
       "passed": true,
       "issues": [],
       "hard_fail": false,
       "judge_score": 9.0,
       "judge_model": "mock/smart",
       "heuristic_score": 0.8
      }
     ],
     "judge_model": "mock/smart",
     "best_score": 0.9,
     "passed": true,
     "text": "Check: best score 0.90 ✓ passed (judge mock/smart)"
    },
    {
     "type": "stage_end",
     "t": 1.872,
     "stage": 4,
     "job": "check",
     "ms": 76,
     "answers": 0,
     "requests_used": 4,
     "requests_left": 8,
     "time_left_s": 58.1,
     "text": "Stage 4 done in 0.1s · 8 free requests left · 58.1s left"
    },
    {
     "type": "answer_final",
     "t": 1.872,
     "answer": "This is an improved offline demo answer from **Fast Demo Model** (stage job: fix). It addresses the issues the checker found in the earlier draft.\n\nAdd a free API key (for example `GROQ_API_KEY`) or point `OLLAMA_API_BASE` at a local Ollama server to get real answers.",
     "model": "mock/fast",
     "stage": 3,
     "score": 0.9,
     "passed": true,
     "reasoning": null,
     "tool_calls": null,
     "text": "Final answer from mock/fast (stage 3, score 0.90)"
    },
    {
     "type": "done",
     "t": 1.872,
     "model": "mock/fast",
     "stages": 4,
     "attempts": 4,
     "requests": 4,
     "total_ms": 1872,
     "stop_reason": "passed",
     "strategy": "cascade",
     "text": "Done in 1.9s · 4 stages · 4 model calls · 4 free used · answer passed its check"
    }
   ]
  }
 ]
};
