from odoo import fields, models


class WebsiteMonitorGroups(models.Model):
    """Notification group: Odoo users and external contacts alerted about a monitor."""
    _name = 'website.monitor.groups'
    _description = 'Website Uptime Monitor Groups'

    name = fields.Char(string="Group Name", required=True)
    contacts_ids = fields.One2many(
        'website.monitor.contacts', 'monitor_group_id', string="Contacts")
    user_ids = fields.Many2many(
        'res.users', string="Odoo Users",
        domain=[('share', '=', False)],
        help="Internal users notified at the email of their contact card.")
    active = fields.Boolean(default=True)

    def _get_notification_targets(self):
        """Return the recipients: ``{'email', 'lang', 'partner_id'}``."""
        self.ensure_one()
        targets, seen = [], set()
        for user in self.user_ids.filtered('active'):
            email = user.partner_id.email
            if email and email.lower() not in seen:
                seen.add(email.lower())
                targets.append({
                    'email': email, 'lang': user.lang,
                    'partner_id': user.partner_id.id})
        for contact in self.contacts_ids:
            if (contact.notification_type == 'email' and contact.email
                    and contact.email.lower() not in seen):
                seen.add(contact.email.lower())
                targets.append({
                    'email': contact.email, 'lang': False,
                    'partner_id': False})
        return targets
