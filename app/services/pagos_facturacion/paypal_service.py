"""
PayPal Orders API v2 — async service wrapper.

All PayPal credentials (client_id, client_secret, webhook_id) live exclusively on
the backend. The mobile client never sees or stores any PayPal secret.

Supported flow:
  create_order  →  approve_url sent to mobile  →  user approves in WebView
  capture_order →  payment confirmed in DB
  verify_webhook_signature  →  async webhook confirmation
"""
import base64
import logging
from dataclasses import dataclass

import httpx

from app.config import get_settings

logger = logging.getLogger(__name__)

SANDBOX_BASE_URL = "https://api-m.sandbox.paypal.com"
LIVE_BASE_URL = "https://api-m.paypal.com"

_TIMEOUT = httpx.Timeout(30.0)


@dataclass(slots=True)
class PayPalOrderResult:
    order_id: str
    approve_url: str
    status: str


class PayPalError(Exception):
    """Raised when the PayPal API returns an unexpected or failed response."""


class PayPalNotConfiguredError(PayPalError):
    """Raised when PayPal credentials have not been set."""


class PayPalService:
    """
    Thin async wrapper around PayPal REST Orders API v2.

    Instantiate once per request or reuse as a singleton — each method
    opens its own httpx.AsyncClient context.
    """

    def __init__(self) -> None:
        s = get_settings()
        self._client_id = s.paypal_client_id
        self._client_secret = s.paypal_client_secret
        self._mode = s.paypal_mode  # "sandbox" | "live"
        self._currency = s.paypal_currency  # e.g. "USD"
        self._webhook_id = s.paypal_webhook_id
        self._base_url = SANDBOX_BASE_URL if self._mode == "sandbox" else LIVE_BASE_URL

    @property
    def configured(self) -> bool:
        """Returns True only when both client_id and client_secret are set."""
        return bool(self._client_id and self._client_secret)

    def _require_configured(self) -> None:
        if not self.configured:
            raise PayPalNotConfiguredError(
                "PayPal no está configurado. Define PAYPAL_CLIENT_ID y PAYPAL_CLIENT_SECRET en .env"
            )

    async def _get_access_token(self) -> str:
        """Exchange client credentials for a short-lived Bearer token."""
        self._require_configured()
        credentials = base64.b64encode(
            f"{self._client_id}:{self._client_secret}".encode()
        ).decode()
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            response = await client.post(
                f"{self._base_url}/v1/oauth2/token",
                headers={
                    "Authorization": f"Basic {credentials}",
                    "Content-Type": "application/x-www-form-urlencoded",
                },
                content=b"grant_type=client_credentials",
            )
        if response.status_code != 200:
            raise PayPalError(
                f"Error autenticando con PayPal ({response.status_code}): "
                f"{response.text[:200]}"
            )
        return response.json()["access_token"]

    async def create_order(
        self,
        amount: float,
        solicitud_id: int,
        return_url: str,
        cancel_url: str,
    ) -> PayPalOrderResult:
        """
        Create a PayPal CAPTURE order and return (order_id, approve_url).

        :param amount: Amount in the configured currency (PAYPAL_CURRENCY).
        :param solicitud_id: Internal solicitud ID used as reference.
        :param return_url: URL PayPal redirects to after user approves.
        :param cancel_url: URL PayPal redirects to after user cancels.
        """
        token = await self._get_access_token()
        body = {
            "intent": "CAPTURE",
            "purchase_units": [
                {
                    "reference_id": f"solicitud-{solicitud_id}",
                    "description": f"Asistencia vehicular #{solicitud_id}",
                    "amount": {
                        "currency_code": self._currency,
                        "value": f"{amount:.2f}",
                    },
                }
            ],
            "application_context": {
                "return_url": return_url,
                "cancel_url": cancel_url,
                "brand_name": "Asistencia Vehicular",
                "user_action": "PAY_NOW",
                "shipping_preference": "NO_SHIPPING",
                "landing_page": "LOGIN",
            },
        }
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            response = await client.post(
                f"{self._base_url}/v2/checkout/orders",
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json",
                    "Prefer": "return=representation",
                },
                json=body,
            )
        if response.status_code not in (200, 201):
            raise PayPalError(
                f"PayPal create_order falló ({response.status_code}): {response.text[:300]}"
            )
        data = response.json()
        approve_url = next(
            (lnk["href"] for lnk in data.get("links", []) if lnk.get("rel") == "approve"),
            None,
        )
        if not approve_url:
            raise PayPalError("PayPal no devolvió un enlace de aprobación (approve link)")
        return PayPalOrderResult(
            order_id=data["id"],
            approve_url=approve_url,
            status=data.get("status", "CREATED"),
        )

    async def capture_order(self, order_id: str) -> dict:
        """
        Capture a PayPal order that the user has already approved.
        Returns the raw PayPal capture response (JSON dict).
        """
        token = await self._get_access_token()
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            response = await client.post(
                f"{self._base_url}/v2/checkout/orders/{order_id}/capture",
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json",
                },
            )
        if response.status_code not in (200, 201):
            raise PayPalError(
                f"PayPal capture falló ({response.status_code}): {response.text[:300]}"
            )
        return response.json()

    async def get_order(self, order_id: str) -> dict:
        """Fetch the current state of a PayPal order."""
        token = await self._get_access_token()
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            response = await client.get(
                f"{self._base_url}/v2/checkout/orders/{order_id}",
                headers={"Authorization": f"Bearer {token}"},
            )
        if response.status_code != 200:
            raise PayPalError(
                f"PayPal get_order falló ({response.status_code}): {response.text[:300]}"
            )
        return response.json()

    async def verify_webhook_signature(
        self,
        *,
        transmission_id: str,
        transmission_time: str,
        cert_url: str,
        auth_algo: str,
        transmission_sig: str,
        webhook_event_body: str,
    ) -> bool:
        """
        Verify an incoming PayPal webhook signature via the PayPal API.
        Returns True when the signature is valid or when PAYPAL_WEBHOOK_ID is
        not configured (development mode — skip verification).
        """
        if not self._webhook_id:
            logger.warning(
                "PAYPAL_WEBHOOK_ID no configurado — omitiendo verificación de firma del webhook"
            )
            return True
        try:
            token = await self._get_access_token()
        except PayPalError:
            return False
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            response = await client.post(
                f"{self._base_url}/v1/notifications/verify-webhook-signature",
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json",
                },
                json={
                    "auth_algo": auth_algo,
                    "cert_url": cert_url,
                    "transmission_id": transmission_id,
                    "transmission_sig": transmission_sig,
                    "transmission_time": transmission_time,
                    "webhook_id": self._webhook_id,
                    "webhook_event": webhook_event_body,
                },
            )
        if response.status_code != 200:
            return False
        return response.json().get("verification_status") == "SUCCESS"


def get_paypal_service() -> PayPalService:
    """FastAPI dependency — returns a configured PayPalService instance."""
    return PayPalService()
