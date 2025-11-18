Title: Integrate Azure Container Apps Agents With Azure AI Foundry for Orchestration
1. Introduction

Explain the rise of agentic applications and why developers increasingly deploy agents as microservices.
Introduce the goal of the post:

You’ll build an agent using Microsoft Agent Framework, deploy it on Azure Container Apps, instrument it with OpenTelemetry, and integrate it with Azure AI Foundry for orchestration using A2A.

Include a simple architecture diagram.

2. Why Azure Container Apps Is a Great Runtime for Agents

Serverless containers (no infra to manage)

Autoscaling based on HTTP, events, or GPU utilization

Cost-optimized for microservices

Perfect for multi-agent systems—each agent is its own service

Multi-cloud portability

Built-in support for secrets, scale rules, and networking

3. Why Azure AI Foundry for Orchestration

Orchestrate multi-step, multi-agent workflows

Agent-to-Agent (A2A) connections

Tool execution through hosted agents

Built-in evaluation workflows

End-to-end observability via Application Insights

Centralized lifecycle & governance

4. Architecture Overview

Show a diagram:

User → Foundry Orchestrator → A2A → ACA-hosted Agent  
                                   ↳ App Insights (OTEL traces)

5. Build an ACA Agent Using Microsoft Agent Framework
Key components:

Agent definition

Tools and tool routes

A2A connector binding

Request/response schemas

Show a minimal code snippet.

6. Add OpenTelemetry Instrumentation (MAF + ACA)

Explain:

setup_observability()

Exporters

How MAF emits gen_ai.* spans

What ACA logs look like

Why request spans are needed for Foundry dashboards

Mention the difference between:

MAF spans

HTTP request spans

A2A tool spans

7. Send Traces to Application Insights

Steps:

Add AI connection string in environment variables

Confirm traces with a Kusto query

Explain what spans you should expect (gen_ai., http., tool.*)

Optionally include:

how to test locally with OTEL_EXPORTER_OTLP_ENDPOINT

8. Deploy to Azure Container Apps

Use azd up to:

provision ACA environment

build and deploy the agent container

configure secrets (AI connection string)

9. Connect Your ACA Agent to Azure AI Foundry

Steps:

Create A2A connection

Register your tool in Foundry

Add connection ID to your agent config

Test locally using the Foundry A2A SDK

Validate in Foundry’s playground/orchestrator

This is where you show the “wow moment”:

Foundry orchestrator calling your ACA agent as a tool.

10. Validate End-to-End Observability

Walk them through verifying:

In ACA

logs

console

debug console

In App Insights

traces (gen_ai.*)

request spans

A2A spans

In Foundry

Orchestration dashboard

Tool execution logs

Evaluation results

11. Conclusion

Summarize benefits:

Real-world architecture for production agents

ACA + MAF = ideal agent host

Foundry = ideal orchestrator

App Insights = full observability

A2A = plug-and-play for multi-agent systems

Invite readers to try using:

GPUs,

private endpoints,

multi-agent patterns,

eval pipelines.