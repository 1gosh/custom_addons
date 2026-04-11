/** @odoo-module **/

import { patch } from "@web/core/utils/patch";
import { CalendarCommonRenderer } from "@web/views/calendar/calendar_common/calendar_common_renderer";
import { ConfirmationDialog } from "@web/core/confirmation_dialog/confirmation_dialog";
import { _t } from "@web/core/l10n/translation";

patch(CalendarCommonRenderer.prototype, {
    async onEventDrop(info) {
        const model = this.props.model;
        if (model.meta.resModel !== "repair.pickup.appointment") {
            return super.onEventDrop(info);
        }
        this.fc.api.unselect();

        const notify = await new Promise((resolve) => {
            this.env.services.dialog.add(ConfirmationDialog, {
                title: _t("Notifier le client ?"),
                body: _t("Envoyer un mail au client pour l'informer du changement de rendez-vous ?"),
                confirmLabel: _t("Oui"),
                cancelLabel: _t("Non"),
                confirm: () => resolve(true),
                cancel: () => resolve(false),
            });
        });

        const record = this.fcEventToRecord(info.event);
        if (notify) {
            // Backend write() override sends the reschedule mail automatically.
            await model.updateRecord(record, { moved: true });
        } else {
            // Write directly so we can pass skip_reschedule_notification=True
            // in the context, bypassing the mail-send branch of write().
            const rawRecord = model.buildRawRecord(record, { moved: true });
            delete rawRecord.name;
            await model.orm.write(
                model.meta.resModel,
                [record.id],
                rawRecord,
                {
                    context: {
                        from_ui: true,
                        skip_reschedule_notification: true,
                    },
                }
            );
            await model.load();
        }
    },
});
