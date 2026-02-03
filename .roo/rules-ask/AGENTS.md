# AGENTS.md

This file provides Ask mode specific guidance for agents.

## Project Documentation Rules (Non-Obvious Only)

- **Primary Language:** The system's primary development and operational language is **French**. Always reference field names, labels, and module names using their French context.
- **Core Model:** The main model is `repair.order` but its implementation spans multiple files in `repair_custom/models/`, including [`repair_custom/models/repair_order.py`](repair_custom/models/repair_order.py), making a single-file analysis incomplete.
- **Custom Configuration:** The configurable SAR warranty period is accessed via the `ir.config_parameter` key `'repair_custom.sar_warranty_months'`. This is a hidden setting not visible through standard field definitions.
- **Testing Status:** The project currently **does not have a `tests/` directory** and no visible unit tests. Do not assume standard Odoo testing practices are in place.