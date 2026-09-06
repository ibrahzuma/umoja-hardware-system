"""Generate docs/Umoja_System_Diagram.pdf — how the system works, in diagrams.

Five landscape pages: what the system covers, how the pieces are wired, the
purchase-to-stock flow (including the admin sign-off on short deliveries), the
sale-to-dispatch flow, and who does what.

    python scripts/build_architecture_pdf.py
"""

from __future__ import annotations

import math
from pathlib import Path

from reportlab.lib.colors import HexColor, white
from reportlab.lib.pagesizes import A4, landscape
from reportlab.pdfgen import canvas as pdfcanvas

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs" / "Umoja_System_Diagram.pdf"
LOGO = ROOT / "static" / "img" / "logo.png"

PAGE_W, PAGE_H = landscape(A4)          # 842 x 595
MARGIN = 34

# Palette shared with the training guide (scripts/build_user_guide.py)
BRAND = HexColor("#4055E6")
INK = HexColor("#141A23")
MUTED = HexColor("#6B7686")
ACCENT = HexColor("#16A37B")
DANGER = HexColor("#D93B3B")
WARN = HexColor("#E08A1E")
PAPER = HexColor("#F7F8FB")
LINE = HexColor("#D6DAE4")

BODY = "Helvetica"
BOLD = "Helvetica-Bold"


# --------------------------------------------------------------------------
# drawing helpers
# --------------------------------------------------------------------------

def wrap(c, text, font, size, max_w):
    """Greedy wrap of `text` to `max_w` points."""
    c.setFont(font, size)
    words, lines, line = text.split(), [], ""
    for w in words:
        trial = f"{line} {w}".strip()
        if c.stringWidth(trial, font, size) <= max_w:
            line = trial
        else:
            if line:
                lines.append(line)
            line = w
    if line:
        lines.append(line)
    return lines


def box(c, x, y, w, h, title, lines=(), *, fill=white, stroke=LINE,
        title_color=INK, accent=None, title_size=10, body_size=7.6):
    """Rounded box with a bold title and optional wrapped body lines.

    (x, y) is the bottom-left corner. `accent` paints a thick left edge.
    """
    c.setFillColor(fill)
    c.setStrokeColor(stroke)
    c.setLineWidth(1)
    c.roundRect(x, y, w, h, 5, stroke=1, fill=1)

    if accent is not None:
        c.setFillColor(accent)
        c.roundRect(x, y, 3.5, h, 1.75, stroke=0, fill=1)

    pad = 8 if accent is None else 11
    ty = y + h - 13
    c.setFillColor(title_color)
    c.setFont(BOLD, title_size)
    for ln in wrap(c, title, BOLD, title_size, w - pad - 6):
        c.drawString(x + pad, ty, ln)
        ty -= title_size + 1.5

    c.setFillColor(MUTED)
    c.setFont(BODY, body_size)
    ty -= 2
    for item in lines:
        for ln in wrap(c, item, BODY, body_size, w - pad - 6):
            c.drawString(x + pad, ty, ln)
            ty -= body_size + 2.2


def arrowhead(c, x, y, angle, color, size=6):
    c.setFillColor(color)
    p = c.beginPath()
    p.moveTo(x, y)
    p.lineTo(x - size * math.cos(angle - 0.42), y - size * math.sin(angle - 0.42))
    p.lineTo(x - size * math.cos(angle + 0.42), y - size * math.sin(angle + 0.42))
    p.close()
    c.drawPath(p, stroke=0, fill=1)


def arrow(c, points, color=BRAND, label=None, dashed=False, label_at=0.5,
          label_dy=5, label_size=7.4, width=1.4):
    """Polyline arrow through `points`, arrowhead on the final segment."""
    c.setStrokeColor(color)
    c.setLineWidth(width)
    c.setDash(3, 3) if dashed else c.setDash()
    path = c.beginPath()
    path.moveTo(*points[0])
    for pt in points[1:]:
        path.lineTo(*pt)
    c.drawPath(path, stroke=1, fill=0)
    c.setDash()

    (x1, y1), (x2, y2) = points[-2], points[-1]
    arrowhead(c, x2, y2, math.atan2(y2 - y1, x2 - x1), color)

    if label:
        # place the label along the longest segment
        best, blen = (points[0], points[1]), -1
        for a, b in zip(points, points[1:]):
            d = math.hypot(b[0] - a[0], b[1] - a[1])
            if d > blen:
                best, blen = (a, b), d
        (ax, ay), (bx, by) = best
        lx = ax + (bx - ax) * label_at
        ly = ay + (by - ay) * label_at + label_dy
        c.setFont(BOLD, label_size)
        tw = c.stringWidth(label, BOLD, label_size)
        c.setFillColor(white)
        c.rect(lx - tw / 2 - 2, ly - 2, tw + 4, label_size + 2, stroke=0, fill=1)
        c.setFillColor(color)
        c.drawCentredString(lx, ly, label)


def page_header(c, eyebrow, title, subtitle=None):
    c.setFillColor(PAPER)
    c.rect(0, PAGE_H - 74, PAGE_W, 74, stroke=0, fill=1)
    c.setStrokeColor(LINE)
    c.setLineWidth(1)
    c.line(0, PAGE_H - 74, PAGE_W, PAGE_H - 74)

    c.setFillColor(BRAND)
    c.setFont(BOLD, 8)
    c.drawString(MARGIN, PAGE_H - 30, eyebrow.upper())
    c.setFillColor(INK)
    c.setFont(BOLD, 17)
    c.drawString(MARGIN, PAGE_H - 50, title)
    if subtitle:
        c.setFillColor(MUTED)
        c.setFont(BODY, 8.6)
        c.drawString(MARGIN, PAGE_H - 64, subtitle)


def page_footer(c, n):
    c.setFillColor(MUTED)
    c.setFont(BODY, 7.4)
    c.drawString(MARGIN, 20, "Umoja Hardware System — how it works")
    c.drawRightString(PAGE_W - MARGIN, 20, f"{n}")


def note(c, x, y, w, text, color=BRAND, h=30):
    c.setFillColor(HexColor("#EEF1FE") if color == BRAND else HexColor("#FDF3E7"))
    c.setStrokeColor(color)
    c.setLineWidth(0.8)
    c.roundRect(x, y, w, h, 4, stroke=1, fill=1)
    c.setFillColor(color)
    c.setFont(BOLD, 8)
    ty = y + h - 12
    for ln in wrap(c, text, BOLD, 8, w - 16):
        c.drawString(x + 9, ty, ln)
        ty -= 10


# --------------------------------------------------------------------------
# page 1 — cover + what the system covers
# --------------------------------------------------------------------------

def page_cover(c):
    c.setFillColor(BRAND)
    c.rect(0, 0, PAGE_W, PAGE_H, stroke=0, fill=1)

    if LOGO.exists():
        try:
            # The logo art has a dark background baked in, so it sits on a dark
            # card rather than straight on the blue.
            c.setFillColor(INK)
            c.roundRect(MARGIN, PAGE_H - 122, 80, 80, 8, stroke=0, fill=1)
            c.drawImage(str(LOGO), MARGIN + 6, PAGE_H - 116, width=68, height=68,
                        mask="auto", preserveAspectRatio=True)
        except Exception:
            pass

    c.setFillColor(white)
    c.setFont(BOLD, 34)
    c.drawString(MARGIN, PAGE_H - 175, "Umoja Hardware System")
    c.setFont(BODY, 14)
    c.drawString(MARGIN, PAGE_H - 199, "How the system works — in diagrams")

    c.setStrokeColor(white)
    c.setLineWidth(2)
    c.line(MARGIN, PAGE_H - 214, MARGIN + 120, PAGE_H - 214)

    intro = ("A multi-branch ERP for a hardware retailer: it follows goods from the supplier "
             "into the store and out to the customer, and follows the money both ways. "
             "Every screen belongs to a role, and every role sees only its own work.")
    c.setFont(BODY, 10.5)
    ty = PAGE_H - 240
    for ln in wrap(c, intro, BODY, 10.5, 386):
        c.drawString(MARGIN, ty, ln)
        ty -= 15

    stats = [("7", "business modules"), ("12", "roles"), ("3", "ways to connect"), ("1", "shared database")]
    sy = 150
    for i, (num, label) in enumerate(stats):
        sx = MARGIN + i * 106
        c.setFillColor(white)
        c.setFont(BOLD, 26)
        c.drawString(sx, sy, num)
        c.setFillColor(HexColor("#C8CFF7"))
        c.setFont(BODY, 8)
        for j, ln in enumerate(wrap(c, label, BODY, 8, 96)):
            c.drawString(sx, sy - 13 - j * 10, ln)

    contents = [
        ("1", "What the system covers", "the four areas of the business"),
        ("2", "How the pieces are wired", "clients, server, database, live updates"),
        ("3", "Goods in: purchase to stock", "ordering, delivery checks, admin sign-off"),
        ("4", "Goods out: sale to delivery", "quote, sale, approval, dispatch"),
        ("5", "Who does what", "the roles and their daily work"),
    ]
    bx, by = 470, PAGE_H - 250
    for i, (num, title, sub) in enumerate(contents):
        y = by - i * 46
        c.setFillColor(HexColor("#5E70EA"))
        c.roundRect(bx, y - 8, 330, 38, 5, stroke=0, fill=1)
        c.setFillColor(white)
        c.setFont(BOLD, 15)
        c.drawString(bx + 13, y + 10, num)
        c.setFont(BOLD, 10.5)
        c.drawString(bx + 34, y + 15, title)
        c.setFont(BODY, 8.4)
        c.drawString(bx + 34, y + 3, sub)

    c.setFillColor(HexColor("#C8CFF7"))
    c.setFont(BODY, 8)
    c.drawString(MARGIN, 30, "Generated from the codebase · scripts/build_architecture_pdf.py")
    c.showPage()


def page_coverage(c):
    page_header(c, "1 · Scope", "What the system covers",
                "Four areas of the business, one database. A movement recorded in one area shows up in the others.")

    top = PAGE_H - 110
    col_w, gap = 188, 16
    xs = [MARGIN + i * (col_w + gap) for i in range(4)]
    groups = [
        ("Goods in", ACCENT, [
            ("Suppliers", "who we buy from"),
            ("Purchase orders", "what we ordered, at what price"),
            ("Delivery checks", "what actually arrived"),
            ("Goods received notes", "store confirmation"),
            ("Stock per branch", "live quantity, low-stock alerts"),
        ]),
        ("Goods out", BRAND, [
            ("Quotations", "prices offered to a customer"),
            ("Sales (POS)", "cash, credit, part-deposit"),
            ("Order approval", "a manager releases the order"),
            ("Dispatch", "vehicle, driver, delivery note"),
            ("Customers", "who we sell to"),
        ]),
        ("Money", WARN, [
            ("Expenses & income", "with receipts attached"),
            ("Debtors", "what customers still owe"),
            ("Supplier payments", "what we owe, what we paid"),
            ("Taxes", "VAT, PAYE, SDL and the rest"),
            ("CRM register", "customer trade history, credit"),
        ]),
        ("People", HexColor("#8E5BD9"), [
            ("Users & roles", "who may do what"),
            ("Employees", "records, documents, contracts"),
            ("Attendance & leave", "who is in, who is off"),
            ("Payroll", "NSSF, NHIF, PAYE, net pay"),
            ("Performance", "reviews and discipline"),
        ]),
    ]

    for x, (name, colour, items) in zip(xs, groups):
        c.setFillColor(colour)
        c.roundRect(x, top - 26, col_w, 26, 4, stroke=0, fill=1)
        c.setFillColor(white)
        c.setFont(BOLD, 11)
        c.drawString(x + 11, top - 18, name)

        y = top - 34
        for label, desc in items:
            box(c, x, y - 40, col_w, 40, label, [desc], accent=colour)
            y -= 46

    band_y = 118
    c.setFillColor(PAPER)
    c.setStrokeColor(LINE)
    c.roundRect(MARGIN, band_y, PAGE_W - 2 * MARGIN, 74, 5, stroke=1, fill=1)
    c.setFillColor(INK)
    c.setFont(BOLD, 10)
    c.drawString(MARGIN + 14, band_y + 55, "Underneath all four: one shared spine")
    spine = [
        ("Branches", "every stock figure, sale and expense belongs to a branch"),
        ("Roles", "the same screen shows different things to different roles"),
        ("Audit trail", "who changed what, and when"),
        ("Live updates", "stock and new sales appear without a refresh"),
    ]
    sw = (PAGE_W - 2 * MARGIN - 28) / 4
    for i, (t, d) in enumerate(spine):
        sx = MARGIN + 14 + i * sw
        c.setFillColor(BRAND)
        c.setFont(BOLD, 8.6)
        c.drawString(sx, band_y + 36, t)
        c.setFillColor(MUTED)
        c.setFont(BODY, 7.6)
        yy = band_y + 25
        for ln in wrap(c, d, BODY, 7.6, sw - 12):
            c.drawString(sx, yy, ln)
            yy -= 9.5

    page_footer(c, 2)
    c.showPage()


# --------------------------------------------------------------------------
# page 2 — architecture
# --------------------------------------------------------------------------

def column_label(c, x, text):
    c.setFillColor(MUTED)
    c.setFont(BOLD, 8)
    c.drawString(x, 496, text)


def page_architecture(c):
    page_header(c, "2 · Wiring", "How the pieces are wired",
                "Three ways in, one server, one database. The web pages and the phone app use the same API.")

    cw, sw, rw = 176, 250, 196
    sx = MARGIN + cw + 78          # server column
    rx = sx + sw + 74              # storage column  (ends at 808 = page - margin)

    column_label(c, MARGIN, "WHO CONNECTS")
    column_label(c, sx, "THE SERVER (one Linode host)")
    column_label(c, rx, "WHERE THINGS LIVE")

    clients = [
        ("Office browser", ["Full system. Every role works here.", "Server-rendered pages + JavaScript."]),
        ("Android app", ["Field sales: POS, stock look-up, customers,", "quotations. Signs in with a token."]),
        ("Desktop app", ["Windows/macOS shell that opens the", "same site in a window."]),
    ]
    for i, (t, body) in enumerate(clients):
        box(c, MARGIN, 428 - i * 62, cw, 52, t, body, accent=BRAND)

    box(c, MARGIN, 200, cw, 96, "Signing in",
        ["Sessions expire after 20 minutes idle.",
         "Cookies are renamed and locked to the site.",
         "The admin page is not at /admin/.",
         "The phone app uses a token, not a cookie."], accent=DANGER)

    box(c, sx, 440, sw, 40, "nginx",
        ["Terminates HTTPS, serves uploaded files,", "passes everything else back."], accent=INK)
    box(c, sx, 376, sw, 44, "daphne — ASGI server",
        ["Runs Django. ASGI, not WSGI, because the", "app holds live connections open."], accent=INK)

    dj_y, dj_h = 186, 170
    c.setFillColor(HexColor("#EEF1FE"))
    c.setStrokeColor(BRAND)
    c.setLineWidth(1.2)
    c.roundRect(sx, dj_y, sw, dj_h, 6, stroke=1, fill=1)
    c.setFillColor(BRAND)
    c.setFont(BOLD, 10.5)
    c.drawString(sx + 12, dj_y + dj_h - 17, "Django — the application")
    c.setFillColor(MUTED)
    c.setFont(BODY, 7.4)
    c.drawString(sx + 12, dj_y + dj_h - 29, "One app per part of the business:")

    for i, name in enumerate(["core", "users", "inventory", "sales", "finance", "hr", "crm"]):
        px = sx + 12 + (i % 4) * 58
        py = dj_y + dj_h - 52 - (i // 4) * 24
        c.setFillColor(white)
        c.setStrokeColor(BRAND)
        c.setLineWidth(0.8)
        c.roundRect(px, py, 52, 18, 3, stroke=1, fill=1)
        c.setFillColor(BRAND)
        c.setFont(BOLD, 7.6)
        c.drawCentredString(px + 26, py + 6, name)

    c.setFillColor(MUTED)
    c.setFont(BODY, 7.4)
    yy = dj_y + 42
    for ln in ["Every app serves BOTH the web pages and the REST",
               "API — so the browser and the phone app always",
               "agree on the same rules and the same figures."]:
        c.drawString(sx + 12, yy, ln)
        yy -= 10

    box(c, rx, 396, rw, 84, "PostgreSQL",
        ["Every product, sale, expense, employee", "and CRM record lives here.",
         "It also keeps the history rows: who", "changed what, and when."], accent=ACCENT)
    box(c, rx, 306, rw, 80, "Live updates",
        ["When stock moves or a sale is made, the", "server pushes a message to every open",
         "screen — no refresh needed. Low stock", "raises an alert the same way."], accent=WARN)
    box(c, rx, 210, rw, 86, "Files on disk",
        ["Expense receipt photos, the company", "logo, employee photos, and the Android",
         "app that staff download from the", "login page."], accent=MUTED)

    for i in range(3):
        arrow(c, [(MARGIN + cw, 454 - i * 62), (sx - 6, 460)], BRAND)
    arrow(c, [(sx + sw / 2, 440), (sx + sw / 2, 424)], INK)
    arrow(c, [(sx + sw / 2, 376), (sx + sw / 2, 360)], INK)
    arrow(c, [(sx + sw, 300), (rx - 5, 408)], ACCENT, label="reads / writes")
    arrow(c, [(sx + sw, 262), (rx - 5, 330)], WARN, label="pushes")

    note(c, MARGIN, 104, PAGE_W - 2 * MARGIN,
         "Why it matters: because the phone app and the web pages go through the same API, a rule fixed in one "
         "place is fixed everywhere. Nothing is written twice, so the two cannot disagree.", BRAND, h=32)

    page_footer(c, 3)
    c.showPage()


# --------------------------------------------------------------------------
# page 3 — purchase to stock
# --------------------------------------------------------------------------

def page_purchase_flow(c):
    page_header(c, "3 · Goods in", "From purchase order to stock on the shelf",
                "Three people sign off before goods count as stock. A short delivery cannot be waved through.")

    bw, bh = 140, 54
    x1, x2, x3 = MARGIN, MARGIN + 180, MARGIN + 360      # 34..174, 214..354, 394..534
    rX, rW = 560, 200                                     # right column 560..760
    lane_reject = 346                                     # vertical routing lane
    yA = 430                                              # main row

    box(c, x1, yA, bw, bh, "1 · Afisa Ugavi",
        ["Raises the purchase order:", "supplier, items, quantities."], accent=BRAND)
    box(c, x2, yA, bw, bh, "2 · Goods arrive",
        ["Afisa Ugavi confirms it and", "sends it to the store."], accent=BRAND)
    box(c, x3, yA, bw, bh, "3 · Store Manager",
        ["Opens that order alone and checks", "each item, one at a time."], accent=BRAND)

    arrow(c, [(x1 + bw, yA + bh / 2), (x2 - 6, yA + bh / 2)], BRAND, label="confirm")
    arrow(c, [(x2 + bw, yA + bh / 2), (x3 - 6, yA + bh / 2)], BRAND)

    box(c, rX, 438, rW, 48, "RECEIVED",
        ["Stock goes up. The order is closed."],
        fill=HexColor("#E8F7F1"), stroke=ACCENT, title_color=ACCENT, accent=ACCENT)
    arrow(c, [(x3 + bw / 2, yA + bh), (x3 + bw / 2, 505), (rX + rW / 2, 505), (rX + rW / 2, 492)],
          ACCENT, label="everything arrived")

    box(c, x3, 330, bw, bh, "4 · Afisa Ugavi explains",
        ["Why is it short? He writes the", "reason on the order."], accent=WARN)
    arrow(c, [(x3 + bw / 2, yA), (x3 + bw / 2, 390)], WARN, label="something is short", label_at=0.55)

    box(c, x3, 200, bw, 58, "5 · Admin decides",
        ["Sees ordered against arrived, plus", "the explanation. Confirms or rejects."], accent=DANGER)
    arrow(c, [(x3 + bw / 2, 330), (x3 + bw / 2, 264)], WARN)

    box(c, rX, 250, rW, 54, "CONFIRM — what arrived is fine",
        ["Only the delivered quantity goes", "into stock. The rest stays owing."],
        fill=HexColor("#E8F7F1"), stroke=ACCENT, title_color=ACCENT, accent=ACCENT)
    box(c, rX, 158, rW, 54, "REJECT — explain again",
        ["Back to Afisa Ugavi. Nothing at all", "is added to stock."],
        fill=HexColor("#FDEDED"), stroke=DANGER, title_color=DANGER, accent=DANGER)

    arrow(c, [(x3 + bw, 244), (rX - 6, 272)], ACCENT)
    arrow(c, [(x3 + bw, 214), (rX - 6, 190)], DANGER)

    # rejected: back round to the explanation, nothing banked
    arrow(c, [(rX, 176), (lane_reject, 176), (lane_reject, 357), (x3 - 6, 357)],
          DANGER, dashed=True, label="back for a better reason")

    # confirmed and nothing left owing
    arrow(c, [(rX + rW / 2, 304), (rX + rW / 2, 432)], ACCENT, label="nothing left owing", label_at=0.42)

    # confirmed but a balance is still owed
    box(c, x1, 250, 250, 76, "PARTIALLY RECEIVED",
        ["The order stays open showing exactly", "what is still owed. Nothing is lost:",
         "when the balance is delivered, Afisa", "Ugavi confirms it and it goes round again."],
        fill=HexColor("#FFF6E9"), stroke=WARN, title_color=HexColor("#9A5B04"), accent=WARN)
    # routed around the outside rather than straight across, so it crosses
    # none of the other paths
    arrow(c, [(rX + rW, 277), (790, 277), (790, 140), (x1 + 125, 140), (x1 + 125, 244)],
          ACCENT, label="balance still owed")
    arrow(c, [(x1 + 125, 326), (x1 + 125, 400), (x2 + bw / 2, 400), (x2 + bw / 2, yA - 6)],
          WARN, dashed=True, label="the rest arrives")

    note(c, MARGIN, 60, 396,
         "The rule that protects the stock figures: on a short delivery NOTHING is added to stock — not even the "
         "items that did arrive in full — until the Admin confirms it.", DANGER, h=44)
    note(c, MARGIN + 412, 60, PAGE_W - MARGIN - 412 - MARGIN,
         "An order can be delivered in instalments. Every round keeps what was counted, who explained the "
         "shortfall and what the Admin decided, so the whole delivery history stays on the order.", BRAND, h=44)

    page_footer(c, 4)
    c.showPage()


# --------------------------------------------------------------------------
# page 4 — sale to dispatch
# --------------------------------------------------------------------------

def page_sales_flow(c):
    page_header(c, "4 · Goods out", "From quotation to goods leaving the yard",
                "A sale is raised by one person and released by another. Stock only drops when the lorry is loaded.")

    yA = PAGE_H - 168
    bw, bh = 150, 58
    gap = 46                    # wide enough for the label that sits on each arrow
    xs = [MARGIN + i * (bw + gap) for i in range(4)]

    box(c, xs[0], yA, bw, bh, "1 · Sales Rep — quotation",
        ["Optional. Prices offered to a", "customer, printable as a PDF."], accent=MUTED)
    box(c, xs[1], yA, bw, bh, "2 · Sales Rep — POS",
        ["Records the sale: items, discount,", "cash / credit / part deposit."], accent=BRAND)
    box(c, xs[2], yA, bw, bh, "3 · Sales & Credit Manager",
        ["Checks the order and the customer's", "credit, then approves or declines."], accent=BRAND)
    box(c, xs[3], yA, bw, bh, "4 · Store Manager — dispatch",
        ["Assigns a store keeper and a vehicle,", "then releases the goods."], accent=BRAND)

    arrow(c, [(xs[0] + bw, yA + bh / 2), (xs[1] - 6, yA + bh / 2)], MUTED, label="accepted")
    arrow(c, [(xs[1] + bw, yA + bh / 2), (xs[2] - 6, yA + bh / 2)], BRAND, label="pending")
    arrow(c, [(xs[2] + bw, yA + bh / 2), (xs[3] - 6, yA + bh / 2)], BRAND, label="approved")

    # declined
    box(c, xs[2], yA - 78, bw, 44, "DECLINED", ["The order is cancelled."],
        fill=HexColor("#FDEDED"), stroke=DANGER, title_color=DANGER, accent=DANGER)
    arrow(c, [(xs[2] + bw / 2, yA), (xs[2] + bw / 2, yA - 34)], DANGER, label="declined", label_at=0.5)

    # dispatched outcome
    yB = yA - 108
    box(c, xs[3], yB, bw, 66, "DISPATCHED",
        ["Stock drops by what was loaded.", "Invoice and delivery note print.", "The vehicle is marked busy."],
        fill=HexColor("#E8F7F1"), stroke=ACCENT, title_color=ACCENT, accent=ACCENT)
    arrow(c, [(xs[3] + bw / 2, yA), (xs[3] + bw / 2, yB + 66 + 6)], ACCENT)

    # money trail
    yC = 150
    c.setFillColor(MUTED)
    c.setFont(BOLD, 8)
    c.drawString(MARGIN, yC + 92, "AND THE MONEY")

    mw = (PAGE_W - 2 * MARGIN - 3 * 22) / 4
    money = [
        ("Paid in full", ["Cash, bank or mobile money.", "Recorded against the sale."], ACCENT),
        ("Part deposit", ["Some now, the rest on credit.", "The balance is tracked."], WARN),
        ("On credit", ["Nothing yet. The sale appears", "on the Debtors screen."], DANGER),
        ("Collections", ["The Accountant or Credit Manager", "records payments as they come in."], BRAND),
    ]
    for i, (t, body, colour) in enumerate(money):
        box(c, MARGIN + i * (mw + 22), yC, mw, 74, t, body, accent=colour)

    arrow(c, [(xs[1] + bw / 2, yA), (xs[1] + bw / 2, yC + 74 + 8)], MUTED, dashed=True,
          label="how was it paid?", label_at=0.62)

    note(c, MARGIN, 62, PAGE_W - 2 * MARGIN,
         "Two hands on every order: the person who raises the sale is never the person who releases it, and the "
         "goods are only deducted from stock at dispatch — so an order sitting unapproved never quietly drains "
         "the shelf.", BRAND, h=44)

    page_footer(c, 5)
    c.showPage()


# --------------------------------------------------------------------------
# page 5 — roles
# --------------------------------------------------------------------------

def page_roles(c):
    page_header(c, "5 · People", "Who does what",
                "Signing in decides what you see. Each role opens on its own workspace and its own dashboard.")

    roles = [
        ("Afisa Ugavi", "Procurement", BRAND,
         ["Raises purchase orders", "Confirms deliveries arrived", "Explains short deliveries",
          "Suppliers and supplier payments", "Trucks and transport costs"]),
        ("Store Manager", "The store floor", BRAND,
         ["Checks deliveries item by item", "Dispatches approved orders", "Stock transfers between branches",
          "Drivers and truck maintenance"]),
        ("Stock Controller", "Stock accuracy", ACCENT,
         ["Goods received notes", "Stock balances and adjustments", "Ageing and ABC reports"]),
        ("Store Keeper", "Physical goods", ACCENT,
         ["Verifies goods received", "Physical stock counts", "Loading and offloading"]),
        ("Sales Representative", "The shop counter", WARN,
         ["Quotations for customers", "Records sales at the POS", "Adds and looks after customers"]),
        ("Sales & Credit Manager", "Releasing and collecting", WARN,
         ["Approves or declines orders", "Chases credit customers", "Records payments received",
          "Watches the debtors list"]),
        ("Accountant", "The money", HexColor("#8E5BD9"),
         ["Expenses, income and banking", "Supplier payments and taxes", "Customer receipts and debtors",
          "The CRM customer register"]),
        ("HR Officer / Manager", "The people", HexColor("#8E5BD9"),
         ["Employee records and documents", "Leave and attendance", "Payroll and payslips",
          "Performance and discipline"]),
        ("Admin", "Oversight", DANGER,
         ["Confirms or rejects short deliveries", "Creates users and sets roles",
          "Sees every branch and every screen", "Company settings"]),
    ]

    cols, cw, gap = 3, 250, 20
    top = PAGE_H - 116
    for i, (name, tag, colour, duties) in enumerate(roles):
        col, row = i % cols, i // cols
        x = MARGIN + col * (cw + gap)
        y = top - row * 132 - 116
        c.setFillColor(white)
        c.setStrokeColor(LINE)
        c.setLineWidth(1)
        c.roundRect(x, y, cw, 116, 5, stroke=1, fill=1)
        c.setFillColor(colour)
        c.roundRect(x, y + 116 - 24, cw, 24, 5, stroke=0, fill=1)
        c.rect(x, y + 116 - 24, cw, 8, stroke=0, fill=1)
        c.setFillColor(white)
        c.setFont(BOLD, 10)
        c.drawString(x + 11, y + 116 - 17, name)
        c.setFont(BODY, 7.6)
        c.drawRightString(x + cw - 11, y + 116 - 17, tag)

        yy = y + 116 - 38
        c.setFont(BODY, 8)
        for d in duties:
            c.setFillColor(colour)
            c.circle(x + 14, yy + 3, 1.8, stroke=0, fill=1)
            c.setFillColor(INK)
            for ln in wrap(c, d, BODY, 8, cw - 34):
                c.drawString(x + 21, yy, ln)
                yy -= 10.5
            yy -= 3.5

    note(c, MARGIN, 40, PAGE_W - 2 * MARGIN,
         "A person can hold more than one role — a manager who also keeps the books sees both workspaces. "
         "What each role may open is set once by the administrator, not screen by screen.", BRAND, h=34)

    page_footer(c, 6)
    c.showPage()


def main():
    OUT.parent.mkdir(parents=True, exist_ok=True)
    c = pdfcanvas.Canvas(str(OUT), pagesize=landscape(A4))
    c.setTitle("Umoja Hardware System — How It Works")
    c.setAuthor("Umoja Hardware")

    page_cover(c)
    page_coverage(c)
    page_architecture(c)
    page_purchase_flow(c)
    page_sales_flow(c)
    page_roles(c)

    c.save()
    print(f"wrote {OUT.relative_to(ROOT)}  ({OUT.stat().st_size / 1024:.0f} KB)")


if __name__ == "__main__":
    main()
