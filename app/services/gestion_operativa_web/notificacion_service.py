from pathlib import Path
import json

from firebase_admin import credentials, initialize_app, messaging
from firebase_admin.exceptions import FirebaseError

from app.config import get_settings


firebase_app = None


def inicializar_firebase() -> None:
    global firebase_app
    if firebase_app:
        return

    settings = get_settings()
    raw_credentials = (settings.firebase_credentials or "").strip()
    if not raw_credentials:
        return

    try:
        if raw_credentials.startswith("{"):
            payload = json.loads(raw_credentials)
            firebase_app = initialize_app(credentials.Certificate(payload))
            return
        credentials_path = Path(raw_credentials)
        if credentials_path.exists():
            firebase_app = initialize_app(credentials.Certificate(str(credentials_path)))
    except Exception:
        firebase_app = None
        return


def enviar_notificacion_push(token: str, titulo: str, mensaje: str, data: dict[str, str] | None = None) -> str | None:
    try:
        inicializar_firebase()
        if not firebase_app:
            return None

        message = messaging.Message(
            token=token,
            notification=messaging.Notification(title=titulo, body=mensaje),
            data=data or {},
            android=messaging.AndroidConfig(
                priority="high",
                notification=messaging.AndroidNotification(
                    channel_id="emergency_alerts",
                    sound="default",
                    default_vibrate_timings=True,
                ),
            ),
        )
        return messaging.send(message, app=firebase_app)
    except FirebaseError:
        return None
