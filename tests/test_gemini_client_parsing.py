from app.services.inteligencia_automatizacion.gemini_client import _extract_text, _try_parse_json


def test_extract_text_from_gemini_response() -> None:
    payload = {
        "candidates": [
            {
                "content": {
                    "parts": [
                        {"text": "hola"},
                    ]
                }
            }
        ]
    }
    assert _extract_text(payload) == "hola"


def test_try_parse_json_extracts_first_object() -> None:
    text = "respuesta:\n```json\n{\"a\":1,\"b\":\"x\"}\n```"
    out = _try_parse_json(text)
    assert out == {"a": 1, "b": "x"}


def test_try_parse_json_returns_none_when_missing_object() -> None:
    assert _try_parse_json("sin json") is None

