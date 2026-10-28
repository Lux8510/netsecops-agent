# v0.3 real-acceptance FAIL regression inputs

These are unmodified, project-local copies of the two failed DeepSeek runs and
their saved acceptance judgments from 2026-10-14. Tests consume these files
without reading historical `agent-runs/` or an API key. They include the model's
original response, its actual API `tool_calls`, returned evidence, guardrail
output, and the original failed judgment. The source records remain intact.

- `normal-trace.json`: `agent-runs/live-normal-20261014T074442Z-bffb7c92/trace.json`;
  original SHA256 `40955174c61fe1b2183624d198af652e8cc8f676a56593f36e5c0e3f70276df5`.
- `normal-acceptance.json`: `agent-runs/live-acceptance-20261014T074430Z-ab8485ed/acceptance.json`.
- `failed-collection-trace.json`: `agent-runs/v03-real-acceptance-20261014T080022Z-aac7786c/case-07/agent-trace.json`;
  original SHA256 `24ab804da06899a3030101ec1ef770702e02edc294074a6399f044d32fd06ae2`.
- `failed-collection-judge.json`: the adjacent `case-07/judge.json`.

These are regression samples, not new model results. No authentication header,
environment dump, or API key belongs in this folder.
