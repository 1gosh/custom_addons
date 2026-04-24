# Manual QA Checklist — Pre-Production Deploy
**Date assembled:** 2026-04-24
**Scope:** 9 feature branches merged since 2026-04-11
**Target environment:** Staging clone of production, then production cutover

---

## 0. Pre-Flight (before touching anything)

- [ ] Full production DB dump + filestore backup taken and restore tested on staging
- [ ] Staging DB is a recent clone of production (not a demo DB) — real client data, real lots, real batches
- [ ] `workon odoo_dev` activated; `./odoo-bin -c ../odoo.conf -u repair_custom,repair_devices --stop-after-init` runs cleanly on staging with zero tracebacks
- [ ] Pre-migration audit logs reviewed:
  - [ ] `serial_number ≠ lot.name` divergences listed (Feature 9)
  - [ ] Appointments with null `start_datetime` listed (Feature 8)
  - [ ] Repairs without `batch_id` listed (Feature 6 / Feature 2 wrapping)
- [ ] Post-upgrade spot checks:
  - [ ] `stock.lot` rows for every draft repair with a legacy serial_number
  - [ ] `repair.appointment.pickup_date` populated; `start_datetime` column dropped
  - [ ] `ir.config_parameter` entries exist for `sar_warranty_months`, `sav_warranty_months`, `auto_validate_equipment_sale`, `pickup_daily_capacity`, reminder/escalation delays
  - [ ] Active CRONs: `ir_cron_repair_quote_process`, `ir_cron_pickup_reminder`
- [ ] Outgoing mail server set to a sink on staging (no real client mails)
- [ ] Portal base URL set correctly (tokens in mail must resolve on staging domain)

---

## 1. Warranty Toasts & Settings (Feature 4 — 2026-04-13)

### Toasts on repair form
- [ ] Select lot with **active SAV** (sold recently) → blue toast "Garantie SAV. Garantie jusqu'au DD/MM/YYYY (Vendu le DD/MM/YYYY)" appears bottom-right, auto-dismisses ~3 s, non-blocking
- [ ] Select lot with **active SAR** (repaired recently, sar_expiry in future) → toast mentions technician name and repair date
- [ ] Select lot with **expired SAR/SAV** → toast "Hors Garantie. Cet appareil a déjà été réparé par <tech> le DD/MM/YYYY (Garantie expirée)"
- [ ] Select lot with **no history** → no toast at all (silent)
- [ ] Select lot whose last technician has no Name set → toast fallback "Inconnu"

### Stock lot form
- [ ] "Garantie" tab shows `last_technician_id` (readonly when set)
- [ ] SAV sale date, SAR expiry, last delivered repair all visible

### Settings (Paramètres → Réparation)
- [ ] Section "Garantie" shows SAR months (default 3) and SAV months (default 12), both editable, persist on save
- [ ] Section "Ventes Équipement" has "Valider automatiquement les livraisons équipement" toggle, persists
- [ ] Change SAR to 6 months → stamp a new repair → verify `sar_expiry = delivered_at + 6 months`

---

## 2. Category Short Name (Feature 5 — 2026-04-15)

- [ ] Inventory → Configuration → Product Categories → form has **"Abréviation"** field right after Name
- [ ] Create parent "Audio" (abbr "AUD"), child "Amplificateurs" (abbr "AMP") → repair label shows **"AUD / AMP"**
- [ ] Clear child abbreviation → label shows **"AUD / Amplificateurs"** (fallback)
- [ ] Clear both abbreviations → label shows full names
- [ ] Top-level category (no parent) → label shows only self
- [ ] 3-level hierarchy (GrandParent → Parent → Self) → label shows only last two, grandparent hidden
- [ ] Print a repair label PDF → visually confirm abbreviation display

---

## 3. Device / Lot Edit Propagation (Feature 9 — 2026-04-23)

### Intake field
- [ ] On a draft repair, `lot_id` Many2one replaces the old `serial_number` Char
- [ ] Autocomplete scoped to the selected product template (other products' lots not suggested)
- [ ] Type a **brand-new serial** and tab out → new `stock.lot` created on the fly, linked to current product; `is_hifi_unit=True` on the new lot
- [ ] External-link arrow on `lot_id` opens the lot form
- [ ] After confirmation, `lot_id` becomes readonly
- [ ] Tree view `lot_id` column shows the serial, searchable via the search bar

### Live propagation (the point of this refactor)
- [ ] Open a confirmed repair; edit the linked `product.template.name` in a second tab → refresh repair tree → device column reflects new name (no recompute button)
- [ ] Edit `product.template.categ_id` → category_short_name on repair label regenerates correctly on next print
- [ ] Edit `stock.lot.name` → repair tree serial column updates live
- [ ] No `serial_number` Char field exists on `repair.order` anymore (verify via developer mode or `ir.model.fields`)

### Reports
- [ ] Repair label PDF: serial reads from `lot_id.name`
- [ ] Repair ticket PDF: same
- [ ] Batch section header in consolidated invoice: S/N pulled from `lot_id.name`

### Migration edge cases
- [ ] Pre-migration: a draft repair with `serial_number="ABC123"` but no lot → post-migration, a `stock.lot` named "ABC123" linked to the same product, `is_hifi_unit=True`
- [ ] Legacy `hifi_inventory_wizard.serial_number` Char still present (NOT removed — different model)
- [ ] `repair_devices/migrations/` legacy scripts untouched

---

## 4. Appointment System — Date-Only (Features 1 + 8 — 2026-04-11 / 2026-04-23)

### Backend calendar
- [ ] Menu **Rendez-vous** → calendar opens in month view by default, showing non-terminal appointments as all-day pills
- [ ] Tile label format: `<Partner> — <Batch ref> (<N devices>)`
- [ ] Tiles color-coded by location (Boutique vs Atelier visually distinct)
- [ ] Hover popover shows full device list + state badge
- [ ] Drag-drop an appointment to another day (as manager) → "Notifier le client ?" dialog → **Oui** posts chatter "RDV déplacé du X au Y" and sends reschedule mail; **Non** silently reassigns
- [ ] Filters present: "En attente de créneau", "Confirmés", "À contacter"
- [ ] Group-by: location, state, partner

### Agenda du jour (daily tree)
- [ ] Menu **Rendez-vous → Agenda du jour** opens tree
- [ ] Default domain: today + tomorrow, `state=scheduled`
- [ ] Grouped by `pickup_date` then `location_id`
- [ ] Columns: partner, first device, first category, location, batch, reschedule_count
- [ ] Today's rows bold, tomorrow's muted
- [ ] Logistics use case: confirm an Atelier pickup visibly lists the device to move to Boutique

### Portal (client-facing)
- [ ] `/my/pickup/<valid-token>` loads month-grid picker
- [ ] Days before today+2 rendered "Indisponible" (non-clickable, grey)
- [ ] Sundays / closure dates rendered "Fermé" (grey)
- [ ] Days with daily capacity reached rendered "Complet" (muted red)
- [ ] Valid open days rendered "Disponible" (green, clickable)
- [ ] Horizon is 14 days — spanning two months → both months stacked visibly
- [ ] Click a day → inline confirmation → **Valider** → confirmation page shows date, location, address, shop hours (15h00–19h30), device list
- [ ] Click **Déplacer mon rendez-vous** → month grid re-opens with current selection highlighted
- [ ] After rescheduling, chatter on appointment shows the move
- [ ] Terminal appointment (`done` / `cancelled` / `no_show`): portal page is read-only, contact info visible, no buttons

### Race / capacity
- [ ] Two browser tabs book the last available day simultaneously → second submit lands back on picker with "Ce créneau n'est plus disponible, veuillez en choisir un autre"

### Reminder CRON
- [ ] Back-date `notification_sent_at` on a pending appointment to today - 4 days; reminder delay = 3 → run CRON manually → `last_reminder_sent_at` set, one mail sent, subsequent CRON runs do not resend
- [ ] After escalation delay → N escalation activities created (one per manager in `group_repair_manager`), summary "Client à contacter — RDV retrait non pris"
- [ ] Manager clicks **Contacté** → all siblings auto-close, `contacted=True`, `contacted_at` set, **no mail sent**
- [ ] Wait escalation delay from `contacted_at` → new escalation activity created, `contacted` reset to False

### Admin-only
- [ ] As `group_repair_admin`, developer button **Renvoyer la notification initiale** appears and fires the ready-for-pickup mail on demand

### Multi-company
- [ ] Appointment created in Company A invisible to Company B users
- [ ] Location closure without `location_id` (global) applies to all locations

---

## 5. Quote Lifecycle Automation (Feature 3 — 2026-04-11)

### Tech path
- [ ] On a confirmed repair, tech clicks **Demander un devis** → `quote_state='pending'`, repair appears in manager queue filter "Devis à préparer", menu badge increments

### Manager path
- [ ] Menu **Devis → Devis à préparer** is the default landing
- [ ] Tree columns: ref, partner, device, technician, internal notes, quote_requested_date, quote_state badge
- [ ] Filters present and each gives the expected set:
  - [ ] À préparer (pending + no SO)
  - [ ] À envoyer (pending + SO exists)
  - [ ] En attente client (sent, no open escalation)
  - [ ] À relancer (sent + reminder sent, no escalation)
  - [ ] À contacter (has escalation activity)
  - [ ] Devis refusés (à statuer)
- [ ] Decoration: orange warning row when reminder sent; red danger when escalation open
- [ ] Pricing wizard creates one `sale.order` per repair (template `repair_quote`), linked
- [ ] Manager sends mail from sale.order → `quote_state='sent'`, `quote_sent_date` set

### Approval
- [ ] Manager confirms SO manually → `quote_state='approved'`, chatter mentions the tech (via `@user_id`); open escalations close
- [ ] Client approves via portal → same final state, chatter note distinguishes "via le portail"
- [ ] Tech without `user_id` → no @mention but message still posts

### Refusal
- [ ] Manager cancels SO manually → `quote_state='refused'`, one refusal activity per manager
- [ ] Client refuses via portal → same, chatter note "via le portail"

### Reminder cascade
- [ ] `quote_sent_date` back-dated by reminder_delay + 1 days → CRON sends one reminder, `last_reminder_sent_at` set
- [ ] After escalation_delay more days → escalation activities fan out to all managers
- [ ] **Contacté** closes siblings, sets `contacted_at`, no mail
- [ ] Repeats after escalation_delay if still unresolved

### Draft rewind
- [ ] SO taken back to draft → `quote_state` returns to `pending`, repair lands back in "À préparer"; no duplicate activities

---

## 6. Completion → Pickup → Invoice → SAR (Feature 2 — 2026-04-11)

### Completion dialog
- [ ] Marking the **last** repair of a batch as `done` pops dialog: "Dossier prêt pour retrait. Souhaitez-vous notifier <client>?"
- [ ] **Envoyer la notification** sends ready-for-pickup mail: device summary + intervention notes + approved quote PDF (if any) + portal link
- [ ] **Plus tard** closes dialog; fallback button **Notifier client – dossier prêt** appears on batch form, also visible as **Notifier client** on individual repair forms

### Counter pickup routing
- [ ] Button **Traiter le retrait** on repair form (visible when `delivery_state='none'` + state in done/irreparable) routes to:
  - linked `sale.order` form if an approved quote exists
  - `repair.pricing.wizard` in invoice mode if no quote
- [ ] Button **Traiter le retrait** on batch form routes to first eligible repair's path

### Invoice → delivery dialog
- [ ] Post an invoice from an approved quote → dialog "Marquer la réparation comme livrée?" → **Confirmer la livraison** transitions all eligible repairs to `delivered`, stamps SAR on `done` (not `irreparable`), creates stock picking workshop → customer, marks appointment done, posts chatter

### SAR stamping
- [ ] On `done` + delivered → `lot.sar_expiry = today + SAR_months`, `lot.last_technician_id` set, `lot.last_delivered_repair_id` set
- [ ] On `irreparable` + delivered → no SAR expiry set, but `last_delivered_repair_id` updated
- [ ] On abandon → warranty fields cleared

### Edge cases
- [ ] Multi-invoice batch: post two invoices independently → each triggers its own delivery dialog; second correctly exits if eligible set empty
- [ ] Batch with no appointment ever created → action_mark_delivered exits cleanly
- [ ] Refused quote in a mixed batch → delivery transitions the refused repair to `delivered` with `state='cancel'`, no SAR, no invoice line
- [ ] Legacy `mail_act_repair_done` activities auto-closed with feedback "Clôture automatique — flux de livraison refondu"

---

## 7. Batch / Repair UX Polish (Feature 6 — 2026-04-22)

### Deferred batch
- [ ] Create a new draft repair → `batch_id` empty, no auto-batch
- [ ] Draft repair: partner, device, `lot_id`, pickup location all freely editable
- [ ] Click **Confirmer** → singleton batch auto-created, `batch_id` now set
- [ ] Open an existing batch → **Ajouter appareil** wizard still works, adds new repair to that batch

### Batch delivery state
- [ ] Batch form shows `delivery_state` badge: none / partiel / délivré / abandonné
- [ ] **Livrer** button visible when state ≠ delivered/abandoned
- [ ] Clicking **Livrer** transitions all eligible repairs together; recomputes batch state to fully delivered if all eligible are delivered
- [ ] Mixed (some delivered, some pending) → "Partiellement livré"
- [ ] All eligible abandoned → "Abandonné"

### Sibling banner
- [ ] Multi-device batch: opening any one repair shows banner **"Autres appareils du dossier :"** with clickable chips for siblings
- [ ] Singleton batch: banner hidden
- [ ] Click a sibling chip → opens that repair's form

### Archive cascade
- [ ] Delete the only repair in a batch → batch auto-archived (not deleted)
- [ ] Archive a repair via `active=False` on the last active one → batch archived
- [ ] Un-archive → batch un-archives
- [ ] Multi-repair batch: deleting/archiving one leaves the batch active

### Navigation bridge
- [ ] Repair form: smart button **Dossier (N)** opens batch (N = sibling count)
- [ ] Smart button hidden on draft (no batch yet)
- [ ] Repair tree: `batch_id` column visible, click-through works
- [ ] Repair search view has **Grouper par dossier** filter
- [ ] Batch tree defaults to **Dossiers multi-appareils** filter (singletons hidden by default); toggle reveals singletons

---

## 8. Per-Repair Quote & Consolidated Invoice Model (Feature 7 — 2026-04-22)

### Per-repair quotes
- [ ] Pricing wizard is quote-only (no invoice-mode radio button)
- [ ] Two repairs in same batch: quote requested on repair A does NOT block/change repair B's state
- [ ] Exactly one `sale.order` per repair (for new flows)

### Invoicing from quotes
- [ ] Repair form: **Facturer le devis** visible when `quote_state='approved'` + SO + `invoice_status='to invoice'`, invoices only that SO
- [ ] Batch form: **Facturer les devis acceptés** consolidates all approved quotes into ONE `account.move`
- [ ] Native sale.order **Créer la facture** button is hidden/replaced on repair quotes
- [ ] Consolidated invoice includes `line_section` headers: `Réparation : <device_name> (S/N: <serial>)` per SO, ordered to match lines

### Partial acceptance
- [ ] Batch with 3 quotes: approve 2, refuse 1 → consolidation button shows 2 approved
- [ ] Consolidated invoice covers only the 2 approved lines
- [ ] On batch **Livrer**: all 3 repairs transition together; accepted get SAR, refused gets `state='cancel'` with no SAR and no invoice line

### Auto-stamp
- [ ] Any `account.move` created from a repair quote has `repair_id` (if exactly one) and `batch_id` (if exactly one) populated — check a few new invoices
- [ ] Bulk-create via list view also stamps correctly

### Legacy
- [ ] Legacy multi-repair SOs still invoice; section header falls back to `Devis : <SO name>` (one header per SO)

### Walk-in (no-quote-then-invoice)
- [ ] Walk-in flow: manager opens pricing wizard, creates quote, approves SO, invoices — 3-click path confirmed working

---

## 9. Cross-Feature End-to-End Scenarios (run at least these three)

### Scenario A — Single-device happy path
1. [ ] Create draft repair for existing customer + existing lot → `batch_id` empty
2. [ ] Confirm → batch created
3. [ ] Tech requests quote → pending
4. [ ] Manager builds quote via wizard, sends mail → sent
5. [ ] Client approves via portal → approved, tech mentioned in chatter
6. [ ] Mark repair done → completion dialog → send notification → client receives mail with portal link
7. [ ] Client books day via portal → appointment scheduled; appears on daily agenda
8. [ ] Counter pickup → **Traiter le retrait** → opens SO → invoice posted → delivery dialog → delivered, SAR stamped on lot, picking validated, appointment marked done

### Scenario B — Multi-device batch with partial refusal
1. [ ] Create 3 draft repairs for same customer via batch wizard
2. [ ] Confirm first one → batch created; subsequent added to batch
3. [ ] Three separate quotes sent; client approves A and B, refuses C
4. [ ] Batch **Facturer les devis acceptés** → single invoice with two section headers
5. [ ] Batch **Livrer** → all 3 transition; A and B get SAR, C is `state='cancel'` with no SAR
6. [ ] Appointment marked done on physical pickup

### Scenario C — Escalation paths
1. [ ] Quote sent, back-date `quote_sent_date` past reminder+escalation delays → CRON → escalation activity fan-out
2. [ ] One manager clicks **Contacté** → siblings close, `contacted_at` set, no mail
3. [ ] Fast-forward another escalation_delay → new escalation appears
4. [ ] Separately: pickup appointment notification_sent past reminder+escalation → escalation activities fan out → **Contacté** behavior same

---

## 10. Regression Sweep (existing features)

- [ ] Standard repair state machine: draft → confirmed → under_repair → done still works
- [ ] Existing `/my/repairs` and `/my/quotes` portal routes unaffected
- [ ] Dashboard tiles load, employee filtering still works
- [ ] Mail threading on repair chatter still works
- [ ] Technician group: read-only on repairs, can tag failures; cannot see manager queue menus
- [ ] Manager group: full queue access, escalation activities visible in "My Activities"
- [ ] Admin group: Settings → Réparation sections editable
- [ ] Multi-company: Company A user cannot see Company B repairs/batches/appointments/lots
- [ ] Stock lots: quant updates, picking validation still work on equipment sales and rentals (see sales_rental_stock_logic memory)
- [ ] Existing mail templates (pre-existing, not modified) render correctly

---

## 11. Production Cutover Gate

Do not deploy until all of:

- [ ] Scenarios A + B + C green on staging against a real production clone
- [ ] Migration audit logs reviewed; any divergences understood or fixed
- [ ] At least one manager trained on the new **Devis à préparer** queue and batch **Livrer** / **Facturer les devis acceptés** buttons
- [ ] At least one client pickup notification round-tripped end-to-end on staging (portal booking + reschedule)
- [ ] Backup of production DB + filestore taken within last 2 hours of cutover
- [ ] Rollback plan documented (DB restore path, tag to revert to)
