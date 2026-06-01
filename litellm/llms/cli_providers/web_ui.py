"""AIHub Provider Management UI

Adds /aihub routes to the LiteLLM proxy at startup. Wires up the
"setup-token" OAuth flow that the Claude CLI exposes — a paste-the-code
flow that works on headless remote servers (the redirect goes to
``platform.claude.com``, not the container's localhost).

Wire up via the LITELLM_WORKER_STARTUP_HOOKS environment variable:

    LITELLM_WORKER_STARTUP_HOOKS=litellm.llms.cli_providers.web_ui:register_routes

Routes added:
    GET  /aihub                              HTML provider dashboard
    GET  /aihub/api/status                   JSON — all provider statuses
    GET  /aihub/api/providers/{id}/auth      JSON — single provider status
    POST /aihub/api/providers/{id}/login     JSON — start login, return OAuth URL
    POST /aihub/api/providers/{id}/submit    JSON — paste OAuth code, finish login
    POST /aihub/api/providers/{id}/logout    JSON — clear local session
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import shutil
import subprocess
import time
import uuid
from typing import Any, Optional

from fastapi import APIRouter, Body
from fastapi.responses import HTMLResponse, JSONResponse

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────
# Provider registry
# ─────────────────────────────────────────────────────────────

_PROVIDERS: list[dict[str, Any]] = [
    {
        "id": "claude",
        "name": "Claude Code",
        "bin": "claude",
        "icon": "⬛",
        "tagline": "Anthropic • paste-code login",
        # `setup-token` prints an OAuth URL whose callback is
        # platform.claude.com (NOT localhost) → user copies the code
        # from the result page and pastes it back into stdin.
        "login_args": ["setup-token"],
        "url_pattern": r"https://claude\.com/[^\s\x1b\r\n\"'<>]+",
        "needs_pty": True,
    },
    {
        "id": "gemini",
        "name": "Gemini CLI",
        "bin": "gemini",
        "icon": "🔷",
        "tagline": "Google • Google account login",
        "login_args": ["auth", "login"],
        "url_pattern": r"https://[^\s\x1b\r\n\"'<>]+",
        "needs_pty": True,
        "session_file": "~/.config/gemini/credentials.json",
    },
]

_PROVIDER_MAP: dict[str, dict] = {p["id"]: p for p in _PROVIDERS}

# ANSI escape sequences appear all over the Claude CLI's TUI output —
# strip them before regex-matching the OAuth URL so a `[1C` cursor-move
# embedded in the middle of the URL doesn't break the match.
_ANSI_RE = re.compile(r"\x1b\[[\d;?]*[A-Za-z]|\x1b\][^\x07]*\x07")


def _strip_ansi(s: str) -> str:
    return _ANSI_RE.sub("", s)


# ─────────────────────────────────────────────────────────────
# Active login session tracking
# ─────────────────────────────────────────────────────────────


class LoginSession:
    """Holds a running `setup-token` (or equivalent) subprocess until the
    user pastes back the OAuth code. PTY-backed so the CLI's TUI prompt
    works the same way it does on a real terminal."""

    def __init__(self, provider_id: str, pid: int, master_fd: int) -> None:
        self.id: str = uuid.uuid4().hex[:12]
        self.provider_id = provider_id
        self.pid = pid
        self.master_fd = master_fd
        self.created_at = time.time()
        self.completed = False

    def close(self) -> None:
        """Best-effort cleanup — kill subprocess and close PTY."""
        try:
            os.kill(self.pid, 15)  # SIGTERM
        except (ProcessLookupError, PermissionError):
            pass
        try:
            os.close(self.master_fd)
        except OSError:
            pass


_sessions: dict[str, LoginSession] = {}
_SESSION_TTL_SECONDS = 600  # 10 min


def _gc_sessions() -> None:
    """Evict sessions older than _SESSION_TTL_SECONDS."""
    now = time.time()
    stale = [sid for sid, s in _sessions.items() if now - s.created_at > _SESSION_TTL_SECONDS]
    for sid in stale:
        _sessions[sid].close()
        del _sessions[sid]


# ─────────────────────────────────────────────────────────────
# Status checks
# ─────────────────────────────────────────────────────────────


def _get_version(bin_path: str) -> Optional[str]:
    try:
        r = subprocess.run(
            [bin_path, "--version"], capture_output=True, text=True, timeout=5,
        )
        line = (r.stdout or r.stderr).strip().split("\n")[0]
        return line or None
    except Exception:
        return None


def _check_claude_auth(bin_path: str) -> dict:
    """Use `claude auth status --json`. Returns {status, email, org}."""
    try:
        r = subprocess.run(
            [bin_path, "auth", "status", "--json"],
            capture_output=True, text=True, timeout=8,
        )
        m = re.search(r"\{.*\}", r.stdout, re.DOTALL)
        if m:
            data = json.loads(m.group())
            if data.get("loggedIn"):
                return {
                    "status": "ok",
                    "email": data.get("email"),
                    "org": data.get("orgName"),
                }
            return {"status": "missing"}
    except Exception as exc:
        logger.debug("claude auth status failed: %s", exc)
    return {"status": "unknown"}


def _check_gemini_auth(_bin_path: str, provider: dict) -> dict:
    session_file = os.path.expanduser(provider.get("session_file", ""))
    if session_file and os.path.exists(session_file):
        return {"status": "ok"}
    return {"status": "missing"}


def _provider_status(provider: dict) -> dict:
    pid = provider["id"]
    bin_path = shutil.which(provider["bin"])

    if bin_path is None:
        return {
            "id": pid, "name": provider["name"], "icon": provider["icon"],
            "tagline": provider["tagline"], "installed": False,
            "auth": None, "email": None, "org": None,
            "version": None, "path": None,
        }

    version = _get_version(bin_path)

    if pid == "claude":
        auth_info = _check_claude_auth(bin_path)
    elif pid == "gemini":
        auth_info = _check_gemini_auth(bin_path, provider)
    else:
        auth_info = {"status": "unknown"}

    return {
        "id": pid, "name": provider["name"], "icon": provider["icon"],
        "tagline": provider["tagline"], "installed": True,
        "auth": auth_info.get("status", "unknown"),
        "email": auth_info.get("email"),
        "org": auth_info.get("org"),
        "version": version,
        "path": bin_path,
    }


# ─────────────────────────────────────────────────────────────
# Subprocess: start setup-token with PTY, capture OAuth URL
# ─────────────────────────────────────────────────────────────


async def _start_login_subprocess(
    bin_path: str, login_args: list[str], url_pattern: str,
    timeout_seconds: float = 12.0,
) -> tuple[Optional[str], Optional[LoginSession], str]:
    """Spawn the login CLI under a PTY, scan its output for the OAuth URL.

    Returns (url, session, raw_output). The subprocess is left running
    so the caller can later write the OAuth code back via PTY stdin.
    """
    import pty as _pty

    master_fd, slave_fd = _pty.openpty()

    # Force a very wide terminal so the CLI doesn't word-wrap the OAuth
    # URL across lines (default 80 cols breaks the URL mid-string and
    # makes capture unreliable). COLUMNS=500 keeps it on one line.
    try:
        import fcntl, struct, termios
        winsz = struct.pack("HHHH", 50, 500, 0, 0)
        fcntl.ioctl(slave_fd, termios.TIOCSWINSZ, winsz)
    except Exception:  # pragma: no cover — best-effort, falls back to env
        pass

    proc = subprocess.Popen(
        [bin_path, *login_args],
        stdin=slave_fd, stdout=slave_fd, stderr=slave_fd,
        close_fds=True,
        env={
            **os.environ,
            "TERM": "xterm-256color",
            "COLUMNS": "500",
            "LINES": "50",
        },
    )
    os.close(slave_fd)

    loop = asyncio.get_event_loop()
    deadline = loop.time() + timeout_seconds
    collected = bytearray()
    url: Optional[str] = None
    pattern = re.compile(url_pattern)

    while loop.time() < deadline:
        try:
            chunk = await loop.run_in_executor(
                None,
                lambda: _read_with_timeout(master_fd, 0.4),
            )
        except OSError:
            break
        if chunk:
            collected.extend(chunk)
            cleaned = _strip_ansi(collected.decode("utf-8", errors="replace"))
            # `cleaned` keeps newlines — with COLUMNS=500 the URL sits on
            # one line and stops at the newline before "Paste code here…"
            # prompt. `cleaned_oneline` is a fallback for CLIs that still
            # wrap; it can glue unrelated trailing text to the URL, so
            # only use it when the per-line match fails.
            m = pattern.search(cleaned)
            if not m:
                cleaned_oneline = re.sub(r"\s+", "", cleaned)
                m = pattern.search(cleaned_oneline)
            if m:
                url = m.group()
                break
        if proc.poll() is not None:
            break

    if url is None:
        # Couldn't capture URL — tear down and return raw output for debugging
        try: proc.terminate()
        except Exception: pass
        try: os.close(master_fd)
        except OSError: pass
        return None, None, _strip_ansi(collected.decode("utf-8", errors="replace"))

    session = LoginSession(provider_id="", pid=proc.pid, master_fd=master_fd)
    return url, session, _strip_ansi(collected.decode("utf-8", errors="replace"))


def _read_with_timeout(fd: int, timeout: float) -> bytes:
    """Non-blocking read from a PTY master fd."""
    import select
    r, _, _ = select.select([fd], [], [], timeout)
    if not r:
        return b""
    try:
        return os.read(fd, 4096)
    except OSError:
        return b""


# ─────────────────────────────────────────────────────────────
# FastAPI router
# ─────────────────────────────────────────────────────────────

aihub_router = APIRouter()


@aihub_router.get("/aihub", response_class=HTMLResponse, include_in_schema=False)
async def providers_ui() -> HTMLResponse:
    return HTMLResponse(content=_PROVIDERS_HTML)


@aihub_router.get("/aihub/api/status")
async def providers_status() -> JSONResponse:
    return JSONResponse({"providers": [_provider_status(p) for p in _PROVIDERS]})


@aihub_router.get("/aihub/api/providers/{provider_id}/auth")
async def provider_auth(provider_id: str) -> JSONResponse:
    provider = _PROVIDER_MAP.get(provider_id)
    if not provider:
        return JSONResponse({"error": "Unknown provider"}, status_code=404)
    return JSONResponse(_provider_status(provider))


@aihub_router.post("/aihub/api/providers/{provider_id}/login")
async def start_login(provider_id: str) -> JSONResponse:
    _gc_sessions()
    provider = _PROVIDER_MAP.get(provider_id)
    if not provider:
        return JSONResponse({"error": "Unknown provider"}, status_code=404)

    bin_path = shutil.which(provider["bin"])
    if bin_path is None:
        return JSONResponse(
            {
                "status": "not_installed",
                "message": (
                    f"{provider['name']} is not installed in this AIHub node. "
                    "Add it to the image, or install it on the host and retry."
                ),
            },
            status_code=400,
        )

    url, session, debug_output = await _start_login_subprocess(
        bin_path, provider.get("login_args", []), provider.get("url_pattern", r"https://[^\s]+"),
    )

    if url is None or session is None:
        return JSONResponse({
            "status": "no_url",
            "message": "Could not capture the OAuth URL from the CLI.",
            "hint": f"Run `{provider['bin']} {' '.join(provider.get('login_args', []))}` manually inside the container.",
            "output": debug_output[:500],
        })

    session.provider_id = provider_id
    _sessions[session.id] = session
    logger.info("AIHub: login session %s started for %s", session.id, provider_id)
    return JSONResponse({
        "status": "waiting_code",
        "url": url,
        "session_id": session.id,
        "instructions": (
            "1. Open the URL in a browser  "
            "2. Sign in on claude.com  "
            "3. Copy the code shown after sign-in  "
            "4. Paste it below"
        ),
    })


@aihub_router.post("/aihub/api/providers/{provider_id}/submit")
async def submit_code(
    provider_id: str,
    body: dict = Body(...),
) -> JSONResponse:
    session_id = body.get("session_id")
    code = (body.get("code") or "").strip()

    if not session_id or not code:
        return JSONResponse(
            {"status": "error", "message": "session_id and code are required"},
            status_code=400,
        )

    session = _sessions.get(session_id)
    if session is None or session.provider_id != provider_id:
        return JSONResponse(
            {"status": "expired", "message": "Login session not found or expired. Click Login again."},
            status_code=410,
        )

    # Write code into the subprocess's stdin (via the PTY master)
    try:
        os.write(session.master_fd, (code + "\n").encode())
    except OSError as exc:
        session.close()
        _sessions.pop(session_id, None)
        return JSONResponse(
            {"status": "error", "message": f"Could not write code to CLI: {exc}"},
            status_code=500,
        )

    # Give the CLI up to 15s to swap the code for a token + persist
    loop = asyncio.get_event_loop()
    deadline = loop.time() + 15.0
    tail = bytearray()
    while loop.time() < deadline:
        chunk = await loop.run_in_executor(
            None, lambda: _read_with_timeout(session.master_fd, 0.5)
        )
        if chunk:
            tail.extend(chunk)
        # Poll the subprocess — when it exits, the token exchange is done
        try:
            ret = os.waitpid(session.pid, os.WNOHANG)
            if ret != (0, 0):
                break
        except ChildProcessError:
            break

    session.completed = True
    session.close()
    _sessions.pop(session_id, None)

    # Re-check auth status
    bin_path = shutil.which(_PROVIDER_MAP[provider_id]["bin"])
    auth = _check_claude_auth(bin_path) if (provider_id == "claude" and bin_path) else {"status": "unknown"}

    tail_clean = _strip_ansi(tail.decode("utf-8", errors="replace"))
    if auth.get("status") == "ok":
        return JSONResponse({"status": "ok", "email": auth.get("email"), "org": auth.get("org")})
    return JSONResponse({
        "status": "failed",
        "message": "Login did not complete. The code may be wrong or expired.",
        "output": tail_clean[-400:],
    })


@aihub_router.post("/aihub/api/providers/{provider_id}/logout")
async def logout_provider(provider_id: str) -> JSONResponse:
    provider = _PROVIDER_MAP.get(provider_id)
    if not provider:
        return JSONResponse({"error": "Unknown provider"}, status_code=404)
    bin_path = shutil.which(provider["bin"])
    if bin_path is None:
        return JSONResponse({"status": "not_installed"}, status_code=400)

    try:
        r = subprocess.run(
            [bin_path, "auth", "logout"],
            capture_output=True, text=True, timeout=10,
        )
        return JSONResponse({
            "status": "ok" if r.returncode == 0 else "failed",
            "output": (r.stdout + r.stderr)[:400],
        })
    except Exception as exc:
        return JSONResponse({"status": "error", "message": str(exc)}, status_code=500)


# ─────────────────────────────────────────────────────────────
# Startup hook — called by LITELLM_WORKER_STARTUP_HOOKS
# ─────────────────────────────────────────────────────────────


def register_routes() -> None:
    """Add AIHub provider management routes to the LiteLLM FastAPI app."""
    from litellm.proxy.proxy_server import app  # lazy: avoid circular import

    app.include_router(aihub_router)
    logger.info("AIHub: provider management UI registered at /aihub")


# ─────────────────────────────────────────────────────────────
# HTML UI
# ─────────────────────────────────────────────────────────────

_PROVIDERS_HTML = """\
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>AIHub — Providers</title>
  <style>
    *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
    body {
      font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', 'Inter', sans-serif;
      background: #0d0f18; color: #dde1ec; min-height: 100vh; padding: 2.5rem 2rem;
    }
    a { color: inherit; }

    .page-header { display: flex; align-items: baseline; gap: 1rem; margin-bottom: 0.4rem; }
    .page-title  { font-size: 1.6rem; font-weight: 700; letter-spacing: -0.01em; }
    .page-badge  {
      font-size: 0.7rem; font-weight: 600; text-transform: uppercase; letter-spacing: 0.06em;
      padding: 0.2rem 0.55rem; border-radius: 4px; background: #1e2236; color: #6b7280;
    }
    .page-sub { font-size: 0.85rem; color: #6b7280; margin-bottom: 2.5rem; }

    .section-label {
      font-size: 0.7rem; font-weight: 700; text-transform: uppercase; letter-spacing: 0.09em;
      color: #4b5363; margin-bottom: 1rem;
    }
    .grid {
      display: grid; grid-template-columns: repeat(auto-fill, minmax(290px, 1fr));
      gap: 0.75rem; margin-bottom: 3rem;
    }

    .card {
      background: #13151f; border: 1px solid #1e2236; border-radius: 12px;
      padding: 1.25rem; display: flex; flex-direction: column; gap: 0.9rem;
      transition: border-color 0.15s;
    }
    .card.auth-ok  { border-color: #1d3a2a; }
    .card.auth-bad { border-color: #2e1e10; }

    .card-top  { display: flex; align-items: flex-start; gap: 0.75rem; }
    .card-icon { font-size: 1.6rem; line-height: 1.1; flex-shrink: 0; }
    .card-info { flex: 1; min-width: 0; }
    .card-name { font-weight: 600; font-size: 0.95rem; }
    .card-tagline { font-size: 0.75rem; color: #6b7280; margin-top: 0.15rem; }

    .badge {
      display: inline-flex; align-items: center; gap: 0.3rem;
      font-size: 0.72rem; font-weight: 600; padding: 0.22rem 0.6rem; border-radius: 999px;
    }
    .badge-ok      { background: #0b2419; color: #4ade80; }
    .badge-missing { background: #281a08; color: #fb923c; }
    .badge-unknown { background: #191c2a; color: #6b7280; }
    .badge-none    { background: #1c0f0f; color: #f87171; }

    .card-meta { font-size: 0.75rem; color: #6b7280; }
    .version-row { font-size: 0.73rem; color: #4b5363; }

    .card-actions { display: flex; gap: 0.5rem; flex-wrap: wrap; }
    button {
      cursor: pointer; font-size: 0.78rem; font-weight: 600;
      padding: 0.42rem 0.9rem; border-radius: 7px; border: 1px solid transparent;
      transition: background 0.12s, opacity 0.12s;
    }
    .btn-primary   { background: #3b6cf4; color: #fff; border-color: #3b6cf4; }
    .btn-primary:hover:not(:disabled) { background: #2d5de0; }
    .btn-secondary {
      background: transparent; color: #8892a4; border-color: #1e2236;
    }
    .btn-secondary:hover:not(:disabled) { background: #1e2236; color: #dde1ec; }
    .btn-danger    { background: transparent; color: #f87171; border-color: #2e1e10; }
    .btn-danger:hover:not(:disabled) { background: #2e1e10; }
    button:disabled { opacity: 0.45; cursor: not-allowed; }

    .login-box {
      display: none; background: #0d0f18; border: 1px solid #1e2236;
      border-radius: 8px; padding: 0.9rem; font-size: 0.78rem;
    }
    .login-box.open { display: block; }
    .login-step { color: #8892a4; margin-bottom: 0.55rem; line-height: 1.55; }
    .login-step b { color: #dde1ec; }
    .login-url {
      display: block; word-break: break-all; color: #60a5fa; text-decoration: underline;
      margin-bottom: 0.7rem; cursor: pointer; font-size: 0.74rem;
    }
    .code-form { display: flex; gap: 0.4rem; margin-top: 0.4rem; }
    .code-input {
      flex: 1; background: #1e2236; border: 1px solid #2d3145; color: #dde1ec;
      padding: 0.42rem 0.6rem; border-radius: 6px; font-family: 'SF Mono', monospace;
      font-size: 0.78rem; outline: none;
    }
    .code-input:focus { border-color: #3b6cf4; }
    .login-error { color: #f87171; margin-top: 0.4rem; }
    .login-success { color: #4ade80; margin-top: 0.4rem; }
    .login-cmd {
      font-family: 'SF Mono', 'Fira Code', monospace; background: #1e2236;
      padding: 0.25rem 0.5rem; border-radius: 4px; color: #dde1ec;
      font-size: 0.74rem; display: inline-block; margin-top: 0.25rem;
    }

    .spin {
      display: inline-block; width: 12px; height: 12px;
      border: 2px solid rgba(255,255,255,0.15); border-top-color: #3b6cf4;
      border-radius: 50%; animation: rotate 0.65s linear infinite;
      vertical-align: middle; margin-right: 5px;
    }
    @keyframes rotate { to { transform: rotate(360deg); } }

    .footer { margin-top: 1rem; font-size: 0.8rem; color: #4b5363; }
    .footer a { color: #3b6cf4; text-decoration: none; }
    .footer a:hover { text-decoration: underline; }
  </style>
</head>
<body>

  <div class="page-header">
    <span class="page-title">AIHub</span>
    <span class="page-badge">Node</span>
  </div>
  <p class="page-sub">Provider status — CLI agents detected on this machine</p>

  <div class="section-label">CLI Providers</div>
  <div id="grid" class="grid">
    <div style="color:#4b5363;font-size:0.85rem;">Loading…</div>
  </div>

  <div class="footer">
    ← <a href="/ui">LiteLLM Admin UI</a> &nbsp;·&nbsp; <a href="/docs">API docs</a>
  </div>

<script>
const $ = id => document.getElementById(id);

function badge(p) {
  if (!p.installed)         return '<span class="badge badge-none">✕ Not installed</span>';
  if (p.auth === 'ok')      return '<span class="badge badge-ok">✓ Authenticated</span>';
  if (p.auth === 'missing') return '<span class="badge badge-missing">⚠ Login required</span>';
  return '<span class="badge badge-unknown">? Unknown</span>';
}

function cardClass(p) {
  if (!p.installed) return 'card';
  if (p.auth === 'ok') return 'card auth-ok';
  if (p.auth === 'missing') return 'card auth-bad';
  return 'card';
}

function renderCard(p) {
  const meta = [p.email, p.org].filter(Boolean).join(' · ');
  const loginBtn = p.auth !== 'ok'
    ? `<button class="btn-primary" id="btn-login-${p.id}" onclick="startLogin('${p.id}')">Login</button>`
    : `<button class="btn-danger" onclick="logout('${p.id}')">Logout</button>`;

  return `
<div id="card-${p.id}" class="${cardClass(p)}">
  <div class="card-top">
    <div class="card-icon">${p.icon}</div>
    <div class="card-info">
      <div class="card-name">${p.name}</div>
      <div class="card-tagline">${p.tagline}</div>
    </div>
  </div>
  ${badge(p)}
  ${meta ? `<div class="card-meta">${meta}</div>` : ''}
  ${p.version ? `<div class="version-row">${p.version}${p.path ? ' · ' + p.path : ''}</div>` : ''}
  ${p.installed ? `
  <div class="card-actions">
    ${loginBtn}
    <button class="btn-secondary" onclick="refreshAll()">↻ Refresh</button>
  </div>
  <div class="login-box" id="login-box-${p.id}">
    <div id="login-content-${p.id}"></div>
  </div>` : ''}
</div>`;
}

async function refreshAll() {
  const res = await fetch('/aihub/api/status');
  const { providers } = await res.json();
  $('grid').innerHTML = providers.map(renderCard).join('');
}

async function startLogin(id) {
  const btn = $('btn-login-' + id);
  const box = $('login-box-' + id);
  const body = $('login-content-' + id);

  btn.disabled = true;
  btn.innerHTML = '<span class="spin"></span>Starting…';
  box.classList.add('open');
  body.innerHTML = '<div class="login-step"><span class="spin"></span>Launching CLI login flow…</div>';

  try {
    const res  = await fetch(`/aihub/api/providers/${id}/login`, { method: 'POST' });
    const data = await res.json();

    if (data.status === 'waiting_code' && data.url && data.session_id) {
      body.innerHTML = `
        <div class="login-step"><b>1.</b> Open this URL in your browser:</div>
        <a class="login-url" href="${data.url}" target="_blank" rel="noopener">${data.url}</a>
        <div class="login-step"><b>2.</b> Sign in on claude.com and copy the code shown.</div>
        <div class="login-step"><b>3.</b> Paste the code below and click <b>Submit</b>:</div>
        <div class="code-form">
          <input type="text" class="code-input" id="code-input-${id}"
                 placeholder="paste code here" autocomplete="off" spellcheck="false">
          <button class="btn-primary" id="btn-submit-${id}"
                  onclick="submitCode('${id}', '${data.session_id}')">Submit</button>
        </div>
        <div id="submit-status-${id}"></div>`;
      btn.innerHTML = 'Waiting for code…';
      // Auto-focus the input
      setTimeout(() => { const i = $('code-input-' + id); if (i) i.focus(); }, 100);

    } else if (data.status === 'not_installed') {
      body.innerHTML = `<div class="login-error">${data.message}</div>`;
      btn.disabled = false; btn.innerHTML = 'Login';

    } else {
      body.innerHTML = `
        <div class="login-step">${data.message || 'Could not capture the OAuth URL automatically.'}</div>
        <span class="login-cmd">${data.hint || id + ' setup-token'}</span>
        ${data.output ? `<div class="login-step" style="margin-top:0.5rem;font-size:0.7rem;opacity:.6">${data.output}</div>` : ''}`;
      btn.disabled = false; btn.innerHTML = 'Login';
    }
  } catch(e) {
    body.innerHTML = `<div class="login-error">Error: ${e.message}</div>`;
    btn.disabled = false; btn.innerHTML = 'Login';
  }
}

async function submitCode(id, sessionId) {
  const code = $('code-input-' + id).value.trim();
  const submitBtn = $('btn-submit-' + id);
  const statusEl  = $('submit-status-' + id);

  if (!code) {
    statusEl.innerHTML = '<div class="login-error">Please paste the code first.</div>';
    return;
  }

  submitBtn.disabled = true;
  submitBtn.innerHTML = '<span class="spin"></span>Verifying…';
  statusEl.innerHTML = '';

  try {
    const res  = await fetch(`/aihub/api/providers/${id}/submit`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ session_id: sessionId, code }),
    });
    const data = await res.json();

    if (data.status === 'ok') {
      statusEl.innerHTML = `<div class="login-success">✓ Logged in as <b>${data.email || 'user'}</b></div>`;
      setTimeout(refreshAll, 800);
    } else {
      statusEl.innerHTML = `<div class="login-error">✗ ${data.message || 'Login failed.'}</div>`;
      submitBtn.disabled = false;
      submitBtn.innerHTML = 'Submit';
    }
  } catch(e) {
    statusEl.innerHTML = `<div class="login-error">Error: ${e.message}</div>`;
    submitBtn.disabled = false;
    submitBtn.innerHTML = 'Submit';
  }
}

async function logout(id) {
  if (!confirm('Logout this provider? Anyone using AIHub will lose access until you log in again.')) return;
  await fetch(`/aihub/api/providers/${id}/logout`, { method: 'POST' });
  await refreshAll();
}

refreshAll();
</script>
</body>
</html>
"""
