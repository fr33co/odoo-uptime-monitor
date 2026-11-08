from odoo import models


class WebsiteMonitorNotification(models.AbstractModel):
    """Email notifications of a monitor to its notification group."""
    _name = 'website.monitor.notification.mixin'
    _description = 'Website Monitor: Notifications'

    _TEMPLATES = {
        'alert': 'email_monitor_template',
        'recovery': 'email_monitor_recovery_template',
        'ssl_expiry': 'email_monitor_expiry_template',
        'domain_expiry': 'email_monitor_expiry_template',
        'db_expiry': 'email_monitor_expiry_template',
    }

    def _notify(self, kind):
        """Email the group's contacts with the template matching ``kind``."""
        template = self.env.ref(
            'server_uptime_monitor.' + self._TEMPLATES[kind]).with_context(
                expiry_kind=kind)
        for monitor in self:
            for target in monitor.monitor_group_id._get_notification_targets():
                values = {'email_to': target['email'], 'recipient_ids': []}
                if target['partner_id']:
                    values = {
                        'email_to': False,
                        'recipient_ids': [(6, 0, [target['partner_id']])],
                    }
                lang_template = (
                    template.with_context(lang=target['lang'])
                    if target['lang'] else template)
                lang_template.send_mail(monitor.id, email_values=values)

    def action_send_mail(self):
        """Send the failure alert to the group, regardless of the incident state.

        Public methods are callable over RPC by any user who can read the
        record, so require the same right as editing the monitor.
        """
        self.check_access('write')
        self._notify('alert')
