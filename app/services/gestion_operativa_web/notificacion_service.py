from pathlib import Path

from firebase_admin import credentials, initialize_app, messaging
from firebase_admin.exceptions import FirebaseError

from app.config import get_settings


settings = get_settings()
firebase_app = None


def inicializar_firebase() -> None:
    global firebase_app
    if firebase_app or not settings.firebase_credentials:
        return

    credentials_path = Path(settings.firebase_credentials)
    if credentials_path.exists():
        firebase_app = initialize_app(credentials.Certificate(str(credentials_path)))


def enviar_notificacion_push(token: str, titulo: str, mensaje: str, data: dict[str, str] | None = None) -> str | None:
    try:
        inicializar_firebase()
        if not firebase_app:
            return None

        message = messaging.Message(
            token=token,
            notification=messaging.Notification(title=titulo, body=mensaje),
            data=data or {},
        )
        return messaging.send(message, app=firebase_app)
    except FirebaseError:
        return None
