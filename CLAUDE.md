# Repair Management System (Odoo Modules)

## What This Is

French-language Odoo ERP system for managing Hi-Fi equipment repairs. Two interdependent modules:
- `repair_devices` - Device catalog, brands, categories, inventory units
- `repair_custom` - Repair orders, quotes, invoicing, batch processing, customer tracking

## Tech Stack

**Framework:** Odoo 17 (Python 3.10+, PostgreSQL)
**Frontend:** XML-based Odoo Views (Tree, Form, Kanban, Activity)
**Key Dependencies:** base, stock, sale_management, account, hr, mail, web
**Libraries:** dateutil, uuid, json, re

## Project Structure

```
custom_addons/
├── repair_custom/              # Main repair workflow (1292-line core model)
│   ├── models/repair.py        # 14 classes: Repair, RepairBatch, Dashboard, Tags, etc.
│   ├── wizard/                 # Pricing wizard (401 lines), bulk manager (103 lines)
│   ├── views/                  # XML UI definitions
│   ├── controllers/            # Public tracking routes
│   ├── security/               # 3-tier access control (technician/manager/admin)
│   ├── data/                   # Sequences, activities, dashboard fixtures
│   └── report/                 # PDF templates (tickets, invoices, labels)
│
└── repair_devices/             # Device catalog (386-line model)
    ├── models/repair_device.py # 5 classes: Device, Brand, Category, Variant, Unit
    ├── wizard/                 # Device reclassification utility
    └── views/                  # Device management UI
```

### Key Files by Purpose

**Core Models:**
- `repair_devices/models/repair_device.py:15` - Device catalog
- `repair_devices/models/repair_device.py:290` - Physical units
- `repair_custom/models/repair.py:12` - Repair order (main class)
- `repair_custom/models/repair.py:1071` - RepairBatch (deposit grouping)

**Business Logic:**
- `repair_custom/wizard/repair_pricing_wizard.py:1` - Quote/invoice generation
- `repair_custom/wizard/repair_manager.py:1` - Bulk updates
- `repair_custom/controllers/repair_tracking.py:12` - Public tracking

**State Machines:** See repair.py:54-66
- Repair: `draft` → `confirmed` → `under_repair` → `done`/`irreparable`/`cancel`
- Quote: `no_quote` → `draft` → `sent` → `accepted`/`refused`
- Delivery: `none` → `delivered`/`abandoned`

## How to Use (Odoo Commands)

**Python Environment:**
This project uses pyenv virtualenvwrapper. Activate the environment before running Odoo:
```bash
workon odoo_dev
```

**Install/Update modules:**
```bash
# From Odoo root directory (/Users/martin/Documents/odoo_dev/odoo)
./odoo-bin -c ../odoo.conf -u repair_custom,repair_devices

# Development mode (auto-reload)
./odoo-bin -c ../odoo.conf --dev=reload,xml

# Debug logging
./odoo-bin -c ../odoo.conf --log-level=debug
```

**Installation order:** Install `repair_devices` first (base dependency), then `repair_custom`.

## Key Workflows

**Standard Repair:**
1. Create repair order (draft) → attach device + customer
2. Confirm order → starts activity tracking
3. Start repair → state: "under_repair"
4. Complete work → validates quotes (repair.py:496)
5. Generate invoice via wizard (repair.py:589)
6. Mark as delivered

**Batch Processing:**
- Group multiple repairs under RepairBatch (repair.py:1071)
- Batch state auto-computes from children (repair.py:1095)

**Quote Management:**
- Set quote_required=True, use action_open_pricing_wizard() (repair.py:589)
- Template-based or manual pricing
- Validation happens at action_repair_done() (repair.py:496)

**Public Tracking:**
- Unique tracking_token per repair (repair.py:139)
- Access: `/my/repair/track/<token>` (repair_tracking.py:12)

## Data Model Core Relationships

```
# Device Catalog
RepairDeviceBrand (1) → (N) RepairDevice (M2M) ↔ (M) RepairDeviceVariant
RepairDevice (1) → (N) RepairDeviceUnit

# Repair Orders
Partner → (N) Repair ← (N) HrEmployee (technician)
RepairDeviceUnit (1) → (N) Repair
Repair (N) → (1) RepairBatch
Repair (M2M) ↔ (M) RepairTags
Repair → SaleOrder (quote), AccountMove (invoice)
```

Full field definitions: repair.py:12-200, repair_device.py:15-330

## Security Model

**Three user groups:**
- `group_repair_technician` - Read/write repairs, tag failures
- `group_repair_manager` - Create/modify repairs, manage templates
- `group_repair_admin` - Full system configuration

Field-level permissions: `repair_custom/security/ir.model.access.csv:1`

## Key Features

- **Smart device parsing:** Extracts brand from "Bang Olufsen Beogram 3000" → Brand: Bang & Olufsen, Model: Beogram 3000 (repair_device.py:130)
- **Warranty logic:** SAR calculates 3-month guarantee from last repair (repair.py:384)
- **Dashboard:** 7 configurable tiles with employee filtering (repair.py:1119)
- **Template-based invoicing:** Weighted line distribution (repair_pricing_wizard.py:1)
- **Mail integration:** Activity tracking, threading, notifications (repair.py:35)
- **Hierarchical categories:** Unlimited depth with full path search (repair_device.py:190)

## Additional Documentation

- `.claude/docs/architectural_patterns.md` - Design patterns, conventions, coding standards, integration patterns, and performance optimizations observed in this codebase

## Development Notes

**Recent commits:**
- "Recherches + Gestionnaire appareils" (search improvements + device manager)
- "Notifs" (notification system)
- Bug fixes, UI improvements, logic optimization

**Current changes:**
- Added repair_manager.py wizard
- Modified repair.py (32 lines)
- Deleted test files (tests/ directory removed)
- New wizard views and data files

**Language:** All UI, labels, and data fixtures in French.

## Adding New Features or Fixing Bugs

**important**: When you work on a new feature or bug, create a git branch first.
Then work on changes in that branch for the remaineder of the session.
