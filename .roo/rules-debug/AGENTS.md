# AGENTS.md

This file provides Debug mode specific guidance for agents.

## Project Debug Rules (Non-Obvious Only)

- **Workflow Interruption:** If a state transition fails without a clear error, check the related model's action methods (e.g., `action_repair_done`, `action_atelier_start`) for **mandatory wizard calls** (e.g., `repair.warn.quote.wizard`, `repair.start.wizard`) which can halt execution until user interaction.
- **Concurrency Errors:** Database deadlock or contention errors during critical state changes (Done, Delivered, Start) are likely due to the **raw SQL `FOR UPDATE NOWAIT`** row-level locking mechanism in the core model.
- **Permission Debugging:** If updates to fields like `tracking_token`, `invoice_ids`, etc., fail silently or are unexpectedly reverted, remember the **custom `write()` override security** in `repair.order` (L372-390) may be stripping the values.
- **Configuration-based Logic:** The SAR warranty period is a critical configuration value retrieved from `ir.config_parameter` using the key `'repair_custom.sar_warranty_months'`. Check this parameter when debugging warranty or history issues.