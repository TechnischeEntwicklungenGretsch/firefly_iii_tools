#!/usr/bin/env python3
import subprocess
import re
import csv
import sys
import json
from pathlib import Path
from difflib import SequenceMatcher


DATE_RE = re.compile(r"^\d{2}\.\d{2}\.\d{4}")
AMOUNT_RE = re.compile(r"-?\d{1,3}(?:\.\d{3})*,\d{2}")
END_MARKER_RE = re.compile(
    r"(Kontostand am|Gesamtumsatzsummen|Ihr Dispositionskredit|Hinweise zum Kontoauszug|Deutsche Kreditbank AG|Seite \d+ von)",
    re.IGNORECASE
)

def fuzzy_score(a, b):
    return SequenceMatcher(None, a.lower(), b.lower()).ratio()

def get_fuzzy_declarations(text, threshold=0.4, limit=5):
    names = load_declarations()
    scored = []

    for name in names:
        score = fuzzy_score(text, name)
        if score >= threshold:
            scored.append((name, score))

    scored.sort(key=lambda x: x[1], reverse=True)
    return scored[:limit]

def clean_description(text):
    if not text:
        return ""
    # führende Satzzeichen + Leerzeichen entfernen
    return text.lstrip(" ,.;:-/)_")

def load_reference_db(path="references.json"):
    if not Path(path).exists():
        return {}
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)

def load_declarations(path="split_rules.json"):
    db = load_reference_db(path)
    # eindeutige Anzeigenamen, alphabetisch
    names = sorted(set(db.values()))
    return names

def ask_user_select_declaration(text):
    while True:
        fuzzy = get_fuzzy_declarations(text)

        if fuzzy:
            print("\n🔎 Ähnliche Deklarationen:")
            for i, (name, score) in enumerate(fuzzy, 1):
                print(f"[{i}] {name} ({int(score * 100)}%)")

            choice = input(
                "👉 Nummer wählen | a = alle anzeigen | n = neu: "
            ).strip().lower()

            if choice.isdigit():
                idx = int(choice) - 1
                if 0 <= idx < len(fuzzy):
                    return fuzzy[idx][0]

            if choice == "a":
                break  # → unten alle anzeigen
            elif choice == "n":
                return None
            else:
                print("⚠️ Ungültige Auswahl")
                continue
        else: 
            break
    
    # Fallback: komplette Liste
    names = load_declarations()
    if not names:
        return None

    print("\n📋 Alle Deklarationen:")
    for i, name in enumerate(names, 1):
        print(f"[{i}] {name}")

    choice = input(
        "👉 Nummer wählen | n = neu: "
    ).strip().lower()

    if choice.isdigit():
        idx = int(choice) - 1
        if 0 <= idx < len(names):
            return names[idx]

    if choice == "n":
        return None

    print("⚠️ Ungültige Auswahl")

def save_reference(key, value, path="references.json"):
    db = load_reference_db(path)
    db[key] = value
    with open(path, "w", encoding="utf-8") as f:
        json.dump(db, f, indent=2, ensure_ascii=False)

def pdf_to_text(pdf_path):
    result = subprocess.run(
        ["pdftotext", "-layout", pdf_path, "-"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    return result.stdout.splitlines()

def normalize_amount(val):
    if not val:
        return ""
    return val.replace(".", "").replace(",", ".")

def show_split_preview(text, idx):
    left = text[:idx]
    right = text[idx:]
    print("\n🔎 Trennvorschlag:")
    print(f"[{left}] | [{right}]")

def format_amount(b):
    if b.get("Soll"):
        return f"-{b['Soll']} EUR"
    if b.get("Haben"):
        return f"+{b['Haben']} EUR"
    return "0,00 EUR"


def parse_lines(lines):
    bookings = []
    current = None
    seen_description = False

    for line in lines:
        line = line.rstrip()

        # Neue Buchung
        if DATE_RE.match(line):
            if current:
                bookings.append(current)

            seen_description = False
            date = line[:10]
            line = line[10:].strip()

            current = {
                "Datum": date,
                "Buchungsart": "",
                "Textblock": "",
                "Soll": "",
                "Haben": "",
                }
            
            # Betrag extrahieren
            amounts = AMOUNT_RE.findall(line)
            if amounts:
                amount = normalize_amount(amounts[-1])
                if "-" in amounts[-1]:
                    current["Soll"] = amount
                else:
                    current["Haben"] = amount

            line = line[:20].strip()
            current["Buchungsart"] = line


        # Folgezeilen
        elif current:
            if END_MARKER_RE.search(line):
                bookings.append(current)
                current = None
                break

            clean = line.strip()
            if len(clean) < 2:
                continue

            # 👇 alles in EIN Feld
            if current["Textblock"]:
                current["Textblock"] += " " + clean
            else:
                current["Textblock"] = clean


    if current:
        bookings.append(current)

    return bookings

def apply_split_rule(text, rules):
    for key, value in rules.items():
        if key in text:
            return value, text[len(key):].strip()
    return None, None

def ask_user_for_split(b):
    text = b["Textblock"].strip()
    print("\n❓ Unklare Buchung:")
    print("=" * 60)
    print(f"📄 Datei: {b.get('Quelle', 'unbekannt')}")
    print(f"📅 Datum: {b.get('Datum', '')}")
    print(f"💶 Betrag: {format_amount(b)}")
    print("-" * 60)
    print(text)
    print("=" * 60)

    while True:
        marker = input(
            "👉 Letzte Zeichen/Ziffern des Namens eingeben: "
        ).strip()

        if not marker:
            return None, None, None

        text_lower = text.lower()
        marker_lower = marker.lower()

        idx = text_lower.rfind(marker_lower)
        if idx == -1:
            print("⚠️ Marker nicht im Text gefunden, nochmal probieren")
            continue
        
        break

    # Start-Trennpunkt = Ende des Markers
    split_idx = idx + len(marker)

    while True:
        show_split_preview(text, split_idx)

        try:
            shift = int(
                input("👉 Trennung verschieben (− links / + rechts / 0 = ok): ")
            )
        except ValueError:
            break

        if shift == 0 or shift == "":
            break

        split_idx += shift
        split_idx = max(0, min(len(text), split_idx))
    

    rest = text[split_idx:].strip()
    key = text[:split_idx].strip()

    selected = ask_user_select_declaration(key)
    if selected:
        return selected, rest, key

    name = input("👉 Name zum ersetzen (z.B. PayPal, wenn leer gelassen wird, wird gleicher Text hergenommen): ").strip()
    if not name:
        return key, rest, key

    return name, rest, key


def enrich_bookings(bookings, interactive=True):
    for b in bookings:
        rules = load_reference_db("split_rules.json")
        text = b["Textblock"].strip()

        label, rest = apply_split_rule(text, rules)
        if label:
            b["Name"] = label
            b["Beschreibung"] = clean_description(rest)
            continue

        if interactive:
            print("\n" + "=" * 60)
            print(f"📄 Aktive Datei: {b.get('Quelle', 'unbekannt')}")
            print("=" * 60)
            name, rest, key = ask_user_for_split(b)
            if name:
                b["Name"] = key
                b["Beschreibung"] = clean_description(rest)
                save_reference(key, name, "split_rules.json")
            else:
                b["Name"] = text
                b["Beschreibung"] = ""


    return bookings

def write_csv(bookings, out_path):
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow([
            "Datum",
            "Buchungsart",
            "Name",
            "Beschreibung",
            "Betrag_Soll_EUR",
            "Betrag_Haben_EUR",
            "Quelldatei",
        ])

        for b in bookings:
            writer.writerow([
                b["Datum"],
                b["Buchungsart"],
                b.get("Name", "").strip(),
                b.get("Beschreibung", "").strip(),
                b["Soll"],
                b["Haben"],
                b["Quelle"],
            ])

def process_folder(folder_path):
    all_bookings = []

    for pdf in sorted(Path(folder_path).glob("*.pdf")):
        print(f"→ Verarbeite {pdf.name}")
        lines = pdf_to_text(str(pdf))
        bookings = parse_lines(lines)

        for b in bookings:
            b["Quelle"] = pdf.name

        all_bookings.extend(bookings)

    return all_bookings

def main():
    if len(sys.argv) != 3:
        print("Usage:")
        print("  Einzeldatei: dkb_pdf_to_csv.py input.pdf output.csv")
        print("  Ordner:      dkb_pdf_to_csv.py pdf_ordner/ output.csv")
        sys.exit(1)

    input_path = Path(sys.argv[1])
    output_csv = sys.argv[2]

    if input_path.is_dir():
        bookings = process_folder(input_path)
        bookings = enrich_bookings(bookings, interactive=True)

    elif input_path.is_file():
        lines = pdf_to_text(str(input_path))
        bookings = parse_lines(lines)
        bookings = enrich_bookings(bookings, interactive=True)
        for b in bookings:
            b["Quelle"] = input_path.name
    else:
        print("❌ Ungültiger Eingabepfad")
        sys.exit(1)

    write_csv(bookings, output_csv)
    print(f"✔ {len(bookings)} Buchungen nach {output_csv} exportiert")

if __name__ == "__main__":
    main()
