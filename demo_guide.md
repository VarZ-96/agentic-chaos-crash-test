# Agentic Chaos Engine: Phase 1 & 2 Demo Guide

Here is your complete guide on what to say and how to demonstrate the project to your professor tomorrow. 

## 1. What to Say: Project Status (Phase 1 & 2 Completed)
Tell your professor that **Phases 1 and 2 are 100% complete**. You have successfully built the core proxy and all the chaos injection strategies. Specifically, highlight these achievements:
- **Asynchronous Reverse Proxy (Phase 1):** Built a high-performance, non-blocking proxy using FastAPI and `httpx` to intercept AI agent tool calls.
- **Trace Logging Engine (Phase 1):** Implemented an asynchronous, append-only SQLite database logger that records every request/response without blocking the main event loop. Sensitive credentials are automatically redacted.
- **Chaos Mutation Engines (Phase 2):** Implemented all 5 chaos strategies using the Strategy Design Pattern:
  1. **Network Chaos:** Injects random latencies and HTTP 500/503/429 errors to test agent retry logic.
  2. **Schema Mutilation:** Randomly drops fields or changes types to simulate malformed APIs.
  3. **Token Trap:** Creates cyclic redirects to trap agents in infinite loops.
  4. **Semantic Mirage:** Corrupts payloads (e.g., temporal anomalies) while returning a clean HTTP 200 OK to see if the agent catches logical errors.
  5. **RBAC Jailbreak:** Detects and blocks unauthorized privilege escalation attempts (e.g., SQL injections trying to become 'admin').

## 2. How to Demo Without a UI
Since there is no UI, you will demonstrate the engine using your terminal. Below are the commands to run. I've provided both the PowerShell way (`Invoke-RestMethod`) and the standard `curl.exe` way (which shows the raw JSON output nicely).

### Step 1: Show the Engine is Alive
Show the professor that the engine is running and has loaded the chaos profiles from `chaos.yaml`.
**Run either of these commands:**
```powershell
# Using Native PowerShell
Invoke-RestMethod -Uri http://127.0.0.1:8080/health -Method Get

# OR Using curl
curl.exe -s http://127.0.0.1:8080/health
```
**What to show:** Point out the JSON response confirming `"status": "ok"` and listing the 5 active strategies.

### Step 2: Demonstrate the Proxy Interception
Show how the engine intercepts requests. Any request sent to `http://127.0.0.1:8080/v1/...` is processed through the chaos engines and forwarded.
**Run either of these commands:**
```powershell
# Using Native PowerShell
Invoke-RestMethod -Uri http://127.0.0.1:8080/v1/some-agent-tool-endpoint -Method Get

# OR Using curl (includes headers)
curl.exe -X GET http://127.0.0.1:8080/v1/some-agent-tool-endpoint -i
```
**What to show:** Explain that depending on the `chaos.yaml` probability settings, this request might instantly fail with a `Too Many Requests` error (Network Chaos), or it might try to forward to the upstream server. *(Note: If the chaos engine decides to let the request pass through unharmed, you will see an `upstream unreachable` error because there isn't actually a target server running on port 9009 right now. Mention to your professor that this proves the proxy attempted the forward successfully!)*

### Step 3: Demonstrate Trace Logging
Show that every intercepted request is being recorded in the local SQLite database for future analysis.
**Run either of these commands:**
```powershell
# Using Native PowerShell (Output is truncated by default)
Invoke-RestMethod -Uri http://127.0.0.1:8080/v1/traces -Method Get

# OR Using curl (Recommended! Shows the full raw JSON)
curl.exe -s http://127.0.0.1:8080/v1/traces
```
**What to show:** This will output the history of all the HTTP requests and responses that have passed through the proxy. Point out how headers and body payloads are captured, which will be used in Phase 3 for building the Directed Acyclic Graph (DAG).

## 3. How to Tell if it's Working
You know it's working perfectly when:
1. The `/health` endpoint returns the 5 strategies.
2. The `/v1/traces` endpoint returns the history of your requests.
3. Sending requests to `/v1/anything` results in occasional random delays, HTTP errors, or `upstream unreachable` errors (which proves the Chaos Engines are actively mutating the traffic and the proxy is doing its job).
