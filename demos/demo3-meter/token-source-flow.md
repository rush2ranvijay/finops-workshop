# Token Source Flow

This project does **not** read token counts from the local `chatSessions` JSON file directly.

The important point is:
- The local `chatSessions` file stores the chat/session structure.
- Token fields such as `promptTokens` and `completionTokens` come from the Copilot backend response at runtime, if they are present.
- The parser reads those fields when they exist and then normalizes them into project token totals.

## Simple Architecture

```text
User message
   |
   v
VS Code chat UI
   |
   v
Copilot backend API response
   |
   v
Optional runtime metadata
  - promptTokens
  - completionTokens
  - cacheReadTokens
  - cacheCreationTokens
   |
   v
Local chatSessions JSON file
   |
   v
parsing_logic.py
   |
   v
extract_request_token_counts()
   |
   v
token_logic.py
   |
   v
Normalized token totals
  - input
  - output
  - cache_read
  - cache_write
   |
   v
finops_core.py
   |
   v
Session cost and usage summary
```

## What Happens In This Project

1. `parsing_logic.py` opens a chat session record and looks at each request.
2. If `result.metadata` contains token fields, the parser pulls them out.
3. `token_logic.py` maps those raw names into project fields:
   - `promptTokens` -> `input`
   - `completionTokens` -> `output`
   - `cacheReadTokens` -> `cache_read`
   - `cacheCreationTokens` -> `cache_write`
4. `finops_core.py` aggregates the normalized values into session totals.
5. `usd_from_tokens()` converts the totals into cost using the model rate table.

## Important Note

For the sample file we inspected, `result.metadata` contained session and tool metadata, but not token counts. That means the token values are coming from runtime API metadata only when present, not from every saved local chat session record.
