## What

<!-- What this change does, in a sentence or two. -->

## Why

<!-- The problem it solves, or the issue it closes (e.g. "Closes #12"). -->

## Checklist

- [ ] `uv run ruff check .` and `uv run ruff format --check .` pass
- [ ] `uv run pytest` passes, with tests for new behaviour
- [ ] Any new test data is synthetic: no real statements, transactions, names or keys
- [ ] Money stays in integer pence, and figures come from the engine
- [ ] README, the agent skill or TODOS.md updated if behaviour changed
