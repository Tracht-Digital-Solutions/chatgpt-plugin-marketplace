# Prüfstand · 21. Oktober 2026

- Python 3.12.10 unter Windows.
- `python -m unittest discover -s tests -v`: 22 Tests bestanden.
- `python scripts/marketplace.py validate`: 1 Plugin im initialen Katalog geprüft.
- `setup-github.ps1`: PowerShell-Syntaxprüfung ohne Fehler.
- GitHub Actions noch nicht ausgeführt; Ziel-Repository noch nicht angelegt.

Die Tests prüfen die Zusammenführung mit simulierten GitHub-API-Antworten, einschließlich binärer Base64-Blobs, Git-Hashes, Release-Versionen, Pfadsicherheit, Quellenentdeckung, Pagination, Dubletten, privater Quellen und manipulierter Dateien. Sie prüfen außerdem reproduzierbare ZIPs und ausführbare Dateimodi. Sie belegen keine echte ChatGPT-Installation oder OAuth-Verbindung.

Der initiale Git-Verweis wurde anhand des öffentlichen Repositorys `jtracht/gitea-chatgpt-plugin` geprüft: Release `v0.3.2`, Commit `1d40293b6288f133d284763a90ecdd275f375285`, Plugin-Unterordner `plugin`. Die vollständigen Plugin-Dateien werden erst beim ersten erfolgreichen Sync in das neue Repository übernommen.
