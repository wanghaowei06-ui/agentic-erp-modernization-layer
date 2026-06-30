# Evidence Pack

Use the evidence pack to collect local JSON proof for the current demo.

Generate evidence:

```bash
./scripts/collect-demo-evidence.sh
```

Output directory:

```text
docs/evidence/
```

## Captured Evidence

The evidence pack captures:

1. Health checks for ports `8001`, `8002`, `8003`, and `8004`.
2. `GET /company-context` enterprise context snapshot.
3. Five `POST /case-intake/route` responses for PO-1000 through PO-1004.
4. Deterministic proof for PO-1000 with no LLM invocation.
5. Agent-context proof for PO-1001 with company context, `llm_validation_proof`, and recommended ERP action.
6. Approval task creation proof with business remarks and agent reasoning.
7. Proposal inbox proof with API modernization and XAML workflow proposals.
8. Simulation state and demo evidence snapshot.
9. A manifest listing the current evidence files and safety boundaries.

## Interpretation

These files prove the local support services expose the RPA-first ERP Worker
demo path. They do not imply automatic deployment, automatic trusted capability
registration, automatic proposal approval, or automatic Windows XAML
modification.
