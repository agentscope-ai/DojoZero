# BettingAgent Context Management

What actually goes into the LLM model context window.

## Normal Operation (< 20 messages)

```mermaid
flowchart TB
    subgraph Context["LLM Context Window"]
        SYS["System Prompt"]
        MSG1["User: Event 1"]
        MSG2["Assistant: Response 1"]
        MSG3["User: Event 2"]
        MSG4["Assistant: Response 2"]
        MSG5["..."]
        MSGN["User: Event N (≤200 chars)"]
    end

    style Context fill:#f3e5f5
```

Events accumulate in memory as user/assistant message pairs until threshold is reached.

---

## After Compression (≥ 20 messages)

```mermaid
flowchart TB
    subgraph Context["LLM Context Window"]
        SYS["System Prompt"]
        subgraph Compressed["Single User Message"]
            HIST["[Historical Event Summary]<br/>Event 1...Event 10<br/>...(N events omitted)...<br/>Event N-9...Event N"]
            BETS["[Your Betting History]<br/>Last 20 bets with outcomes"]
            NEW["[New Events]<br/>Current event(s)"]
        end
    end

    style Context fill:#e8f5e9
    style Compressed fill:#c8e6c9
```

Memory is cleared. Compressed summary + new events sent as single user message.

---

## Compression Thresholds

| What | Limit |
|------|-------|
| Messages before compression | 20 |
| Events in sparse summary | First 10 + Last 10 |
| Max chars per event | 200 |
| Bet history included | Last 20 bets |
