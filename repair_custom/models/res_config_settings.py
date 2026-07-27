from odoo import fields, models


class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    repair_service_tax_id = fields.Many2one(
        'account.tax',
        string="TVA Services (Réparations/Locations)",
        config_parameter='repair_custom.service_tax_id',
        help="Taxe appliquée automatiquement sur toutes les lignes de réparations et de locations.",
        domain=[('type_tax_use', '=', 'sale'), ('amount_type', '=', 'percent')],
    )

    quote_reminder_delay_days = fields.Integer(
        string="Délai avant relance devis (jours)",
        config_parameter='repair_custom.quote_reminder_delay_days',
        default=5,
        help="Nombre de jours après l'envoi du devis avant qu'un rappel automatique soit envoyé au client.",
    )

    quote_escalation_delay_days = fields.Integer(
        string="Délai avant escalade devis (jours)",
        config_parameter='repair_custom.quote_escalation_delay_days',
        default=3,
        help="Nombre de jours après la relance (ou après un clic 'Contacté') avant qu'une activité d'escalade soit créée pour le manager.",
    )

    sar_warranty_months = fields.Integer(
        string="Durée garantie SAR (mois)",
        config_parameter='repair_custom.sar_warranty_months',
        default=3,
        help="Durée de la garantie SAR (Service Après Réparation) en mois.",
    )

    sav_warranty_months = fields.Integer(
        string="Durée garantie SAV (mois)",
        config_parameter='repair_custom.sav_warranty_months',
        default=12,
        help="Durée de la garantie SAV (Service Après Vente) en mois.",
    )

    auto_validate_equipment_sale = fields.Boolean(
        string="Valider automatiquement les livraisons équipement",
        config_parameter='repair_custom.auto_validate_equipment_sale',
        default=True,
        help="Valider automatiquement les bons de livraison lors de la confirmation d'une vente d'équipement.",
    )

    review_sms_delay_days = fields.Integer(
        string="Délai avant SMS d'avis Google (jours)",
        config_parameter='repair_custom.review_sms_delay_days',
        default=7,
        help="Nombre de jours après la livraison avant l'envoi automatique du SMS d'avis Google.",
    )
    review_sms_dedup_months = fields.Integer(
        string="Délai anti-doublon SMS d'avis (mois)",
        config_parameter='repair_custom.review_sms_dedup_months',
        default=6,
        help="Ne pas renvoyer de SMS d'avis si le client en a reçu un dans les X derniers mois.",
    )
    review_sms_template_id = fields.Many2one(
        'sms.template',
        string="Modèle SMS d'avis Google",
        config_parameter='repair_custom.review_sms_template_id',
        domain=[('model', '=', 'repair.order')],
        help="Modèle utilisé pour le SMS d'avis. Modifier le corps pour mettre à jour le lien Google Review.",
    )
