import json
from datetime import datetime, timezone
import urllib.request

from pywebpush import WebPushException, webpush


# #region debug-point D:webpush-delivery-report
def _debug_report(hypothesis_id: str, location: str, msg: str, data: dict) -> None:
    _p = ".dbg/web-push-missing.env"
    _u = "http://127.0.0.1:7777/event"
    _s = "web-push-missing"
    try:
        with open(_p, encoding="utf-8") as f:
            c = f.read()
        _u = next((line.split("=", 1)[1] for line in c.splitlines() if line.startswith("DEBUG_SERVER_URL=")), _u)
        _s = next((line.split("=", 1)[1] for line in c.splitlines() if line.startswith("DEBUG_SESSION_ID=")), _s)
    except Exception:
        pass
    try:
        payload = {
            "sessionId": _s,
            "runId": "pre-fix",
            "hypothesisId": hypothesis_id,
            "location": location,
            "msg": f"[DEBUG] {msg}",
            "data": data,
            "ts": int(datetime.now(timezone.utc).timestamp() * 1000),
        }
        urllib.request.urlopen(
            urllib.request.Request(
                _u,
                data=json.dumps(payload, default=str).encode(),
                headers={"Content-Type": "application/json"},
            ),
            timeout=0.3,
        ).read()
    except Exception:
        pass
# #endregion


def enviar_web_push(
    *,
    subscription_info: dict,
    titulo: str,
    mensaje: str,
    data: dict[str, str] | None,
    vapid_private_key: str,
    vapid_subject: str,
) -> bool:
    payload = {"title": titulo, "body": mensaje, "data": data or {}}
    try:
        # #region debug-point D:webpush-send-attempt
        _debug_report(
            "D",
            "backend/app/services/gestion_operativa_web/web_push_service.py:enviar_web_push",
            "attempting web push send",
            {
                "endpoint_suffix": subscription_info.get("endpoint", "")[-24:],
                "titulo": titulo,
                "data": data or {},
            },
        )
        # #endregion
        webpush(
            subscription_info=subscription_info,
            data=json.dumps(payload),
            vapid_private_key=vapid_private_key,
            vapid_claims={"sub": vapid_subject},
            ttl=60 * 60,
        )
        # #region debug-point D:webpush-send-ok
        _debug_report(
            "D",
            "backend/app/services/gestion_operativa_web/web_push_service.py:enviar_web_push",
            "web push send completed",
            {
                "endpoint_suffix": subscription_info.get("endpoint", "")[-24:],
                "titulo": titulo,
            },
        )
        # #endregion
        return True
    except WebPushException as exc:
        # #region debug-point D:webpush-send-error
        _debug_report(
            "D",
            "backend/app/services/gestion_operativa_web/web_push_service.py:enviar_web_push",
            "web push send failed",
            {
                "endpoint_suffix": subscription_info.get("endpoint", "")[-24:],
                "titulo": titulo,
                "error": str(exc),
            },
        )
        # #endregion
        return False
