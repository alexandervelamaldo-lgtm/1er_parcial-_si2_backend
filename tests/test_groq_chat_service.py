from app.services.inteligencia_automatizacion.groq_chat_service import _extraer_respuesta


def test_extraer_respuesta_desde_payload_de_groq() -> None:
    payload = {
        "choices": [
            {"message": {"role": "assistant", "content": "Hola, ¿en qué te ayudo?"}},
        ]
    }
    assert _extraer_respuesta(payload) == "Hola, ¿en qué te ayudo?"


def test_extraer_respuesta_recorta_espacios() -> None:
    payload = {"choices": [{"message": {"content": "  con espacios  "}}]}
    assert _extraer_respuesta(payload) == "con espacios"


def test_extraer_respuesta_retorna_vacio_sin_choices() -> None:
    assert _extraer_respuesta({}) == ""
    assert _extraer_respuesta({"choices": []}) == ""


def test_extraer_respuesta_retorna_vacio_con_estructura_invalida() -> None:
    assert _extraer_respuesta({"choices": [{"message": "no es un dict"}]}) == ""
    assert _extraer_respuesta({"choices": [{}]}) == ""
