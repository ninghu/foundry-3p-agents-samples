# 🧪 3P Agents Observability — Hands-on Lab

Welcome! In this lab you'll deploy a **LangGraph travel planner agent** to **Azure Container Apps**,
register it in **Microsoft Foundry**, and see full end-to-end traces — every LLM call, tool invocation,
and agent step — light up in the Foundry portal.

The agent uses **Azure OpenAI GPT 5.2** and automatically streams OpenTelemetry traces
to **Application Insights** via the Azure AI Tracer.

---

## ✅ Prerequisites

| Requirement | Details |
|---|---|
| **Microsoft Foundry account** | A Foundry project with **AI Gateway** and **Application Insights** connected. Create one at [ai.azure.com](https://ai.azure.com) if you don't have one. |
| **Model deployment** | Deploy **GPT 5.2** (or another chat model) from the Foundry model catalog. Note the deployment name (e.g. `gpt-52`), endpoint, and API key. |
| **Azure CLI** | `az` CLI installed and logged in (`az login`). The `containerapp` extension is required — install with `az extension add --name containerapp`. |
| **Python 3.12+** | For local development |

---

## 🔧 Step 1 — Configure Environment

```powershell
cd aca/travel_agent
copy .env.example .env
```

Open `.env` and fill in the values:

| Variable | What to set |
|---|---|
| `PROJECT_ENDPOINT` | Your Microsoft Foundry project endpoint (e.g. `https://your-resource.services.ai.azure.com/api/projects/your-project`) |
| `AZURE_OPENAI_API_KEY` | API key for the Azure OpenAI resource |
| `AZURE_OPENAI_DEPLOYMENT` | The deployment name for GPT 5.2 (e.g. `gpt-52`) |
| `APPLICATION_INSIGHTS_CONNECTION_STRING` | Connection string from the Application Insights resource **connected to your Foundry project** |
| `OTEL_AGENT_ID` | Agent ID for tracing — default `aca-travel-planner`. **This must match the *OpenTelemetry Agent ID* you will enter in Foundry (Step 4).** |

> The deployment variables (`AZURE_RESOURCE_GROUP`, `ACR_NAME`, etc.) are optional — `deploy.py` will prompt you for them if not set.

---

## 💻 Step 2 — Run Locally

```powershell
# Return to the repository root (Step 1 left you in aca/travel_agent)
cd ..\..

# Create virtual environment
python -m venv .venv
.venv\Scripts\Activate.ps1          # Linux/macOS: source .venv/bin/activate

# Install dependencies
pip install -r aca\travel_agent\requirements.txt

# Start the agent server
python -m uvicorn aca.travel_agent.main:app --host 0.0.0.0 --port 8080
```

### 🧪 Test locally

```powershell
# Health check
curl.exe http://localhost:8080/healthz

# Invoke the travel planner
curl.exe -s http://localhost:8080/invoke `
  -H "Content-Type: application/json" `
  -d '{"prompt": "Plan a 4-day honeymoon from Seattle to Paris in November."}'
```

You should receive a JSON response with a `result` field containing a full travel itinerary. 🎉

---

## 🚀 Step 3 — Deploy to Azure Container Apps

Run the deploy script — it will walk you through everything and create
all resources automatically:

```powershell
cd aca/travel_agent
python deploy.py
```

The script will:
1. Read settings from `.env` (or prompt you interactively for any that are missing)
2. Create a **Resource Group** if it doesn't exist
3. Create an **Azure Container Registry** if it doesn't exist
4. Build the container image in ACR
5. Create the **ACA environment** if it doesn't exist
6. Deploy the container app with your environment variables
7. Print the **Agent URL** at the end

Example output:
```
============================================================
  Deployment complete!
  Agent URL:      https://aca-travel-planner.livelyocean-abcd1234.eastus.azurecontainerapps.io
  Health check:   https://aca-travel-planner.livelyocean-abcd1234.eastus.azurecontainerapps.io/healthz
  Invoke endpoint: https://aca-travel-planner.livelyocean-abcd1234.eastus.azurecontainerapps.io/invoke
============================================================
```

Copy the **Agent URL** — you'll need it in the next step! 📋

### ✅ Verify the deployment

```powershell
curl.exe https://<your-aca-fqdn>/healthz
```

---

## 📝 Step 4 — Register Agent in Microsoft Foundry

1. Sign in to [Microsoft Foundry](https://ai.azure.com) (make sure the **New Foundry** toggle is on).

2. On the toolbar, select **Operate** → **Overview** → **Register agent**.

3. Fill in the registration form:

   | Field | Value |
   |---|---|
   | **Agent URL** | `https://<your-aca-fqdn>/` (the ACA URL from Step 3) |
   | **Protocol** | `HTTP` |
   | **OpenTelemetry Agent ID** | `aca-travel-planner` (must **exactly match** `OTEL_AGENT_ID` in your `.env`) |
   | **Agent name** | `ACA Travel Planner` (or any display name you like) |
   | **Project** | Select your project (must have **AI Gateway** + **Application Insights** configured) |

4. Save. The agent now appears under **Operate → Assets** (filter **Source: Custom** to find it quickly).

5. Copy the **AI Gateway Agent URL**:
   - Go to **Operate → Assets**, filter by **Source: Custom**.
   - Click on the **row** (not the agent name link) to open the details pane.
   - Under **Agent URL**, copy the AI Gateway URL. It looks like:
     ```
     https://<your-apim>.azure-api.net/<your-agent-name>
     ```
   - This is the proxied URL that Foundry manages — use it for traffic generation in Step 5.

> **Why must the IDs match?**
> The agent code emits spans with `gen_ai.agent.id = "aca-travel-planner"` to Application Insights.
> Foundry uses the *OpenTelemetry Agent ID* you set during registration to query
> Application Insights for traces with the matching `gen_ai.agent.id` attribute.
> If they don't match, Foundry won't find the traces.

### 📋 Pre-registration checklist

Before registering, confirm in **Operate → Admin**:

- [ ] Your Foundry resource has an **AI Gateway** configured (Admin → AI Gateway tab)
- [ ] Your project has **Application Insights** connected (select project → Connected resources → AppInsights)
- [ ] The Application Insights resource is the **same one** whose connection string is in your `.env`

---

## 🔥 Step 5 — Generate Traffic

Now let's send some requests! Use the included traffic generator to hit the **AI Gateway URL** you copied in Step 4:

```powershell
# Use the AI Gateway URL from Step 4 (not the direct ACA URL)
python aca/travel_agent/generate_traffic.py `
    --url https://<your-apim>.azure-api.net/<your-agent-name> `
    --count 1 `
    --delay 3
```

This ensures traffic flows through Foundry's API Management proxy, so Foundry can track requests and link them to your registered agent.

> You can also test against your local server: `python aca/travel_agent/generate_traffic.py --url http://localhost:8080`

The script picks from 10 diverse travel prompts (different cities, trip types,
traveller counts) and prints the response status for each request.

Wait **3-5 minutes** after the traffic run for traces to propagate to Application Insights. ⏳

---

## 🔍 Step 6 — View Traces in Foundry

This is the exciting part! 🎉

1. In [Microsoft Foundry](https://ai.azure.com), go to **Operate → Assets**.
2. Select your registered agent (**ACA Travel Planner**).
3. The **Traces** section shows one entry per HTTP call to the agent endpoint.
4. Select an entry to expand the trace tree. You should see:
   - `invoke_agent aca-travel-planner` — the top-level agent span
   - `invoke_agent coordinator` — extracts trip details
   - `invoke_agent flight_specialist` → `execute_tool search_flights`
   - `invoke_agent hotel_specialist` → `execute_tool search_hotels`
   - `invoke_agent activity_specialist` → `execute_tool search_activities`
   - `invoke_agent synthesizer` — combines everything into the final itinerary
   - Each node shows a `chat gpt-5.2` call with token count and duration

You did it! 🥳 You've deployed a multi-agent system, registered it in Foundry, and can now observe every step of your agent's reasoning.

You can also check the raw data in Application Insights directly:

```kusto
// Example KQL query in Application Insights → Logs
dependencies
| where customDimensions["gen_ai.agent.id"] == "aca-travel-planner"
| order by timestamp desc
| take 50
```

---

## 📊 Step 7 — Run Trace Evaluations

Run automated evaluations against collected traces using Azure AI built-in evaluators (**Intent Resolution** and **Task Adherence**).

### Prerequisites

1. Add these variables to your `.env` file:

   | Variable | What to set |
   | --- | --- |
   | `APPINSIGHTS_RESOURCE_ID` | Full Azure resource ID of your Application Insights instance (find it in Azure portal → Application Insights → **Properties** → **Resource ID**) |
   | `EVAL_DEPLOYMENT_NAME` | Model deployment name for the evaluator (e.g. `gpt-4.1`) |

   > `eval.py` also reuses `PROJECT_ENDPOINT` from Step 1 — no extra endpoint variable needed.

2. Grant the **Log Analytics Reader** role to your project's managed identity on the Application Insights resource:
   - Azure portal → Application Insights → **Access control (IAM)** → **Add role assignment**
   - Role: **Log Analytics Reader**
   - Assign to: the managed identity of your **Foundry AI Services resource**
   - Allow up to 10 minutes for propagation

### Run the evaluation

If you already generated traffic in Step 5:

```powershell
python -m aca.travel_agent.eval --lookback-hours 2
```

Or generate traffic and evaluate in one step:

```powershell
python -m aca.travel_agent.eval `
    --generate-traffic `
    --url https://<your-apim>.azure-api.net/<your-agent-name> `
    --count 3 `
    --wait-minutes 5
```

The script queries Application Insights for traces matching your `OTEL_AGENT_ID`, submits an evaluation run, and polls until completion. View results in the [Microsoft Foundry](https://ai.azure.com) portal under your project's evaluation section.

---

## 🔧 Troubleshooting

| Symptom | Fix |
|---|---|
| **No traces in Foundry** | 1) Verify `APPLICATION_INSIGHTS_CONNECTION_STRING` is correct. 2) Confirm `OTEL_AGENT_ID` matches the *OpenTelemetry Agent ID* in Foundry. 3) Wait 5 min for propagation. 4) If you added App Insights *after* registering, unregister and re-register the agent. |
| **Agent registration: no projects shown** | Your Foundry resource needs an AI Gateway. Go to **Operate → Admin → AI Gateway** and add one. |
| **Health check returns 500** | Check `PROJECT_ENDPOINT` and `AZURE_OPENAI_API_KEY` are set and valid. The agent validates these at startup. |
| **Timeout on /invoke** | The multi-agent workflow makes ~5 LLM calls sequentially. Increase the HTTP client timeout (default 120s) or check Azure OpenAI quotas. |

---

## 📁 Project Structure

```
aca/travel_agent/
├── agent.py              # LangGraph multi-agent travel planner
├── main.py               # FastAPI server (/healthz, /invoke, /)
├── __init__.py            # Package init
├── Dockerfile             # Container image definition
├── requirements.txt       # Python dependencies
├── .env.example           # Environment variable template
├── deploy.py              # Azure CLI deployment helper
├── generate_traffic.py    # Traffic generation script for testing
├── eval.py                # Trace-based evaluation script
└── README.md              # This file (lab instructions)
```
