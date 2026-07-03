import json
import logging
from datetime import timedelta
from pathlib import Path

from firebase_admin import credentials, initialize_app, messaging
from firebase_admin.exceptions import FirebaseError

from app.config import get_settings


firebase_app = None
logger = logging.getLogger(__name__)


def inicializar_firebase() -> None:
    global firebase_app
    if firebase_app:
        return

    settings = get_settings()
    raw_credentials = (settings.firebase_credentials or "").strip()
    if not raw_credentials:
        logger.warning("FCM disabled: FIREBASE_CREDENTIALS is empty")
        return

    try:
        if raw_credentials.startswith("{"):
            payload = json.loads(raw_credentials)
            firebase_app = initialize_app(credentials.Certificate(payload))
            logger.info("Firebase Admin initialized from inline JSON credentials")
            return
        credentials_path = Path(raw_credentials)
        if credentials_path.exists():
            firebase_app = initialize_app(credentials.Certificate(str(credentials_path)))
            logger.info("Firebase Admin initialized from credentials file path")
            return
        logger.warning("FCM disabled: FIREBASE_CREDENTIALS path does not exist")
    except Exception:
        firebase_app = None
        logger.exception("Firebase Admin initialization failed")
        return


def enviar_notificacion_push(token: str, titulo: str, mensaje: str, data: dict[str, str] | None = None) -> str | None:
    try:
        inicializar_firebase()
        if not firebase_app:
            logger.warning(
                "Skipping mobile push send because Firebase Admin is not initialized",
                extra={"token_suffix": token[-12:] if token else "", "title": titulo, "data_keys": sorted((data or {}).keys())},
            )
            return None

        message = messaging.Message(
            token=token,
            notification=messaging.Notification(title=titulo, body=mensaje),
            data=data or {},
            android=messaging.AndroidConfig(
                priority="high",
                ttl=timedelta(seconds=30),
                direct_boot_ok=True,
                notification=messaging.AndroidNotification(
                    channel_id="emergency_alerts",
                    sound="default",
                    priority="max",
                    visibility="public",
                    default_sound=True,
                    default_vibrate_timings=True,
                    ticker=titulo,
                ),
            ),
            apns=messaging.APNSConfig(
                headers={
                    "apns-priority": "10",
                    "apns-push-type": "alert",
                },
                payload=messaging.APNSPayload(
                    aps=messaging.Aps(
                        sound="default",
                    )
                ),
            ),
        )
        response = messaging.send(message, app=firebase_app)
        logger.info(
            "Mobile push sent successfully",
            extra={"token_suffix": token[-12:] if token else "", "title": titulo, "message_id": response},
        )
        return response
    except FirebaseError as exc:
        if exc.__class__.__name__ == "UnregisteredError":
            logger.warning(
                "Firebase token is unregistered",
                extra={"token_suffix": token[-12:] if token else "", "title": titulo},
            )
            return "__UNREGISTERED__"
        logger.exception(
            "Firebase push send failed",
            extra={"token_suffix": token[-12:] if token else "", "title": titulo, "data_keys": sorted((data or {}).keys())},
        )
        return None
