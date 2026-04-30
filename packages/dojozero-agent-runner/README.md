# dojozero-agent-runner

Single-image runner for externalized DojoZero agents.

One image, parameterized by env vars (`DOJOZERO_PERSONA`, `DOJOZERO_LLM`,
`DOJOZERO_TRIAL_ID`, `DOJOZERO_GATEWAY_URL`, plus the AIP identity vars
read by `aip-identity-sdk`). The same image runs any persona × LLM
combination — N processes, one image.

See [`docs/external_agent_migration.md`](../../docs/external_agent_migration.md)
in the parent repo for the design.
