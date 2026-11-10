"""Voir une page servie sur ta machine : une capture, que lit un modèle de vision.

Le navigateur habituel de Jarvis fait tout passer par le proxy de garde, qui refuse les
adresses locales (une page piégée ne doit pas lui faire lire `http://127.0.0.1:…`). Ici, en
mode shell, il s'agit de *ta* page en développement : un Chromium à part, éphémère, sans
cookies ni session, ouvre seulement `localhost` ; les requêtes de la page vers ton réseau
local (box, imprimante) sont bloquées.
"""

from __future__ import annotations

import ipaddress
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit

from ojarvis.core.errors import UpstreamError
from ojarvis.tools.guards import BlockedTargetError

LOCAL_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})
VIEWPORT_WIDTH, VIEWPORT_HEIGHT = 1280, 800
#: Erreurs de la console gardées (les premières, souvent les plus utiles).
MAX_ERRORS = 10


@dataclass(frozen=True, slots=True)
class PageShot:
    png: bytes
    title: str
    status: int | None
    errors: list[str] = field(default_factory=list)


def local_page(url: str) -> tuple[str, int]:
    """L'adresse nettoyée et son port, si c'est une page servie sur ta machine ; sinon refus."""
    parts = urlsplit(url.strip())
    host = (parts.hostname or "").lower()
    if parts.scheme not in ("http", "https") or host not in LOCAL_HOSTS:
        raise BlockedTargetError(
            "Seulement une page servie sur ta machine (http://localhost:PORT/…). Une page du "
            "web se lit avec lire_page."
        )
    try:
        port = parts.port or (443 if parts.scheme == "https" else 80)
    except ValueError as exc:
        raise BlockedTargetError("Port invalide.") from exc
    return parts.geturl(), port


def blocked_request(url: str) -> bool:
    """Une requête de la page vers le réseau local (hors ta machine) : bloquée."""
    host = (urlsplit(url).hostname or "").lower()
    if host in LOCAL_HOSTS or not host:
        return False
    if host.endswith((".local", ".lan", ".home", ".internal")):
        return True
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return False  # un nom public : ressources de la page (polices, CDN)
    return not address.is_global and not address.is_loopback


async def capture_page(url: str, *, timeout_s: float = 20.0) -> PageShot:
    """Ouvre la page dans un Chromium éphémère et en prend une capture (fenêtre de 1280×800)."""
    from playwright.async_api import Error as PlaywrightError  # noqa: PLC0415
    from playwright.async_api import async_playwright  # noqa: PLC0415

    errors: list[str] = []

    def on_console(message: Any) -> None:
        if message.type == "error" and len(errors) < MAX_ERRORS:
            errors.append(str(message.text)[:300])

    def on_page_error(error: Any) -> None:
        if len(errors) < MAX_ERRORS:
            errors.append(str(error)[:300])

    async def guard(route: Any) -> None:
        if blocked_request(route.request.url):
            await route.abort()
        else:
            await route.continue_()

    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        try:
            context = await browser.new_context(
                viewport={"width": VIEWPORT_WIDTH, "height": VIEWPORT_HEIGHT}
            )
            await context.route("**/*", guard)
            page = await context.new_page()
            page.on("console", on_console)
            page.on("pageerror", on_page_error)
            try:
                response = await page.goto(url, wait_until="networkidle", timeout=timeout_s * 1000)
            except PlaywrightError as exc:
                first = str(exc).splitlines()[0][:200]
                raise UpstreamError(
                    f"La page ne répond pas ({first}). Le serveur tourne-t-il sur ce port ?"
                ) from exc
            png = await page.screenshot(full_page=False)
            title = await page.title()
        finally:
            await browser.close()
    return PageShot(png, title, response.status if response is not None else None, errors)
