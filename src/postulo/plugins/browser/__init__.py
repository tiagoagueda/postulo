"""The browser notifier: a notification in the browser a person uses Postulo in (#209).

It needs nothing from the operator -- no mail server, no gateway, no account anywhere -- and
nothing from the person beyond pressing *Allow* once, which is what makes it the notifier to
reach for first on an instance with no mail set up.

Two ways of arriving, one connection per browser:

- **Web Push.** The browser subscribes, and a message reaches it with no Postulo tab open. The
  browser's vendor runs the push service that carries it; the message is encrypted for that
  one browser (RFC 8291), so the service carries it without being able to read it.
- **While Postulo is open.** Where the browser cannot subscribe, where the person would rather
  nothing went through a vendor's service, or when a push fails, the notification waits on
  this instance and the next open tab shows it. Nothing leaves the instance.

It holds no rows of its own. The subscription is the connection's secret, and a notification
waiting for a tab is left with `postulo.notifications.inbox`, which is Postulo's to scope.
"""

from __future__ import annotations

from urllib.parse import urlsplit

from django.templatetags.static import static
from django.urls import reverse
from django.utils.translation import gettext as _
from django.utils.translation import gettext_lazy as _lazy

from postulo.plugins.api import FieldSpec, TestResult, declares, shipped

from . import webpush

#: Both ways, and falling back from one to the other.
PUSH = "push"
#: Only while a Postulo tab is open. Nothing is handed to a push service.
TAB = "tab"


def _payload(title: str, body: str = "", url: str = "", tag: str = "") -> dict:
    return {
        "title": title[:300],
        "body": body[:1000],
        "url": url,
        "tag": tag,
        "icon": static("brand/icon-192.png"),
    }


@declares(
    shipped(
        name="browser",
        label="Browser",
        kind="notifier",
        description=_lazy(
            "Shows a notification in your browser: pushed to it when it can receive one, "
            "otherwise the next time Postulo is open in it."
        ),
    )
)
class BrowserNotifier:
    def config_fields(self) -> list[FieldSpec]:
        return [
            FieldSpec(
                "delivery",
                str(_("How it arrives")),
                type="choice",
                default=PUSH,
                choices=(
                    (
                        PUSH,
                        str(_("Pushed to this browser, even with Postulo closed")),
                    ),
                    (
                        TAB,
                        str(_("Only while Postulo is open, and nothing leaves this instance")),
                    ),
                ),
                help=str(
                    _(
                        "A push goes through the push service of your browser's maker, "
                        "encrypted so that it cannot be read on the way."
                    )
                ),
            ),
            FieldSpec(
                "subscription",
                str(_("This browser's subscription")),
                type="textarea",
                secret=True,
                required=False,
                help=str(
                    _(
                        "Filled in when you press “Allow notifications in this browser”, "
                        "which needs scripts. Leave it as it is."
                    )
                ),
            ),
        ]

    def form_attributes(self) -> dict[str, str]:
        """What the connection page's script needs to subscribe this browser."""
        return {
            "web-push-key": webpush.application_server_key(),
            "web-push-worker": reverse("notifications:worker"),
            "web-push-allow": str(_("Allow notifications in this browser")),
            "web-push-asking": str(_("Asking this browser…")),
            "web-push-subscribed": str(
                _("This browser will receive notifications. Save to keep it.")
            ),
            "web-push-tab-only": str(
                _(
                    "Notifications are allowed. This browser cannot receive pushes, so they "
                    "will appear while Postulo is open in it. Save to keep it."
                )
            ),
            "web-push-denied": str(
                _(
                    "This browser refused. Allow notifications for this site in its settings, "
                    "then try again."
                )
            ),
            "web-push-unsupported": str(
                _(
                    "This browser cannot show notifications here. Browsers only allow them on "
                    "a secure (HTTPS) address."
                )
            ),
        }

    def validate(self, config: dict) -> dict[str, list[str]]:
        try:
            webpush.parse_subscription(config.get("subscription"))
        except ValueError as error:
            return {"subscription": [str(error)]}
        return {}

    def summary(self, config: dict) -> str:
        try:
            subscription = self._subscription(config)
        except ValueError:
            subscription = None
        if subscription is None:
            return str(_("While Postulo is open"))
        return str(
            _("Pushed through %(service)s") % {"service": urlsplit(subscription.endpoint).hostname}
        )

    @staticmethod
    def _subscription(config: dict):
        """The subscription to push to, or ``None`` when this connection does not push."""
        if config.get("delivery", PUSH) == TAB:
            return None
        return webpush.parse_subscription(config.get("subscription"))

    def send(self, notification, config: dict, user) -> None:
        from postulo.notifications import inbox

        subscription = self._subscription(config)
        if subscription is None:
            inbox.leave(user, notification)
            return
        try:
            webpush.push(
                subscription,
                _payload(
                    notification.title, notification.body, notification.url, notification.event
                ),
            )
        except Exception:
            # Not lost: the next open tab shows it. Still raised, so the connection says the
            # push failed and the person can see why.
            inbox.leave(user, notification)
            raise

    def test(self, config: dict) -> TestResult:
        try:
            subscription = self._subscription(config)
        except ValueError as error:
            return TestResult(False, str(error))
        if subscription is None:
            return TestResult(
                True,
                str(
                    _(
                        "Nothing is sent from the server this way: notifications appear while "
                        "Postulo is open in a browser that allowed them."
                    )
                ),
            )
        try:
            webpush.push(
                subscription,
                _payload(
                    str(_("Notifications are set up")),
                    str(_("Reminders and captures will arrive the same way.")),
                    tag="test",
                ),
            )
        except webpush.PushFailed as error:
            if error.gone:
                return TestResult(
                    False,
                    str(
                        _(
                            "That browser has withdrawn its subscription. Open this connection "
                            "in it and allow notifications again."
                        )
                    ),
                )
            return TestResult(False, str(error))
        return TestResult(True, str(_("Sent. It should appear in the browser that allowed it.")))
