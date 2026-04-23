# Appointment System — Date-Only Revision

**Revision of:** [`2026-04-11-repair-appointment-design.md`](./2026-04-11-repair-appointment-design.md)
**Date:** 2026-04-23
**Scope:** `repair_appointment` addon (already implemented per the original spec)
**Git branch:** `feature/appointment-date-only`

## Why this revision

After running the original two-slot (15:00–17:15 / 17:15–19:30) design in production, the operator reports:

1. Intra-day slot splitting does not meaningfully spread customer arrivals. The split was a proxy for crowd control, but customers treat the shop's opening hours as the real arrival window anyway.
2. The backend calendar is hard to read — all appointments pile up at one of two timestamps, and the calendar's time axis adds visual noise without operational value.
3. The portal slot selector is awkward and doesn't show the full 14-day booking horizon at a glance.

The real operational need driving the feature is **logistics**: many devices dropped at **Boutique** are repaired at **Ateliers** and must be physically moved back to Boutique the day before a confirmed pickup. What the operator actually needs is a **daily move agenda** — "on day D, these devices must be at location L" — not an hour-precise booking grid.

## The change in one sentence

Replace intra-day datetime slots with **date-only pickups**: the appointment commits a client to a *day*, with a per-day capacity cap per location. Backend gains a daily-agenda tree view; portal becomes a month-grid day picker showing the full horizon.

## Decisions captured

| # | Decision | Why |
|---|---|---|
| R1 | Pickup appointments are **date-only** (`Date`, not `Datetime`). No start/end time stored | Intra-day timing provides no operational value; shop open 15:00–19:30 is the implicit arrival window |
| R2 | Capacity is **per-day per-location** (replaces per-slot). Default 6/day | Simpler mental model; matches how the operator actually reasons about pickup load |
| R3 | Portal UI = **month-grid day picker** showing full 14-day horizon at a glance, single click-to-confirm | Full horizon visible; no awkward time question for the client |
| R4 | Backend gains a **daily agenda** tree view (grouped by `pickup_date`, columns: client, device, category, location, batch) filtered to today + tomorrow by default | This is the logistics surface — staff use it to plan Ateliers → Boutique transfers |
| R5 | Backend calendar view kept, but rendered as **all-day events in month mode** (default). No time axis | Strategic/visual overview of the horizon; complements the daily agenda |
| R6 | Reminder CRON logic unchanged | It keys on `state == 'pending'` and `notification_sent_at`, not on datetime fields |

## Data-model changes

### `repair.pickup.appointment`

**Removed:**
- `start_datetime` (Datetime)
- `end_datetime` (Datetime)

**Added:**
- `pickup_date` (Date, required once `state == 'scheduled'`, null while `pending`)

All other fields (token, batch_id, partner_id, location_id, state, notification_sent_at, last_reminder_sent_at, escalation_activity_id, contacted/contacted_at, reschedule_count, repair_ids, company_id) are unchanged.

### `repair.pickup.schedule`

**Removed:**
- `slot1_start`, `slot1_end`, `slot2_start`, `slot2_end` (Float time_of_day)
- `slot_capacity` (Integer)

**Added:**
- `daily_capacity` (Integer, default 6) — max pickups accepted per open day at this location

Day mask (`monday_open`…`sunday_open`), `location_id`, `active` are unchanged. Default seed data: Mon–Sat open, Sun closed, daily_capacity=6.

### `repair.pickup.closure`

Unchanged.

### `res.config.settings`

`appointment_booking_horizon_days` (default 14) and `appointment_min_lead_days` (default 2) are unchanged. Reminder/escalation delays unchanged.

## Method-signature changes

| Before | After | Notes |
|---|---|---|
| `action_schedule(start_dt, end_dt)` | `action_schedule(pickup_date)` | Takes a single `Date`. Validates day is open at the location, not a closure, not past horizon, not below min-lead, daily cap not reached (modulo staff-bypass context). Transitions `pending→scheduled` or updates the date in-place; increments `reschedule_count`; posts chatter note; closes any open escalation activity |
| `_compute_available_slots(date_from, date_to)` | `_compute_available_days(date_from, date_to)` | Returns one entry per calendar day over the range: `[{date, state: 'open'\|'closed'\|'full'\|'lead_time', remaining_capacity}, ...]` |
| `_is_slot_available(start_dt, end_dt)` | `_is_day_available(pickup_date)` | Boolean per-attempt validation |
| `_count_booked_in_slot(start_dt, location_id)` | `_count_booked_on_day(pickup_date, location_id)` | Counts non-terminal appointments (state in `pending`/`scheduled` considered, but `pending` ones have no date, so effectively counts `scheduled`) on the day at the location |

Other action methods (`action_mark_done`, `action_mark_no_show`, `action_cancel`, `action_mark_contacted`, `action_send_reminder_now`) and the batch hook `action_create_pickup_appointment` are unchanged in signature and semantics.

## Portal changes

### Routes

Route paths are unchanged. Payload changes:

- `GET /my/pickup/<token>/slots` → now returns per-day objects: `[{date: 'YYYY-MM-DD', state: 'open'|'closed'|'full'|'lead_time', remaining_capacity: int}, ...]` covering the full horizon from today through `today + booking_horizon_days`.
- `POST /my/pickup/<token>/book` → body is `{pickup_date: 'YYYY-MM-DD'}` (was `start_datetime`).
- `POST /my/pickup/<token>/reschedule` → same body shape.

### Landing page (state = `pending`)

Single-page month-grid day picker:

```
Bonjour <Client>,

Votre appareil est prêt à être récupéré :
  • <Device 1>
  • <Device 2>

Lieu de retrait : <Boutique / Atelier>
Adresse : <address>
Horaires d'ouverture : lundi–samedi, 15h00 – 19h30

Choisissez votre jour de retrait :

┌───────────────────────────────────────────┐
│            Avril 2026                      │
│  L    M    M    J    V    S    D           │
│                      1    2   [3]          │
│  4    5    6    7    8    9   [10]         │
│ [11] [12] [13] [14] [15] [16] [17]         │
│  18   19   20   21   22   23  [24]         │
│  25   26   27   28   29   30               │
└───────────────────────────────────────────┘
  Fermé    Indisponible    Disponible    Complet
```

**Day cell states:**
- **Fermé** (grey, non-clickable) — schedule mask closed OR matches an active closure
- **Indisponible** (grey, non-clickable) — before `today + min_lead_days` OR past horizon
- **Disponible** (clickable, hover highlight) — open day with remaining capacity
- **Complet** (muted red, non-clickable) — open day but daily cap reached

**Horizon rendering:** render enough consecutive weeks (usually 3) to cover from `today` through `today + booking_horizon_days`. If the horizon spans two calendar months, show both months stacked.

**Click interaction:** clicking a *Disponible* day inlines a confirmation block: *"Confirmer le retrait du **mardi 21 avril 2026** à la Boutique ?"* with `[Valider]` / `[Annuler]`. Validation POSTs to `/book`.

### Landing page (state = `scheduled`)

```
Votre retrait est confirmé :
  📅 Mardi 21 avril 2026
  📍 Boutique – <address>
  🕒 Ouverture de 15h00 à 19h30

Appareils à récupérer :
  • <Device 1>
  • <Device 2>

[Déplacer mon rendez-vous]

Un empêchement ? Contactez-nous : <phone> / <email>
```

Clicking *Déplacer mon rendez-vous* reopens the same month grid with the current `pickup_date` highlighted; selecting a different day triggers `/reschedule`.

### Terminal states

Unchanged — read-only closed-out page with shop contact info.

### Tech stack

Plain QWeb template + CSS grid for the calendar + small `<script>` block performing one `fetch()` to `/slots` on load and another `fetch()` to `/book` on confirm. No JS framework.

## Backend changes

### Calendar view

```xml
<calendar date_start="pickup_date" color="location_id" mode="month">
  ...
</calendar>
```

- No `date_stop` → Odoo renders each appointment as an **all-day event** on its `pickup_date`.
- Default mode `month`; `week` and `day` still available.
- Color by `location_id`.
- Same tile content, same popover, same drag/drop rules as the original spec. Drag-drop moves the `pickup_date` by whole days; "Notifier le client ?" dialog flow is unchanged.
- Default search filter: `state in ('pending','scheduled')`.

### New daily agenda tree view

New tree view and menu entry *"Agenda du jour"*, visible to all repair users:

- Model: `repair.pickup.appointment`
- Default domain: `state == 'scheduled' AND pickup_date in (today, today + 1 day)`
- Default group-by: `pickup_date`, then `location_id`
- Columns: `partner_id`, first repair's `device_id`, first repair's device category, `location_id`, `batch_id`, `reschedule_count`
- Tree decoration: bold for today, muted for tomorrow
- Accessible from a new "Agenda du jour" submenu entry under the existing "Rendez-vous" menu

Two helper computed fields on `repair.pickup.appointment` to power the tree columns (stored, depends on `repair_ids`):

- `primary_device_id` — M2O `repair.device` — first repair's device; display-only
- `primary_device_category_id` — M2O `repair.device.category` — first repair's device category; display-only

If the batch has multiple devices, the tree shows the first one and a "+N" suffix (or the list view can be expanded per-row).

### Menu structure

Under the existing *"Rendez-vous"* top-level menu:

- **Agenda** — calendar view (month default) *(existing menu, retitled)*
- **Agenda du jour** — daily agenda tree *(new)*
- **Tous les rendez-vous** — full tree view *(existing)*
- **Configuration** *(admin only — unchanged)*

### Form view

Replace the `start_datetime` / `end_datetime` fields in the form with a single `pickup_date` field. Everything else unchanged.

### Tree view (full list)

Replace `start_datetime` column with `pickup_date`. Other columns unchanged.

### Search view

Filter labels adjusted where they referenced time-of-day. Add a "Aujourd'hui" filter on `pickup_date == today` and "Cette semaine" on `pickup_date in current_week`. "À contacter" (has escalation activity), "En attente de créneau" (`state=pending`), "Confirmés" (`state=scheduled`) unchanged.

## Migration of existing data

The module is already installed and has live appointments. A data migration script is required:

### `migrations/17.0.1.1.0/pre-migration.py`

For each existing `repair.pickup.appointment`:
- If `start_datetime` is set: `pickup_date = start_datetime.date()` (timezone-aware: convert from UTC to `company_id.partner_id.tz` or fall back to Europe/Paris before taking the date).
- If `start_datetime` is null (pending appointments): `pickup_date` stays null.
- Drop `start_datetime` and `end_datetime` columns after the copy.

For each existing `repair.pickup.schedule`:
- `daily_capacity = slot_capacity` (preserve the operator's configured capacity as the new daily cap; operator can bump to 6 after install if desired).
- Drop `slot1_start`, `slot1_end`, `slot2_start`, `slot2_end`, `slot_capacity` columns.

Manifest version bumped to `17.0.1.1.0` to trigger the migration on module update.

## Reminder CRON

**Unchanged.** The CRON filters on `state == 'pending' AND notification_sent_at IS NOT NULL` and computes deltas against `notification_sent_at` / `last_reminder_sent_at` / `contacted_at`. None of those fields change in this revision.

## Security

Unchanged.

## Testing strategy

### Updated tests

| Test file | Change |
|---|---|
| `test_appointment_model.py` | Adapt state-transition tests to `pickup_date` (Date) instead of datetime |
| `test_slot_availability.py` | **Rename** to `test_day_availability.py`; rewrite around `_compute_available_days` and `_is_day_available`. Keep coverage: closed day (Sunday), closure date, daily cap reached, min lead time respected, horizon respected |
| `test_reminder_cron.py` | No functional change (CRON logic unchanged); update fixtures that set `start_datetime` to set `pickup_date` |
| `test_portal_controller.py` | Update payloads: `{pickup_date}` instead of `{start_datetime}`; update race test to be "two clients book the last remaining day slot at the same location" |
| `test_batch_integration.py` | No change |

### New tests

| Test file | Covers |
|---|---|
| `test_migration.py` | Given a pre-migration DB snapshot with `start_datetime`/`end_datetime` populated, run the migration script and assert `pickup_date` is populated with correct timezone handling. Skipped in CI if snapshot not available; manual run only |
| `test_daily_agenda_view.py` | Action/domain returned by the "Agenda du jour" menu filters correctly to today + tomorrow; group-by order correct |

### Manual QA checklist

1. Update module; verify migration ran (existing appointments show `pickup_date`, no datetime fields)
2. Verify `repair.pickup.schedule` records show `daily_capacity` populated from old `slot_capacity`
3. Open portal token for a pending appointment → month grid renders, full 14-day horizon visible
4. Click a day → confirmation inline → `[Valider]` → appointment scheduled, chatter note posted
5. Reopen portal for the scheduled appointment → "Déplacer" button works, picks another day in-place
6. Backend calendar → month view shows all-day pills on correct dates
7. Drag-drop to move a day → "Notifier le client ?" dialog → mail sent on confirm
8. Open "Agenda du jour" → shows today + tomorrow's pickups grouped by date then location
9. Book enough pickups on one day/location to hit `daily_capacity` → day renders as "Complet" on portal
10. Back-date `notification_sent_at` to trigger reminder CRON → reminder mail sent, then escalation activity

## Out of scope for this revision

- No changes to sub-project 2 (quote lifecycle) or sub-project 3 (completion → pickup → invoice → SAR) design. Sub-project 3 will still call `batch.action_create_pickup_appointment()` and `appointment.action_mark_done()`; those signatures are unchanged.
- No changes to auth (token-only), security groups, or multi-company handling.
- No SMS, no portal cancellation, no auto-no-show — all still deferred.

## Open questions

None — all decisions settled during brainstorming.
