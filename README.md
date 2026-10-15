# Pulse Meter data repo — AFA auto-update

Files to add to your `pulsemeter-data` repo (next to the existing `tariff.json`):

    .github/workflows/update-afa.yml
    scripts/update_afa.py
    scripts/test_update_afa.py

Do NOT overwrite your own tariff.json with another copy.

One-time setup: Settings -> Pages -> Source = "GitHub Actions", then Actions tab ->
"Update AFA and publish tariff.json" -> Run workflow.
