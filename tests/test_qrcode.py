from app.services.qrcode import svg_for


def test_svg_contains_url_modules():
    svg = svg_for("http://newscast.local:8080")
    assert svg.startswith("<svg")
    assert "<path" in svg or "<rect" in svg
