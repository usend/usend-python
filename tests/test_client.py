import base64, hashlib, hmac, json, os, threading, time, unittest, urllib.parse
from http.server import BaseHTTPRequestHandler, HTTPServer

from usend import Usend, UsendError, smtp_settings, verify_webhook


class Handler(BaseHTTPRequestHandler):
    seen = []

    def _reply(self):
        body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
        Handler.seen.append((self.command, self.path, dict(self.headers), body))
        if self.path == "/emails" and json.loads(body).get("to"):
            out, code = {"id": "e1"}, 200
        else:
            out, code = {"statusCode": 422, "name": "validation_error", "message": "The `to` field is required."}, 422
        data = json.dumps(out).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    do_POST = do_GET = _reply

    def log_message(self, *a):
        pass


class ClientTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv = HTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        cls.usend = Usend("us_test", base_url=f"http://127.0.0.1:{cls.srv.server_port}/")

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()

    def test_send(self):
        res = self.usend.emails.send({"from": "a@x.com", "to": "b@y.com", "subject": "Hi", "html": "x"}, idempotency_key="k1")
        self.assertEqual(res, {"id": "e1"})
        method, path, headers, _ = Handler.seen[-1]
        self.assertEqual((method, path), ("POST", "/emails"))
        self.assertEqual(headers["Authorization"], "Bearer us_test")
        self.assertEqual(headers["Idempotency-Key"], "k1")

    def test_error(self):
        with self.assertRaises(UsendError) as cm:
            self.usend.emails.send({})
        self.assertEqual((cm.exception.status_code, cm.exception.name), (422, "validation_error"))

    def test_missing_key(self):
        old = os.environ.pop("USEND_API_KEY", None)
        try:
            with self.assertRaises(UsendError):
                Usend()
        finally:
            if old:
                os.environ["USEND_API_KEY"] = old

    def test_verify_webhook(self):
        raw = os.urandom(24)
        secret = "whsec_" + base64.b64encode(raw).decode()
        payload = json.dumps({"type": "email.delivered", "data": {"email_id": "e1"}})
        ts = str(int(time.time()))
        sig = base64.b64encode(hmac.new(raw, f"m1.{ts}.{payload}".encode(), hashlib.sha256).digest()).decode()
        headers = {"Webhook-Id": "m1", "Webhook-Timestamp": ts, "Webhook-Signature": "v1," + sig}
        self.assertEqual(verify_webhook(payload, headers, secret)["data"]["email_id"], "e1")
        with self.assertRaises(UsendError):
            verify_webhook(payload + " ", headers, secret)


class SmtpSettingsTest(unittest.TestCase):
    def test_ports_and_defaults(self):
        os.environ.pop("USEND_SMTP_HOST", None)
        a = smtp_settings("us_k")
        self.assertEqual((a["host"], a["port"], a["username"], a["password"], a["use_ssl"], a["starttls"]),
                         ("smtp.usend.et", 465, "usend", "us_k", True, False))
        b = smtp_settings("us_k", port=587)
        self.assertEqual((b["port"], b["use_ssl"], b["starttls"]), (587, False, True))
        with self.assertRaises(UsendError):
            smtp_settings("us_k", port=25)

    def test_host_follows_base_url(self):
        os.environ.pop("USEND_SMTP_HOST", None)
        self.assertEqual(Usend("us_k", base_url="https://api.acme.com").smtp_settings()["host"], "smtp.acme.com")
        self.assertEqual(Usend("us_k", base_url="https://api.acme.com").smtp_settings(host="mail.acme.com")["host"], "mail.acme.com")
        self.assertEqual(Usend("us_k", base_url="http://localhost:8080").smtp_settings()["host"], "smtp.usend.et")


class Recorder(BaseHTTPRequestHandler):
    seen = []

    def _reply(self):
        body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
        Recorder.seen.append((self.command, self.path, dict(self.headers), json.loads(body) if body else None))
        if self.path == "/oauth/token" and b"bad" in body:
            out, code, ctype = json.dumps({"error": "invalid_grant", "error_description": "Code expired."}).encode(), 400, "application/json"
        elif self.path.endswith("/raw"):
            out, code, ctype = b"Subject: hi", 200, "message/rfc822"
        elif "/attachments/" in self.path and self.path.startswith("/inbound"):
            out, code, ctype = b"\x00\x01\x02", 200, "application/octet-stream"
        else:
            out, code, ctype = json.dumps({"object": "x"}).encode(), 200, "application/json"
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)

    do_GET = do_POST = do_PATCH = do_DELETE = _reply

    def log_message(self, *a):
        pass


class NewResourcesTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv = HTTPServer(("127.0.0.1", 0), Recorder)
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        cls.u = Usend("us_test", base_url=f"http://127.0.0.1:{cls.srv.server_port}")

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()

    def test_methods_paths_and_bodies(self):
        u = self.u
        cases = [
            (lambda: u.emails.metrics(start_date="2026-10-01", dimensions="domain"), "GET", "/emails/metrics?start_date=2026-10-01&dimensions=domain", None),
            (lambda: u.emails.share("e1", expires_in="2 hours"), "POST", "/emails/e1/share", {"expires_in": "2 hours"}),
            (lambda: u.emails.test({"from": "a@x.com", "html": "x"}), "POST", "/emails/test", {"from": "a@x.com", "html": "x"}),
            (lambda: u.emails.insights("e1"), "GET", "/emails/e1/insights", None),
            (lambda: u.received.attachments("r1"), "GET", "/emails/receiving/r1/attachments", None),
            (lambda: u.received.attachment("r1", "a1"), "GET", "/emails/receiving/r1/attachments/a1", None),
            (lambda: u.templates.versions("welcome"), "GET", "/templates/welcome/versions", None),
            (lambda: u.templates.version("welcome", "v1"), "GET", "/templates/welcome/versions/v1", None),
            (lambda: u.templates.restore_version("welcome", "v1"), "POST", "/templates/welcome/versions/v1/restore", None),
            (lambda: u.domains.insights("d1"), "GET", "/domains/d1/insights", None),
            (lambda: u.domains.create_claim("a.com", custom_return_path="mail"), "POST", "/domains/claim", {"name": "a.com", "custom_return_path": "mail"}),
            (lambda: u.domains.get_claim("c1"), "GET", "/domains/claim/c1", None),
            (lambda: u.domains.verify_claim("c1"), "POST", "/domains/claim/c1/verify", None),
            (lambda: u.api_keys.update("k1", {"name": "n"}), "PATCH", "/api-keys/k1", {"name": "n"}),
            (lambda: u.audiences.create({"name": "A"}), "POST", "/audiences", {"name": "A"}),
            (lambda: u.audiences.create_contact("a1", {"email": "x@y.com"}), "POST", "/audiences/a1/contacts", {"email": "x@y.com"}),
            (lambda: u.audiences.list_contacts("a1", limit=5), "GET", "/audiences/a1/contacts?limit=5", None),
            (lambda: u.audiences.import_contacts("a1", "x"), "POST", "/audiences/a1/contacts/import", {"csv": "x"}),
            (lambda: u.audiences.get_contact("a1", "x@y.com"), "GET", "/audiences/a1/contacts/x%40y.com", None),
            (lambda: u.audiences.update_contact("a1", "c1", {"unsubscribed": True}), "PATCH", "/audiences/a1/contacts/c1", {"unsubscribed": True}),
            (lambda: u.audiences.remove_contact("a1", "c1"), "DELETE", "/audiences/a1/contacts/c1", None),
            (lambda: u.segments.update("s1", {"name": "N"}), "PATCH", "/segments/s1", {"name": "N"}),
            (lambda: u.segments.contacts("s1", subscription="subscribed"), "GET", "/segments/s1/contacts?subscription=subscribed", None),
            (lambda: u.segments.metrics("s1"), "GET", "/segments/s1/metrics", None),
            (lambda: u.contacts.stats(), "GET", "/contacts/stats", None),
            (lambda: u.contacts.import_csv("x", segment_id="s1"), "POST", "/contacts/import", {"csv": "x", "segment_id": "s1"}),
            (lambda: u.contacts.create_import({"contacts": [{"email": "a@b.com"}]}), "POST", "/contacts/imports", {"contacts": [{"email": "a@b.com"}]}),
            (lambda: u.contacts.list_imports(status="completed"), "GET", "/contacts/imports?status=completed", None),
            (lambda: u.contacts.get_import("i1"), "GET", "/contacts/imports/i1", None),
            (lambda: u.contacts.topics("c1"), "GET", "/contacts/c1/topics", None),
            (lambda: u.contacts.update_topics("c1", [{"id": "t1", "subscription": "opt_out"}]), "PATCH", "/contacts/c1/topics", {"topics": [{"id": "t1", "subscription": "opt_out"}]}),
            (lambda: u.contact_properties.create({"key": "plan", "type": "string"}), "POST", "/contact-properties", {"key": "plan", "type": "string"}),
            (lambda: u.contact_properties.get("p1"), "GET", "/contact-properties/p1", None),
            (lambda: u.contact_properties.update("p1", {"fallback_value": "x"}), "PATCH", "/contact-properties/p1", {"fallback_value": "x"}),
            (lambda: u.contact_properties.remove("p1"), "DELETE", "/contact-properties/p1", None),
            (lambda: u.topics.create({"name": "News"}), "POST", "/topics", {"name": "News"}),
            (lambda: u.topics.remove("t1"), "DELETE", "/topics/t1", None),
            (lambda: u.short_links.create("https://example.com", code="promo"), "POST", "/short-links", {"target_url": "https://example.com", "code": "promo"}),
            (lambda: u.short_links.list(limit=5), "GET", "/short-links?limit=5", None),
            (lambda: u.short_links.get("s1"), "GET", "/short-links/s1", None),
            (lambda: u.short_links.update("s1", {"disabled": True}), "PATCH", "/short-links/s1", {"disabled": True}),
            (lambda: u.short_links.remove("s1"), "DELETE", "/short-links/s1", None),
            (lambda: u.short_links.stats("s1", days=7), "GET", "/short-links/s1/stats?days=7", None),
            (lambda: u.broadcasts.cancel("b1"), "POST", "/broadcasts/b1/cancel", None),
            (lambda: u.broadcasts.duplicate("b1"), "POST", "/broadcasts/b1/duplicate", None),
            (lambda: u.broadcasts.recipients("b1", type="opened"), "GET", "/broadcasts/b1/recipients?type=opened", None),
            (lambda: u.broadcasts.clicked_links("b1"), "GET", "/broadcasts/b1/clicked-links", None),
            (lambda: u.broadcasts.performance("b1"), "GET", "/broadcasts/b1/performance", None),
            (lambda: u.webhooks.rotate_secret("w1"), "POST", "/webhooks/w1/rotate-secret", None),
            (lambda: u.webhooks.events(type="email.bounced"), "GET", "/webhooks/events?type=email.bounced", None),
            (lambda: u.webhooks.get_event("v1"), "GET", "/webhooks/events/v1", None),
            (lambda: u.webhooks.replay_event("v1"), "POST", "/webhooks/events/v1/replay", None),
            (lambda: u.webhooks.event_attempts("v1"), "GET", "/webhooks/events/v1/attempts", None),
            (lambda: u.suppressions.get("a@b.com"), "GET", "/suppressions/a%40b.com", None),
            (lambda: u.suppressions.create_batch(["a@b.com"]), "POST", "/suppressions/batch", {"emails": ["a@b.com"]}),
            (lambda: u.suppressions.remove_batch(["a@b.com"]), "DELETE", "/suppressions/batch", {"emails": ["a@b.com"]}),
            (lambda: u.events.create({"name": "order.paid"}), "POST", "/events", {"name": "order.paid"}),
            (lambda: u.events.list(), "GET", "/events", None),
            (lambda: u.events.get("order.paid"), "GET", "/events/order.paid", None),
            (lambda: u.events.update("e1", {"schema": None}), "PATCH", "/events/e1", {"schema": None}),
            (lambda: u.events.remove("e1"), "DELETE", "/events/e1", None),
            (lambda: u.events.send("order.paid", email="a@b.com", payload={"total": 4}), "POST", "/events/send", {"event": "order.paid", "email": "a@b.com", "payload": {"total": 4}}),
            (lambda: u.automations.create({"name": "A", "steps": [], "connections": []}), "POST", "/automations", {"name": "A", "steps": [], "connections": []}),
            (lambda: u.automations.list(status="enabled"), "GET", "/automations?status=enabled", None),
            (lambda: u.automations.update("a1", {"status": "disabled"}), "PATCH", "/automations/a1", {"status": "disabled"}),
            (lambda: u.automations.duplicate("a1"), "POST", "/automations/a1/duplicate", None),
            (lambda: u.automations.stop("a1"), "POST", "/automations/a1/stop", None),
            (lambda: u.automations.runs("a1", status="running"), "GET", "/automations/a1/runs?status=running", None),
            (lambda: u.automations.run("a1", "r1"), "GET", "/automations/a1/runs/r1", None),
            (lambda: u.inboxes.create({"name": "Support", "address": "s@a.com"}), "POST", "/inboxes", {"name": "Support", "address": "s@a.com"}),
            (lambda: u.inboxes.update("i1", {"enabled": False}), "PATCH", "/inboxes/i1", {"enabled": False}),
            (lambda: u.inboxes.labels.create("i1", {"name": "VIP"}), "POST", "/inboxes/i1/labels", {"name": "VIP"}),
            (lambda: u.inboxes.labels.remove("i1", "l1"), "DELETE", "/inboxes/i1/labels/l1", None),
            (lambda: u.inboxes.threads.list("i1", folder="inbox", unread="true"), "GET", "/inboxes/i1/threads?folder=inbox&unread=true", None),
            (lambda: u.inboxes.threads.update("i1", "t1", {"read": True}), "PATCH", "/inboxes/i1/threads/t1", {"read": True}),
            (lambda: u.inboxes.threads.remove("i1", "t1"), "DELETE", "/inboxes/i1/threads/t1", None),
            (lambda: u.inboxes.threads.emails("i1", "t1"), "GET", "/inboxes/i1/threads/t1/emails", None),
            (lambda: u.inboxes.threads.email("i1", "t1", "m1"), "GET", "/inboxes/i1/threads/t1/emails/m1", None),
            (lambda: u.inboxes.threads.reply("i1", "t1", "m1", {"text": "ok"}), "POST", "/inboxes/i1/threads/t1/emails/m1/reply", {"text": "ok"}),
            (lambda: u.inboxes.threads.forward("i1", "t1", "m1", {"to": ["a@b.com"]}), "POST", "/inboxes/i1/threads/t1/emails/m1/forward", {"to": ["a@b.com"]}),
            (lambda: u.inboxes.drafts.create("i1", {"subject": "S"}), "POST", "/inboxes/i1/drafts", {"subject": "S"}),
            (lambda: u.inboxes.drafts.send("i1", "d1"), "POST", "/inboxes/i1/drafts/d1/send", None),
            (lambda: u.logs.list(status="4xx"), "GET", "/logs?status=4xx", None),
            (lambda: u.logs.get("l1"), "GET", "/logs/l1", None),
            (lambda: u.logs.user_agents(), "GET", "/logs/user-agents", None),
            (lambda: u.stats.get(days=7), "GET", "/stats?days=7", None),
            (lambda: u.tools.dmarc_check("a.com"), "POST", "/tools/dmarc-check", {"domain": "a.com"}),
        ]
        for call, method, path, body in cases:
            res = call()
            self.assertEqual(res, {"object": "x"}, path)
            m, p, headers, b = Recorder.seen[-1]
            self.assertEqual((m, p, b), (method, path, body), path)
            self.assertEqual(headers["Authorization"], "Bearer us_test")

    def test_events_send_idempotency_key(self):
        self.u.events.send("x", email="a@b.com", idempotency_key="k9")
        self.assertEqual(Recorder.seen[-1][2]["Idempotency-Key"], "k9")

    def test_raw_and_download(self):
        self.assertEqual(self.u.received.raw("r1"), "Subject: hi")
        self.assertEqual(self.u.received.download("r1", "a1"), b"\x00\x01\x02")

    def test_oauth(self):
        u = self.u
        u.oauth.metadata()
        self.assertNotIn("Authorization", Recorder.seen[-1][2])
        self.assertEqual(Recorder.seen[-1][1], "/.well-known/oauth-authorization-server")
        u.oauth.register("App", ["https://a.test/cb"])
        self.assertEqual(Recorder.seen[-1][3], {"client_name": "App", "redirect_uris": ["https://a.test/cb"]})
        u.oauth.token(grant_type="refresh_token", client_id="c", refresh_token="r")
        self.assertEqual((Recorder.seen[-1][1], Recorder.seen[-1][3]["grant_type"]), ("/oauth/token", "refresh_token"))
        self.assertNotIn("Authorization", Recorder.seen[-1][2])
        u.oauth.revoke("t")
        self.assertEqual(Recorder.seen[-1][3], {"token": "t"})
        with self.assertRaises(UsendError) as cm:
            u.oauth.token(grant_type="authorization_code", client_id="c", code="bad")
        self.assertEqual((cm.exception.status_code, cm.exception.name, cm.exception.message), (400, "invalid_grant", "Code expired."))
        url = u.oauth.authorize_url("c", "https://a.test/cb", "full_access", "abc", state="s1")
        parsed = urllib.parse.urlparse(url)
        q = urllib.parse.parse_qs(parsed.query)
        self.assertEqual(parsed.path, "/oauth/authorize")
        self.assertEqual((q["response_type"], q["code_challenge_method"], q["state"]), (["code"], ["S256"], ["s1"]))


class RetryTest(unittest.TestCase):
    """A scripted server: answers with the queued (status, headers, body) tuples in order and records each request."""

    def setUp(self):
        outer = self
        self.queue, self.calls = [], []

        class H(BaseHTTPRequestHandler):
            def _go(self):
                n = int(self.headers.get("Content-Length") or 0)
                self.rfile.read(n)
                outer.calls.append((self.command, self.path))
                status, headers, body = outer.queue.pop(0)
                data = json.dumps(body).encode()
                self.send_response(status)
                for k, v in headers.items():
                    self.send_header(k, v)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            do_GET = do_POST = do_DELETE = _go

            def log_message(self, *a):
                pass

        self.srv = HTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.client = Usend("us_test", base_url=f"http://127.0.0.1:{self.srv.server_port}", max_retries=2)
        import usend.client as mod
        self._sleep, mod.time.sleep = mod.time.sleep, lambda s: None  # keep the tests fast
        self.mod = mod

    def tearDown(self):
        self.mod.time.sleep = self._sleep
        self.srv.shutdown()

    def test_429_is_retried_for_any_request(self):
        limited = (429, {"Retry-After": "0"}, {"statusCode": 429, "name": "rate_limit_exceeded", "message": "slow down"})
        self.queue = [limited, (200, {}, {"id": "e1"})]
        self.assertEqual(self.client.emails.send({"from": "a@x.com", "to": "b@y.com", "subject": "x", "text": "y"}), {"id": "e1"})
        self.assertEqual(len(self.calls), 2)

    def test_5xx_get_is_retried(self):
        boom = (503, {}, {"statusCode": 503, "name": "internal_server_error", "message": "x"})
        self.queue = [boom, (200, {}, {"data": []})]
        self.assertEqual(self.client.domains.list(), {"data": []})
        self.assertEqual(len(self.calls), 2)

    def test_5xx_post_without_idempotency_key_is_not_retried(self):
        boom = (503, {}, {"statusCode": 503, "name": "internal_server_error", "message": "x"})
        self.queue = [boom, (200, {}, {"id": "e1"})]
        with self.assertRaises(UsendError) as cm:
            self.client.emails.send({"from": "a@x.com", "to": "b@y.com", "subject": "x", "text": "y"})
        self.assertEqual(cm.exception.status_code, 503)
        self.assertEqual(len(self.calls), 1)

    def test_5xx_post_with_idempotency_key_is_retried(self):
        boom = (503, {}, {"statusCode": 503, "name": "internal_server_error", "message": "x"})
        self.queue = [boom, (200, {}, {"id": "e1"})]
        res = self.client.emails.send({"from": "a@x.com", "to": "b@y.com", "subject": "x", "text": "y"}, idempotency_key="k1")
        self.assertEqual(res, {"id": "e1"})
        self.assertEqual(len(self.calls), 2)

    def test_gives_up_after_max_retries_and_never_waits_over_a_minute(self):
        limited = (429, {"Retry-After": "0"}, {"statusCode": 429, "name": "rate_limit_exceeded", "message": "slow"})
        self.queue = [limited, limited, limited, (200, {}, {})]
        with self.assertRaises(UsendError) as cm:
            self.client.domains.list()
        self.assertEqual(cm.exception.status_code, 429)
        self.assertEqual(len(self.calls), 3)  # the first try and two retries
        self.queue = [(429, {"Retry-After": "120"}, {"statusCode": 429, "name": "rate_limit_exceeded", "message": "slow"})]
        self.calls.clear()
        with self.assertRaises(UsendError):
            self.client.domains.list()
        self.assertEqual(len(self.calls), 1)  # a two-minute wait is returned as the error, not slept


if __name__ == "__main__":
    unittest.main()