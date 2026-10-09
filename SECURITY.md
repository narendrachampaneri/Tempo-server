# Security policy

## Reporting a vulnerability

Please report security problems privately, not in a public issue:

- use GitHub's **Report a vulnerability** button (Security → Advisories) on this repository.

Include what you found, how to reproduce it, and what an attacker could do. We aim to reply
within 7 days and to fix confirmed problems within 30 days, and we will credit you if you wish.

## In scope

- The key vault: users' provider keys are encrypted at rest and must never be returned, printed
  or logged (they are shown only as a one-way fingerprint).
- Authentication and access: Tempo API keys, the admin key, one user reaching another user's
  keys, questions or usage.
- The API and web page: injection, request forgery, anything that makes the server call an
  address a caller chose.
- Prompt injection between stages: a model's output steering a later stage (outputs are fenced
  as data).
- Provider rules: anything that would make Tempo use an owner-only provider for other users,
  use a server key for a provider that takes only users' own keys, or send a private request to
  a model flagged as logging or training on prompts.
- The code sandbox that runs code and maths answers ([docs/SANDBOX.md](docs/SANDBOX.md)): a
  program reaching the network, files outside its folder or the host, or running past its
  time, memory, output or disk limits.

## Out of scope

- Problems in the providers' own services (report those to the provider).
- The quality of model answers, unless it bypasses a check Tempo claims to make.

## Handling keys

Never include a real key in a report. If you believe a key was exposed, say where, and the
owner will rotate it.
