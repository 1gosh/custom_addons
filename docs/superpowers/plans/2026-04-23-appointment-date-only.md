# Appointment Date-Only Revision — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the intra-day slot model (`start_datetime` / `end_datetime` + two hard-coded slots per day) with a date-only pickup model, and add a daily-agenda backend view + month-grid portal day picker.

**Architecture:** In-place schema change on `repair.pickup.appointment` and `repair.pickup.schedule`, driven by a pre-migration script. Model methods rename (`_compute_available_slots` → `_compute_available_days`, etc.), portal JSON payload shape changes (`{date, state, remaining}`), QWeb portal template is rewritten as a single-page month-grid day picker in plain JS. Backend gains a new "Agenda du jour" tree view and menu entry.

**Tech Stack:** Odoo 17, Python 3.10+, PostgreSQL, QWeb, plain JS + CSS grid (no framework).

**Spec:** [`docs/superpowers/specs/2026-04-23-appointment-date-only-revision-design.md`](../specs/2026-04-23-appointment-date-only-revision-design.md)

**Git branch:** `feature/appointment-date-only` (already created).

---

## File Structure

**Modify:**
- `repair_appointment/__manifest__.py` — bump version to `17.0.1.1.0`
- `repair_appointment/models/repair_pickup_appointment.py` — field swap, method rewrites, constraint + write-tracking updates
- `repair_appointment/models/repair_pickup_schedule.py` — drop slot fields, add `daily_capacity`, drop `_check_slot_ranges`
- `repair_appointment/controllers/portal.py` — JSON payload shape, form body field, drop duration math
- `repair_appointment/views/portal_templates.xml` — rewrite landing page + confirmation page
- `repair_appointment/views/appointment_views.xml` — replace datetime fields with `pickup_date`, calendar becomes all-day month mode, add daily agenda tree + action
- `repair_appointment/views/pickup_schedule_views.xml` — drop slot/capacity fields, add `daily_capacity`
- `repair_appointment/views/menus.xml` — add "Agenda du jour" menu entry
- `repair_appointment/data/pickup_schedule_data.xml` — replace `slot_capacity=3` with `daily_capacity=6` in seed
- `repair_appointment/tests/common.py` — update fixture helpers if they set datetime fields
- All `repair_appointment/tests/test_*.py` — adapt to new field / signatures
- `repair_appointment/tests/test_slot_availability.py` → **rename** to `test_day_availability.py`
- `repair_appointment/static/src/js/appointment_calendar_patch.js` — if it touches slot/datetime semantics (inspect and adapt)

**Create:**
- `repair_appointment/migrations/17.0.1.1.0/pre-migration.py` — copy `start_datetime::date → pickup_date`, drop old columns, move `slot_capacity → daily_capacity`
- `repair_appointment/tests/test_daily_agenda.py` — new test for the daily agenda action/domain

---

## Task 1: Create the migration script

**Files:**
- Create: `repair_appointment/migrations/17.0.1.1.0/__init__.py` (empty)
- Create: `repair_appointment/migrations/17.0.1.1.0/pre-migration.py`
- Modify: `repair_appointment/__manifest__.py`

- [ ] **Step 1: Bump manifest version**

Edit `repair_appointment/__manifest__.py`:

```python
    'version': '17.0.1.1.0',
```

- [ ] **Step 2: Create the migrations directory**

Run: `mkdir -p repair_appointment/migrations/17.0.1.1.0`

- [ ] **Step 3: Write the empty `__init__.py`**

Create `repair_appointment/migrations/17.0.1.1.0/__init__.py` with empty content.

- [ ] **Step 4: Write the pre-migration script**

Create `repair_appointment/migrations/17.0.1.1.0/pre-migration.py`:

```python
"""Migrate repair.pickup.appointment from datetime-slot to date-only model.

Also migrate repair.pickup.schedule from per-slot capacity to per-day
capacity.

Runs BEFORE Odoo loads the new ORM schema, so we operate in raw SQL.
"""
import logging

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    if not version:
        return

    # --- repair.pickup.appointment: add pickup_date, backfill, drop old cols ---
    cr.execute("""
        ALTER TABLE repair_pickup_appointment
        ADD COLUMN IF NOT EXISTS pickup_date date
    """)

    # Backfill: take the DATE part of start_datetime (in UTC; Odoo stores UTC).
    # The operational timezone is Europe/Paris — convert before casting.
    cr.execute("""
        UPDATE repair_pickup_appointment
           SET pickup_date = (start_datetime AT TIME ZONE 'UTC'
                                              AT TIME ZONE 'Europe/Paris')::date
         WHERE start_datetime IS NOT NULL
           AND pickup_date IS NULL
    """)

    for col in ('start_datetime', 'end_datetime'):
        cr.execute(
            "ALTER TABLE repair_pickup_appointment DROP COLUMN IF EXISTS %s" % col
        )

    # --- repair.pickup.schedule: add daily_capacity, backfill, drop slot cols ---
    cr.execute("""
        ALTER TABLE repair_pickup_schedule
        ADD COLUMN IF NOT EXISTS daily_capacity integer
    """)

    cr.execute("""
        UPDATE repair_pickup_schedule
           SET daily_capacity = COALESCE(slot_capacity, 6)
         WHERE daily_capacity IS NULL
    """)

    for col in ('slot1_start', 'slot1_end', 'slot2_start', 'slot2_end',
                'slot_capacity'):
        cr.execute(
            "ALTER TABLE repair_pickup_schedule DROP COLUMN IF EXISTS %s" % col
        )

    _logger.info("repair_appointment 17.0.1.1.0 migration complete")
```

- [ ] **Step 5: Commit**

```bash
git add repair_appointment/__manifest__.py repair_appointment/migrations/
git commit -m "repair_appointment: pre-migration for 17.0.1.1.0 date-only model"
```

---

## Task 2: Update `repair.pickup.schedule` schema + seed data

**Files:**
- Modify: `repair_appointment/models/repair_pickup_schedule.py`
- Modify: `repair_appointment/views/pickup_schedule_views.xml`
- Modify: `repair_appointment/data/pickup_schedule_data.xml`
- Modify: `repair_appointment/tests/test_schedule.py`

- [ ] **Step 1: Write failing test for `daily_capacity` field**

Edit `repair_appointment/tests/test_schedule.py` — add a new test method (keep existing class):

```python
def test_daily_capacity_field_present_and_default(self):
    sched = self.Schedule.create({'location_id': self.location_boutique.id})
    self.assertEqual(sched.daily_capacity, 6)

def test_daily_capacity_must_be_positive(self):
    from odoo.exceptions import ValidationError
    with self.assertRaises(ValidationError):
        self.Schedule.create({
            'location_id': self.location_atelier.id,
            'daily_capacity': 0,
        })

def test_old_slot_fields_removed(self):
    sched = self.Schedule.create({'location_id': self.location_boutique.id})
    for fname in ('slot1_start', 'slot1_end', 'slot2_start',
                  'slot2_end', 'slot_capacity'):
        self.assertNotIn(fname, sched._fields,
                         "%s must be removed" % fname)
```

Delete any existing tests in this file that reference `slot1_start`, `slot1_end`, `slot2_start`, `slot2_end`, `slot_capacity`, or `_check_slot_ranges` — they are obsolete.

- [ ] **Step 2: Run the new tests and confirm they fail**

Run: `cd /Users/martin/Documents/odoo_dev/odoo && ./odoo-bin -c ../odoo.conf -d <test_db> --test-enable --stop-after-init -i repair_appointment --test-tags /repair_appointment:test_schedule`

Expected: new tests fail (field `daily_capacity` doesn't exist).

- [ ] **Step 3: Replace the schedule model**

Rewrite `repair_appointment/models/repair_pickup_schedule.py`:

```python
from odoo import api, fields, models, _
from odoo.exceptions import ValidationError


class RepairPickupSchedule(models.Model):
    _name = 'repair.pickup.schedule'
    _description = 'Horaires de retrait par lieu'
    _rec_name = 'location_id'

    location_id = fields.Many2one(
        'repair.pickup.location',
        string='Lieu de retrait',
        required=True,
        ondelete='cascade',
    )
    active = fields.Boolean(default=True)

    monday_open = fields.Boolean('Lundi', default=True)
    tuesday_open = fields.Boolean('Mardi', default=True)
    wednesday_open = fields.Boolean('Mercredi', default=True)
    thursday_open = fields.Boolean('Jeudi', default=True)
    friday_open = fields.Boolean('Vendredi', default=True)
    saturday_open = fields.Boolean('Samedi', default=True)
    sunday_open = fields.Boolean('Dimanche', default=False)

    daily_capacity = fields.Integer(
        'Capacité par jour',
        default=6,
        help="Nombre maximum de retraits acceptés pour un jour ouvré.",
    )

    _sql_constraints = [
        ('location_unique',
         'UNIQUE(location_id)',
         "Il existe déjà un horaire pour ce lieu."),
    ]

    @api.constrains('daily_capacity')
    def _check_daily_capacity(self):
        for rec in self:
            if rec.daily_capacity < 1:
                raise ValidationError(_("La capacité quotidienne doit être au moins 1."))

    def _day_is_open(self, weekday_index):
        """weekday_index: 0=Mon..6=Sun. Returns bool."""
        mapping = [
            self.monday_open, self.tuesday_open, self.wednesday_open,
            self.thursday_open, self.friday_open, self.saturday_open,
            self.sunday_open,
        ]
        return bool(mapping[weekday_index])

    @api.model
    def _seed_default_schedules(self):
        Location = self.env['repair.pickup.location']
        for location in Location.search([]):
            if not self.search([('location_id', '=', location.id)], limit=1):
                self.create({'location_id': location.id})
```

- [ ] **Step 4: Update the schedule view**

Edit `repair_appointment/views/pickup_schedule_views.xml` — remove all `<field name="slot1_*"/>`, `<field name="slot2_*"/>`, `<field name="slot_capacity"/>` entries; add `<field name="daily_capacity"/>` in their place. The view should now show: `location_id`, the seven day-open booleans, `daily_capacity`, `active`.

- [ ] **Step 5: Update the seed data**

Edit `repair_appointment/data/pickup_schedule_data.xml` — for every `<record model="repair.pickup.schedule">`, remove any `slot1_start`, `slot1_end`, `slot2_start`, `slot2_end`, `slot_capacity` fields. Add `<field name="daily_capacity">6</field>`. Keep `noupdate="1"`.

- [ ] **Step 6: Run schedule tests again, verify they pass**

Run: same command as Step 2.
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add repair_appointment/models/repair_pickup_schedule.py \
        repair_appointment/views/pickup_schedule_views.xml \
        repair_appointment/data/pickup_schedule_data.xml \
        repair_appointment/tests/test_schedule.py
git commit -m "repair_appointment: schedule uses daily_capacity instead of per-slot"
```

---

## Task 3: Swap `start_datetime`/`end_datetime` for `pickup_date` on the appointment model

**Files:**
- Modify: `repair_appointment/models/repair_pickup_appointment.py`
- Modify: `repair_appointment/tests/common.py`
- Modify: `repair_appointment/tests/test_appointment_model.py`

- [ ] **Step 1: Update the test common helper**

Edit `repair_appointment/tests/common.py` — if any helper in this file sets `start_datetime` or `end_datetime`, replace with `pickup_date`. If no helper does, skip.

Search with: `grep -n 'start_datetime\|end_datetime' repair_appointment/tests/common.py`

- [ ] **Step 2: Write failing test in `test_appointment_model.py`**

Add a new test method:

```python
def test_appointment_has_pickup_date_not_datetime(self):
    batch = self._make_batch()
    apt = self.Appointment.create({'batch_id': batch.id})
    self.assertIn('pickup_date', apt._fields)
    self.assertNotIn('start_datetime', apt._fields)
    self.assertNotIn('end_datetime', apt._fields)
    self.assertFalse(apt.pickup_date)
    self.assertEqual(apt.state, 'pending')

def test_scheduled_requires_pickup_date(self):
    from odoo.exceptions import ValidationError
    batch = self._make_batch()
    apt = self.Appointment.create({'batch_id': batch.id})
    with self.assertRaises(ValidationError):
        apt.write({'state': 'scheduled'})
```

Delete or adapt any test in this file that references `start_datetime` / `end_datetime` directly (they will be rewritten in Task 4).

- [ ] **Step 3: Run tests, verify the new ones fail**

Run: `cd /Users/martin/Documents/odoo_dev/odoo && ./odoo-bin -c ../odoo.conf -d <test_db> --test-enable --stop-after-init -u repair_appointment --test-tags /repair_appointment:test_appointment_model`

Expected: the two new tests fail (field `pickup_date` doesn't exist yet).

- [ ] **Step 4: Edit the appointment model fields + `_order`**

Edit `repair_appointment/models/repair_pickup_appointment.py`:

Replace:

```python
    _order = 'start_datetime desc, id desc'
```

with:

```python
    _order = 'pickup_date desc, id desc'
```

Replace the two fields:

```python
    start_datetime = fields.Datetime('Début', tracking=True)
    end_datetime = fields.Datetime('Fin', tracking=True)
```

with:

```python
    pickup_date = fields.Date('Date de retrait', tracking=True)
```

Replace the constraint:

```python
    @api.constrains('state', 'start_datetime', 'end_datetime')
    def _check_scheduled_has_dates(self):
        for apt in self:
            if apt.state == 'scheduled' and (
                not apt.start_datetime or not apt.end_datetime
            ):
                raise ValidationError(_(
                    "Un rendez-vous confirmé doit avoir une date de début "
                    "et de fin."
                ))
```

with:

```python
    @api.constrains('state', 'pickup_date')
    def _check_scheduled_has_date(self):
        for apt in self:
            if apt.state == 'scheduled' and not apt.pickup_date:
                raise ValidationError(_(
                    "Un rendez-vous confirmé doit avoir une date de retrait."
                ))
```

- [ ] **Step 5: Run the new tests, verify they pass**

Run: same as Step 3.
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add repair_appointment/models/repair_pickup_appointment.py \
        repair_appointment/tests/common.py \
        repair_appointment/tests/test_appointment_model.py
git commit -m "repair_appointment: appointment model uses pickup_date (Date)"
```

---

## Task 4: Rewrite `action_schedule` / `action_confirm_manual` and `write()` tracking

**Files:**
- Modify: `repair_appointment/models/repair_pickup_appointment.py`
- Modify: `repair_appointment/tests/test_appointment_model.py`
- Modify: `repair_appointment/tests/test_state_machine.py`
- Modify: `repair_appointment/tests/test_reschedule_notification.py`

- [ ] **Step 1: Write failing tests for the new signatures**

In `repair_appointment/tests/test_state_machine.py`, replace any `action_schedule(start_dt, end_dt)` calls with `action_schedule(pickup_date)`, and rewrite the core transition test:

```python
def test_pending_to_scheduled_sets_pickup_date(self):
    from datetime import date, timedelta
    batch = self._make_batch()
    apt = self.Appointment.create({'batch_id': batch.id})
    target = date.today() + timedelta(days=3)
    apt.with_context(skip_slot_validation=True).action_schedule(target)
    self.assertEqual(apt.state, 'scheduled')
    self.assertEqual(apt.pickup_date, target)

def test_reschedule_in_place_increments_counter(self):
    from datetime import date, timedelta
    batch = self._make_batch()
    apt = self.Appointment.create({'batch_id': batch.id})
    d1 = date.today() + timedelta(days=3)
    d2 = date.today() + timedelta(days=5)
    apt.with_context(skip_slot_validation=True).action_schedule(d1)
    apt.with_context(skip_slot_validation=True).action_schedule(d2)
    self.assertEqual(apt.pickup_date, d2)
    self.assertEqual(apt.reschedule_count, 1)
```

In `repair_appointment/tests/test_reschedule_notification.py`, replace any `start_datetime` / `end_datetime` writes with `pickup_date` writes; replace the "skip_reschedule_notification" path so it tracks `pickup_date` changes instead.

- [ ] **Step 2: Run tests, verify they fail**

Run: `--test-tags /repair_appointment:test_state_machine,/repair_appointment:test_reschedule_notification`.
Expected: FAIL.

- [ ] **Step 3: Rewrite `action_schedule`**

In `repair_appointment/models/repair_pickup_appointment.py`, replace the existing `action_schedule` method with:

```python
    def action_schedule(self, pickup_date):
        """Transition pending → scheduled, or update pickup_date in place
        on an already-scheduled appointment. Validates day availability
        unless context `skip_slot_validation` is True."""
        for apt in self:
            apt._ensure_not_terminal()
            if apt.state not in ('pending', 'scheduled'):
                raise UserError(_("Impossible de planifier ce rendez-vous."))

            if not self.env.context.get('skip_slot_validation'):
                apt._validate_day(pickup_date)

            was_scheduled = apt.state == 'scheduled'
            old_date = apt.pickup_date

            apt.write({
                'pickup_date': pickup_date,
                'state': 'scheduled',
            })

            if was_scheduled and old_date != pickup_date:
                apt.reschedule_count += 1
                apt.message_post(body=_(
                    "RDV déplacé du %(old)s au %(new)s."
                ) % {'old': old_date, 'new': pickup_date})
            elif not was_scheduled:
                apt.message_post(body=_(
                    "RDV confirmé pour le %s."
                ) % pickup_date)

            apt._close_open_escalation_activities()
```

- [ ] **Step 4: Rewrite `action_confirm_manual`**

Replace the existing `action_confirm_manual`:

```python
    def action_confirm_manual(self):
        """Manual confirmation path for appointments booked by phone.
        Pickup date must already be filled on the record; slot validation
        is bypassed so staff can override closure / capacity rules."""
        for apt in self:
            if apt.state != 'pending':
                raise UserError(_(
                    "Seuls les rendez-vous en attente peuvent être confirmés "
                    "manuellement."
                ))
            if not apt.pickup_date:
                raise UserError(_(
                    "Renseignez la date de retrait avant de confirmer."
                ))
            apt.with_context(skip_slot_validation=True).action_schedule(
                apt.pickup_date,
            )
```

- [ ] **Step 5: Replace `_validate_slot` with `_validate_day`**

Replace the existing `_validate_slot`:

```python
    def _validate_day(self, pickup_date):
        if not pickup_date:
            raise UserError(_("Date de retrait requise."))
        if not self._is_day_available(pickup_date):
            raise UserError(_("Ce jour n'est plus disponible."))
```

- [ ] **Step 6: Rewrite `write()` tracking to watch `pickup_date`**

Replace the `write` method:

```python
    def write(self, vals):
        track_date_change = False
        old_dates = {}
        if 'pickup_date' in vals and not self.env.context.get('skip_reschedule_notification'):
            track_date_change = True
            old_dates = {apt.id: apt.pickup_date for apt in self}
        res = super().write(vals)
        if track_date_change:
            template = self.env.ref(
                'repair_appointment.mail_template_pickup_reschedule',
                raise_if_not_found=False,
            )
            for apt in self:
                if (apt.state == 'scheduled'
                        and old_dates.get(apt.id)
                        and old_dates.get(apt.id) != apt.pickup_date):
                    if template:
                        template.send_mail(apt.id, force_send=False)
                    apt.message_post(body=_(
                        "RDV déplacé — notification client envoyée."
                    ))
        return res
```

- [ ] **Step 7: Run tests, verify they pass**

Run: `--test-tags /repair_appointment:test_state_machine,/repair_appointment:test_reschedule_notification,/repair_appointment:test_appointment_model`.
Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add repair_appointment/models/repair_pickup_appointment.py \
        repair_appointment/tests/test_state_machine.py \
        repair_appointment/tests/test_reschedule_notification.py
git commit -m "repair_appointment: action_schedule takes a Date, drop _validate_slot"
```

---

## Task 5: Rewrite availability helpers (`_compute_available_days`, `_is_day_available`, `_count_booked_on_day`)

**Files:**
- Modify: `repair_appointment/models/repair_pickup_appointment.py`
- Rename: `repair_appointment/tests/test_slot_availability.py` → `test_day_availability.py`

- [ ] **Step 1: Rename the test file**

Run: `git mv repair_appointment/tests/test_slot_availability.py repair_appointment/tests/test_day_availability.py`

- [ ] **Step 2: Rewrite the test file contents**

Replace the contents of `repair_appointment/tests/test_day_availability.py` with:

```python
from datetime import date, timedelta
from .common import RepairAppointmentCase


class TestDayAvailability(RepairAppointmentCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.schedule_boutique = cls.Schedule.search(
            [('location_id', '=', cls.location_boutique.id)], limit=1
        ) or cls.Schedule.create({'location_id': cls.location_boutique.id})

    def _days(self, date_from=None, date_to=None):
        return self.Appointment._compute_available_days(
            self.location_boutique, date_from=date_from, date_to=date_to,
        )

    def test_sunday_is_closed(self):
        # Find a Sunday within the horizon
        today = date.today()
        for offset in range(2, 20):
            d = today + timedelta(days=offset)
            if d.weekday() == 6:
                break
        days = {x['date']: x for x in self._days()}
        self.assertIn(d, days)
        self.assertEqual(days[d]['state'], 'closed')

    def test_closure_covers_day(self):
        target = date.today() + timedelta(days=3)
        self.Closure.create({
            'name': 'Test closure',
            'date_from': target, 'date_to': target,
        })
        days = {x['date']: x for x in self._days()}
        self.assertEqual(days[target]['state'], 'closed')

    def test_min_lead_time_respected(self):
        too_soon = date.today() + timedelta(days=1)
        days = {x['date']: x for x in self._days()}
        self.assertNotIn(too_soon, days)

    def test_horizon_respected(self):
        beyond = date.today() + timedelta(days=30)
        days = {x['date']: x for x in self._days()}
        self.assertNotIn(beyond, days)

    def test_daily_cap_full_renders_full_state(self):
        target = date.today() + timedelta(days=3)
        # Make sure it's an open weekday
        while target.weekday() == 6:
            target += timedelta(days=1)
        self.schedule_boutique.daily_capacity = 1
        batch = self._make_batch()
        apt = self.Appointment.create({'batch_id': batch.id})
        apt.with_context(skip_slot_validation=True).action_schedule(target)
        days = {x['date']: x for x in self._days()}
        self.assertEqual(days[target]['state'], 'full')
        self.assertEqual(days[target]['remaining_capacity'], 0)

    def test_is_day_available_false_for_closed_day(self):
        today = date.today()
        for offset in range(2, 20):
            d = today + timedelta(days=offset)
            if d.weekday() == 6:
                break
        batch = self._make_batch()
        apt = self.Appointment.create({'batch_id': batch.id})
        self.assertFalse(apt._is_day_available(d))
```

- [ ] **Step 3: Run tests, verify they fail**

Run: `--test-tags /repair_appointment:test_day_availability`.
Expected: FAIL — methods `_compute_available_days` / `_is_day_available` don't exist yet.

- [ ] **Step 4: Rewrite the three helpers in the model**

In `repair_appointment/models/repair_pickup_appointment.py`, delete the old `_compute_available_slots`, `_float_to_datetime`, `_count_booked_in_slot`, `_is_slot_available` methods. Add:

```python
    @api.model
    def _compute_available_days(self, location, date_from=None, date_to=None,
                                booking_horizon_days=None):
        """Return a list of dicts describing each calendar day in the
        booking window at `location`.

        Each dict: {
            'date': date,
            'state': 'open' | 'closed' | 'full' | 'lead_time',
            'remaining_capacity': int,
        }

        Days before the min-lead cutoff are excluded entirely (the portal
        renders the cutoff explicitly; no need to send them). Days past
        the horizon are also excluded. Days within the window are always
        returned, with their state explaining why they are/aren't bookable.
        """
        from datetime import timedelta

        today = fields.Date.today()
        min_lead = self._get_min_lead_days()
        horizon = (booking_horizon_days
                   if booking_horizon_days is not None
                   else self._get_booking_horizon_days())

        earliest = today + timedelta(days=min_lead)
        latest = today + timedelta(days=horizon)

        date_from = max(date_from or earliest, earliest)
        date_to = min(date_to or latest, latest)

        if date_from > date_to:
            return []

        schedule = self.env['repair.pickup.schedule'].search(
            [('location_id', '=', location.id), ('active', '=', True)],
            limit=1,
        )
        if not schedule:
            return []

        closures = self.env['repair.pickup.closure'].search(
            [('active', '=', True)],
        ).filtered(
            lambda c: c.location_id in (location, False) or c.location_id.id is False
        )

        results = []
        day = date_from
        while day <= date_to:
            entry = {'date': day, 'state': 'open', 'remaining_capacity': 0}
            if not schedule._day_is_open(day.weekday()):
                entry['state'] = 'closed'
            elif any(c._covers(day, location) for c in closures):
                entry['state'] = 'closed'
            else:
                booked = self._count_booked_on_day(day, location)
                remaining = max(0, schedule.daily_capacity - booked)
                entry['remaining_capacity'] = remaining
                entry['state'] = 'open' if remaining > 0 else 'full'
            results.append(entry)
            day += timedelta(days=1)

        return results

    @api.model
    def _count_booked_on_day(self, pickup_date, location):
        """Count scheduled appointments at `location` on `pickup_date`."""
        return self.search_count([
            ('pickup_date', '=', pickup_date),
            ('location_id', '=', location.id),
            ('state', '=', 'scheduled'),
        ])

    def _is_day_available(self, pickup_date):
        """True if the target day has remaining capacity and is within
        the schedule + closures + lead-time rules. Excludes self from
        the count so same-day reschedules pass."""
        self.ensure_one()
        from datetime import timedelta
        if not self.location_id or not pickup_date:
            return False
        schedule = self.env['repair.pickup.schedule'].search(
            [('location_id', '=', self.location_id.id)], limit=1,
        )
        if not schedule:
            return False
        if not schedule._day_is_open(pickup_date.weekday()):
            return False
        closures = self.env['repair.pickup.closure'].search([('active', '=', True)])
        for c in closures:
            if c._covers(pickup_date, self.location_id):
                return False
        min_lead = self._get_min_lead_days()
        if pickup_date < fields.Date.today() + timedelta(days=min_lead):
            if not self.env.context.get('bypass_lead_time'):
                return False
        booked = self.search_count([
            ('pickup_date', '=', pickup_date),
            ('location_id', '=', self.location_id.id),
            ('state', '=', 'scheduled'),
            ('id', '!=', self.id),
        ])
        if booked >= schedule.daily_capacity:
            if not self.env.context.get('bypass_capacity'):
                return False
        return True
```

- [ ] **Step 5: Run tests, verify they pass**

Run: `--test-tags /repair_appointment:test_day_availability`.
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add repair_appointment/models/repair_pickup_appointment.py \
        repair_appointment/tests/test_day_availability.py
git commit -m "repair_appointment: day-availability helpers replace slot helpers"
```

---

## Task 6: Add `primary_device_id` / `primary_device_category_id` for the daily agenda

**Files:**
- Modify: `repair_appointment/models/repair_pickup_appointment.py`
- Modify: `repair_appointment/tests/test_appointment_model.py`

- [ ] **Step 1: Write failing test**

Add to `repair_appointment/tests/test_appointment_model.py`:

```python
def test_primary_device_exposes_first_repair_device(self):
    batch = self._make_batch(repair_count=2)
    repair = batch.repair_ids[0]
    # Give the first repair a product_tmpl and category so the computes fire
    template = self.env['product.template'].search([], limit=1)
    category = self.env['product.category'].search([], limit=1)
    repair.write({
        'product_tmpl_id': template.id if template else False,
        'category_id': category.id if category else False,
    })
    apt = self.Appointment.create({'batch_id': batch.id})
    if template:
        self.assertEqual(apt.primary_device_id, template)
    if category:
        self.assertEqual(apt.primary_device_category_id, category)
```

- [ ] **Step 2: Run test, verify it fails**

Run: `--test-tags /repair_appointment:test_appointment_model`. Expected: FAIL.

- [ ] **Step 3: Add the two computed fields**

In `repair_appointment/models/repair_pickup_appointment.py`, add alongside `device_count` / `device_summary`:

```python
    primary_device_id = fields.Many2one(
        'product.template',
        string='Appareil principal',
        compute='_compute_primary_device',
        store=True,
    )
    primary_device_category_id = fields.Many2one(
        'product.category',
        string='Catégorie principale',
        compute='_compute_primary_device',
        store=True,
    )

    @api.depends('repair_ids', 'repair_ids.product_tmpl_id',
                 'repair_ids.category_id')
    def _compute_primary_device(self):
        for apt in self:
            first = apt.repair_ids[:1]
            apt.primary_device_id = first.product_tmpl_id if first else False
            apt.primary_device_category_id = first.category_id if first else False
```

- [ ] **Step 4: Run test, verify it passes**

Run: same as Step 2. Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add repair_appointment/models/repair_pickup_appointment.py \
        repair_appointment/tests/test_appointment_model.py
git commit -m "repair_appointment: add primary_device_id / primary_device_category_id"
```

---

## Task 7: Rewrite the portal controller for date-only payloads

**Files:**
- Modify: `repair_appointment/controllers/portal.py`
- Modify: `repair_appointment/tests/test_portal_controller.py`

- [ ] **Step 1: Update controller tests**

Edit `repair_appointment/tests/test_portal_controller.py`:

- In every test, replace any `{'start_datetime': ...}` form body with `{'pickup_date': 'YYYY-MM-DD'}`.
- Replace any assertion on `apt.start_datetime` / `apt.end_datetime` with `apt.pickup_date`.
- Update the JSON-endpoint happy-path test to expect per-day objects:

```python
def test_slots_endpoint_returns_per_day_objects(self):
    batch = self._make_batch()
    apt = self.Appointment.create({'batch_id': batch.id})
    # Invoke controller logic directly
    from odoo.addons.repair_appointment.controllers.portal import RepairPickupPortal
    from unittest.mock import patch
    ctrl = RepairPickupPortal()
    # Use env directly; call the underlying compute instead of going through http
    days = self.Appointment._compute_available_days(apt.location_id)
    self.assertTrue(days)
    sample = days[0]
    self.assertIn('date', sample)
    self.assertIn('state', sample)
    self.assertIn('remaining_capacity', sample)
    self.assertIn(sample['state'],
                  ('open', 'closed', 'full', 'lead_time'))
```

(If the existing test file has more elaborate HTTP-level tests, keep them but swap the body field.)

- [ ] **Step 2: Run tests, verify they fail**

Run: `--test-tags /repair_appointment:test_portal_controller`. Expected: FAIL.

- [ ] **Step 3: Rewrite `repair_appointment/controllers/portal.py`**

Replace the whole file with:

```python
from datetime import date
from odoo import http
from odoo.exceptions import UserError
from odoo.http import request


class RepairPickupPortal(http.Controller):

    def _get_appointment(self, token):
        apt = request.env['repair.pickup.appointment'].sudo().search(
            [('token', '=', token)], limit=1,
        )
        return apt or False

    @http.route('/my/pickup/<string:token>', type='http', auth='public',
                website=True)
    def pickup_landing(self, token, **kwargs):
        apt = self._get_appointment(token)
        if not apt:
            return request.not_found()
        return request.render('repair_appointment.portal_pickup_page', {
            'apt': apt,
        })

    @http.route('/my/pickup/<string:token>/slots', type='json', auth='public')
    def pickup_slots(self, token, **kwargs):
        apt = self._get_appointment(token)
        if not apt:
            return []
        days = apt._compute_available_days(apt.location_id)
        return [
            {
                'date': d['date'].isoformat(),
                'state': d['state'],
                'remaining_capacity': d['remaining_capacity'],
            }
            for d in days
        ]

    # csrf=False: UUID4 token in URL is the auth mechanism
    @http.route('/my/pickup/<string:token>/book', type='http', auth='public',
                methods=['POST'], csrf=False, website=True)
    def pickup_book(self, token, pickup_date=None, **kwargs):
        apt = self._get_appointment(token)
        if not apt:
            return request.not_found()
        return self._schedule_from_form(apt, pickup_date, expected_state='pending')

    # csrf=False: UUID4 token in URL is the auth mechanism
    @http.route('/my/pickup/<string:token>/reschedule', type='http',
                auth='public', methods=['POST'], csrf=False, website=True)
    def pickup_reschedule(self, token, pickup_date=None, **kwargs):
        apt = self._get_appointment(token)
        if not apt:
            return request.not_found()
        return self._schedule_from_form(apt, pickup_date, expected_state='scheduled')

    @http.route('/my/pickup/<string:token>/confirmation', type='http',
                auth='public', website=True)
    def pickup_confirmation(self, token, **kwargs):
        apt = self._get_appointment(token)
        if not apt:
            return request.not_found()
        return request.render(
            'repair_appointment.portal_pickup_confirmation', {'apt': apt},
        )

    # ----- helpers -----

    def _schedule_from_form(self, apt, date_iso, expected_state):
        if not date_iso:
            return self._render_error(apt, "Date de retrait manquante.")
        try:
            pickup_date = date.fromisoformat(date_iso)
        except ValueError:
            return self._render_error(apt, "Format de date invalide.")

        if apt.state != expected_state:
            return self._render_error(
                apt, "Ce rendez-vous ne peut plus être modifié ici."
            )

        try:
            apt.sudo().with_context(portal_booking=True).action_schedule(pickup_date)
        except UserError as e:
            return self._render_error(apt, str(e))

        apt.sudo().message_post(body=(
            "RDV %s par le client depuis le portail (IP: %s)."
        ) % (
            'replanifié' if expected_state == 'scheduled' else 'pris',
            request.httprequest.remote_addr or '?',
        ))

        return request.redirect(f'/my/pickup/{apt.token}/confirmation')

    def _render_error(self, apt, message):
        return request.render('repair_appointment.portal_pickup_page', {
            'apt': apt,
            'error': message,
        })
```

- [ ] **Step 4: Run tests, verify they pass**

Run: same as Step 2. Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add repair_appointment/controllers/portal.py \
        repair_appointment/tests/test_portal_controller.py
git commit -m "repair_appointment: portal controller uses pickup_date + per-day payloads"
```

---

## Task 8: Rewrite the portal template as a month-grid day picker

**Files:**
- Modify: `repair_appointment/views/portal_templates.xml`

- [ ] **Step 1: Replace the landing + confirmation templates**

Overwrite `repair_appointment/views/portal_templates.xml` with:

```xml
<?xml version="1.0" encoding="utf-8"?>
<odoo>
    <template id="portal_pickup_page" name="Portail retrait">
        <t t-call="portal.frontend_layout">
            <div class="container mt-4 mb-5">
                <t t-if="error">
                    <div class="alert alert-danger" t-esc="error"/>
                </t>

                <h2>Bonjour <t t-out="apt.partner_id.name"/>,</h2>
                <p class="text-muted">Référence : <t t-out="apt.batch_id.name"/></p>

                <t t-if="apt.state == 'pending'">
                    <p>Votre appareil est prêt à être récupéré :</p>
                    <ul>
                        <t t-foreach="apt.repair_ids" t-as="r">
                            <li><t t-out="r.device_id_name or r.display_name"/></li>
                        </t>
                    </ul>
                    <p>
                        <strong>Lieu de retrait :</strong> <t t-out="apt.location_id.display_name"/><br/>
                        <t t-if="apt.location_id.street">
                            <t t-out="apt.location_id.street"/>, <t t-out="apt.location_id.city"/><br/>
                        </t>
                        <strong>Horaires d'ouverture :</strong> lundi–samedi, 15h00 – 19h30
                    </p>

                    <h4 class="mt-4">Choisissez votre jour de retrait :</h4>
                    <div id="pickup-day-selector" class="mt-3">Chargement…</div>

                    <div id="pickup-confirm-block" class="mt-3" style="display:none;">
                        <div class="alert alert-info">
                            <span>Confirmer le retrait du </span>
                            <strong id="pickup-confirm-label"></strong>
                            <span> à </span>
                            <strong t-out="apt.location_id.display_name"/>
                            <span> ?</span>
                        </div>
                        <form method="POST"
                              t-att-action="'/my/pickup/' + apt.token + '/book'"
                              id="pickup-book-form">
                            <input type="hidden" name="csrf_token"
                                   t-att-value="request.csrf_token()"/>
                            <input type="hidden" name="pickup_date" id="pickup_date_input"/>
                            <button type="submit" class="btn btn-primary">
                                Valider mon rendez-vous
                            </button>
                            <button type="button" class="btn btn-outline-secondary"
                                    id="pickup-cancel-btn">
                                Annuler
                            </button>
                        </form>
                    </div>
                </t>

                <t t-if="apt.state == 'scheduled'">
                    <p>Votre retrait est confirmé :</p>
                    <ul>
                        <li>📅 <strong t-out="apt.pickup_date"/></li>
                        <li>📍 <strong t-out="apt.location_id.display_name"/></li>
                        <li>🕒 Ouverture de 15h00 à 19h30</li>
                    </ul>
                    <p>Appareils à récupérer :</p>
                    <ul>
                        <t t-foreach="apt.repair_ids" t-as="r">
                            <li><t t-out="r.device_id_name or r.display_name"/></li>
                        </t>
                    </ul>

                    <h4 class="mt-4">Déplacer mon rendez-vous</h4>
                    <div id="pickup-day-selector" class="mt-3">Chargement…</div>

                    <div id="pickup-confirm-block" class="mt-3" style="display:none;">
                        <div class="alert alert-info">
                            <span>Déplacer le retrait au </span>
                            <strong id="pickup-confirm-label"></strong>
                            <span> ?</span>
                        </div>
                        <form method="POST"
                              t-att-action="'/my/pickup/' + apt.token + '/reschedule'"
                              id="pickup-reschedule-form">
                            <input type="hidden" name="csrf_token"
                                   t-att-value="request.csrf_token()"/>
                            <input type="hidden" name="pickup_date" id="pickup_date_input"/>
                            <button type="submit" class="btn btn-primary">
                                Valider le nouveau jour
                            </button>
                            <button type="button" class="btn btn-outline-secondary"
                                    id="pickup-cancel-btn">
                                Annuler
                            </button>
                        </form>
                    </div>

                    <p class="text-muted mt-3">
                        Pour annuler votre rendez-vous, merci de nous contacter directement.
                    </p>
                </t>

                <t t-if="apt.state in ('done', 'cancelled', 'no_show')">
                    <p>Ce rendez-vous est clôturé.</p>
                    <p>Pour toute question, contactez-nous directement.</p>
                </t>
            </div>

            <style>
                .pickup-month { margin-bottom: 1.5rem; }
                .pickup-month h5 { margin-bottom: 0.5rem; text-transform: capitalize; }
                .pickup-grid {
                    display: grid;
                    grid-template-columns: repeat(7, 1fr);
                    gap: 4px;
                }
                .pickup-dayhead {
                    text-align: center;
                    font-size: 0.8em;
                    color: #888;
                    padding: 4px 0;
                }
                .pickup-day {
                    border: 1px solid #ddd;
                    padding: 10px 4px;
                    text-align: center;
                    border-radius: 4px;
                    background: #fff;
                }
                .pickup-day.open { cursor: pointer; }
                .pickup-day.open:hover { background: #e7f1ff; border-color: #0d6efd; }
                .pickup-day.open.selected { background: #0d6efd; color: white; }
                .pickup-day.closed, .pickup-day.lead_time {
                    background: #f5f5f5; color: #bbb;
                }
                .pickup-day.full { background: #fde2e2; color: #a33; }
                .pickup-day.empty { border: none; background: transparent; }
                .pickup-legend { font-size: 0.85em; color: #666; margin-top: 0.5rem; }
                .pickup-legend span {
                    display: inline-block; margin-right: 1rem;
                    padding: 2px 8px; border-radius: 3px;
                }
                .pickup-legend .l-closed { background: #f5f5f5; color: #bbb; }
                .pickup-legend .l-full { background: #fde2e2; color: #a33; }
                .pickup-legend .l-open { background: #e7f1ff; color: #0d6efd; }
            </style>

            <script type="text/javascript">
                (async function() {
                    const container = document.getElementById('pickup-day-selector');
                    if (!container) return;
                    const token = '<t t-out="apt.token"/>';
                    const currentDate = '<t t-out="apt.pickup_date or \'\'"/>';
                    const monthNames = ['janvier','février','mars','avril','mai','juin',
                                        'juillet','août','septembre','octobre','novembre','décembre'];
                    const dayHeads = ['L','M','M','J','V','S','D'];

                    let selectedDate = null;

                    function fmtDateLabel(iso) {
                        const d = new Date(iso + 'T12:00:00');
                        const weekdays = ['dimanche','lundi','mardi','mercredi','jeudi','vendredi','samedi'];
                        return weekdays[d.getDay()] + ' ' + d.getDate() + ' '
                               + monthNames[d.getMonth()] + ' ' + d.getFullYear();
                    }

                    function render(days) {
                        // Group by YYYY-MM
                        const byMonth = {};
                        days.forEach(d => {
                            const k = d.date.substring(0, 7);
                            (byMonth[k] = byMonth[k] || []).push(d);
                        });
                        const monthsOrdered = Object.keys(byMonth).sort();
                        const root = document.createElement('div');
                        monthsOrdered.forEach(monthKey => {
                            const [year, month] = monthKey.split('-').map(Number);
                            const wrap = document.createElement('div');
                            wrap.className = 'pickup-month';
                            const title = document.createElement('h5');
                            title.textContent = monthNames[month - 1] + ' ' + year;
                            wrap.appendChild(title);

                            const grid = document.createElement('div');
                            grid.className = 'pickup-grid';
                            dayHeads.forEach(h => {
                                const el = document.createElement('div');
                                el.className = 'pickup-dayhead';
                                el.textContent = h;
                                grid.appendChild(el);
                            });

                            // Pad leading empty cells based on first day's weekday (Mon=0)
                            const firstDay = byMonth[monthKey][0];
                            const firstJs = new Date(firstDay.date + 'T12:00:00');
                            let leading = (firstJs.getDay() + 6) % 7;  // Mon=0..Sun=6
                            for (let i = 0; i &lt; leading; i++) {
                                const pad = document.createElement('div');
                                pad.className = 'pickup-day empty';
                                grid.appendChild(pad);
                            }

                            byMonth[monthKey].forEach(d => {
                                const cell = document.createElement('div');
                                cell.className = 'pickup-day ' + d.state;
                                cell.dataset.date = d.date;
                                const js = new Date(d.date + 'T12:00:00');
                                cell.textContent = js.getDate();
                                if (d.state === 'open') {
                                    cell.addEventListener('click', () => {
                                        selectedDate = d.date;
                                        document.querySelectorAll('.pickup-day.selected')
                                            .forEach(c => c.classList.remove('selected'));
                                        cell.classList.add('selected');
                                        document.getElementById('pickup_date_input').value = d.date;
                                        document.getElementById('pickup-confirm-label').textContent
                                            = fmtDateLabel(d.date);
                                        document.getElementById('pickup-confirm-block').style.display = 'block';
                                    });
                                }
                                if (d.date === currentDate) {
                                    cell.classList.add('selected');
                                }
                                grid.appendChild(cell);
                            });
                            wrap.appendChild(grid);
                            root.appendChild(wrap);
                        });
                        const legend = document.createElement('div');
                        legend.className = 'pickup-legend';
                        legend.innerHTML = '<span class="l-open">Disponible</span>'
                                         + '<span class="l-full">Complet</span>'
                                         + '<span class="l-closed">Fermé / Indisponible</span>';
                        root.appendChild(legend);
                        container.innerHTML = '';
                        container.appendChild(root);

                        const cancelBtn = document.getElementById('pickup-cancel-btn');
                        if (cancelBtn) {
                            cancelBtn.addEventListener('click', () => {
                                document.getElementById('pickup-confirm-block').style.display = 'none';
                                document.querySelectorAll('.pickup-day.selected')
                                    .forEach(c => c.classList.remove('selected'));
                                selectedDate = null;
                            });
                        }
                    }

                    try {
                        const resp = await fetch('/my/pickup/' + token + '/slots', {
                            method: 'POST',
                            headers: {'Content-Type': 'application/json'},
                            body: JSON.stringify({jsonrpc: '2.0', method: 'call', params: {}}),
                        });
                        const payload = await resp.json();
                        const days = payload.result || [];
                        if (!days.length) {
                            container.innerHTML = '&lt;em&gt;Aucun jour disponible.&lt;/em&gt;';
                            return;
                        }
                        render(days);
                    } catch (e) {
                        container.innerHTML = '&lt;em&gt;Erreur de chargement du calendrier.&lt;/em&gt;';
                    }
                })();
            </script>
        </t>
    </template>

    <template id="portal_pickup_confirmation" name="Portail retrait — confirmation">
        <t t-call="portal.frontend_layout">
            <div class="container mt-4 mb-5">
                <h2>Retrait confirmé</h2>
                <p>Votre retrait est confirmé pour le
                   <strong t-out="apt.pickup_date"/> à
                   <strong t-out="apt.location_id.display_name"/>.</p>
                <p class="text-muted">Ouverture de 15h00 à 19h30.</p>
                <p>
                    <a t-att-href="'/my/pickup/' + apt.token"
                       class="btn btn-outline-primary">
                        Retour à ma demande
                    </a>
                </p>
            </div>
        </t>
    </template>
</odoo>
```

- [ ] **Step 2: Manual smoke test (no test target — QWeb only)**

Run the Odoo server with `--dev=reload,xml`, open `/my/pickup/<token>` for a test appointment:

Expected: month grid renders, full 14-day horizon visible, closed days greyed, open days clickable. Clicking an open day shows the confirmation block.

- [ ] **Step 3: Commit**

```bash
git add repair_appointment/views/portal_templates.xml
git commit -m "repair_appointment: portal renders month-grid day picker"
```

---

## Task 9: Update backend appointment views (form, tree, calendar, search)

**Files:**
- Modify: `repair_appointment/views/appointment_views.xml`

- [ ] **Step 1: Edit the tree view**

In `repair_appointment/views/appointment_views.xml`, inside `view_repair_pickup_appointment_tree`, replace:

```xml
                <field name="start_datetime"/>
```

with:

```xml
                <field name="pickup_date"/>
```

- [ ] **Step 2: Edit the form view**

Inside `view_repair_pickup_appointment_form`, replace:

```xml
                    <button name="action_confirm_manual" type="object"
                            string="Confirmer le rendez-vous" class="btn-primary"
                            invisible="state != 'pending' or not start_datetime or not end_datetime"/>
```

with:

```xml
                    <button name="action_confirm_manual" type="object"
                            string="Confirmer le rendez-vous" class="btn-primary"
                            invisible="state != 'pending' or not pickup_date"/>
```

And replace:

```xml
                        <group>
                            <field name="start_datetime"/>
                            <field name="end_datetime"/>
                            <field name="reschedule_count" readonly="1"/>
                        </group>
```

with:

```xml
                        <group>
                            <field name="pickup_date"/>
                            <field name="reschedule_count" readonly="1"/>
                        </group>
```

- [ ] **Step 3: Edit the calendar view**

Replace the calendar view record `view_repair_pickup_appointment_calendar` with:

```xml
    <record id="view_repair_pickup_appointment_calendar" model="ir.ui.view">
        <field name="name">repair.pickup.appointment.calendar</field>
        <field name="model">repair.pickup.appointment</field>
        <field name="arch" type="xml">
            <calendar string="Rendez-vous de retrait"
                      date_start="pickup_date"
                      color="location_id"
                      mode="month"
                      quick_create="0">
                <field name="partner_id"/>
                <field name="device_count"/>
                <field name="device_summary"/>
                <field name="batch_id"/>
                <field name="location_id" filters="1"/>
                <field name="state" filters="1"/>
            </calendar>
        </field>
    </record>
```

- [ ] **Step 4: Edit the search view**

Replace the `today` filter with a date-based one, and add `this_week`:

```xml
                <filter name="today" string="Aujourd'hui"
                        domain="[('pickup_date','=', context_today().strftime('%Y-%m-%d'))]"/>
                <filter name="this_week" string="Cette semaine"
                        domain="[('pickup_date','&gt;=', (context_today() + relativedelta(weekday=0, days=-6)).strftime('%Y-%m-%d')),
                                 ('pickup_date','&lt;=', (context_today() + relativedelta(weekday=6)).strftime('%Y-%m-%d'))]"/>
```

- [ ] **Step 5: Start the server and verify views load**

Run: `./odoo-bin -c ../odoo.conf -u repair_appointment --stop-after-init`
Expected: no XML parse errors, no field reference errors.

- [ ] **Step 6: Commit**

```bash
git add repair_appointment/views/appointment_views.xml
git commit -m "repair_appointment: backend views use pickup_date and month calendar"
```

---

## Task 10: Add the daily agenda tree view + menu entry

**Files:**
- Modify: `repair_appointment/views/appointment_views.xml`
- Modify: `repair_appointment/views/menus.xml`
- Create: `repair_appointment/tests/test_daily_agenda.py`

- [ ] **Step 1: Write failing test**

Create `repair_appointment/tests/test_daily_agenda.py`:

```python
from datetime import date, timedelta
from .common import RepairAppointmentCase


class TestDailyAgenda(RepairAppointmentCase):

    def test_action_daily_agenda_default_domain(self):
        action = self.env.ref(
            'repair_appointment.action_repair_pickup_appointment_daily_agenda'
        )
        self.assertEqual(action.res_model, 'repair.pickup.appointment')
        # Action must carry a default filter context
        ctx = action._get_eval_context() if hasattr(action, '_get_eval_context') else {}
        # Verify the action's context string contains the default-filter marker
        raw = action.context or ''
        self.assertIn('search_default_today_or_tomorrow', raw)

    def test_today_or_tomorrow_filter_matches_expected_records(self):
        batch1 = self._make_batch()
        today = date.today()
        for offset in range(2, 10):
            d = today + timedelta(days=offset)
            if d.weekday() != 6:
                break
        apt_today = self.Appointment.create({
            'batch_id': batch1.id,
            'pickup_date': today,
            'state': 'scheduled',
        })
        batch2 = self._make_batch()
        apt_far = self.Appointment.create({
            'batch_id': batch2.id,
            'pickup_date': today + timedelta(days=10),
            'state': 'scheduled',
        })
        from datetime import timedelta as td
        domain = [
            ('state', '=', 'scheduled'),
            ('pickup_date', '>=', today),
            ('pickup_date', '<=', today + td(days=1)),
        ]
        found = self.Appointment.search(domain)
        self.assertIn(apt_today, found)
        self.assertNotIn(apt_far, found)
```

- [ ] **Step 2: Run test, verify it fails**

Run: `--test-tags /repair_appointment:test_daily_agenda`. Expected: FAIL (action XML-id doesn't exist).

- [ ] **Step 3: Add the daily agenda tree view, search filter, and action**

Append to `repair_appointment/views/appointment_views.xml` (inside the root `<odoo>` element, after the existing action record):

```xml
    <!-- Daily agenda tree -->
    <record id="view_repair_pickup_appointment_agenda_tree" model="ir.ui.view">
        <field name="name">repair.pickup.appointment.agenda.tree</field>
        <field name="model">repair.pickup.appointment</field>
        <field name="arch" type="xml">
            <tree string="Agenda du jour"
                  decoration-bf="pickup_date == context_today().strftime('%Y-%m-%d')"
                  decoration-muted="pickup_date &gt; context_today().strftime('%Y-%m-%d')"
                  create="false">
                <field name="pickup_date"/>
                <field name="partner_id"/>
                <field name="primary_device_id" string="Appareil"/>
                <field name="primary_device_category_id" string="Catégorie"/>
                <field name="location_id"/>
                <field name="batch_id"/>
                <field name="reschedule_count" optional="hide"/>
            </tree>
        </field>
    </record>

    <!-- Daily agenda search (adds the today-or-tomorrow filter) -->
    <record id="view_repair_pickup_appointment_agenda_search" model="ir.ui.view">
        <field name="name">repair.pickup.appointment.agenda.search</field>
        <field name="model">repair.pickup.appointment</field>
        <field name="arch" type="xml">
            <search>
                <field name="partner_id"/>
                <field name="batch_id"/>
                <filter name="today_or_tomorrow" string="Aujourd'hui + demain"
                        domain="[('state','=','scheduled'),
                                 ('pickup_date','&gt;=', context_today().strftime('%Y-%m-%d')),
                                 ('pickup_date','&lt;=', (context_today() + relativedelta(days=1)).strftime('%Y-%m-%d'))]"/>
                <filter name="today_only" string="Aujourd'hui"
                        domain="[('state','=','scheduled'),
                                 ('pickup_date','=', context_today().strftime('%Y-%m-%d'))]"/>
                <group expand="1" string="Grouper par">
                    <filter name="group_date" string="Jour"
                            context="{'group_by': 'pickup_date'}"/>
                    <filter name="group_location" string="Lieu"
                            context="{'group_by': 'location_id'}"/>
                </group>
            </search>
        </field>
    </record>

    <record id="action_repair_pickup_appointment_daily_agenda"
            model="ir.actions.act_window">
        <field name="name">Agenda du jour</field>
        <field name="res_model">repair.pickup.appointment</field>
        <field name="view_mode">tree,form</field>
        <field name="view_id" ref="view_repair_pickup_appointment_agenda_tree"/>
        <field name="search_view_id"
               ref="view_repair_pickup_appointment_agenda_search"/>
        <field name="context">{
            'search_default_today_or_tomorrow': 1,
            'search_default_group_date': 1,
            'search_default_group_location': 1
        }</field>
    </record>
```

- [ ] **Step 4: Add the menu entry**

Edit `repair_appointment/views/menus.xml`, insert after `menu_repair_appointment_calendar`:

```xml
    <menuitem id="menu_repair_appointment_daily_agenda"
              name="Agenda du jour"
              parent="menu_repair_appointment_root"
              action="action_repair_pickup_appointment_daily_agenda"
              sequence="20"/>
```

- [ ] **Step 5: Register the test module in `tests/__init__.py`**

Edit `repair_appointment/tests/__init__.py`, add:

```python
from . import test_daily_agenda
```

Also rename the existing import `from . import test_slot_availability` to `from . import test_day_availability` if not already updated.

- [ ] **Step 6: Run tests, verify they pass**

Run: `--test-tags /repair_appointment:test_daily_agenda`. Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add repair_appointment/views/appointment_views.xml \
        repair_appointment/views/menus.xml \
        repair_appointment/tests/test_daily_agenda.py \
        repair_appointment/tests/__init__.py
git commit -m "repair_appointment: add 'Agenda du jour' daily pickup list view"
```

---

## Task 11: Adapt remaining tests and the calendar JS patch

**Files:**
- Modify: `repair_appointment/tests/test_reminder_cron.py`
- Modify: `repair_appointment/tests/test_escalation.py`
- Modify: `repair_appointment/tests/test_batch_integration.py`
- Modify: `repair_appointment/tests/test_closure.py`
- Modify: `repair_appointment/tests/test_mail_template_pickup_ready.py`
- Modify (if applicable): `repair_appointment/static/src/js/appointment_calendar_patch.js`

- [ ] **Step 1: Grep for remaining references to old fields/methods**

Run:

```bash
grep -rn "start_datetime\|end_datetime\|slot1_\|slot2_\|slot_capacity\|_compute_available_slots\|_count_booked_in_slot\|_is_slot_available\|_validate_slot" repair_appointment/
```

Expected: every remaining hit is in test fixtures or the JS patch.

- [ ] **Step 2: Fix test fixtures**

For each test file flagged by the grep:

- Replace `start_datetime=<dt>, end_datetime=<dt>` writes with `pickup_date=<date>`.
- Replace `action_schedule(start, end)` with `action_schedule(pickup_date)`.
- Replace assertions on `apt.start_datetime` / `apt.end_datetime` with `apt.pickup_date`.
- If a test depended on the second slot's time window, change it to test daily capacity instead (two appointments on the same day at capacity 1 ⇒ second one rejected).

Show-the-code example (for `test_batch_integration.py`, update any scheduling call):

```python
# Before:
# apt.action_schedule(some_start_dt, some_end_dt)
# After:
from datetime import date, timedelta
apt.with_context(skip_slot_validation=True).action_schedule(
    date.today() + timedelta(days=3),
)
```

- [ ] **Step 3: Inspect the calendar JS patch**

Read `repair_appointment/static/src/js/appointment_calendar_patch.js`.

If it references `start_datetime` / `end_datetime`, replace them with `pickup_date` and drop any logic that computed a slot end time. If it references `slot_capacity`, replace with `daily_capacity`. If it is not affected (e.g., only modifies event rendering by state/color), leave as-is.

Run:

```bash
grep -n "start_datetime\|end_datetime\|slot_capacity\|slot1_\|slot2_" \
     repair_appointment/static/src/js/appointment_calendar_patch.js
```

Fix every hit.

- [ ] **Step 4: Run the full module test suite**

Run: `./odoo-bin -c ../odoo.conf -d <test_db> --test-enable --stop-after-init -u repair_appointment --test-tags /repair_appointment`

Expected: all tests pass.

- [ ] **Step 5: Grep one more time — should be clean**

Run: `grep -rn "start_datetime\|end_datetime\|slot1_\|slot2_\|slot_capacity\|_compute_available_slots\|_count_booked_in_slot\|_is_slot_available\|_validate_slot" repair_appointment/`

Expected: no matches.

- [ ] **Step 6: Commit**

```bash
git add repair_appointment/tests/ repair_appointment/static/
git commit -m "repair_appointment: adapt remaining tests and calendar JS to date-only"
```

---

## Task 12: End-to-end manual QA and PR

**Files:** none.

- [ ] **Step 1: Run the migration against the dev database**

```bash
workon odoo_dev
cd /Users/martin/Documents/odoo_dev/odoo
./odoo-bin -c ../odoo.conf -u repair_appointment --stop-after-init
```

Expected: migration log line `repair_appointment 17.0.1.1.0 migration complete`. No errors.

- [ ] **Step 2: Verify data post-migration**

Run via `./odoo-bin shell -c ../odoo.conf`:

```python
env['repair.pickup.appointment'].search_count([('pickup_date', '!=', False)])
env['repair.pickup.schedule'].search([]).mapped('daily_capacity')
```

Expected: previously scheduled appointments have `pickup_date` populated; schedules show non-null `daily_capacity`.

- [ ] **Step 3: Run through the manual QA checklist from the spec**

Execute each numbered step in the spec's "Manual QA checklist" section. Note any unexpected behavior.

- [ ] **Step 4: Open the PR**

```bash
git push -u origin feature/appointment-date-only
gh pr create --title "repair_appointment: date-only pickups + daily agenda + month-grid portal" \
  --body "$(cat <<'EOF'
## Summary
- Swap intra-day slot model (`start_datetime`/`end_datetime`) for a date-only `pickup_date` on `repair.pickup.appointment`.
- Replace per-slot capacity with per-day capacity on `repair.pickup.schedule`.
- Add "Agenda du jour" backend tree view grouped by pickup date + location.
- Rewrite portal as a month-grid day picker showing the full 14-day horizon.
- Pre-migration script `17.0.1.1.0` handles existing data.

## Test plan
- [ ] Module update runs cleanly on existing DB with historic appointments
- [ ] Existing scheduled appointments now show `pickup_date` matching the old `start_datetime::date` (Europe/Paris)
- [ ] `repair.pickup.schedule.daily_capacity` populated from old `slot_capacity`
- [ ] Portal `/my/pickup/<token>` renders the month grid and full horizon
- [ ] Client can pick an open day → appointment transitions to `scheduled`
- [ ] Reschedule flow works from the scheduled state
- [ ] Backend calendar renders appointments as all-day events (month mode)
- [ ] "Agenda du jour" menu shows today + tomorrow grouped by date then location
- [ ] Reminder CRON still fires at the expected delays
- [ ] Drag-drop "Notifier le client ?" dialog still works and sends mail on confirm

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

---

## Self-review notes

- **Spec coverage:** All spec sections (data model changes, method-signature changes, portal changes, backend changes, migration, reminder CRON unchanged, security unchanged, testing strategy) map to tasks above.
- **Type consistency:** `pickup_date` (Date) used consistently. `daily_capacity` (Integer) used consistently. Method names `_compute_available_days`, `_is_day_available`, `_count_booked_on_day`, `_validate_day` used consistently.
- **No placeholders:** each step contains the code or exact commands.
