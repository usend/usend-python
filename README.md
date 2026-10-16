# usend for Python

The official Python SDK for [usend](https://usend.et), the email API for developers.

```bash
pip install usend
```

```python
import os
from usend import Usend

usend = Usend(os.environ["USEND_API_KEY"])

email = usend.emails.send({
    "from": "Acme <hello@acme.com>",
    "to": "ann@example.com",
    "subject": "Your login code",
    "html": "<p>Your code is <b>482913</b></p>",
})
print(email["id"])
```

Methods return the API's JSON as dicts and raise `usend.UsendError` (`status_code`, `name`, `message`).

- Python 3.9 or newer. No dependencies.
- The key defaults to `USEND_API_KEY`; the base URL to `USEND_BASE_URL` or `https://api.usend.et`.
- `smtp_settings(key)` / `usend.smtp_settings()` return SMTP settings (host `smtp.usend.et`, port 465 or 587).
- Resources: `emails` (incl. `metrics`, `share`, `test`, `insights`), `batch`, `received`, `templates` (+ versions),
  `domains` (+ claims, `insights`), `api_keys`, `audiences`, `segments`, `contacts` (+ imports, topics),
  `contact_properties`, `topics`, `short_links` (+ `stats`), `broadcasts` (+ `cancel`, `duplicate`, `recipients`, `clicked_links`, `performance`),
  `webhooks` (+ `rotate_secret`, events), `suppressions` (+ `create_batch`, `remove_batch`), `events`, `automations`
  (+ `runs`), `inboxes` (`.labels`, `.threads`, `.drafts`), `logs`, `stats`, `tools`, `oauth`.
- Every endpoint is described in `openapi/openapi.yaml` (OpenAPI 3.1) in the usend repository.

```python
usend.events.send("order.paid", email="ann@example.com", payload={"total": 42})
usend.suppressions.create_batch(["a@example.com", "b@example.com"])
usend.broadcasts.recipients(broadcast_id, type="opened")  # `type` is required
```

Send through SMTP; the API key is the password:

```python
import smtplib
from usend import smtp_settings

s = smtp_settings("us_...")  # smtp.usend.et:465, or smtp_settings("us_...", port=587) for STARTTLS
with smtplib.SMTP_SSL(s["host"], s["port"]) as smtp:
    smtp.login(s["username"], s["password"])
    smtp.send_message(message)
```

Verify a webhook (pass the raw body):

```python
from usend import verify_webhook

event = verify_webhook(raw_body, request.headers, os.environ["USEND_WEBHOOK_SECRET"])
```

Docs: https://usend.et/docs
