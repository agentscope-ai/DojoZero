# dojozero-agent-runner

Single-image runner for externalized DojoZero agents.

One image, parameterized by env vars (`DOJOZERO_PERSONA`, `DOJOZERO_LLM`,
`DOJOZERO_TRIAL_ID`, `DOJOZERO_GATEWAY_URL`, plus the AgentID identity
vars `AGENTID_AGENT_ID` / `AGENTID_AGENT_KID` / `AGENTID_PRIVATE_KEY`
read by `agent-id-client-sdk`). The same image runs any persona × LLM
combination — N processes, one image.

The Phase 1 canary runs **`DOJOZERO_PERSONA=degen` × `DOJOZERO_LLM=Qwen`**
(matches `qwen3-max` in `agents/llms/default.yaml`) against the gateway
configured for `pre.agent-id.live`. Required env for the canary: also
set `DOJOZERO_DASHSCOPE_API_KEY` for the model and the three `AGENTID_*`
vars from the agent's provisioned identity bundle.

See [`docs/external_agent_migration.md`](../../docs/external_agent_migration.md)
in the parent repo for the design.
