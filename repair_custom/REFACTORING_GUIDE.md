# Repair.py Refactoring Guide

## Status: IN PROGRESS

The repair.py god class (1,293 lines, 14 classes) has been split into focused modules.

## New File Structure

```
models/
├── __init__.py (TO UPDATE)
├── repair_order.py ✅ CREATED (main Repair class - 800 lines)
├── repair_batch.py (TO CREATE)
├── repair_tags.py (TO CREATE)
├── repair_location.py (TO CREATE)
├── repair_notes.py (TO CREATE)
├── repair_dashboard.py (TO CREATE)
├── repair_extensions.py (TO CREATE - model extensions)

wizards/
├── __init__.py (TO UPDATE)
├── repair_start_wizard.py (TO CREATE)
├── repair_pricing_wizard.py ✅ EXISTS
├── repair_manager.py ✅ EXISTS
```

## Migration Steps

### Step 1: Create Remaining Model Files

Extract classes from old repair.py:

1. **repair_batch.py** (lines 1190-1236)
   - RepairBatch class

2. **repair_tags.py** (lines 972-1029)
   - RepairTags class

3. **repair_location.py** (lines 957-971)
   - RepairPickupLocation class

4. **repair_notes.py** (lines 1108-1189)
   - RepairNotesTemplate
   - RepairTemplateSelector  
   - RepairTemplateLine

5. **repair_dashboard.py** (lines 1238-1436)
   - AtelierDashboardTile class

6. **repair_extensions.py** (lines 1030-1107, 1069-1074, 1075-1107, 1437-1445)
   - RepairDeviceUnit (inheritance)
   - AccountMove (inheritance)
   - SaleOrder (inheritance)
   - HrEmployee (inheritance)

### Step 2: Create Wizard Files

1. **wizards/repair_start_wizard.py** (lines 921-956)
   - RepairWarnQuoteWizard
   - RepairStartWizard

### Step 3: Update __init__.py Files

**models/__init__.py:**
```python
from . import repair_order
from . import repair_batch
from . import repair_tags
from . import repair_location
from . import repair_notes
from . import repair_dashboard
from . import repair_extensions
```

**wizards/__init__.py:**
```python
from . import repair_pricing_wizard
from . import repair_manager
from . import repair_start_wizard
```

### Step 4: Backup and Remove Old File

```bash
# Backup
cp models/repair.py models/repair.py.backup

# Remove old file
rm models/repair.py
```

### Step 5: Update Odoo Module

```bash
./odoo-bin -c ./config.conf -u repair_custom
```

## Benefits

✅ **Maintainability**: Each class in its own file
✅ **Testability**: Easier to test individual components
✅ **Team Collaboration**: Reduced merge conflicts
✅ **Code Navigation**: Easier to find and understand code
✅ **Single Responsibility**: Each file has one clear purpose

## File Size Comparison

| Before | After |
|--------|-------|
| 1 file: 1,293 lines | 7 model files: ~150-200 lines each |
| 14 classes mixed | 1-3 classes per file |

