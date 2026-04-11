# Repair Appointment System — Design Spec

**Sub-project 1 of 3** in the repair workflow overhaul.
**Date:** 2026-04-11
**Target module:** new `repair_appointment` addon
**Depends on:** `repair_custom`, `mail`, `portal`

## Context and scope

The repair shop needs a pickup appointment system for clients whose devices are ready to leave the shop (repair done, quote refused, or device abandoned). Today, notification and pickup scheduling are ad-hoc. This sub-project introduces a first-class appointment entity with a client-facing portal, a backend calendar for staff, and an automated reminder workflow, all wired to the existing `repair.batch` (deposit dossier) model.

This is the first of three sub-projects:

1. **Appointment system** (this spec) — scheduling infrastructure, reusable by sub-projects 2 and 3.
2. **Quote lifecycle automation** — tech-requested quotes, manager review, client notification, CRON reminders, refusal handling; refused quotes will use this sub-project's portal for device pickup.
3. **Completion → pickup → invoice → SAR** — finishing work, notifying clients with repair summary, processing the physical pickup, opening the sale order, invoicing, starting the SAR warranty. Depends on both 1 and 2.

### Out of scope for this sub-project

- Drop-off appointments, general consultations, walk-in booking — **pickup-only**.
- SMS notifications. Dropped for v1 because the shop runs Odoo Community (no IAP subscription). All client notifications are **mail-only**.
- Creating the initial "ready for pickup" notification. Sub-project 3 owns that flow; this sub-project only provides the infrastructure (model, CRON, portal, mail template for the reminder).
- No-show resolution logic beyond a manual staff button. No automatic no-show detection.
- Cancellation from the portal. Clients can reschedule but not cancel outright; cancellation is a staff-only manual action.

## Decisions captured from brainstorming

| # | Decision | Why |
|---|---|---|
| 1 | Pickup-only scope | Matches the user's operational need; drop-offs stay informal during open hours |
| 2 | Fixed capacity per slot, configurable setting (default 3) | Realistic throughput (5–15 min per pickup) without artificial blocking |
| 3 | Location follows the repair's `pickup_location_id` | No choice shown to client — you pick up where you dropped off |
| 4 | Weekly schedule per location + ad-hoc closure dates | Covers "closed Sundays + August vacation" without manual slot management. Shop is open Monday–Saturday afternoons |
| 5 | Token magic link authentication (no account) | Reuses existing `tracking_token` pattern; friction-free client UX |
| 6 | One RDV per batch (batch discipline is enforced by the team) | Clean data model; a standalone repair gets wrapped in a singleton batch |
| 7 | Configurable booking horizon, default 14 days; reschedule allowed (in-place datetime update), no portal cancellation | Balance of flexibility and control |
| 8 | States: `pending`, `scheduled`, `done`, `no_show`, `cancelled`. No auto no-show. In-place reschedule | Cleanest lifecycle; relies on staff discipline without over-engineering |
| 9 | All repair users can view the calendar; managers/admins edit. Staff manual booking bypasses all rules (warnings only, no hard blocks). Drag-drop shows a "Notifier le client?" confirmation dialog | Trust staff judgment while preserving client communication control |
| 10 | 1 reminder → then escalation activity. Two global delay settings (reminder delay, escalation delay), default 3 days each. Escalation assigned to the whole manager group. "Contacté" resets only the escalation clock (no new mail) | Matches the original workflow brief; keeps client mail volume low |
| 11 | Mail only; no SMS (Odoo Community constraint) | Operational |
| 12 | Staff visibility into portal events: chatter notes only, no activities or mass mails | Calendar is the primary staff surface; chatter gives an audit trail |

## Architecture

**Separate addon: `repair_appointment`**, depending on `repair_custom`, `mail`, `portal`. Owns the appointment model, schedule/closure configuration, portal controller, backend calendar view, reminder CRON, mail template for the reminder, and integration hooks for sub-project 3.

Rationale: matches the existing two-module discipline (`repair_devices` + `repair_custom`), isolates appointment concerns from repair state concerns, gives sub-project 3 a clean API to integrate with later.

### Module layout

```
repair_appointment/
├── __manifest__.py                 # depends: repair_custom, mail, portal
├── __init__.py
├── models/
│   ├── __init__.py
│   ├── repair_pickup_appointment.py    # main model
│   ├── repair_pickup_schedule.py       # weekly pattern per location
│   ├── repair_pickup_closure.py        # ad-hoc closure dates
│   ├── repair_batch.py                 # inherit, add appointment_ids O2M + helper
│   └── res_config_settings.py          # configurable delays, horizon, capacity
├── controllers/
│   └── portal.py                       # /my/pickup/<token> routes
├── views/
│   ├── appointment_views.xml           # tree / form / calendar / search
│   ├── schedule_views.xml              # form / tree for schedules + closures
│   ├── repair_batch_views.xml          # inherit to show appointment_ids
│   ├── portal_templates.xml            # QWeb: pickup booking page
│   ├── res_config_settings_views.xml   # settings UI
│   └── menus.xml                       # "Rendez-vous" menu under repair module
├── data/
│   ├── mail_templates.xml              # reminder mail template
│   ├── mail_activity_type_data.xml     # custom "Client à contacter" activity type
│   ├── ir_cron.xml                     # reminder CRON
│   ├── pickup_schedule_data.xml        # default Mon-Sat schedule per location (noupdate=1)
│   └── ir_sequence.xml                 # appointment reference sequence
├── security/
│   ├── ir.model.access.csv
│   └── appointment_security.xml        # record rules (manager-only write)
├── tests/
│   ├── __init__.py
│   ├── common.py
│   ├── test_appointment_model.py
│   ├── test_slot_availability.py
│   ├── test_reminder_cron.py
│   ├── test_portal_controller.py
│   └── test_batch_integration.py
└── i18n/
    └── fr.po                           # all labels in French
```

## Data model

### `repair.pickup.appointment`

First-class entity representing a single client pickup slot. Inherits `mail.thread` and `mail.activity.mixin`.

| Field | Type | Notes |
|---|---|---|
| `name` | Char | Auto-generated ref (e.g., `RDV/2026/00042`) via `ir.sequence` |
| `batch_id` | M2O `repair.batch` | Required; the deposit dossier this pickup covers |
| `partner_id` | M2O `res.partner` | Related to `batch_id.partner_id`, stored |
| `location_id` | M2O `repair.pickup.location` | Computed from the first repair's `pickup_location_id`, stored. If the batch has no repairs with a location (shouldn't happen in practice), falls back to the first active `repair.pickup.location` record rather than hardcoding "Boutique" |
| `state` | Selection | `pending`, `scheduled`, `done`, `no_show`, `cancelled`. Default: `pending` |
| `start_datetime` | Datetime | Slot start (null while `pending`) |
| `end_datetime` | Datetime | Slot end (null while `pending`) |
| `token` | Char | UUID4, unique, readonly, auto-generated at create |
| `notification_sent_at` | Datetime | When initial "ready for pickup" mail was sent (set by sub-project 3) |
| `last_reminder_sent_at` | Datetime | When the reminder was last sent by the CRON |
| `escalation_activity_id` | M2O `mail.activity` | Computed, stored. Resolves to the first open activity of type `activity_pickup_to_contact` on this record (via `activity_ids` filter); falls back to False. Stored so it's queryable from search views and the CRON |
| `contacted` | Boolean | Manager clicked "contacté" — used to reset escalation clock |
| `contacted_at` | Datetime | Timestamp of the above, for CRON math |
| `repair_ids` | O2M related | Related through `batch_id.repair_ids`, display only |
| `reschedule_count` | Integer | Incremented on every datetime change, for audit |
| `company_id` | M2O `res.company` | Standard multi-company field |

### `repair.pickup.schedule`

Weekly availability pattern per location. Seeded on module install for both Boutique and Atelier with a Monday–Saturday open / Sunday closed mask.

| Field | Type | Notes |
|---|---|---|
| `location_id` | M2O `repair.pickup.location` | Required, unique per location |
| `monday_open` ... `sunday_open` | Boolean | Day mask (default: Mon–Sat true, Sun false) |
| `slot1_start` / `slot1_end` | Float (time_of_day widget) | Default 15.00 / 17.25 |
| `slot2_start` / `slot2_end` | Float (time_of_day widget) | Default 17.25 / 19.50 |
| `slot_capacity` | Integer | Default 3, per-slot hard cap |
| `active` | Boolean | Standard archive pattern |

### `repair.pickup.closure`

Ad-hoc closure dates (public holidays, vacations, exceptional closures).

| Field | Type | Notes |
|---|---|---|
| `name` | Char | Label (e.g., "Congés août") |
| `location_id` | M2O `repair.pickup.location` | Nullable — if null, applies to all locations |
| `date_from` | Date | Inclusive start |
| `date_to` | Date | Inclusive end |
| `active` | Boolean | Standard archive pattern |

### `res.config.settings` extension

All four settings stored via `ir.config_parameter` (standard Odoo `config_parameter` field attribute).

| Setting | Type | Default | Purpose |
|---|---|---|---|
| `appointment_booking_horizon_days` | Integer | 14 | Max window visible on the portal |
| `appointment_min_lead_days` | Integer | 2 | Minimum days between today and the earliest bookable slot |
| `appointment_reminder_delay_days` | Integer | 3 | Days after initial notification before reminder is sent |
| `appointment_escalation_delay_days` | Integer | 3 | Days after reminder before escalation activity is created |

### `repair.batch` extension

| Field | Type | Notes |
|---|---|---|
| `appointment_ids` | O2M | Inverse of `batch_id` on appointment |
| `current_appointment_id` | M2O computed | The one non-terminal appointment (`pending` or `scheduled`), if any |

Enables the batch form to display a smart button to the current appointment and a manual "create appointment" button for edge cases.

## State machine

```
           (sub-project 3 creates)
                     │
                     ▼
                 ┌────────┐
                 │pending │───────────────┐
                 └───┬────┘               │
   action_schedule() │                    │ action_cancel()
      (client picks  │                    │  (staff, rare)
       from portal   │                    │
       or staff adds │                    │
       manually)     │                    │
                     ▼                    ▼
                 ┌──────────┐         ┌──────────┐
          ┌──────│scheduled │         │cancelled │ (terminal)
          │      └─┬──┬─────┘         └──────────┘
          │        │  │
          │        │  │  action_schedule() again = in-place datetime update
          │        │  │  (reschedule_count++, chatter note)
          │        │  │
          │        │  │ action_cancel()
          │        │  ▼
          │        │ (cancelled)
          │        │
action_mark_       │ action_mark_no_show()
  done()           ▼
(sub-proj 3)    ┌────────┐
          │     │no_show │ (terminal)
          ▼     └────────┘
      ┌─────┐
      │done │ (terminal)
      └─────┘
```

### Public methods on `repair.pickup.appointment`

| Method | Purpose | Who calls it |
|---|---|---|
| `action_schedule(start_dt, end_dt)` | Validates slot (capacity, closure, min lead time — honoring staff bypass via context), transitions `pending→scheduled` or updates datetime in-place, increments `reschedule_count`, posts chatter note, marks any open escalation activity as done | Portal controller (public), backend calendar drag/drop, manual form edit |
| `action_mark_done()` | Validates current state is `scheduled`, transitions to `done`, posts chatter | Sub-project 3 when pickup is processed |
| `action_mark_no_show()` | `scheduled→no_show`, creates manager activity "Client absent – à recontacter" | Manual staff button on appointment form |
| `action_cancel()` | Any non-terminal → `cancelled`, posts chatter | Manual staff button (confirmation dialog) |
| `action_mark_contacted()` | Marks **all** sibling escalation activities as done across the manager group, sets `contacted=True` and `contacted_at=now()`. No mail is sent | Manual manager button shown when an escalation activity is open |
| `action_send_reminder_now()` | Forces the reminder mail independent of CRON | Optional manual button for managers |

### Helper methods (internal)

| Method | Returns | Notes |
|---|---|---|
| `_compute_available_slots(date_from, date_to)` | List of `{datetime_start, datetime_end, remaining_capacity}` | Used by portal to render the selector; respects schedule, closures, min lead time, capacity |
| `_is_slot_available(start_dt, end_dt)` | Boolean | Per-attempt validation during `action_schedule` |
| `_count_booked_in_slot(start_dt, location_id)` | Integer | Counts `scheduled` appointments in the same slot; excludes `cancelled`/`no_show`/`done` |

## Integration hooks for sub-project 3

Sub-project 1 exposes one method on `repair.batch` that sub-project 3 will call:

```python
def action_create_pickup_appointment(self, notify=True):
    """
    Create a pending appointment for this batch and (optionally) send
    the initial "ready for pickup" mail with the token link.
    Idempotent: if a non-terminal appointment already exists, returns it.
    Called by sub-project 3 when repairs in the batch move to 'done'.
    """
```

Sub-project 3 will also call `appointment.action_mark_done()` when the physical pickup is processed. Those are the only two touch points between modules.

For independent testing of sub-project 1 before sub-project 3 is built: the appointment form exposes a developer-only smart button "Renvoyer la notification initiale" (visible to `group_repair_admin`) to manually trigger initial notifications during QA.

## Reminder CRON

**Frequency:** hourly (`ir.cron` with `interval_number=1, interval_type='hours'`). Hourly gives near-daily resolution without tight coupling to the wall clock. All time comparisons use day-granularity deltas from the settings.

**Algorithm (pseudocode):**

```python
def _cron_process_pending_appointments(self):
    today = fields.Datetime.now()
    reminder_delay = settings.appointment_reminder_delay_days
    escalation_delay = settings.appointment_escalation_delay_days

    pending = self.search([('state', '=', 'pending'),
                           ('notification_sent_at', '!=', False)])

    for apt in pending:
        # Phase 1: the single reminder mail
        if (not apt.last_reminder_sent_at
                and not apt.contacted
                and today >= apt.notification_sent_at + timedelta(days=reminder_delay)):
            apt._send_reminder_mail()
            apt.last_reminder_sent_at = today
            continue

        # Phase 2: escalation activity
        if apt.escalation_activity_id:
            continue  # still open, wait for manager action

        if apt.contacted:
            # After "contacté" click, wait escalation_delay from contacted_at
            if today >= apt.contacted_at + timedelta(days=escalation_delay):
                apt._create_escalation_activity()
                apt.contacted = False  # consume the "contacted" flag
        elif apt.last_reminder_sent_at:
            if today >= apt.last_reminder_sent_at + timedelta(days=escalation_delay):
                apt._create_escalation_activity()
```

**Stop conditions:** the CRON query filters on `state == 'pending'`, so any transition (to `scheduled`, `cancelled`) automatically drops the appointment from processing. When `action_schedule` fires, it also marks any open escalation activity as done, so the activity feed stays clean.

### Escalation activity behavior

Odoo's `mail.activity.user_id` is a single-user M2O — there is no native group-assignment mechanism. "Whole manager group" is implemented as:

- Loop over `group_repair_manager.users` and create **one activity per manager** (N activities for N users)
- Each manager sees it in their personal activity list
- When any one of them clicks "Contacté," `action_mark_contacted` joins sibling activities by `(res_id, res_model, activity_type_id, create_date)` and marks **all** as done so the others don't see stale entries

**Activity metadata:**
- `activity_type_id` — a custom type `repair_appointment.activity_pickup_to_contact` defined in `data/mail_activity_type_data.xml` (not the built-in "Appel à faire," so we have full control over the label, icon, and can filter on it unambiguously from the search view + the `escalation_activity_id` compute)
- `summary` — "Client à contacter — RDV retrait non pris"
- `note` — link to the appointment + the batch + the client's phone number

## Portal flow (client-facing)

### Routes

| Route | Auth | Purpose |
|---|---|---|
| `GET /my/pickup/<string:token>` | `public` | Landing page: shows client name, batch reference, device list, current appointment state, and either the slot picker (if `pending`) or the scheduled datetime (if `scheduled`). Validates token; 404 on mismatch |
| `GET /my/pickup/<string:token>/slots` | `public` | JSON endpoint returning available slots for the next `booking_horizon_days` days. Response: `[{start, end, label, available}, ...]`. Called via `fetch()` from the landing page |
| `POST /my/pickup/<string:token>/book` | `public` | Body: `{start_datetime}`. Calls `appointment.action_schedule(...)`. On success, redirects to confirmation. On slot-taken race, returns the landing page with an error banner "Ce créneau n'est plus disponible, veuillez en choisir un autre." |
| `POST /my/pickup/<string:token>/reschedule` | `public` | Same as `/book` but only allowed when `state == 'scheduled'`. Updates the datetime in-place, increments `reschedule_count`, posts chatter note "RDV déplacé par le client du X au Y." |
| `GET /my/pickup/<string:token>/confirmation` | `public` | Static confirmation page with date, location, address, and (if reschedule is still allowed) a "Déplacer mon rendez-vous" button |

All routes bypass Odoo's `/my` portal user requirement (`auth='public'`). Authorization is entirely via token matching — no session, no cookies needed. Controllers call `action_schedule` through `sudo()`.

### Token security

- Token is a UUID4, generated once at appointment create, unique constraint
- 404 (not 403) on mismatch — don't leak existence
- Token is never regenerated (keeps existing notification links valid)
- No expiry — the appointment state itself gates what actions are possible (terminal states show a read-only page)
- Every portal action is logged to the appointment chatter with `request.httprequest.remote_addr` for audit

### Portal page states

**State = `pending` (slot not yet picked):**

```
Bonjour <Client>,

Votre appareil est prêt à être récupéré :
  • <Device 1>
  • <Device 2>

Lieu de retrait : <Boutique / Atelier>
Adresse : <address>

Choisissez un créneau de retrait :
[Calendrier — 2 semaines à l'avance, à partir de J+2, jours fermés grisés]

Sélectionnez un jour puis choisissez :
  ◯ 15h00 – 17h15   (X places restantes)
  ◯ 17h15 – 19h30   (Y places restantes)

[Valider mon rendez-vous]
```

**State = `scheduled`:**

```
Votre rendez-vous est confirmé :
  📅 Mardi 21 avril 2026
  🕒 15h00 – 17h15
  📍 Boutique – <address>

Appareils à récupérer :
  • <Device 1>
  • <Device 2>

Un changement d'agenda ?
[Déplacer mon rendez-vous]

(pas de possibilité d'annulation depuis cette page —
 contactez-nous si nécessaire : <phone> / <email>)
```

**States `done` / `cancelled` / `no_show`:** read-only closed-out page with shop contact info. No actions.

### Slot selector mechanics

- Shows the next `booking_horizon_days` days starting from `today + min_lead_days` (J+2)
- Sundays and closure dates rendered with a "Fermé" badge, non-clickable
- For each open day, the two slots are shown with remaining capacity; slot disabled at 0
- Reschedule from the `scheduled` state uses the same UI with the current RDV highlighted
- Minimum lead time for reschedule: same as booking — ≥ J+2 from **today**, not from the current RDV date
- No JavaScript framework — plain QWeb template + small `<script>` for the `fetch()` call

### Mail template shipped by this sub-project

Only the **reminder** mail template belongs here. The initial "ready for pickup" notification template belongs to sub-project 3.

**`mail_template_pickup_reminder`**
- Subject: *"Rappel : votre appareil est prêt à être récupéré"*
- Body: polite reminder, device list, portal link `{{ object._portal_url() }}`, shop opening hours
- Uses `{{ object }}` = appointment record; `mail.template` standard Jinja rendering

## Backend views

### Menu placement

New top-level menu entry *"Rendez-vous"* under the existing repair module, visible to anyone with repair access.

### Calendar view (default on the menu)

- Calendar view on `repair.pickup.appointment`, mode: `week` (default) + `month` + `day`
- `date_start="start_datetime"`, `date_stop="end_datetime"`
- Color by `location_id` (Boutique vs Atelier — two colors)
- Tile content: `<Partner> — <Batch ref> (<repair_count>)`
- Hover popover: full device list + state badge
- Group-by filters in search panel: `location_id`, `state`, `partner_id`
- Default search filter: `state in ('pending','scheduled')` (hide terminal states)
- Drag/drop enabled only for `group_repair_manager` + `group_repair_admin` (enforced via access rules)
- On drag/drop: JavaScript intercepts the drop to show a "Notifier le client ?" confirmation dialog. If yes, the `write()` triggers `action_schedule` which sends the reschedule mail; if no, a context `{'skip_reschedule_notification': True}` suppresses the mail in the overridden `write()`

### Appointment form view

**Header:** state bar (`pending`, `scheduled`, `done`, `no_show`, `cancelled`) + action buttons:
- `Marquer terminé` — visible when `scheduled`, calls `action_mark_done`
- `Marquer absent` — visible when `scheduled`, calls `action_mark_no_show`
- `Annuler` — visible when not terminal, calls `action_cancel` with confirmation
- `Contacté` — visible only when `escalation_activity_id` is set, calls `action_mark_contacted`
- `Renvoyer la notification initiale` — admin-only developer button for testing

**Smart button:** "Dossier de dépôt" → open linked `repair.batch`

**Main:** `batch_id`, `partner_id` (readonly), `location_id` (readonly), `start_datetime`, `end_datetime`, `state`, `reschedule_count`

**Tab "Appareils":** list of `repair_ids` (readonly, related via batch)

**Tab "Suivi":** `notification_sent_at`, `last_reminder_sent_at`, `contacted`, `contacted_at`, `token` (admin-only)

**Chatter** at the bottom (mail.thread).

### Appointment tree view

- Columns: `name`, `partner_id`, `batch_id`, `location_id`, `start_datetime`, `state`, `reschedule_count`
- Decoration: red for `no_show`, muted for `cancelled`/`done`, bold for `pending`

### Search view

- Filters: "En attente de créneau" (`state=pending`), "Confirmés" (`state=scheduled`), "À contacter" (has escalation activity), "Aujourd'hui", "Cette semaine"
- Group-by: `location_id`, `state`, `partner_id`

### Batch form inheritance

Add a smart button on `repair.batch`:
- If `current_appointment_id` exists: "Voir le rendez-vous" → open appointment form
- If not: action button "Créer un rendez-vous manuellement" (for edge cases — calls `action_create_pickup_appointment` with `notify=False`)

### Schedule + closure views

Small form/tree views under *"Configuration → Retraits → Horaires"* and *"Configuration → Retraits → Fermetures"*. Admin-only. Standard Odoo tree/form widgets.

## Security

### Model access (`ir.model.access.csv`)

| Model | Group | Read | Write | Create | Delete |
|---|---|---|---|---|---|
| `repair.pickup.appointment` | `repair_custom.group_repair_technician` | ✓ | | | |
| `repair.pickup.appointment` | `repair_custom.group_repair_manager` | ✓ | ✓ | ✓ | |
| `repair.pickup.appointment` | `repair_custom.group_repair_admin` | ✓ | ✓ | ✓ | ✓ |
| `repair.pickup.schedule` | `repair_custom.group_repair_admin` | ✓ | ✓ | ✓ | ✓ |
| `repair.pickup.schedule` | others (tech + manager) | ✓ | | | |
| `repair.pickup.closure` | `repair_custom.group_repair_admin` | ✓ | ✓ | ✓ | ✓ |
| `repair.pickup.closure` | others (tech + manager) | ✓ | | | |

### Portal controller

Runs specific operations as `sudo()` (token lookup, `action_schedule`). Token validation is the entire auth — no groups checked on the public endpoints.

### Record rules

Multi-company rule on `company_id` (standard pattern). No user-restriction rules; all repair users see all appointments in their company.

## Settings UI

New section under *Paramètres → Réparation → Rendez-vous de retrait*:

```
Horizon de réservation          [14] jours
  (fenêtre maximum visible sur le portail client)

Délai minimum avant RDV         [ 2] jours
  (le client ne peut pas réserver avant J+N)

Délai avant rappel              [ 3] jours
  (envoi du rappel si le client n'a pas réservé)

Délai avant escalade            [ 3] jours
  (création de l'activité "à contacter")
```

## Testing strategy

### Unit tests (`repair_appointment/tests/`)

| Test file | What it covers |
|---|---|
| `test_appointment_model.py` | Appointment creation, token generation uniqueness, computed fields, state transitions, every `action_*` method |
| `test_slot_availability.py` | `_compute_available_slots` with closed day (Sunday), closure date, capacity full, partially full, min lead time respected, horizon respected |
| `test_reminder_cron.py` | CRON behavior: reminder after N days, escalation after reminder + N days, activity de-dup across manager group, `contacted` flag reset + re-escalation cycle, stop condition when state transitions |
| `test_portal_controller.py` | Happy path book, reschedule, slot-taken race (concurrent booking), invalid token 404, wrong state rejection, chatter note posted |
| `test_batch_integration.py` | `repair.batch.action_create_pickup_appointment()` idempotency, hook into sub-project 3 mock |

**Test data:** `tests/common.py` fixture providing a `repair.batch` with two repairs, Boutique location, default schedule, no closures. Uses `freezegun` or manual `fields.Datetime.now()` patching for CRON time travel.

### Manual QA checklist

1. Install module; verify default schedules seeded for Boutique + Atelier
2. Create a test batch; run `action_create_pickup_appointment(notify=False)`
3. Open the portal URL with the generated token; verify the slot picker shows the right window
4. Book a slot; verify it appears on the backend calendar
5. Reschedule from the portal; verify chatter note + updated datetime
6. Drag-drop in the backend calendar; verify confirmation dialog + mail sent
7. Back-date `notification_sent_at` to trigger the reminder + escalation via CRON
8. Click "Contacté" as one manager; verify all N sibling activities are marked done
9. Verify terminal states make the portal read-only

## Git branch

`feature/repair-appointment` from `main`. All sub-project 1 work happens on this branch.

## Open questions deferred to sub-project 3

- Exact content of the initial "ready for pickup" mail (repair summary, intervention details, validated quote attachment, etc.)
- Whether the physical pickup action (marking an RDV done) should be one button or a multi-step wizard
- How SAR warranty start interacts with the appointment `done` transition
