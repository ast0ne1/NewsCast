import segno


def svg_for(url: str) -> str:
    qr = segno.make(url, error="m")
    return qr.svg_inline(scale=6, border=2)
