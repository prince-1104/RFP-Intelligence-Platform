"""Generate synthetic native PDF, HTML and optional scanned PDF for local verification."""

import argparse
import io
from pathlib import Path

import pymupdf
from PIL import Image, ImageDraw, ImageFont


def create_sample(output: Path, include_scan: bool = False) -> None:
    output.mkdir(parents=True, exist_ok=True)
    (output / "notice.html").write_text(
        """<!doctype html><html><head>
    <meta charset="utf-8"><title>Synthetic equipment bid</title></head><body>
    <h1 id="notice">Synthetic procurement notice</h1><p>Date issued: 2026-01-15</p>
    <p>Supply equipment for a fictional demonstration. No real bid is represented.</p>
    <a href="specifications.pdf#page=1">Read specifications</a>
    <table id="items"><tr><th>Model</th><th>Quantity</th></tr>
    <tr><td>PX-420</td><td>20</td></tr></table></body></html>""",
        encoding="utf-8",
    )
    with pymupdf.open() as document:
        page = document.new_page()
        page.insert_text((50, 50), "Synthetic specifications: supply twenty pumps model PX-420.")
        for x in (50, 220, 390):
            page.draw_line((x, 100), (x, 200))
        for y in (100, 150, 200):
            page.draw_line((50, y), (390, y))
        for x, y, value in [
            (60, 120, "Model"),
            (230, 120, "Quantity"),
            (60, 170, "PX-420"),
            (230, 170, "20"),
        ]:
            page.insert_text((x, y), value)
        page = document.new_page()
        page.insert_text(
            (50, 50), "Second page: installation and documentation must accompany delivery."
        )
        document.save(output / "specifications.pdf")
    if include_scan:
        image = Image.new("RGB", (1600, 600), "white")
        try:
            draw = ImageDraw.Draw(image)
            font = ImageFont.load_default(size=48)
            draw.text((70, 100), "SCANNED PROCUREMENT NOTICE", font=font, fill="black")
            draw.text((70, 220), "Supply twenty pumps model PX-420.", font=font, fill="black")
            draw.text((70, 340), "Include installation and documentation.", font=font, fill="black")
            with io.BytesIO() as buffer:
                image.save(buffer, format="PNG")
                with pymupdf.open() as document:
                    page = document.new_page(width=800, height=300)
                    page.insert_image(page.rect, stream=buffer.getvalue())
                    document.save(output / "scanned-notice.pdf")
        finally:
            image.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("artifacts/synthetic_bid"))
    parser.add_argument("--include-scan", action="store_true")
    args = parser.parse_args()
    create_sample(args.output, args.include_scan)
    print(f"Synthetic sources written to {args.output}")
