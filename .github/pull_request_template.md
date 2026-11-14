## What and why

<!-- One or two sentences. -->

## Checklist

- [ ] One feature per branch; no unrelated refactors
- [ ] Tests added or updated; no existing test weakened or deleted
- [ ] Anything that takes caller input has a test with hostile input (injection, oversize, malformed)
- [ ] No secrets, `.env` values, prompts or user data in code, logs, events or fixtures
- [ ] Prompts, API keys and tokens are not written to logs, spans or Sentry
- [ ] New dependency? Justified, and the lockfile is updated
- [ ] Schema change? `schema-approved` label requested
- [ ] Auth, routing-policy or provider changes? Called out below for careful review
- [ ] README still contains only capabilities and installation

## Risk and rollout

<!-- What could break, how you would notice, how to roll back. -->

## Evidence

<!-- Test output, screenshots, preview URL. -->
