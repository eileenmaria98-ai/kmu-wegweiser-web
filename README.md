# KMU-Wegweiser Basel-Stadt – Website

Bewilligungen, Fristen und Ämter für KMU in Basel-Stadt, nach Situation sortiert.
Ein Angebot des Gewerbeverbands Basel-Stadt.

Diese Dateien werden aus dem (privaten) App-Projekt übernommen – hier nicht von Hand ändern.
`data.json` wird auch von der iPhone-App geladen, damit Inhaltskorrekturen ohne App-Update ankommen.

## Baustellen-Karte (`/karte/`)
Karte mit Baustellen, Verkehrsführungen, Demos und Veranstaltungen (data.bs.ch), Routenprüfung und «Mein Geschäft» (im Browser gespeichert).
`karte/data.json` wird mit `tools/basel_karte.py` erstellt: `python3 -u tools/basel_karte.py karte`
