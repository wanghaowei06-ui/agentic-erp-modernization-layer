# Capability Evolution Loop

PO-1003 demonstrates a capability gap path.

Input:

- `case_id=CASE-003`
- `po_id=PO-1003`
- `detected_exception_type=inventory_shortage`
- `required_business_action=request_inventory_review`

The reasoning-agent returns:

- `detected_exception_type=inventory_shortage`
- `next_action=create_capability_gap_proposal`
- `next_stage=CAPABILITY_GAP_DETECTED`

The validation-suite support endpoint records the gap:

`POST http://localhost:8004/capability-gaps/inventory-shortage`

Output file:

`memory/data/capability_gap_CASE-003.json`

The recorded gap recommends `HandleInventoryShortageReview.xaml` as an implementation candidate. This is a proposal record only. The system does not automatically generate, deploy, approve, or run a new XAML workflow at runtime.

## Intended Loop

1. UiPath routes the unsupported exception to a capability gap stage.
2. Structured Automation Memory records the missing workflow.
3. A human owner reviews the proposed capability.
4. A future authoring process may draft workflow or API implementation assets.
5. The new capability must pass validation and governance before trusted-tool registration.
