"""
Start plugin services

This plugin module can define user-created services for the Portal to
start.

This module must handle all imports and setups required to start
twisted services (see examples in evennia.server.portal.portal). It
must also contain a function start_plugin_services(application).
Evennia will call this function with the main Portal application (so
your services can be added to it). The function should not return
anything. Plugin services are started last in the Portal startup
process.

"""

import logging

logger = logging.getLogger("mygame")


def _disable_webclient_autologin_cookie():
    """Stop the webclient protocols persisting the shared auto-login cookie.

    Evennia's webclient protocols write ``webclient_authenticated_uid`` into the
    browser session on login, so a *new* webclient session (a new tab) silently
    auto-authenticates as that account with no connect screen — and with
    MULTISESSION_MODE=0 that new session then USURPS the character already
    playing on another session. We want every new session to require explicit
    credentials.

    We neutralize the ``at_login`` cookie-write on both the websocket and AJAX
    webclient protocols. This is done as a startup monkeypatch (rather than a
    ``*_PROTOCOL_CLASS`` subclass in settings) on purpose: the AJAX resource
    resolves ``settings.AJAX_PROTOCOL_CLASS`` in its class body at import time, so
    pointing that setting at a subclass module that imports the AJAX base creates
    a circular import. Patching here — after all portal modules are imported —
    sidesteps that cleanly.

    The paired *website* → webclient share (SharedLoginMiddleware) is disabled
    separately in settings.py. With both writers off, the cookie is never set, so
    the protocols' connect-time read always misses and each new socket lands at
    the login screen.

    Scope: the cookie is the ONLY thing that re-authenticates a REOPENED socket
    (webclient.py onOpen / webclient_ajax.py mode_init gate the whole "already
    logged in" branch on it), so disabling it also forces a re-login on a page
    RELOAD of an existing tab, not just on a brand-new tab — the two are
    indistinguishable (both are a fresh socket reading the same cookie). We
    accept the reload cost to close the new-tab usurpation hole. An in-place
    live socket that never closes is unaffected.
    """
    patched = []

    try:
        from evennia.server.portal.webclient import WebSocketClient
        WebSocketClient.at_login = lambda self: None
        patched.append("websocket")
    except Exception:  # noqa: BLE001 - never let this block portal startup
        logger.warning("Could not disable websocket webclient auto-login cookie",
                       exc_info=True)

    try:
        from evennia.server.portal.webclient_ajax import AjaxWebClientSession
        AjaxWebClientSession.at_login = lambda self: None
        patched.append("ajax")
    except Exception:  # noqa: BLE001
        logger.warning("Could not disable AJAX webclient auto-login cookie",
                       exc_info=True)

    if patched:
        logger.info(
            "Webclient auto-login cookie disabled (%s); new sessions require "
            "an explicit login.", ", ".join(patched),
        )


def start_plugin_services(portal):
    """
    This hook is called by Evennia, last in the Portal startup process.

    portal - a reference to the main portal application.
    """
    _disable_webclient_autologin_cookie()
