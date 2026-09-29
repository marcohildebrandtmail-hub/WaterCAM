# WaterCAM 💧📷

[![HACS Custom](https://img.shields.io/badge/HACS-Custom-orange.svg)](https://hacs.xyz/)
[![Home Assistant](https://img.shields.io/badge/Home%20Assistant-2024%2B-blue.svg)](https://www.home-assistant.io/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

**WaterCAM** ist eine vollwertige Home Assistant Custom Integration und OCR-Lösung zur automatischen Auslesung digitaler LCD-Wasserzähler (**Axioma QALCOSONIC W1**) mittels einer fest montierten USB-Kamera (**Microsoft LifeCam Studio**).

Die Integration bindet den Wasserzähler als eigenständiges **Home Assistant Gerät (Device)** ein – inklusive Zählerstand-Sensor (kompatibel mit dem Home Assistant Energy/Water Dashboard), Diagnose-Sensoren, Live-Kamera-Streams und Sofort-Auslese-Button.

---

## 🌟 Highlights & Funktionen

- 💧 **Echtes Home Assistant Gerät:** Alle Entitäten, Kameras und Buttons übersichtlich an einem Ort gebündelt.
- ⚡ **Energy Dashboard kompatibel:** `sensor.watercam_zaehlerstand` mit `device_class: water`, `state_class: total_increasing` und Einheit `m³`.
- 📷 **Live-Kamera-Einbindung:**
  - `camera.watercam_live_snapshot`: Live-Vollbild mit farbigen Ziffern-Erkennungs-Boxen und Sicherheitsanzeige.
  - `camera.watercam_lcd_display`: Scharfer, unveränderter Display-Ausschnitt.
- 🎯 **Pixelgenaue 7-Segment-Erkennung:**
  - Robuste Segment-Abtastung für 6 Großziffern und 3 Nachkomma-Kleinziffern.
  - Vollständiger Mustervergleich statt einer störanfälligen globalen Pixelschwelle.
  - Zeitlicher Median und Mehrheitsprüfung über den gesamten Aufnahmestapel.
  - Physikalische Plausibilitätsprüfung gegen die maximale Durchflussmenge des Zählers.
  - Automatischer Nachtschutz: Bei zu dunklem Kamerabild wird höchstens alle 30 Minuten erneut geprüft; eine manuelle Messung bleibt sofort möglich.
- 📐 **Automatische Bildausrichtung:** Kleine Verschiebungen, Drehungen und Abstandsänderungen der Kamera werden anhand fester Gehäusemerkmale korrigiert, bevor die OCR-Boxen ausgewertet werden.
- 🔄 **On-Demand Messung:** Button `button.watercam_jetzt_auslesen` triggert per Klick sofort eine Neu-Messung via API.
- 🎛️ **Kamerasteuerung:** Manueller Fokus, Helligkeit, Belichtungszeit und automatische/manuelle Belichtung direkt am Home-Assistant-Gerät. Autofokus bleibt immer ausgeschaltet.
- ⏱️ **Messintervall:** Das OCR-Intervall lässt sich direkt in Home Assistant konfigurieren.
- 🌐 **UI Konfigurations-Flow:** Bequemes Hinzufügen über *Einstellungen → Geräte & Dienste*.

---

## 📦 Verfügbare Entitäten

| Plattform | Entity ID | Name | Beschreibung |
| :--- | :--- | :--- | :--- |
| **Sensor** | `sensor.watercam_zaehlerstand` | Zählerstand | Zählerstand in `m³` (für Wasserverbrauch / Energie-Dashboard) |
| **Sensor** | `sensor.watercam_sicherheit` | Erkennungs-Sicherheit | Sicherheitsabstand der Segmenterkennung |
| **Sensor** | `sensor.watercam_rohanzeige` | Rohanzeige | Raw-String aus der OCR (z.B. `000363.068`) |
| **Sensor** | `sensor.watercam_letzte_erfolgreiche_auslesung` | Letzte erfolgreiche Auslesung | Zeitpunkt des letzten erfolgreich akzeptierten Zählerstands |
| **Sensor** | `sensor.watercam_letzter_ausleseversuch` | Letzter Ausleseversuch | Zeitpunkt jedes OCR-Versuchs, auch wenn die Erkennung verworfen wurde |
| **Binary Sensor** | `binary_sensor.watercam_ocr_status` | OCR Status | Konnektivität und Erkennungsstatus (`on` = ok) |
| **Camera** | `camera.watercam_live_snapshot` | Live-Kamerabild (Annotiert) | Vollbild mit Erkennungs-Boxen (`/snapshot.jpg`) |
| **Camera** | `camera.watercam_lcd_display` | LCD-Display | Reiner LCD-Ausschnitt (`/display.jpg`) |
| **Button** | `button.watercam_jetzt_auslesen` | Jetzt auslesen | Triggert sofort eine neue Messung |
| **Number** | `number.watercam_fokus` | Fokus | Manueller Kamerafokus (`0–40`) |
| **Number** | `number.watercam_helligkeit` | Helligkeit | Kamerabild-Helligkeit (`30–255`) |
| **Number** | `number.watercam_belichtungszeit` | Belichtungszeit | Manuelle Belichtungszeit in Millisekunden |
| **Switch** | `switch.watercam_belichtung_automatisch` | Belichtung automatisch | Schaltet zwischen automatischer und manueller Belichtung um |
| **Number** | `number.watercam_ocr_messintervall` | OCR Messintervall | Zeit zwischen automatischen Messungen in Sekunden |

---

## 🚀 Installation in Home Assistant

### Option 1: HACS (Empfohlen)
1. Öffne **HACS** in Home Assistant.
2. Klicke oben rechts auf das Drei-Punkte-Menü und wähle **Benutzerdefinierte Repositories**.
3. Repository-URL eintragen: `https://github.com/marcohildebrandtmail-hub/WaterCAM`  
   Typ: **Integration**
4. Klicke auf **Hinzufügen** und installiere die Integration **WaterCAM**.
5. Starte Home Assistant neu.
6. Gehe zu **Einstellungen → Geräte & Dienste → Integration hinzufügen**, suche nach **WaterCAM** und gib die IP des OCR-Servers ein (Standard: `192.168.10.57`, Port `8080`).

### Option 2: Manuell
1. Kopiere den Ordner `custom_components/watercam` in deinen Home Assistant Ordner `config/custom_components/`.
2. Starte Home Assistant neu.
3. Füge die Integration über die Benutzeroberfläche hinzu.

---

## 🖥️ OCR Server (Proxmox LXC Container)

Der Server läuft als schlanker Dienst in einem Debian LXC Container (z.B. CT 121 auf Proxmox) mit durchgereichter USB-Kamera (`/dev/video0`).

Das kanonische Rohbild für die automatische Ausrichtung liegt dauerhaft als `alignment_reference.jpg` im konfigurierten `DATA_DIR`. Fehlt es bei einer Neuinstallation, wird es bei der ersten Aufnahme erzeugt.

### Dateien im Ordner `server/`:
- `water_meter_ocr.py`: Das Python-Skript zur Kamerasteuerung, 7-Segment-Analyse und HTTP-REST-API.
- `water-meter-ocr.service`: Systemd Service Unit für Autostart und automatische Wiederherstellung.

### REST-API Endpunkte (Port 8080):
- `GET /api/status`: JSON-Status mit Zählerstand, Rohwerten, Zeitstempel und Sicherheit.
- `POST /api/measure`: Triggert sofortige neue Messung.
- `GET /api/camera`: Liefert die gespeicherten Kameraeinstellungen.
- `GET /api/alignment`: Liefert Verschiebung, Drehung, Skalierung und Qualität der letzten automatischen Bildausrichtung.
- `POST /api/camera`: Speichert Kameraeinstellungen und triggert eine neue Messung.
- `POST /api/interval`: Speichert das OCR-Messintervall.
- `GET /snapshot.jpg`: Zuletzt aufgenommenes Vollbild mit Visualisierungs-Boxen.
- `GET /display.jpg`: Direkter LCD-Display-Ausschnitt.

---

## 📄 Lizenz

Dieses Projekt steht unter der [MIT License](LICENSE).
