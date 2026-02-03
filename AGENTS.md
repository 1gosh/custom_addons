# AGENTS.md

This file provides guidance to agents when working with code in this repository.

## Critical Project Context

- **Language & Locale:** All user-facing strings (UI, labels, data fixtures, error messages) are in **French**. Use the `_()` Odoo translation function for all new user-facing strings.
- **Module Dependency/Order:** The `repair_devices` module must be installed/updated **before** `repair_custom`.
- **Core Model Complexity:** The main model is `repair.order` defined in multiple files, primarily [`repair_custom/models/repair_order.py`](repair_custom/models/repair_order.py), and is over 1200 lines, encompassing all primary business logic.

## Build/Test Commands

Since this is an Odoo project, all commands assume execution from the Odoo root directory, with a specific config file.

- **Installation/Update:** Update modules from the Odoo root.
  ```bash
  ./odoo-bin -c ../odoo.conf -u repair_custom,repair_devices
  ```
- **Development Mode (Auto-Reload):** Use the following flags for XML/Python auto-reload.
  ```bash
  ./odoo-bin -c ../odoo.conf --dev=reload,xml
  ```
- **Testing:** The standard `tests/` directory has been removed. There are currently no unit tests. If new tests are required, create them in the standard Odoo location, or discuss a test strategy with the developer first. A generic test command would be:
  ```bash
  ./odoo-bin -c ../odoo.conf --test-enable --test-file /path/to/test_file.py
  ```

## Non-Obvious Coding/Architectural Rules

- **Mandatory Workflow Wizards:** Do not manually change `state` or `quote_state` during complex workflows. State transitions must be initiated by calling the corresponding methods, which often launch security/validation wizards:
  - Starting repair: Use `action_atelier_start()` (may launch `repair.start.wizard`).
  - Completing repair: Use `action_repair_done()` (may launch `repair.warn.quote.wizard`).
- **Row-Level Locking:** Critical state transition methods (`action_repair_done`, `action_repair_delivered`, `action_atelier_start`) use **raw SQL `FOR UPDATE NOWAIT`** for concurrency control. Any new critical transaction must also implement this locking pattern.
- **Custom Security Layer:** The `repair.order` model's `write()` method implements a custom, non-standard ORM security layer (L372-390) that strips protected fields (`tracking_token`, `invoice_ids`, etc.) from non-admin/non-manager users. Be aware of this when modifying field updates.
- **Configurable Warranty:** The SAR (Service Après-Vente Retour) warranty duration (default: 3 months) is configurable via `ir.config_parameter` key `'repair_custom.sar_warranty_months'`. Use `_get_sar_warranty_months()` in the model to retrieve this value.
- **Note Template Utility:** When adding content via templates (e.g., `_onchange_notes_template_id`), new content is appended to `internal_notes` using the separator `\n\n---\n\n`.