/** @odoo-module **/

import { patch } from "@web/core/utils/patch";
import { FormController } from "@web/views/form/form_controller";
import { useService } from "@web/core/utils/hooks";
import { onRendered } from "@odoo/owl";

const STORAGE_KEY = "odoo_repair_workstation";
const LOCATION_NAMES = { "1": "Boutique", "2": "Atelier" };

patch(FormController.prototype, {
    setup() {
        super.setup(...arguments);
        this._repairORM = useService("orm");
        this._repairNotification = useService("notification");
        this._repairLocationApplied = false;

        onRendered(() => {
            if (
                this.props.resModel === "repair.order" &&
                this.model.root &&
                this.model.root.isNew &&
                !this._repairLocationApplied
            ) {
                this._repairLocationApplied = true;
                this._applyWorkstationLocation();
            }
        });
    },

    onWillLoadRoot() {
        super.onWillLoadRoot(...arguments);
        this._repairLocationApplied = false;
    },

    async _applyWorkstationLocation() {
        let workstation = localStorage.getItem(STORAGE_KEY);

        if (!workstation) {
            workstation = this._promptWorkstation();
            if (!workstation) {
                return;
            }
        }

        // Skip if location was explicitly set via context (e.g. batch creation)
        const ctx = this.props.context || {};
        if (ctx.default_pickup_location_id) {
            return;
        }

        const locations = await this._repairORM.searchRead(
            "repair.pickup.location",
            [["name", "=", workstation]],
            ["id", "name"],
            { limit: 1 }
        );

        if (locations.length && this.model.root && this.model.root.isNew) {
            await this.model.root.update({
                pickup_location_id: [locations[0].id, locations[0].name],
            });
        }
    },

    _promptWorkstation() {
        const choice = window.prompt(
            "Configuration poste de travail :\n\n" +
            "1 = Boutique\n" +
            "2 = Atelier\n\n" +
            "Entrez 1 ou 2 :"
        );

        const name = LOCATION_NAMES[choice];
        if (name) {
            localStorage.setItem(STORAGE_KEY, name);
            this._repairNotification.add(
                `Poste configuré : ${name}`,
                { type: "success" }
            );
            return name;
        }
        return null;
    },
});
