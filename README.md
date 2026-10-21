# ChatGPT Plugin Marketplace

Ein zentraler GitHub-Marktplatz für die ChatGPT-/Codex-Plugins von Julian Tracht. GitHub Actions führt stabile Plugin-Releases automatisch zu einem nativen Katalog zusammen.

**Ziel-Repository:** `jtracht/chatgpt-plugin-marketplace`.

Gegenstück zum [Claude Code Plugin Marketplace](https://github.com/jtracht/claude-plugin-marketplace): gleiche Discovery per Topic, gleiche Auslöser und gleicher `DISPATCH_TOKEN`.

## Einmalig veröffentlichen

Voraussetzungen: Git, GitHub CLI und Python 3.12 oder neuer. ZIP in einen neuen Ordner entpacken, darin PowerShell öffnen und ausführen:

```powershell
gh auth login
.\setup-github.ps1
```

Das Skript prüft das Konto `jtracht`, führt die Tests aus, erstellt das öffentliche Repository und pusht den initialen Commit. Es überschreibt kein bestehendes GitHub-Repository. Nach dem Push startet **Actions → Sync plugin marketplace** automatisch. Bei fehlender Anmeldung wird abgebrochen; Tokens werden nicht in Projektdateien geschrieben.

Alternativ ein leeres öffentliches Repository namens `chatgpt-plugin-marketplace` unter `jtracht` anlegen und den Projektinhalt dorthin pushen. Keine fremden Geheimnisse oder privaten Plugin-Quellen hinzufügen.

## Automatische Zusammenführung

- **Alle sechs Stunden:** Neueste veröffentlichte stabile Releases abrufen.
- **Bei Änderungen an Konfiguration oder Collector:** Sofort synchronisieren.
- **Manuell:** Actions → Sync plugin marketplace → Run workflow.
- **Sofort nach einem Plugin-Release:** Das Plugin-Repo sendet ein `repository_dispatch` mit Typ `plugin-updated` (Secret `DISPATCH_TOKEN`, Fine-grained PAT mit *Contents: read & write* auf dieses Repository):

  ```yaml
  - name: Notify marketplace
    run: gh api repos/jtracht/chatgpt-plugin-marketplace/dispatches -f event_type=plugin-updated
    env:
      GH_TOKEN: ${{ secrets.DISPATCH_TOKEN }}
  ```

Quellen werden in `marketplace.config.json` festgelegt. Zusätzlich durchsucht die Pipeline die öffentlichen Repositories von `jtracht` und `Tracht-Digital-Solutions` nach dem Topic `chatgpt-plugin`. Sie ignoriert Forks und archivierte Repositories. Neue passende Repositories werden automatisch übernommen; das Topic deshalb nur für Plugins setzen, die veröffentlicht werden sollen. Über `exclude` in `marketplace.config.json` lassen sich gefundene Repositories ausschließen.

Für neue Plugins:

1. Portables Plugin mit `plugin/plugin.json`, optional `plugin/mcp.json`, `plugin/skills/` und Assets bereitstellen.
2. Eine stabile Version veröffentlichen, z. B. `v1.2.3`; `plugin.json` muss dieselbe Version `1.2.3` enthalten.
3. Das GitHub-Topic `chatgpt-plugin` setzen. Bei anderem Plugin-Pfad das Repository explizit in `sources` eintragen.

```json
{"repository": "jtracht/mein-plugin", "path": "plugin"}
```

Für ein Plugin am Repository-Root `"path": "."` nutzen. Für eine festgehaltene Release-Version zusätzlich `"tag": "v1.2.3"` setzen. Explizite Quellen haben Vorrang vor der automatischen Erkennung. Claude-Code-Plugins werden nicht automatisch als ChatGPT-Plugins behandelt.

Die Pipeline löst jeden Release-Tag auf einen Commit auf, kopiert den Plugin-Ordner nach `plugins/<name>/` und erzeugt:

| Datei | Zweck |
| --- | --- |
| `.agents/plugins/marketplace.json` | Nativer ChatGPT-/Codex-Marktplatz |
| `plugins/` | Gebündelte Plugin-Dateien |
| `catalog.lock.json` | Repository, Release, Commit und SHA-256 pro Datei |
| `CATALOG.md` | Lesbarer Plugin-Katalog |

Der Collector führt keinen Plugin-Code und keine Installationsskripte aus. Er prüft Git-Blob-Hashes, Namenskollisionen, Pfade, referenzierte Icons, Versionen und Größenlimits. Private Repositories, Symlinks, Submodule, unvollständige Bäume und widersprüchliche Versionen lassen den gesamten Sync fehlschlagen. Erst nach erfolgreicher Prüfung wird ein gemeinsamer Git-Commit veröffentlicht. Diese technischen Prüfungen ersetzen keine inhaltliche Sicherheitsprüfung der Plugins.

Keine Änderungen bedeuten keinen neuen Commit. Fehler beim Abruf eines Plugins veröffentlichen keinen unvollständigen Katalog. Gelöschte Quellen verschwinden bei der nächsten erfolgreichen vollständigen Zusammenführung. Entfernen aus `sources` allein reicht nicht, wenn das Repository weiterhin per Topic gefunden wird.

Die Pipeline ruft die Plugin-Repositories mit dem Secret `DISPATCH_TOKEN` ab; Commit und Push laufen über das kurzlebige `GITHUB_TOKEN` mit `contents: write` für dieses Repository. Sie kopiert ausschließlich öffentlich lesbare Quellen. Geschützte Branches oder Organisationsregeln können direkte Bot-Commits ablehnen; dann scheitert der Push sichtbar, und es muss ein geeigneter PR-Prozess konfiguriert werden. GitHub kann geplante Läufe verzögern oder bei längerer Inaktivität deaktivieren.

## Marktplatz verwenden

Nach erfolgreicher Veröffentlichung:

```bash
codex plugin marketplace add jtracht/chatgpt-plugin-marketplace --ref main
codex plugin marketplace upgrade chatgpt-plugin-marketplace
```

Danach in der ChatGPT-Desktop-App das Plugin-Verzeichnis öffnen und den Marktplatz **ChatGPT Plugin Marketplace** wählen. Verfügbarkeit und Installation hängen vom Client und den Kontoregeln ab.

Workspace-Administratoren können in **Admin Console → Plugins → Add → Import marketplace** die Repository-URL `https://github.com/jtracht/chatgpt-plugin-marketplace` importieren. **Path** bleibt leer; für fortlaufende Updates Branch `main` wählen. Die Workspace-Synchronisierung ist ein separater Schritt: Sie läuft laut OpenAI standardmäßig täglich oder kann über **Sync now** angestoßen werden. Ein GitHub-Sync erzwingt keine sofortige Aktualisierung in ChatGPT. Repository-Policies ersetzen keine Workspace-Berechtigungen.

Dieser eigene Marktplatz ist unabhängig vom universellen öffentlichen OpenAI-Plugin-Verzeichnis. Der GitHub-Push reicht dort keine Plugins ein.

**Gitea for ChatGPT:** Das Plugin enthält die Vorlage `https://{host}/mcp`. Vor der Nutzung einen eigenen HTTPS-MCP-Server und OAuth einrichten und den Host gemäß [Plugin-Installationsanleitung](https://github.com/jtracht/gitea-chatgpt-plugin/blob/main/docs/de/SCHRITT-FUER-SCHRITT.md) konfigurieren. Der Marktplatz stellt keinen Gitea-MCP-Dienst bereit. Eine erfolgreiche Katalogprüfung belegt keine funktionierende OAuth-Verbindung.

## Prüfungen und Releases

Nur Python-Standardbibliothek, keine zusätzlichen Pakete:

```bash
python -m unittest discover -s tests -v
python scripts/marketplace.py validate
python scripts/marketplace.py sync
python scripts/marketplace.py bundle
```

`sync` braucht GitHub-Zugriff; optional `GH_TOKEN` für höhere API-Limits. `validate` und Tests laufen offline. Der erste Sync muss erfolgreich sein, bevor ein vollständiges Bundle erzeugt werden kann.

Für ein versioniertes Marktplatz-ZIP: **Actions → Release marketplace bundle → Run workflow**, Versionsnummer wie `0.1.0` eintragen. Der Workflow prüft den zuletzt ausgecheckten Katalog und erstellt einen neuen Release mit Tag `v0.1.0`, ZIP und `SHA256SUMS`. Bestehende Releases werden nicht überschrieben. Die Marktplatz-Version ist unabhängig von den Versionen einzelner Plugins.

Zum Zurücksetzen einen früheren vollständigen Katalog-Commit wiederherstellen. Damit der nächste Sync die Rücksetzung nicht sofort ersetzt, zuerst die betroffenen Quellen auf den gewünschten Release-Tag festhalten.

## Quellen und Lizenz

- [OpenAI: Plugin-Pakete und Marketplace-Format](https://developers.openai.com/plugins/build/plugins)
- [OpenAI: GitHub-Marktplätze importieren und synchronisieren](https://help.openai.com/en/articles/20001504-importing-and-syncing-plugin-marketplaces-from-github)

Katalogwerkzeuge: MIT. Gebündelte Plugins behalten ihre eigenen Lizenzen; Root-Lizenz- und Hinweistexte der Quellen werden mit übernommen.
