# agentic-search-intel

## Design tradeoffs

### A missing required tool is a reported gap, not a patched one

The planner is prompted to include exactly one keyword-metrics call, but a
prompt is a request rather than a guarantee. Two obvious responses are both
wrong. Injecting the call in code would move planning out of the planner and
make the node quietly disagree with its own LLM. Failing the run is
disproportionate — a plan without keyword metrics still answers most of the
question.

So a required tool that is absent from the final plan is recorded as a
`CoverageGap` error and the run is marked `partial`. The report then states
which data was unavailable, so a zeroed opportunity score is explained rather
than silently wrong. The check runs *after* the `MAX_PLANNED_CALLS` cap: if
truncation is what dropped the metrics call, that is still a real gap and is
reported as one.

Gaps deliberately do not trigger the self-correction round. The model was
already told to include the call; asking a second time costs another API call
and rarely changes the answer. Validation errors get a retry because the model
can act on the validator's message; a coverage gap is a decision it already
made.

## Testing approach

### Read the failure before assuming the code is broken

`redact()` replaces a sensitive block *wholesale* — hit `auth`, and the whole
sub-dict becomes `***redacted***` rather than being walked into. A test asserted
the opposite (`out["payload"][0]["auth"]["api_key"] == REDACTED`) and failed.
The code was right: wholesale replacement covers secrets nested under `auth`
that nobody has thought to add to `SENSITIVE_KEYS` yet. The test was fixed, not
the redactor — split into one case for the wholesale block and one for
recursion through safe containers.

### The test harness must run the code you ship

`structlog.testing.capture_logs()` swaps out the *entire* processor chain for a
single capturing processor. `merge_contextvars` and the redaction processor
never run, so the `run_id` that `run_context()` binds is simply absent from the
captured lines — and the assertion `"run_id" not in logs[-1]` passed for the
wrong reason, proving nothing.

`build_processors(final_processor)` now returns the chain with a swappable last
step. Production ends it with a JSON renderer; the `captured_logs` fixture in
`tests/conftest.py` ends it with a capture-and-drop processor. Same contextvars,
same redaction, same timestamps — tests see exactly the dict production would
render. `cache_logger_on_first_use=False` in the fixture is load-bearing: with
caching on, a logger bound by an earlier test keeps its old chain and the
fixture has no effect.
