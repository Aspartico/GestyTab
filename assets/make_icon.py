"""Genera assets/arcanote.ico (documento con righe di nota + lente d'ingrandimento)."""
from PIL import Image, ImageDraw

S = 1024  # disegno ad alta risoluzione, poi ridotto


def draw():
    im = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    d.rounded_rectangle((32, 32, S - 32, S - 32), radius=200, fill="#0063b1")      # sfondo blu
    d.rounded_rectangle((200, 130, 740, 880), radius=50, fill="#ffffff")           # foglio
    d.polygon([(600, 130), (740, 270), (600, 270)], fill="#cce4f7")                # angolo piegato
    for i, w in enumerate((440, 380, 440, 300)):                                   # righe di testo
        y = 340 + i * 105
        d.rounded_rectangle((260, y, 260 + w - 100, y + 40), radius=20, fill="#9fb8cf")
    cx, cy, r = 650, 640, 190                                                      # lente
    d.line([(cx + 130, cy + 130), (900, 890)], fill="#ffb900", width=95)           # manico
    d.ellipse((cx - r - 20, cy - r - 20, cx + r + 20, cy + r + 20), fill="#ffb900")
    d.ellipse((cx - r + 30, cy - r + 30, cx + r - 30, cy + r - 30), fill="#ffffff")
    d.ellipse((cx - r + 30, cy - r + 30, cx + r - 30, cy + r - 30), outline="#ffb900", width=12)
    return im


if __name__ == "__main__":
    img = draw()
    img.save("assets/arcanote.ico", sizes=[(256, 256), (128, 128), (64, 64), (48, 48), (32, 32), (24, 24), (16, 16)])
    img.resize((256, 256), Image.LANCZOS).save("assets/arcanote_preview.png")
