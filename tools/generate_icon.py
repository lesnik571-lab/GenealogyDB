"""Generate Windows release icon assets from simple vector geometry."""

from pathlib import Path

from PIL import Image, ImageDraw


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "assets" / "app.ico"


def main():
    """Render the application icon at standard Windows sizes."""
    size = 256
    image = Image.new("RGBA", (size, size), "#f4f6f8")
    draw = ImageDraw.Draw(image)
    line = "#245b45"
    draw.line((128, 52, 128, 160), fill=line, width=16)
    draw.line((72, 116, 184, 116), fill=line, width=16)
    draw.line((72, 116, 72, 160), fill=line, width=16)
    draw.line((184, 116, 184, 160), fill=line, width=16)
    for center, color in (((128, 44), "#d7a53d"), ((72, 176), "#4f8bbd"), ((128, 176), "#d06e7c"), ((184, 176), "#4f8bbd")):
        x, y = center
        draw.ellipse((x - 24, y - 24, x + 24, y + 24), fill=color)
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    image.save(OUTPUT, sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])


if __name__ == "__main__":
    main()