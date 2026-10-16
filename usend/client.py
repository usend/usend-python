from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Mapping, Optional

DEFAULT_BASE_URL = "https://api.usend.et"
DEFAULT_SMTP_HOST = "smtp.usend.et"
VERSION = "1.2.0"

Json = dict[str, Any]


class UsendError(Exception):
    """An error from the usend API, with its status code and error name."""

    def __init__(self, message: str, name: str = "application_error", status_code: Optional[int] = None):
        super().__init__(message)
        self.message = message
        self.name = name
        self.status_code = status_code

    def __repr__(self) -> str:
        return f"UsendError(status_code={self.status_code!r}, name={self.name!r}, message={self.message!r})"


def _q(value: str) -> str:
    return urllib.parse.quote(str(value), safe="")


def _qs(params: Optional[Mapping[str, Any]]) -> str:
    if not params:
        return ""
    clean = {k: v for k, v in params.items() if v is not None}
    return "?" + urllib.parse.urlencode(clean) if clean else ""


class _Resource:
    def __init__(self, client: "Usend"):
        self._c = client


class Emails(_Resource):
    def send(self, email: Json, idempotency_key: Optional[str] = None) -> Json:
        return self._c._request("POST", "/emails", email, _idem(idempotency_key))

    def get(self, email_id: str) -> Json:
        return self._c._request("GET", f"/emails/{_q(email_id)}")

    def list(self, **params: Any) -> Json:
        return self._c._request("GET", f"/emails{_qs(params)}")

    def update(self, email_id: str, scheduled_at: str) -> Json:
        return self._c._request("PATCH", f"/emails/{_q(email_id)}", {"scheduled_at": scheduled_at})

    def cancel(self, email_id: str) -> Json:
        return self._c._request("POST", f"/emails/{_q(email_id)}/cancel")

    def events(self, email_id: str) -> Json:
        return self._c._request("GET", f"/emails/{_q(email_id)}/events")

    def attachments(self, email_id: str) -> Json:
        return self._c._request("GET", f"/emails/{_q(email_id)}/attachments")

    def attachment(self, email_id: str, attachment_id: str) -> Json:
        return self._c._request("GET", f"/emails/{_q(email_id)}/attachments/{_q(attachment_id)}")

    def metrics(self, **params: Any) -> Json:
        """Aggregate metrics: start_date, end_date, timezone, granularity, metrics, dimensions, domain_id, ..."""
        return self._c._request("GET", f"/emails/metrics{_qs(params)}")

    def share(self, email_id: str, expires_in: Optional[str] = None) -> Json:
        """A public read-only link to the email. `expires_in` such as "2 hours" (max 48h)."""
        return self._c._request("POST", f"/emails/{_q(email_id)}/share", {"expires_in": expires_in} if expires_in else {})

    def test(self, email: Json) -> Json:
        """Send the content to the signed-in dashboard user. Dashboard sessions only: API keys get 403."""
        return self._c._request("POST", "/emails/test", email)

    def insights(self, email_id: str) -> Json:
        return self._c._request("GET", f"/emails/{_q(email_id)}/insights")


class Batch(_Resource):
    def send(self, emails: list, idempotency_key: Optional[str] = None, validation: Optional[str] = None) -> Json:
        headers = _idem(idempotency_key)
        if validation:
            headers["x-batch-validation"] = validation
        return self._c._request("POST", "/emails/batch", emails, headers)


class Received(_Resource):
    def list(self, **params: Any) -> Json:
        return self._c._request("GET", f"/inbound/emails{_qs(params)}")

    def get(self, email_id: str) -> Json:
        return self._c._request("GET", f"/inbound/emails/{_q(email_id)}")

    def raw(self, email_id: str) -> str:
        """The raw RFC 822 message."""
        return self._c._request("GET", f"/inbound/emails/{_q(email_id)}/raw", as_="text")

    def attachments(self, email_id: str) -> Json:
        return self._c._request("GET", f"/emails/receiving/{_q(email_id)}/attachments")

    def attachment(self, email_id: str, attachment_id: str) -> Json:
        return self._c._request("GET", f"/emails/receiving/{_q(email_id)}/attachments/{_q(attachment_id)}")

    def download(self, email_id: str, attachment_id: str) -> bytes:
        """The attachment file."""
        return self._c._request("GET", f"/inbound/emails/{_q(email_id)}/attachments/{_q(attachment_id)}", as_="bytes")


class _Crud(_Resource):
    path = ""

    def create(self, body: Json) -> Json:
        return self._c._request("POST", self.path, body)

    def list(self, **params: Any) -> Json:
        return self._c._request("GET", f"{self.path}{_qs(params)}")

    def get(self, ident: str) -> Json:
        return self._c._request("GET", f"{self.path}/{_q(ident)}")

    def update(self, ident: str, body: Json) -> Json:
        return self._c._request("PATCH", f"{self.path}/{_q(ident)}", body)

    def remove(self, ident: str) -> Json:
        return self._c._request("DELETE", f"{self.path}/{_q(ident)}")


class Templates(_Crud):
    path = "/templates"

    def publish(self, id_or_alias: str) -> Json:
        return self._c._request("POST", f"/templates/{_q(id_or_alias)}/publish")

    def duplicate(self, id_or_alias: str) -> Json:
        return self._c._request("POST", f"/templates/{_q(id_or_alias)}/duplicate")

    def versions(self, id_or_alias: str) -> Json:
        return self._c._request("GET", f"/templates/{_q(id_or_alias)}/versions")

    def version(self, id_or_alias: str, version_id: str) -> Json:
        return self._c._request("GET", f"/templates/{_q(id_or_alias)}/versions/{_q(version_id)}")

    def restore_version(self, id_or_alias: str, version_id: str) -> Json:
        """Copy a version into the draft (does not publish)."""
        return self._c._request("POST", f"/templates/{_q(id_or_alias)}/versions/{_q(version_id)}/restore")


class Domains(_Crud):
    path = "/domains"

    def verify(self, domain_id: str) -> Json:
        return self._c._request("POST", f"/domains/{_q(domain_id)}/verify")

    def auto_configure(self, domain_id: str, api_token: str, provider: str = "cloudflare") -> Json:
        body = {"provider": provider, "api_token": api_token}
        return self._c._request("POST", f"/domains/{_q(domain_id)}/auto-configure", body)

    def insights(self, domain_id: str) -> Json:
        return self._c._request("GET", f"/domains/{_q(domain_id)}/insights")

    def create_claim(self, name: str, **options: Any) -> Json:
        """Claim a name another organization verified: publish the TXT record, then verify_claim()."""
        return self._c._request("POST", "/domains/claim", {"name": name, **options})

    def get_claim(self, claim_id: str) -> Json:
        return self._c._request("GET", f"/domains/claim/{_q(claim_id)}")

    def verify_claim(self, claim_id: str) -> Json:
        return self._c._request("POST", f"/domains/claim/{_q(claim_id)}/verify")


class ApiKeys(_Crud):
    path = "/api-keys"


class Audiences(_Crud):
    path = "/audiences"

    def create_contact(self, audience_id: str, contact: Json) -> Json:
        return self._c._request("POST", f"/audiences/{_q(audience_id)}/contacts", contact)

    def list_contacts(self, audience_id: str, **params: Any) -> Json:
        return self._c._request("GET", f"/audiences/{_q(audience_id)}/contacts{_qs(params)}")

    def import_contacts(self, audience_id: str, csv: str) -> Json:
        """Synchronous CSV import (text with an optional header row)."""
        return self._c._request("POST", f"/audiences/{_q(audience_id)}/contacts/import", {"csv": csv})

    def get_contact(self, audience_id: str, id_or_email: str) -> Json:
        return self._c._request("GET", f"/audiences/{_q(audience_id)}/contacts/{_q(id_or_email)}")

    def update_contact(self, audience_id: str, id_or_email: str, contact: Json) -> Json:
        return self._c._request("PATCH", f"/audiences/{_q(audience_id)}/contacts/{_q(id_or_email)}", contact)

    def remove_contact(self, audience_id: str, id_or_email: str) -> Json:
        return self._c._request("DELETE", f"/audiences/{_q(audience_id)}/contacts/{_q(id_or_email)}")


class Segments(_Crud):
    path = "/segments"

    def contacts(self, segment_id: str, **params: Any) -> Json:
        return self._c._request("GET", f"/segments/{_q(segment_id)}/contacts{_qs(params)}")

    def metrics(self, segment_id: str) -> Json:
        return self._c._request("GET", f"/segments/{_q(segment_id)}/metrics")


class Contacts(_Crud):
    path = "/contacts"

    def segments(self, id_or_email: str) -> Json:
        return self._c._request("GET", f"/contacts/{_q(id_or_email)}/segments")

    def add_to_segment(self, id_or_email: str, segment_id: str) -> Json:
        return self._c._request("POST", f"/contacts/{_q(id_or_email)}/segments/{_q(segment_id)}")

    def remove_from_segment(self, id_or_email: str, segment_id: str) -> Json:
        return self._c._request("DELETE", f"/contacts/{_q(id_or_email)}/segments/{_q(segment_id)}")

    def stats(self) -> Json:
        return self._c._request("GET", "/contacts/stats")

    def import_csv(self, csv: str, segment_id: Optional[str] = None) -> Json:
        """Synchronous CSV import into one segment."""
        body: Json = {"csv": csv}
        if segment_id:
            body["segment_id"] = segment_id
        return self._c._request("POST", "/contacts/import", body)

    def create_import(self, body: Json) -> Json:
        """Background import (up to 250,000 rows): {"csv": ...} or {"contacts": [...]}; poll get_import()."""
        return self._c._request("POST", "/contacts/imports", body)

    def list_imports(self, **params: Any) -> Json:
        return self._c._request("GET", f"/contacts/imports{_qs(params)}")

    def get_import(self, import_id: str) -> Json:
        return self._c._request("GET", f"/contacts/imports/{_q(import_id)}")

    def topics(self, id_or_email: str) -> Json:
        return self._c._request("GET", f"/contacts/{_q(id_or_email)}/topics")

    def update_topics(self, id_or_email: str, topics: list) -> Json:
        """topics: [{"id": ..., "subscription": "opt_in" | "opt_out"}]"""
        return self._c._request("PATCH", f"/contacts/{_q(id_or_email)}/topics", {"topics": topics})


class ContactProperties(_Crud):
    path = "/contact-properties"


class Topics(_Crud):
    path = "/topics"


class ShortLinks(_Resource):
    """Short URLs on the tracking host, with click statistics."""

    def create(self, target_url: str, code: Optional[str] = None, title: Optional[str] = None, expires_at: Optional[str] = None) -> Json:
        body = {"target_url": target_url, "code": code, "title": title, "expires_at": expires_at}
        return self._c._request("POST", "/short-links", {k: v for k, v in body.items() if v is not None})

    def list(self, **params: Any) -> Json:
        return self._c._request("GET", f"/short-links{_qs(params)}")

    def get(self, link_id: str) -> Json:
        return self._c._request("GET", f"/short-links/{_q(link_id)}")

    def update(self, link_id: str, fields: Json) -> Json:
        """fields: target_url, title, expires_at (None clears it), disabled. The code cannot change."""
        return self._c._request("PATCH", f"/short-links/{_q(link_id)}", fields)

    def remove(self, link_id: str) -> Json:
        return self._c._request("DELETE", f"/short-links/{_q(link_id)}")

    def stats(self, link_id: str, days: Optional[int] = None) -> Json:
        return self._c._request("GET", f"/short-links/{_q(link_id)}/stats{_qs({'days': days})}")


class Broadcasts(_Crud):
    path = "/broadcasts"

    def send(self, broadcast_id: str, scheduled_at: Optional[str] = None) -> Json:
        body = {"scheduled_at": scheduled_at} if scheduled_at else {}
        return self._c._request("POST", f"/broadcasts/{_q(broadcast_id)}/send", body)

    def test(self, broadcast_id: str, to: str) -> Json:
        return self._c._request("POST", f"/broadcasts/{_q(broadcast_id)}/test", {"to": to})

    def cancel(self, broadcast_id: str) -> Json:
        return self._c._request("POST", f"/broadcasts/{_q(broadcast_id)}/cancel")

    def duplicate(self, broadcast_id: str) -> Json:
        return self._c._request("POST", f"/broadcasts/{_q(broadcast_id)}/duplicate")

    def recipients(self, broadcast_id: str, **params: Any) -> Json:
        return self._c._request("GET", f"/broadcasts/{_q(broadcast_id)}/recipients{_qs(params)}")

    def clicked_links(self, broadcast_id: str) -> Json:
        return self._c._request("GET", f"/broadcasts/{_q(broadcast_id)}/clicked-links")

    def performance(self, broadcast_id: str) -> Json:
        return self._c._request("GET", f"/broadcasts/{_q(broadcast_id)}/performance")


class Webhooks(_Crud):
    path = "/webhooks"

    def deliveries(self, webhook_id: str, **params: Any) -> Json:
        return self._c._request("GET", f"/webhooks/{_q(webhook_id)}/deliveries{_qs(params)}")

    def replay(self, webhook_id: str, delivery_id: str) -> Json:
        return self._c._request("POST", f"/webhooks/{_q(webhook_id)}/deliveries/{_q(delivery_id)}/replay")

    def rotate_secret(self, webhook_id: str) -> Json:
        return self._c._request("POST", f"/webhooks/{_q(webhook_id)}/rotate-secret")

    def events(self, **params: Any) -> Json:
        """Deliveries across all endpoints: webhook_id, type, status, limit, after, before."""
        return self._c._request("GET", f"/webhooks/events{_qs(params)}")

    def get_event(self, event_id: str) -> Json:
        return self._c._request("GET", f"/webhooks/events/{_q(event_id)}")

    def replay_event(self, event_id: str) -> Json:
        return self._c._request("POST", f"/webhooks/events/{_q(event_id)}/replay")

    def event_attempts(self, event_id: str) -> Json:
        return self._c._request("GET", f"/webhooks/events/{_q(event_id)}/attempts")

    @staticmethod
    def verify(payload: str, headers: Mapping[str, str], secret: str, tolerance_seconds: int = 300) -> Json:
        return verify_webhook(payload, headers, secret, tolerance_seconds)


class Suppressions(_Crud):
    path = "/suppressions"

    def create_batch(self, emails: list) -> Json:
        """Suppress up to 100 addresses at once."""
        return self._c._request("POST", "/suppressions/batch", {"emails": emails})

    def remove_batch(self, emails: list) -> Json:
        return self._c._request("DELETE", "/suppressions/batch", {"emails": emails})


class Events(_Crud):
    path = "/events"

    def send(self, event: str, contact_id: Optional[str] = None, email: Optional[str] = None,
             payload: Optional[Json] = None, idempotency_key: Optional[str] = None) -> Json:
        """Fire a custom event for a contact (by `contact_id` or `email`)."""
        body: Json = {"event": event}
        if contact_id:
            body["contact_id"] = contact_id
        if email:
            body["email"] = email
        if payload is not None:
            body["payload"] = payload
        return self._c._request("POST", "/events/send", body, _idem(idempotency_key))


class Automations(_Crud):
    path = "/automations"

    def duplicate(self, automation_id: str) -> Json:
        return self._c._request("POST", f"/automations/{_q(automation_id)}/duplicate")

    def stop(self, automation_id: str) -> Json:
        return self._c._request("POST", f"/automations/{_q(automation_id)}/stop")

    def runs(self, automation_id: str, **params: Any) -> Json:
        return self._c._request("GET", f"/automations/{_q(automation_id)}/runs{_qs(params)}")

    def run(self, automation_id: str, run_id: str) -> Json:
        return self._c._request("GET", f"/automations/{_q(automation_id)}/runs/{_q(run_id)}")


class _Sub(_Resource):
    """A resource nested under an inbox: every call takes the inbox ID first."""

    path = ""

    def _p(self, inbox_id: str, *rest: str) -> str:
        return "/inboxes/" + _q(inbox_id) + self.path + "".join("/" + _q(r) for r in rest)

    def create(self, inbox_id: str, body: Json) -> Json:
        return self._c._request("POST", self._p(inbox_id), body)

    def list(self, inbox_id: str, **params: Any) -> Json:
        return self._c._request("GET", self._p(inbox_id) + _qs(params))

    def get(self, inbox_id: str, ident: str) -> Json:
        return self._c._request("GET", self._p(inbox_id, ident))

    def update(self, inbox_id: str, ident: str, body: Json) -> Json:
        return self._c._request("PATCH", self._p(inbox_id, ident), body)

    def remove(self, inbox_id: str, ident: str) -> Json:
        return self._c._request("DELETE", self._p(inbox_id, ident))


class InboxLabels(_Sub):
    path = "/labels"


class InboxDrafts(_Sub):
    path = "/drafts"

    def send(self, inbox_id: str, draft_id: str) -> Json:
        return self._c._request("POST", self._p(inbox_id, draft_id, "send"))


class InboxThreads(_Sub):
    path = "/threads"

    def create(self, *a: Any, **k: Any) -> Json:  # threads are created by incoming mail
        raise NotImplementedError("Threads are created by received mail.")

    def emails(self, inbox_id: str, thread_id: str, **params: Any) -> Json:
        return self._c._request("GET", self._p(inbox_id, thread_id, "emails") + _qs(params))

    def email(self, inbox_id: str, thread_id: str, email_id: str) -> Json:
        return self._c._request("GET", self._p(inbox_id, thread_id, "emails", email_id))

    def reply(self, inbox_id: str, thread_id: str, email_id: str, body: Json) -> Json:
        return self._c._request("POST", self._p(inbox_id, thread_id, "emails", email_id, "reply"), body)

    def forward(self, inbox_id: str, thread_id: str, email_id: str, body: Json) -> Json:
        return self._c._request("POST", self._p(inbox_id, thread_id, "emails", email_id, "forward"), body)


class Inboxes(_Crud):
    path = "/inboxes"

    def __init__(self, client: "Usend"):
        super().__init__(client)
        self.labels = InboxLabels(client)
        self.threads = InboxThreads(client)
        self.drafts = InboxDrafts(client)


class Logs(_Resource):
    def list(self, **params: Any) -> Json:
        return self._c._request("GET", f"/logs{_qs(params)}")

    def get(self, log_id: str) -> Json:
        return self._c._request("GET", f"/logs/{_q(log_id)}")

    def user_agents(self) -> Json:
        return self._c._request("GET", "/logs/user-agents")


class Stats(_Resource):
    def get(self, **params: Any) -> Json:
        """Sending statistics; `days` defaults to 30."""
        return self._c._request("GET", f"/stats{_qs(params)}")


class Tools(_Resource):
    def dmarc_check(self, domain: str) -> Json:
        return self._c._request("POST", "/tools/dmarc-check", {"domain": domain})


class OAuth(_Resource):
    """OAuth 2.0 authorization server (PKCE S256). These calls send no API key; errors carry the OAuth error code as `name`."""

    def metadata(self) -> Json:
        return self._c._request("GET", "/.well-known/oauth-authorization-server", auth=False)

    def register(self, client_name: str, redirect_uris: list, **options: Any) -> Json:
        return self._c._request("POST", "/oauth/register", {"client_name": client_name, "redirect_uris": redirect_uris, **options}, auth=False)

    def token(self, **body: Any) -> Json:
        """grant_type="authorization_code" with client_id, code, code_verifier, redirect_uri; or "refresh_token" with client_id, refresh_token."""
        return self._c._request("POST", "/oauth/token", body, auth=False)

    def revoke(self, token: str, client_id: Optional[str] = None) -> Json:
        body = {"token": token}
        if client_id:
            body["client_id"] = client_id
        return self._c._request("POST", "/oauth/revoke", body, auth=False)

    def authorize_url(self, client_id: str, redirect_uri: str, scope: str, code_challenge: str, state: Optional[str] = None) -> str:
        """The URL to send the user's browser to. `scope` is "full_access" and/or "sending_access"."""
        params = {"response_type": "code", "client_id": client_id, "redirect_uri": redirect_uri, "scope": scope,
                  "code_challenge": code_challenge, "code_challenge_method": "S256", "state": state}
        return self._c.base_url + "/oauth/authorize" + _qs(params)


class Usend:
    """Client for the usend API. `base_url` defaults to USEND_BASE_URL or https://api.usend.et."""

    def __init__(self, api_key: Optional[str] = None, base_url: Optional[str] = None, timeout: float = 30.0):
        self.api_key = api_key or os.environ.get("USEND_API_KEY")
        if not self.api_key:
            raise UsendError('Missing API key. Pass it to Usend("us_...") or set USEND_API_KEY.', "missing_api_key")
        self.base_url = (base_url or os.environ.get("USEND_BASE_URL") or DEFAULT_BASE_URL).rstrip("/")
        self.timeout = timeout
        self.emails = Emails(self)
        self.batch = Batch(self)
        self.received = Received(self)
        self.templates = Templates(self)
        self.domains = Domains(self)
        self.api_keys = ApiKeys(self)
        self.segments = Segments(self)
        self.contacts = Contacts(self)
        self.broadcasts = Broadcasts(self)
        self.webhooks = Webhooks(self)
        self.suppressions = Suppressions(self)
        self.audiences = Audiences(self)
        self.contact_properties = ContactProperties(self)
        self.topics = Topics(self)
        self.short_links = ShortLinks(self)
        self.events = Events(self)
        self.automations = Automations(self)
        self.inboxes = Inboxes(self)
        self.logs = Logs(self)
        self.stats = Stats(self)
        self.tools = Tools(self)
        self.oauth = OAuth(self)

    def smtp_settings(self, port: int = 465, host: Optional[str] = None) -> Json:
        """SMTP settings for this key. See `smtp_settings()` at module level."""
        return smtp_settings(self.api_key, port=port, host=host, base_url=self.base_url)

    def _request(self, method: str, path: str, body: Any = None, headers: Optional[dict] = None, as_: str = "json", auth: bool = True) -> Any:
        h = {"User-Agent": f"usend-python/{VERSION}", "Accept": "application/json" if as_ == "json" else "*/*"}
        if auth:
            h["Authorization"] = f"Bearer {self.api_key}"
        data = None
        if body is not None:
            data = json.dumps(body).encode()
            h["Content-Type"] = "application/json"
        h.update(headers or {})
        req = urllib.request.Request(self.base_url + path, data=data, headers=h, method=method)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as res:
                raw = res.read()
                if as_ == "bytes":
                    return raw
                if as_ == "text":
                    return raw.decode("utf-8", "replace")
                return json.loads(raw) if raw else {}
        except urllib.error.HTTPError as e:
            try:
                err = json.loads(e.read())
            except ValueError:
                err = {}
            if not isinstance(err, dict):
                err = {}
            message = err.get("message") or err.get("error_description") or f"HTTP {e.code}"
            raise UsendError(message, err.get("name") or err.get("error") or "application_error", e.code) from None
        except urllib.error.URLError as e:
            raise UsendError(str(e.reason)) from None


def _idem(key: Optional[str]) -> dict:
    return {"Idempotency-Key": key} if key else {}


def _smtp_host_for(base_url: Optional[str]) -> str:
    if base_url:
        hostname = urllib.parse.urlparse(base_url).hostname or ""
        if hostname.startswith("api."):
            return "smtp." + hostname[4:]
    return DEFAULT_SMTP_HOST


def smtp_settings(api_key: Optional[str] = None, port: int = 465, host: Optional[str] = None, base_url: Optional[str] = None) -> Json:
    """Settings for sending through usend over SMTP; the API key is the password.

    Port 465 is implicit TLS (`use_ssl`), 587 is STARTTLS (`starttls`). With the standard library:

        s = smtp_settings("us_...")
        with smtplib.SMTP_SSL(s["host"], s["port"]) as smtp:
            smtp.login(s["username"], s["password"])

    The host is smtp.usend.et; for a self-hosted platform pass `host`, set USEND_SMTP_HOST, or pass
    `base_url` ("https://api.x.com" gives "smtp.x.com").
    """
    key = api_key or os.environ.get("USEND_API_KEY")
    if not key:
        raise UsendError('Missing API key. Pass it to smtp_settings("us_...") or set USEND_API_KEY.', "missing_api_key")
    if port not in (465, 587):
        raise UsendError("usend accepts SMTP on port 465 (TLS) or 587 (STARTTLS).", "invalid_port")
    return {
        "host": host or os.environ.get("USEND_SMTP_HOST") or _smtp_host_for(base_url),
        "port": port,
        "username": "usend",
        "password": key,
        "use_ssl": port == 465,
        "starttls": port == 587,
    }


def verify_webhook(payload: str, headers: Mapping[str, str], secret: str, tolerance_seconds: int = 300) -> Json:
    """Verify a webhook delivery (Standard Webhooks) and return the parsed event; raises UsendError if invalid.

    `payload` must be the raw request body exactly as received.
    """
    lower = {k.lower(): v for k, v in headers.items()}
    msg_id, ts, sigs = lower.get("webhook-id"), lower.get("webhook-timestamp"), lower.get("webhook-signature")
    if not (msg_id and ts and sigs):
        raise UsendError("Missing webhook-id, webhook-timestamp or webhook-signature header", "invalid_webhook")
    if abs(time.time() - int(ts)) > tolerance_seconds:
        raise UsendError("Webhook timestamp is too old or too new", "invalid_webhook")
    key = base64.b64decode(secret.removeprefix("whsec_"))
    expected = base64.b64encode(hmac.new(key, f"{msg_id}.{ts}.{payload}".encode(), hashlib.sha256).digest()).decode()
    for part in sigs.split(" "):
        version, _, sig = part.partition(",")
        if version == "v1" and hmac.compare_digest(sig, expected):
            return json.loads(payload)
    raise UsendError("Invalid webhook signature", "invalid_webhook")
