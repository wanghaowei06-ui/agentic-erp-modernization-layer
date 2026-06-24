# Evidence Pack

Use the evidence pack to collect local JSON proof for the demo.

Generate evidence:

```bash
./scripts/collect-demo-evidence.sh
```

Output directory:

```text
docs/evidence/
```

## Evidence Checklist

The evidence pack captures:

1. Health checks for ports `8001`, `8002`, `8003`, and `8004`.
2. PO-1001 triage response with `detected_exception_type=budget_exceeded`.
3. Automation Memory timeline with `TRIAGE_COMPLETED`.
4. Mock ERP RPA write-back with `RPA_WRITEBACK_COMPLETED`.
5. validation-suite result with `VALIDATION_COMPLETED`.
6. generated API response with `API_EXECUTION_COMPLETED`.
7. PO-1003 capability gap with `CAPABILITY_GAP_RECORDED`.
8. `GET /memory/cases/CASE-001/timeline`.
9. `GET /memory/capabilities`.
10. `GET /memory/gaps`.

## Interpretation

These files prove the Hard MVP support services ran locally and wrote governed Automation Memory records. They are not production audit records and do not imply automated deployment or automated XAML generation.
