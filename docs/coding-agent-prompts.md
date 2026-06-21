# Coding Agent Prompts

## Build Prompt

```text
You are building ONLY the non-UiPath support assets for a hackathon MVP called "Agentic ERP Modernization Layer".

Critical product boundary:
- UiPath is the main orchestration, governance, case lifecycle, approval, RPA, validation, and API-mode execution layer.
- Do NOT implement the main workflow orchestration in Python.
- Do NOT describe the Python code as the orchestrator.
- Do NOT claim Codex or any coding agent is triggered at runtime by UiPath unless explicitly implemented.
- The Python assets are only callable support services for UiPath.

Goal:
Create a local demo backend that can be called by UiPath running on Windows, while this repository runs inside WSL2 Ubuntu.
```

This prompt was used to keep the generated code and documentation inside the product boundary: UiPath governs the case lifecycle and the Python services provide local demo support capabilities.
