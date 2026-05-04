"""BTerminal data models — JSON-backed managers for sessions and Consult config.

JsonListManager is a generic CRUD manager for list-of-dicts persisted as
JSON. SessionManager and ClaudeSessionManager are SSH/Claude session
stores. ConsultManager handles consult API key + model registry +
tribunal project presets.

Currently flat at repo root next to bterminal.py — collapses into the
target `bterminal/` package together with config.py in a later
migration etap.
"""

import json
import os
import tempfile
import uuid

from bterminal.config import (
    CLAUDE_SESSIONS_FILE,
    CONFIG_DIR,
    CONSULT_CONFIG_FILE,
    SESSIONS_FILE,
)


# ─── JsonListManager + session managers ───────────────────────────────────────

class JsonListManager:
    """Generic CRUD manager for a list of dicts stored in a JSON file."""

    def __init__(self, filepath):
        self._filepath = filepath
        os.makedirs(CONFIG_DIR, exist_ok=True)
        self.sessions = []
        self.load()

    def validate_entry(self, entry):
        """Override in subclasses to validate before add/update."""
        pass

    def load(self):
        if os.path.exists(self._filepath):
            try:
                with open(self._filepath, "r") as f:
                    self.sessions = json.load(f)
            except (json.JSONDecodeError, IOError):
                self.sessions = []
        else:
            self.sessions = []

    def save(self):
        os.makedirs(CONFIG_DIR, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=CONFIG_DIR, suffix=".tmp")
        try:
            with os.fdopen(fd, "w") as f:
                json.dump(self.sessions, f, indent=2)
            os.replace(tmp, self._filepath)
        except Exception:
            if os.path.exists(tmp):
                os.unlink(tmp)
            raise

    def add(self, session):
        self.validate_entry(session)
        session["id"] = str(uuid.uuid4())
        self.sessions.append(session)
        self.save()
        return session

    def update(self, session_id, data):
        for i, s in enumerate(self.sessions):
            if s["id"] == session_id:
                self.sessions[i].update(data)
                self.validate_entry(self.sessions[i])
                self.save()
                return self.sessions[i]
        return None

    def delete(self, session_id):
        self.sessions = [s for s in self.sessions if s["id"] != session_id]
        self.save()

    def get(self, session_id):
        for s in self.sessions:
            if s["id"] == session_id:
                return s
        return None

    def all(self):
        return list(self.sessions)


class SessionManager(JsonListManager):
    """Zarządzanie zapisanymi sesjami SSH.

    R3a: hasła SSH NIGDY nie są zapisywane na dysku. save() filtruje
    pole 'password' przed serializacją; load() dropuje 'password'
    z plików legacy + loguje info do stderr.

    In-memory password storage: SessionPasswordCache (osobna klasa).
    """

    def __init__(self):
        super().__init__(SESSIONS_FILE)
        self._scrub_legacy_passwords()

    def validate_entry(self, entry):
        if not entry.get("host"):
            raise ValueError("SSH session requires 'host'")

    def save(self):
        """R3a.1: przed zapisem wyfiltruj 'password' z każdej sesji.
        Hasła żyją tylko w SessionPasswordCache (RAM)."""
        # Mutuj in-memory state: drop password keys (idempotent)
        for s in self.sessions:
            s.pop("password", None)
        super().save()

    def _scrub_legacy_passwords(self):
        """R3a.2 + R3a.6: jeśli wczytany sessions.json miał pole 'password'
        (legacy install sprzed R3a) — drop in-memory + zapisz na dysk
        bez tego pola + log info."""
        legacy_count = sum(1 for s in self.sessions if "password" in s)
        if legacy_count == 0:
            return
        for s in self.sessions:
            s.pop("password", None)
        # Self-heal: nadpisz plik bez password (super().save bo nasz save
        # już filtruje, ale chcemy explicit ujawnić że to migracja)
        super().save()
        import sys as _sys
        _sys.stderr.write(
            f"[bterminal] Migrated: removed 'password' field from "
            f"{legacy_count} session(s) in sessions.json. "
            f"Passwords are now in-memory only (R3a).\n"
        )


class SessionPasswordCache:
    """In-memory cache hasł SSH (R3a). Lifetime: bieżący proces BTerminala.

    Hasła nigdy nie idą na dysk. Restart aplikacji = utrata cache,
    user musi ponownie wpisać przy connect.

    Usage:
        cache = SessionPasswordCache()
        cache.set("session-uuid", "secret123")
        pw = cache.get("session-uuid")  # "secret123" lub None
        cache.pop("session-uuid")        # wyczyść po close
    """

    def __init__(self):
        self._cache: dict[str, str] = {}

    def set(self, session_id: str, password: str) -> None:
        """Cache password dla danej sesji. Pusty string = drop entry."""
        if password:
            self._cache[session_id] = password
        else:
            self._cache.pop(session_id, None)

    def get(self, session_id: str) -> str | None:
        return self._cache.get(session_id)

    def pop(self, session_id: str) -> str | None:
        """Atomic get+remove — gdy session jest zamykana."""
        return self._cache.pop(session_id, None)

    def clear(self) -> None:
        """Wyczyść WSZYSTKIE hasła (np. przy quit aplikacji)."""
        self._cache.clear()

    def __contains__(self, session_id: str) -> bool:
        return session_id in self._cache

    def __len__(self) -> int:
        return len(self._cache)


class ClaudeSessionManager(JsonListManager):
    """Zarządzanie zapisanymi konfiguracjami Claude Code."""

    def __init__(self):
        super().__init__(CLAUDE_SESSIONS_FILE)


# ─── ConsultManager ──────────────────────────────────────────────────────────

class ConsultManager:
    """Manage consult configuration (API key, models from OpenRouter & Claude Code)."""

    CLAUDE_CODE_MODELS = {
        "claude-code/opus": {"name": "Claude Opus 4.6", "enabled": True, "source": "claude-code"},
        "claude-code/sonnet": {"name": "Claude Sonnet 4.6", "enabled": True, "source": "claude-code"},
        "claude-code/haiku": {"name": "Claude Haiku 4.5", "enabled": True, "source": "claude-code"},
    }

    DEFAULT_CONFIG = {
        "api_key": "",
        "default_model": "google/gemini-2.5-pro",
        "models": {
            "google/gemini-2.5-pro": {"enabled": True, "name": "Gemini 2.5 Pro", "source": "openrouter"},
            "openai/gpt-4o": {"enabled": True, "name": "GPT-4o", "source": "openrouter"},
            "openai/o3-mini": {"enabled": True, "name": "o3-mini", "source": "openrouter"},
            "deepseek/deepseek-r1": {"enabled": True, "name": "DeepSeek R1", "source": "openrouter"},
            "anthropic/claude-sonnet-4": {"enabled": False, "name": "Claude Sonnet 4", "source": "openrouter"},
            "meta-llama/llama-4-maverick": {
                "enabled": False,
                "name": "Llama 4 Maverick",
                "source": "openrouter",
            },
            "claude-code/opus": {"enabled": True, "name": "Claude Opus 4.6", "source": "claude-code"},
            "claude-code/sonnet": {"enabled": True, "name": "Claude Sonnet 4.6", "source": "claude-code"},
            "claude-code/haiku": {"enabled": True, "name": "Claude Haiku 4.5", "source": "claude-code"},
        },
    }

    def __init__(self):
        os.makedirs(CONFIG_DIR, exist_ok=True)
        self.config = {}
        self.load()

    def load(self):
        if os.path.isfile(CONSULT_CONFIG_FILE):
            try:
                with open(CONSULT_CONFIG_FILE) as f:
                    self.config = json.load(f)
                self._ensure_claude_code_models()
                return
            except (json.JSONDecodeError, IOError):
                pass
        self.config = json.loads(json.dumps(self.DEFAULT_CONFIG))
        self.save()

    def _ensure_claude_code_models(self):
        """Ensure Claude Code models exist and are enabled in config."""
        models = self.config.setdefault("models", {})
        changed = False
        for mid, info in self.CLAUDE_CODE_MODELS.items():
            if mid not in models:
                models[mid] = dict(info)
                changed = True
            elif not models[mid].get("enabled", False):
                models[mid]["enabled"] = True
                changed = True
        if changed:
            self.save()

    def save(self):
        fd, tmp = tempfile.mkstemp(dir=CONFIG_DIR, suffix=".tmp")
        try:
            with os.fdopen(fd, "w") as f:
                json.dump(self.config, f, indent=2)
            os.replace(tmp, CONSULT_CONFIG_FILE)
        except Exception:
            if os.path.exists(tmp):
                os.unlink(tmp)
            raise

    def get_api_key(self):
        return self.config.get("api_key", "")

    def set_api_key(self, key):
        self.config["api_key"] = key
        self.save()

    def get_default_model(self):
        return self.config.get("default_model", "")

    def set_default_model(self, model_id):
        self.config["default_model"] = model_id
        models = self.config.setdefault("models", {})
        if model_id in models:
            models[model_id]["enabled"] = True
        self.save()

    def get_models(self):
        return self.config.get("models", {})

    def set_model_enabled(self, model_id, enabled):
        models = self.config.setdefault("models", {})
        if model_id in models:
            models[model_id]["enabled"] = enabled
            self.save()

    def add_model(self, model_id, name="", enabled=True, source="openrouter"):
        models = self.config.setdefault("models", {})
        models[model_id] = {"name": name or model_id, "enabled": enabled, "source": source}
        self.save()

    def remove_model(self, model_id):
        models = self.config.get("models", {})
        models.pop(model_id, None)
        if self.config.get("default_model") == model_id:
            self.config["default_model"] = ""
        self.save()

    def get_project_preset(self, project_dir):
        """Return tribunal preset for a project dir, or None."""
        presets = self.config.get("tribunal_projects", {})
        return presets.get(project_dir)

    def save_project_preset(self, project_dir, preset):
        """Save tribunal preset for a project dir."""
        if "tribunal_projects" not in self.config:
            self.config["tribunal_projects"] = {}
        self.config["tribunal_projects"][project_dir] = preset
        self.save()

    def delete_project_preset(self, project_dir):
        """Remove tribunal preset for a project dir."""
        presets = self.config.get("tribunal_projects", {})
        presets.pop(project_dir, None)
        self.save()
