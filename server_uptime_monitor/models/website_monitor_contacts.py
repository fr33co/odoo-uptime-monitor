from odoo import api, fields, models, _
from odoo.exceptions import ValidationError


class WebsiteMonitorContacts(models.Model):
    """External notification contact (email or Telegram) belonging to a notification group."""
    _name = 'website.monitor.contacts'
    _description = 'Website Uptime Monitor Contacts'
    _rec_name = 'email'
    _order = 'email, monitor_group_id'

    email = fields.Char(string="Email")
    phone = fields.Char(string="Phone")
    notification_type = fields.Selection(
        [('email', "Email"), ('telegram', "Telegram")],
        string="Notification Type", default='email', required=True)
    monitor_group_id = fields.Many2one(
        'website.monitor.groups', string="Monitor Group",
        required=True, ondelete='cascade', index=True)

    @api.constrains('notification_type', 'email', 'phone')
    def _check_contact_data(self):
        """Require the contact detail matching the notification type (email or phone)."""
        for contact in self:
            if contact.notification_type == 'email' and not contact.email:
                raise ValidationError(_("An email is required for email contacts."))
            if contact.notification_type == 'telegram' and not contact.phone:
                raise ValidationError(_("A phone is required for Telegram contacts."))
