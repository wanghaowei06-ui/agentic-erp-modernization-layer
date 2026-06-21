# API Readiness Scorecard

This deterministic demo score estimates whether the `request_purchase_order_approval` action is a good candidate for a validated API facade.

| Factor | Score |
| --- | ---: |
| frequency | 0.82 |
| business_value | 0.90 |
| field_stability | 0.88 |
| ui_fragility | 0.76 |
| final score | 86 |

The score supports the demo decision to validate this action as a trusted-tool candidate. It is not a production readiness assessment.

`risk_level` from the triage service controls approvals and validation strictness in the UiPath-governed case. It does not control the readiness score.
