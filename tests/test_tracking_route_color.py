from app.routers.gestion_solicitudes.solicitudes import tracking_route_color


def test_tracking_route_color_is_hex_and_deterministic() -> None:
    color_a = tracking_route_color(90)
    color_b = tracking_route_color(90)

    assert color_a == color_b
    assert color_a.startswith("#")
    assert len(color_a) == 7


def test_tracking_route_color_changes_between_requests() -> None:
    assert tracking_route_color(90) != tracking_route_color(91)
