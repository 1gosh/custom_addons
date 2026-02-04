# AGENTS.md

This file provides Architect mode specific guidance for agents.

## Project Architecture Rules (Non-Obvious Only)

- **Concurrency Model:** The core `repair.order` model utilizes a **non-standard, pessimistic locking strategy** using raw SQL `FOR UPDATE NOWAIT` in state-changing methods to ensure transactional integrity. New critical workflows must adopt this same approach for concurrency.
- **Security Bypass/Override:** Field-level security for critical fields (`tracking_token`, `invoice_ids`, etc.) is managed via an explicit **override of the ORM `write()` method**, rather than standard Odoo access rights, which creates a non-obvious coupling between field names and the custom security logic.
- **Workflow Interruption Points:** The business logic heavily relies on **wizard models (`repair.start.wizard`, `repair.warn.quote.wizard`)** to interrupt complex state transitions (`action_atelier_start`, `action_repair_done`) for validation or user input. New workflows should integrate this pattern instead of using direct state manipulation.
- **Inter-Module Dependency:** A strict dependency exists: `repair_custom` depends on `repair_devices`. Any architectural changes must maintain the integrity of `repair.device.unit` and `repair.device.category` used throughout `repair_custom`.
- **Configurability:** Look for configuration parameters in `ir.config_parameter`, such as `'repair_custom.sar_warranty_months'`, before hard-coding values for business logic.