# AGENTS.md

This file provides Code mode specific guidance for agents.

## Project Coding Rules (Non-Obvious Only)

- **Do not manually change state fields** (`state`, `quote_state`, `delivery_state`) in new code paths; they must be changed by calling the appropriate action methods (e.g., `action_repair_done`, `action_atelier_start`) to enforce custom validation and security wizards.
- **Critical transactions** (e.g., state changes, delivery) require **explicit row-level locking** using raw SQL `FOR UPDATE NOWAIT` to prevent concurrent writes, a non-standard Odoo practice. See L413 in [`repair_custom/models/repair_order.py`](repair_custom/models/repair_order.py).
- **Custom Write Security:** The `repair.order` model's `write()` method is overridden to strip protected fields for non-admin/manager users (L372-390). Any new field protection logic should be integrated into this existing override.
- **Template Content Format:** When inserting new content into `internal_notes` from any source, use the existing separator `\n\n---\n\n` for clear delineation, as seen in `_onchange_notes_template_id`.
- **Localization:** All new user-facing strings (labels, messages, field help) must be wrapped in `_()` and written in **French**.