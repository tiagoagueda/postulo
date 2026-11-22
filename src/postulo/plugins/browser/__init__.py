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

import json
from urllib.parse import urlsplit

from django.templatetags.static import static
from django.urls import reverse
from django.utils.translation import gettext as _
from django.utils.translation import gettext_lazy as _lazy

from postulo.plugins.api import ConnectionUnusable, FieldSpec, TestResult, declares, shipped

from . import webpush

#: Both ways, and falling back from one to the other.
PUSH = "push"
#: Only while a Postulo tab is open. Nothing is handed to a push service.
TAB = "tab"


#: The most a push payload may weigh before it is encrypted, in bytes of UTF-8 JSON (#1259).
#: A push service takes at most 4096 bytes of message (RFC 8030 §7.2, which the browser
#: makers' services enforce as a ceiling), and encryption adds a header of 86 bytes, a
#: delimiter and a 16-byte tag; what is left is kept well clear of that, so a link or a tag
#: as long as they may be still fits. Counted in characters, 300 of a title and 1000 of a
#: body in Chinese, Hindi or emoji came to more than twice that, the service refused it,
#: and every retry was refused the same way.
PAYLOAD_BYTES = 3000

#: What a cut text ends with, so the reader can tell it goes on in Postulo.
ELLIPSIS = "…"


def _weight(payload: dict) -> int:
    return len(json.dumps(payload, ensure_ascii=False).encode("utf-8"))


def _fit(payload: dict) -> dict:
    """``payload`` cut until it weighs at most `PAYLOAD_BYTES`: the body first, then the title.

    Each pass cuts by as many bytes as are over, at a character's edge, so a text in any
    script ends on a whole character; escaping (a newline is two bytes in JSON) can leave a
    little over, which the next pass takes.
    """
    for name in ("body", "title"):
        while _weight(payload) > PAYLOAD_BYTES and payload[name]:
            over = _weight(payload) - PAYLOAD_BYTES
            raw = payload[name].encode("utf-8")
            keep = len(raw) - over - len(ELLIPSIS.encode("utf-8"))
            if keep <= 0:
                payload[name] = ""
                continue
            payload[name] = raw[:keep].decode("utf-8", "ignore").rstrip() + ELLIPSIS
    return payload


def _payload(title: str, body: str = "", url: str = "", tag: str = "", language: str = "") -> dict:
    """What a browser is pushed: bounded in characters as the inbox is, then in bytes.

    ``tag`` is what makes a later notification replace an earlier one on the screen, so it
    is the message's own key: two reminders due together are two notifications, and the
    same reminder pushed again replaces itself (#1259).
    """
    payload = {
        "title": title[:300],
        "body": body[:1000],
        "url": url,
        "tag": tag,
        "icon": static("brand/icon-192.png"),
    }
    if language:
        payload["lang"] = language
    return _fit(payload)


@declares(
    shipped(
        name="browser",
        label=_lazy("Browser notifications"),
        kind="notifier",
        parent="builtin-notifications",
        categories=("notifications",),
        icon="message-square-dot",
        description=_lazy(
            "Shows an HTML5 notification in your browser: pushed to it when it can receive one, "
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
            "web-push-install-first": str(
                _(
                    "On an iPhone or an iPad, notifications only reach Postulo added to the Home "
                    "Screen. Add it from the Share menu, open it from there, then come back here."
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
                    notification.title,
                    notification.body,
                    notification.url,
                    notification.key or notification.event,
                    notification.language,
                ),
            )
        except webpush.PushFailed as error:
            # Not lost: the next open tab shows it.
            inbox.leave(user, notification)
            if error.gone:
                # The browser has withdrawn this subscription, so every later push would fail
                # the same way. Say so once, and let Postulo switch the connection off and
                # forget the dead subscription (#216).
                raise ConnectionUnusable(
                    str(
                        _(
                            "This browser withdrew its subscription, so it was switched off. "
                            "Open this connection in that browser and allow notifications "
                            "again."
                        )
                    )
                ) from error
            raise
        except Exception:
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
