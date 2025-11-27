/** @odoo-module **/

import { registry } from "@web/core/registry";

// Cette fonction sera appelée par le Python
async function printDualReports(env, action) {
    const reports = action.params.reports;
    const activeIds = action.params.active_ids;

    // On boucle sur la liste des rapports reçue
    for (const reportXmlId of reports) {
        await env.services.action.doAction({
            type: 'ir.actions.report',
            report_name: reportXmlId,
            report_type: 'qweb-pdf',
            context: {
                active_ids: activeIds,
                active_model: 'repair.order'
            }
        });
    }
    
    // On ferme proprement l'action
    return { type: "ir.actions.act_window_close" };
}

// On enregistre l'action dans le registre d'Odoo
registry.category("actions").add("repair_custom.action_print_dual", printDualReports);