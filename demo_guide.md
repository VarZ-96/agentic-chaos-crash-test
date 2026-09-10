# Agentic Chaos Engine: Phase 1 & 2 Demo Guide

Here is your complete guide on what to say and how to demonstrate the project to your professor tomorrow. 

## 1. What to Say: Project Status
Tell your professor that **Phases 1 and 2 are 100% complete**. You have successfully built the core proxy and all the chaos injection strategies. Specifically, highlight these achievements:
- **Asynchronous Reverse Proxy (Phase 1):** Built a high-performance, non-blocking proxy using FastAPI and `httpx` to intercept AI agent tool calls.
- **Trace Logging Engine (Phase 1):** Implemented an asynchronous, append-only SQLite database logger that records every request/response without blocking the main event loop. Sensitive credentials are automatically redacted.
- **Chaos Mutation Engines (Phase 2):** Implemented all 5 chaos strategies using the Strategy Design Pattern.

---

## 2. The Live Demonstration
Because there is no UI, you will demonstrate the engine using your terminal. 

### Step 0: Ensure Config is Reloaded
You have increased the injection rates in your `chaos.yaml` so the faults trigger reliably for the demo. Ensure the proxy has loaded this config:
```powershell
# Using PowerShell
Invoke-RestMethod -Uri http://127.0.0.1:8080/v1/chaos/reload -Method Put

# OR Using curl
curl.exe -X PUT http://127.0.0.1:8080/v1/chaos/reload
```

### Demo 1: Network Chaos (Latency & HTTP Errors)
Show how the engine injects 503, 500, and 429 errors randomly.
**Run this a few times:**
```powershell
curl.exe -X GET http://127.0.0.1:8080/v1/some-endpoint -i
```
**What to say:** "Depending on our stochastic probabilities, this request either passes through, gets artificially delayed, or instantly fails with an HTTP error like a 429 Too Many Requests."

### Demo 2: Schema Mutilation (JSON Corruption)
Show how the engine can intercept a perfect JSON response from an upstream server and corrupt the schema. We use dynamic upstream routing to point it at a mock API (`jsonplaceholder`).
**Run this command:**
```powershell
curl.exe -s -H "X-Chaos-Upstream: https://jsonplaceholder.typicode.com" http://127.0.0.1:8080/v1/users/1
```
**What to say:** "Here, the proxy fetches perfect user data from a mock upstream server. However, the schema mutilation strategy intercepts the response and randomly deletes fields or injects nulls before the AI agent receives it."

### Demo 3: Semantic Mirage (Logical Poisoning)
Show how the engine poisons valid data payloads logically, returning an HTTP 200 OK but with paradoxical data (like manipulating roles or timestamps) to see if the AI trusts it blindly.
**Run this command:**
```powershell
curl.exe -s -H "X-Chaos-Upstream: https://jsonplaceholder.typicode.com" http://127.0.0.1:8080/v1/users/2
```
**What to say:** "Just like schema mutilation, this intercepts a valid response but instead injects logical paradoxes—like making `last_updated` happen before `created_at`. We return an HTTP 200, testing if the AI blindly trusts the data or actively validates it."

### Demo 4: RBAC Jailbreaker (Security & Redaction)
Show how the proxy acts as a security layer, blocking prohibited AI payloads and redacting sensitive data.
**Run this command to trigger a block:**
```powershell
curl.exe -X POST http://127.0.0.1:8080/v1/some-endpoint -H "Content-Type: application/json" -d "{\"query\": \"UPDATE users SET user_role = 'admin'\"}"
```
**What to say:** "The proxy normalizes incoming payloads and checks against a list of prohibited operations. Here, it caught a simulated privilege escalation attack and blocked it entirely."

### Demo 5: Token Trap (Cyclic Redirects)
Show how the engine creates infinite loops to trap the agent. Look at your `chaos.yaml` under `trap_paths`, which specifies `/tools/service_a`.
**Run this command:**
```powershell
curl.exe -X GET http://127.0.0.1:8080/v1/tools/service_a -i
```
**What to say:** "If an agent queries this specific path, the proxy returns a 307 Temporary Redirect pointing back to itself, essentially trapping the agent in an infinite loop to drain its token budget."

### Final Step: Trace Logging
Finally, show that every single one of those requests was recorded asynchronously.
**Run this command:**
```powershell
curl.exe -s http://127.0.0.1:8080/v1/traces
```
**What to say:** "Every interaction we just did was logged asynchronously to a SQLite database. Sensitive headers like Authorization were redacted on the fly, and this data will be used in Phase 3 to generate the Execution DAG."
