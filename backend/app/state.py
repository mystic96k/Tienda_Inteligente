"""Shared start-up status so the HTTP port opens immediately while data loads / models train in the background."""
status = {"ready": False, "stage": "iniciando", "error": None}
