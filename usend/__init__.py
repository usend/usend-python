"""usend for Python: the email API for developers.

    from usend import Usend

    usend = Usend("us_...")  # or set USEND_API_KEY
    email = usend.emails.send({"from": "Acme <hi@acme.com>", "to": "ann@example.com",
                               "subject": "Hi", "html": "<p>Hi</p>"})
    print(email["id"])

Methods return the API's JSON as dicts and raise UsendError on failure.
"""

from .client import Usend, UsendError, smtp_settings, verify_webhook

__all__ = ["Usend", "UsendError", "smtp_settings", "verify_webhook"]
__version__ = "1.2.0"
