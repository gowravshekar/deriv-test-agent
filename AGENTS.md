# Coding Agent Guide

Cursor project instructions for this Google ADK agent. Operational constraints live in `.cursor/rules/`. Implement in `app/`; run `uv` and `agents-cli` in the terminal. Do not invent extra files.

## Prerequisites

Install the CLI (one-time):

```bash
uv tool install google-agents-cli
```

Run `agents-cli install` before Python commands.

## Development Phases

### Phase 1: Understand Requirements

Before writing any code, understand the project's requirements, constraints, and success criteria.

### Phase 2: Build and Implement

Implement agent logic in `app/`. Use `agents-cli playground` for interactive testing. Iterate based on user feedback.

### Phase 3: The Evaluation Loop (Main Iteration Phase)

See `.cursor/rules/adk-eval.mdc`. Start with 1–2 eval cases, run `agents-cli eval run`, and iterate until satisfied.

### Phase 4: Pre-Deployment Tests

Run `uv run pytest tests/unit tests/integration`. Fix issues until all tests pass.

### Phase 5–6: Deploy

See `.cursor/rules/adk-deploy.mdc`. Deploy and infra changes require explicit human approval.

## Development Commands

| Command | Purpose |
|---------|---------|
| `agents-cli playground` | Interactive local testing |
| `uv run pytest tests/unit tests/integration` | Run unit and integration tests |
| `agents-cli eval dataset synthesize` | Synthesize multi-turn eval scenarios for your agent |
| `agents-cli eval run` | Run the agent over the eval dataset and grade the traces |
| `agents-cli eval generate` / `agents-cli eval grade` | Decoupled form: produce traces, then grade them |
| `agents-cli eval compare` | Compare two grade-results files (regression check) |
| `agents-cli eval analyze` | Cluster failure modes from grade results |
| `agents-cli eval metric list` | List built-in metrics available in the SDK |
| `agents-cli eval optimize` | Auto-tune agent prompts using eval data |
| `agents-cli lint` | Check code quality |
| `uv run pre-commit install` | Install pre-commit and commit-msg hooks |
| `uv run cz commit` | Create a conventional commit (Commitizen) |
| `agents-cli infra single-project` | Set up project infrastructure (Terraform) |
| `agents-cli deploy` | Deploy to dev |
| `agents-cli scaffold enhance` | Add deployment target or CI/CD to project |
| `agents-cli scaffold upgrade` | Upgrade project to latest version |
