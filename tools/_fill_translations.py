#!/usr/bin/env python3
"""Fill UI translations for all supported languages.

One-shot generator. Reads `locale/<short>/LC_MESSAGES/bterminal.po`
(initialised by `./tools/i18n.sh new <locale>`) and fills msgstr from
the per-language dictionaries below. After running:

    ./tools/i18n.sh compile

…to produce the runtime `.mo` files.

Adding a new language: drop a new entry into `TRANSLATIONS` keyed by
language short code (matches `locale/<short>/`), then run this script.
The keys must match the msgids in `locale/bterminal.pot` (run
`./tools/i18n.sh extract` first if unsure).

Plural forms: each language entry includes a `_plurals` mapping where
the key is `(singular_msgid, plural_msgid)` and the value is a tuple
of forms in the gettext order required by that language's
Plural-Forms header. msginit auto-fills the header; we only fill
the msgstr[N] values.
"""

from babel.messages.pofile import read_po, write_po

LOCALE_DIR = "locale"

# ─── Translations ────────────────────────────────────────────────────────────
# Keys match msgids in locale/bterminal.pot exactly. Whitespace and
# trailing punctuation must be preserved 1:1 with the source.

# A single sentinel used as a sub-dict key for plural-form translations.
PLURALS_KEY = "_plurals"


PL = {
    # license.py
    "BTerminal — License Agreement ({context})": "BTerminal — Umowa licencyjna ({context})",
    "Decline and exit": "Odrzuć i wyjdź",
    "Accept": "Akceptuję",
    "Please read the license agreement below.": "Proszę przeczytać poniższą umowę licencyjną.",
    "You must accept these terms to use BTerminal.": "Musisz zaakceptować te warunki, aby korzystać z BTerminala.",
    "I have read and accept the license terms.": "Przeczytałem i akceptuję warunki licencji.",
    "First run": "Pierwsze uruchomienie",
    "Update": "Aktualizacja",
    # updater.py
    "No repository": "Brak repozytorium",
    "Cannot check for updates — repository directory not found.":
        "Nie można sprawdzić aktualizacji — katalog repozytorium nie został znaleziony.",
    "Checking for updates": "Sprawdzanie aktualizacji",
    "Connecting to server... ({seconds}s)": "Łączenie z serwerem... ({seconds}s)",
    "Cancel": "Anuluj",
    "Cannot check for updates — timed out.": "Nie można sprawdzić — przekroczono limit czasu.",
    "Close": "Zamknij",
    "BTerminal is up to date. No new updates.": "BTerminal jest aktualny. Brak nowych aktualizacji.",
    "Cannot check for updates.": "Nie można sprawdzić aktualizacji.",
    "New BTerminal version": "Nowa wersja BTerminal",
    "A new version of BTerminal is available": "Dostępna nowa wersja BTerminal",
    "Show errata": "Pokaż erratę",
    "Not now": "Nie teraz",
    "Update and restart": "Aktualizuj i uruchom ponownie",
    "BTerminal errata": "Errata BTerminal",
    "No errata entries.": "Brak wpisów errata.",
    "BTerminal update": "Aktualizacja BTerminal",
    "Update in progress…": "Aktualizacja w toku…",
    (
        "The new version of BTerminal could not be installed.\n\n"
        "The previous version was restored automatically — "
        "BTerminal continues to work normally."
    ): (
        "Nowa wersja BTerminal nie mogła zostać zainstalowana.\n\n"
        "Poprzednia wersja została automatycznie przywrócona — "
        "BTerminal działa normalnie."
    ),
    (
        "Installation failed and no previous version is available "
        "to restore.\n\nDetails:\n{details}"
    ): (
        "Instalacja nie powiodła się i nie ma poprzedniej wersji do przywrócenia.\n\n"
        "Szczegóły:\n{details}"
    ),
    # app.py
    "New local tab": "Nowa karta lokalna",
    "New SSH session…": "Nowa sesja SSH…",
    "New Claude Code session…": "Nowa sesja Claude Code…",
    "Options…": "Opcje…",
    "Quit": "Zamknij aplikację",
    "File": "Plik",
    "Toggle sidebar (Ctrl+B)": "Przełącz sidebar (Ctrl+B)",
    "Toggle Git panel (Ctrl+G)": "Przełącz panel Git (Ctrl+G)",
    "Toggle theme ☀/🌙": "Przełącz motyw ☀/🌙",
    "Sessions": "Sesje",
    "Ctx": "Ctx",
    "Consult": "Konsultacja",
    "Tasks": "Zadania",
    "Plugins": "Wtyczki",
    "View": "Widok",
    "Check for updates": "Sprawdź aktualizacje",
    "Errata…": "Errata…",
    "Tools": "Narzędzia",
    (
        "The file ~/.config/bterminal/options.json was corrupted — "
        "default settings have been restored.\n\n"
        "Cause: {exc_type}: {exc}"
    ): (
        "Plik ~/.config/bterminal/options.json był uszkodzony — "
        "przywrócono ustawienia domyślne.\n\n"
        "Przyczyna: {exc_type}: {exc}"
    ),
    # ctx/dialogs.py
    (
        "Project context has not been collected yet. "
        "Gather context as you work and save important findings: "
        "ctx set <project> <key> <value>"
    ): (
        "Kontekst projektu nie został jeszcze zebrany. "
        "Zbierz kontekst w trakcie pracy i zapisuj ważne odkrycia: "
        "ctx set <project> <key> <value>"
    ),
    # ui/dialogs/options.py
    "Appearance": "Wygląd",
    "Theme:": "Motyw:",
    "Dark (Mocha)": "Ciemny (Mocha)",
    "Light (Latte)": "Jasny (Latte)",
    "Terminal font:": "Font terminala:",
    "Terminal": "Terminal",
    "Default shell:": "Domyślny shell:",
    "default ({shell})": "domyślny ({shell})",
    "General": "Ogólne",
    "Check for updates at startup:": "Sprawdzaj aktualizacje przy starcie:",
    "Save": "Zapisz",
    # ui/panels/git.py
    "Working tree clean": "Drzewo robocze czyste",
    # app.py — sidebar tooltips
    "Hide sidebar (Ctrl+B)": "Ukryj sidebar (Ctrl+B)",
    "Show sidebar (Ctrl+B)": "Pokaż sidebar (Ctrl+B)",
    "Toggle light/dark theme": "Przełącz motyw jasny/ciemny",
    "Show Git panel (Ctrl+G)": "Pokaż panel Git (Ctrl+G)",
    "Memory": "Memory",
    "Files": "Pliki",
    "Skills": "Skills",
    # ui/sidebar.py
    "{app} Sessions": "Sesje {app}",
    # ui/dialogs/options.py — Language section
    "Language": "Język",
    "Interface language:": "Język interfejsu:",
    "Auto-detect": "Wykryj automatycznie",
    "Restart BTerminal to apply language change.":
        "Uruchom BTerminala ponownie, aby zastosować zmianę języka.",
    "Tell the AI agent which language I speak":
        "Powiedz agentowi AI, w jakim języku mówię",
    PLURALS_KEY: {
        ("{n} file", "{n} files"): ("{n} plik", "{n} pliki", "{n} plików"),
    },
}


DE = {
    "BTerminal — License Agreement ({context})": "BTerminal — Lizenzvereinbarung ({context})",
    "Decline and exit": "Ablehnen und beenden",
    "Accept": "Annehmen",
    "Please read the license agreement below.": "Bitte lesen Sie die untenstehende Lizenzvereinbarung.",
    "You must accept these terms to use BTerminal.": "Sie müssen diese Bedingungen akzeptieren, um BTerminal zu nutzen.",
    "I have read and accept the license terms.": "Ich habe die Lizenzbedingungen gelesen und akzeptiere sie.",
    "First run": "Erster Start",
    "Update": "Aktualisierung",
    "No repository": "Kein Repository",
    "Cannot check for updates — repository directory not found.":
        "Kann nicht auf Updates prüfen — Repository-Verzeichnis nicht gefunden.",
    "Checking for updates": "Suche nach Updates",
    "Connecting to server... ({seconds}s)": "Verbinde mit Server... ({seconds}s)",
    "Cancel": "Abbrechen",
    "Cannot check for updates — timed out.": "Update-Prüfung fehlgeschlagen — Zeitüberschreitung.",
    "Close": "Schließen",
    "BTerminal is up to date. No new updates.": "BTerminal ist aktuell. Keine neuen Updates.",
    "Cannot check for updates.": "Update-Prüfung fehlgeschlagen.",
    "New BTerminal version": "Neue BTerminal-Version",
    "A new version of BTerminal is available": "Eine neue Version von BTerminal ist verfügbar",
    "Show errata": "Errata anzeigen",
    "Not now": "Nicht jetzt",
    "Update and restart": "Aktualisieren und neu starten",
    "BTerminal errata": "BTerminal Errata",
    "No errata entries.": "Keine Errata-Einträge.",
    "BTerminal update": "BTerminal-Aktualisierung",
    "Update in progress…": "Aktualisierung läuft…",
    (
        "The new version of BTerminal could not be installed.\n\n"
        "The previous version was restored automatically — "
        "BTerminal continues to work normally."
    ): (
        "Die neue Version von BTerminal konnte nicht installiert werden.\n\n"
        "Die vorherige Version wurde automatisch wiederhergestellt — "
        "BTerminal funktioniert normal weiter."
    ),
    (
        "Installation failed and no previous version is available "
        "to restore.\n\nDetails:\n{details}"
    ): (
        "Installation fehlgeschlagen und keine vorherige Version zum "
        "Wiederherstellen verfügbar.\n\nDetails:\n{details}"
    ),
    "New local tab": "Neuer lokaler Tab",
    "New SSH session…": "Neue SSH-Sitzung…",
    "New Claude Code session…": "Neue Claude-Code-Sitzung…",
    "Options…": "Einstellungen…",
    "Quit": "Beenden",
    "File": "Datei",
    "Toggle sidebar (Ctrl+B)": "Seitenleiste umschalten (Strg+B)",
    "Toggle Git panel (Ctrl+G)": "Git-Panel umschalten (Strg+G)",
    "Toggle theme ☀/🌙": "Design umschalten ☀/🌙",
    "Sessions": "Sitzungen",
    "Ctx": "Ctx",
    "Consult": "Consult",
    "Tasks": "Aufgaben",
    "Plugins": "Plugins",
    "View": "Ansicht",
    "Check for updates": "Auf Updates prüfen",
    "Errata…": "Errata…",
    "Tools": "Werkzeuge",
    (
        "The file ~/.config/bterminal/options.json was corrupted — "
        "default settings have been restored.\n\n"
        "Cause: {exc_type}: {exc}"
    ): (
        "Die Datei ~/.config/bterminal/options.json war beschädigt — "
        "Standardeinstellungen wurden wiederhergestellt.\n\n"
        "Ursache: {exc_type}: {exc}"
    ),
    (
        "Project context has not been collected yet. "
        "Gather context as you work and save important findings: "
        "ctx set <project> <key> <value>"
    ): (
        "Der Projektkontext wurde noch nicht gesammelt. "
        "Sammeln Sie Kontext während der Arbeit und speichern Sie wichtige Erkenntnisse: "
        "ctx set <project> <key> <value>"
    ),
    "Appearance": "Erscheinungsbild",
    "Theme:": "Design:",
    "Dark (Mocha)": "Dunkel (Mocha)",
    "Light (Latte)": "Hell (Latte)",
    "Terminal font:": "Terminal-Schriftart:",
    "Terminal": "Terminal",
    "Default shell:": "Standard-Shell:",
    "default ({shell})": "Standard ({shell})",
    "General": "Allgemein",
    "Check for updates at startup:": "Beim Start auf Updates prüfen:",
    "Save": "Speichern",
    "Working tree clean": "Arbeitsverzeichnis sauber",
    "Hide sidebar (Ctrl+B)": "Seitenleiste ausblenden (Strg+B)",
    "Show sidebar (Ctrl+B)": "Seitenleiste anzeigen (Strg+B)",
    "Toggle light/dark theme": "Helles/dunkles Design umschalten",
    "Show Git panel (Ctrl+G)": "Git-Panel anzeigen (Strg+G)",
    "Memory": "Speicher",
    "Files": "Dateien",
    "Skills": "Skills",
    "{app} Sessions": "{app}-Sitzungen",
    "Language": "Sprache",
    "Interface language:": "Oberflächensprache:",
    "Auto-detect": "Automatisch erkennen",
    "Restart BTerminal to apply language change.":
        "BTerminal neu starten, um die Sprachänderung zu übernehmen.",
    "Tell the AI agent which language I speak":
        "Dem KI-Agenten mitteilen, welche Sprache ich spreche",
    PLURALS_KEY: {
        ("{n} file", "{n} files"): ("{n} Datei", "{n} Dateien"),
    },
}


ES = {
    "BTerminal — License Agreement ({context})": "BTerminal — Acuerdo de licencia ({context})",
    "Decline and exit": "Rechazar y salir",
    "Accept": "Aceptar",
    "Please read the license agreement below.": "Por favor, lea el acuerdo de licencia a continuación.",
    "You must accept these terms to use BTerminal.": "Debe aceptar estos términos para usar BTerminal.",
    "I have read and accept the license terms.": "He leído y acepto los términos de la licencia.",
    "First run": "Primer inicio",
    "Update": "Actualización",
    "No repository": "Sin repositorio",
    "Cannot check for updates — repository directory not found.":
        "No se puede comprobar actualizaciones — directorio del repositorio no encontrado.",
    "Checking for updates": "Buscando actualizaciones",
    "Connecting to server... ({seconds}s)": "Conectando al servidor... ({seconds}s)",
    "Cancel": "Cancelar",
    "Cannot check for updates — timed out.": "No se pudo comprobar — tiempo de espera agotado.",
    "Close": "Cerrar",
    "BTerminal is up to date. No new updates.": "BTerminal está actualizado. No hay actualizaciones nuevas.",
    "Cannot check for updates.": "No se pudo comprobar actualizaciones.",
    "New BTerminal version": "Nueva versión de BTerminal",
    "A new version of BTerminal is available": "Hay una nueva versión de BTerminal disponible",
    "Show errata": "Mostrar erratas",
    "Not now": "Ahora no",
    "Update and restart": "Actualizar y reiniciar",
    "BTerminal errata": "Erratas de BTerminal",
    "No errata entries.": "Sin entradas de erratas.",
    "BTerminal update": "Actualización de BTerminal",
    "Update in progress…": "Actualización en curso…",
    (
        "The new version of BTerminal could not be installed.\n\n"
        "The previous version was restored automatically — "
        "BTerminal continues to work normally."
    ): (
        "No se pudo instalar la nueva versión de BTerminal.\n\n"
        "La versión anterior se restauró automáticamente — "
        "BTerminal sigue funcionando con normalidad."
    ),
    (
        "Installation failed and no previous version is available "
        "to restore.\n\nDetails:\n{details}"
    ): (
        "La instalación falló y no hay una versión anterior disponible "
        "para restaurar.\n\nDetalles:\n{details}"
    ),
    "New local tab": "Nueva pestaña local",
    "New SSH session…": "Nueva sesión SSH…",
    "New Claude Code session…": "Nueva sesión de Claude Code…",
    "Options…": "Opciones…",
    "Quit": "Salir",
    "File": "Archivo",
    "Toggle sidebar (Ctrl+B)": "Alternar barra lateral (Ctrl+B)",
    "Toggle Git panel (Ctrl+G)": "Alternar panel Git (Ctrl+G)",
    "Toggle theme ☀/🌙": "Alternar tema ☀/🌙",
    "Sessions": "Sesiones",
    "Ctx": "Ctx",
    "Consult": "Consult",
    "Tasks": "Tareas",
    "Plugins": "Complementos",
    "View": "Ver",
    "Check for updates": "Comprobar actualizaciones",
    "Errata…": "Erratas…",
    "Tools": "Herramientas",
    (
        "The file ~/.config/bterminal/options.json was corrupted — "
        "default settings have been restored.\n\n"
        "Cause: {exc_type}: {exc}"
    ): (
        "El archivo ~/.config/bterminal/options.json estaba dañado — "
        "se han restaurado los ajustes predeterminados.\n\n"
        "Causa: {exc_type}: {exc}"
    ),
    (
        "Project context has not been collected yet. "
        "Gather context as you work and save important findings: "
        "ctx set <project> <key> <value>"
    ): (
        "El contexto del proyecto aún no se ha recopilado. "
        "Recopile contexto mientras trabaja y guarde hallazgos importantes: "
        "ctx set <project> <key> <value>"
    ),
    "Appearance": "Apariencia",
    "Theme:": "Tema:",
    "Dark (Mocha)": "Oscuro (Mocha)",
    "Light (Latte)": "Claro (Latte)",
    "Terminal font:": "Fuente del terminal:",
    "Terminal": "Terminal",
    "Default shell:": "Shell predeterminada:",
    "default ({shell})": "predeterminado ({shell})",
    "General": "General",
    "Check for updates at startup:": "Buscar actualizaciones al iniciar:",
    "Save": "Guardar",
    "Working tree clean": "Árbol de trabajo limpio",
    "Hide sidebar (Ctrl+B)": "Ocultar barra lateral (Ctrl+B)",
    "Show sidebar (Ctrl+B)": "Mostrar barra lateral (Ctrl+B)",
    "Toggle light/dark theme": "Alternar tema claro/oscuro",
    "Show Git panel (Ctrl+G)": "Mostrar panel Git (Ctrl+G)",
    "Memory": "Memoria",
    "Files": "Archivos",
    "Skills": "Skills",
    "{app} Sessions": "Sesiones de {app}",
    "Language": "Idioma",
    "Interface language:": "Idioma de la interfaz:",
    "Auto-detect": "Detectar automáticamente",
    "Restart BTerminal to apply language change.":
        "Reinicie BTerminal para aplicar el cambio de idioma.",
    "Tell the AI agent which language I speak":
        "Decir al agente de IA qué idioma hablo",
    PLURALS_KEY: {
        ("{n} file", "{n} files"): ("{n} archivo", "{n} archivos"),
    },
}


FR = {
    "BTerminal — License Agreement ({context})": "BTerminal — Contrat de licence ({context})",
    "Decline and exit": "Refuser et quitter",
    "Accept": "Accepter",
    "Please read the license agreement below.": "Veuillez lire le contrat de licence ci-dessous.",
    "You must accept these terms to use BTerminal.": "Vous devez accepter ces conditions pour utiliser BTerminal.",
    "I have read and accept the license terms.": "J'ai lu et j'accepte les conditions de la licence.",
    "First run": "Premier lancement",
    "Update": "Mise à jour",
    "No repository": "Aucun dépôt",
    "Cannot check for updates — repository directory not found.":
        "Impossible de vérifier les mises à jour — dossier du dépôt introuvable.",
    "Checking for updates": "Recherche de mises à jour",
    "Connecting to server... ({seconds}s)": "Connexion au serveur... ({seconds}s)",
    "Cancel": "Annuler",
    "Cannot check for updates — timed out.": "Vérification impossible — délai dépassé.",
    "Close": "Fermer",
    "BTerminal is up to date. No new updates.": "BTerminal est à jour. Aucune nouvelle mise à jour.",
    "Cannot check for updates.": "Impossible de vérifier les mises à jour.",
    "New BTerminal version": "Nouvelle version de BTerminal",
    "A new version of BTerminal is available": "Une nouvelle version de BTerminal est disponible",
    "Show errata": "Afficher les errata",
    "Not now": "Pas maintenant",
    "Update and restart": "Mettre à jour et redémarrer",
    "BTerminal errata": "Errata de BTerminal",
    "No errata entries.": "Aucune entrée d'errata.",
    "BTerminal update": "Mise à jour de BTerminal",
    "Update in progress…": "Mise à jour en cours…",
    (
        "The new version of BTerminal could not be installed.\n\n"
        "The previous version was restored automatically — "
        "BTerminal continues to work normally."
    ): (
        "La nouvelle version de BTerminal n'a pas pu être installée.\n\n"
        "La version précédente a été restaurée automatiquement — "
        "BTerminal continue de fonctionner normalement."
    ),
    (
        "Installation failed and no previous version is available "
        "to restore.\n\nDetails:\n{details}"
    ): (
        "L'installation a échoué et aucune version précédente n'est disponible "
        "pour la restauration.\n\nDétails :\n{details}"
    ),
    "New local tab": "Nouvel onglet local",
    "New SSH session…": "Nouvelle session SSH…",
    "New Claude Code session…": "Nouvelle session Claude Code…",
    "Options…": "Options…",
    "Quit": "Quitter",
    "File": "Fichier",
    "Toggle sidebar (Ctrl+B)": "Basculer la barre latérale (Ctrl+B)",
    "Toggle Git panel (Ctrl+G)": "Basculer le panneau Git (Ctrl+G)",
    "Toggle theme ☀/🌙": "Basculer le thème ☀/🌙",
    "Sessions": "Sessions",
    "Ctx": "Ctx",
    "Consult": "Consult",
    "Tasks": "Tâches",
    "Plugins": "Plugins",
    "View": "Affichage",
    "Check for updates": "Rechercher les mises à jour",
    "Errata…": "Errata…",
    "Tools": "Outils",
    (
        "The file ~/.config/bterminal/options.json was corrupted — "
        "default settings have been restored.\n\n"
        "Cause: {exc_type}: {exc}"
    ): (
        "Le fichier ~/.config/bterminal/options.json était corrompu — "
        "les paramètres par défaut ont été restaurés.\n\n"
        "Cause : {exc_type} : {exc}"
    ),
    (
        "Project context has not been collected yet. "
        "Gather context as you work and save important findings: "
        "ctx set <project> <key> <value>"
    ): (
        "Le contexte du projet n'a pas encore été recueilli. "
        "Recueillez le contexte au fur et à mesure de votre travail et enregistrez les découvertes importantes : "
        "ctx set <project> <key> <value>"
    ),
    "Appearance": "Apparence",
    "Theme:": "Thème :",
    "Dark (Mocha)": "Sombre (Mocha)",
    "Light (Latte)": "Clair (Latte)",
    "Terminal font:": "Police du terminal :",
    "Terminal": "Terminal",
    "Default shell:": "Shell par défaut :",
    "default ({shell})": "par défaut ({shell})",
    "General": "Général",
    "Check for updates at startup:": "Rechercher les mises à jour au démarrage :",
    "Save": "Enregistrer",
    "Working tree clean": "Arborescence de travail propre",
    "Hide sidebar (Ctrl+B)": "Masquer la barre latérale (Ctrl+B)",
    "Show sidebar (Ctrl+B)": "Afficher la barre latérale (Ctrl+B)",
    "Toggle light/dark theme": "Basculer thème clair/sombre",
    "Show Git panel (Ctrl+G)": "Afficher le panneau Git (Ctrl+G)",
    "Memory": "Mémoire",
    "Files": "Fichiers",
    "Skills": "Skills",
    "{app} Sessions": "Sessions {app}",
    "Language": "Langue",
    "Interface language:": "Langue de l'interface :",
    "Auto-detect": "Détecter automatiquement",
    "Restart BTerminal to apply language change.":
        "Redémarrez BTerminal pour appliquer le changement de langue.",
    "Tell the AI agent which language I speak":
        "Indiquer à l'agent IA quelle langue je parle",
    PLURALS_KEY: {
        ("{n} file", "{n} files"): ("{n} fichier", "{n} fichiers"),
    },
}


IT = {
    "BTerminal — License Agreement ({context})": "BTerminal — Contratto di licenza ({context})",
    "Decline and exit": "Rifiuta ed esci",
    "Accept": "Accetta",
    "Please read the license agreement below.": "Si prega di leggere il contratto di licenza qui sotto.",
    "You must accept these terms to use BTerminal.": "È necessario accettare questi termini per utilizzare BTerminal.",
    "I have read and accept the license terms.": "Ho letto e accetto i termini della licenza.",
    "First run": "Primo avvio",
    "Update": "Aggiornamento",
    "No repository": "Nessun repository",
    "Cannot check for updates — repository directory not found.":
        "Impossibile controllare gli aggiornamenti — directory del repository non trovata.",
    "Checking for updates": "Ricerca aggiornamenti",
    "Connecting to server... ({seconds}s)": "Connessione al server... ({seconds}s)",
    "Cancel": "Annulla",
    "Cannot check for updates — timed out.": "Impossibile controllare — tempo scaduto.",
    "Close": "Chiudi",
    "BTerminal is up to date. No new updates.": "BTerminal è aggiornato. Nessun nuovo aggiornamento.",
    "Cannot check for updates.": "Impossibile controllare gli aggiornamenti.",
    "New BTerminal version": "Nuova versione di BTerminal",
    "A new version of BTerminal is available": "È disponibile una nuova versione di BTerminal",
    "Show errata": "Mostra errata",
    "Not now": "Non ora",
    "Update and restart": "Aggiorna e riavvia",
    "BTerminal errata": "Errata di BTerminal",
    "No errata entries.": "Nessuna voce di errata.",
    "BTerminal update": "Aggiornamento di BTerminal",
    "Update in progress…": "Aggiornamento in corso…",
    (
        "The new version of BTerminal could not be installed.\n\n"
        "The previous version was restored automatically — "
        "BTerminal continues to work normally."
    ): (
        "Non è stato possibile installare la nuova versione di BTerminal.\n\n"
        "La versione precedente è stata ripristinata automaticamente — "
        "BTerminal continua a funzionare normalmente."
    ),
    (
        "Installation failed and no previous version is available "
        "to restore.\n\nDetails:\n{details}"
    ): (
        "Installazione non riuscita e nessuna versione precedente disponibile "
        "per il ripristino.\n\nDettagli:\n{details}"
    ),
    "New local tab": "Nuova scheda locale",
    "New SSH session…": "Nuova sessione SSH…",
    "New Claude Code session…": "Nuova sessione Claude Code…",
    "Options…": "Opzioni…",
    "Quit": "Esci",
    "File": "File",
    "Toggle sidebar (Ctrl+B)": "Mostra/nascondi barra laterale (Ctrl+B)",
    "Toggle Git panel (Ctrl+G)": "Mostra/nascondi pannello Git (Ctrl+G)",
    "Toggle theme ☀/🌙": "Cambia tema ☀/🌙",
    "Sessions": "Sessioni",
    "Ctx": "Ctx",
    "Consult": "Consult",
    "Tasks": "Attività",
    "Plugins": "Plugin",
    "View": "Visualizza",
    "Check for updates": "Cerca aggiornamenti",
    "Errata…": "Errata…",
    "Tools": "Strumenti",
    (
        "The file ~/.config/bterminal/options.json was corrupted — "
        "default settings have been restored.\n\n"
        "Cause: {exc_type}: {exc}"
    ): (
        "Il file ~/.config/bterminal/options.json era corrotto — "
        "le impostazioni predefinite sono state ripristinate.\n\n"
        "Causa: {exc_type}: {exc}"
    ),
    (
        "Project context has not been collected yet. "
        "Gather context as you work and save important findings: "
        "ctx set <project> <key> <value>"
    ): (
        "Il contesto del progetto non è ancora stato raccolto. "
        "Raccogli il contesto mentre lavori e salva le scoperte importanti: "
        "ctx set <project> <key> <value>"
    ),
    "Appearance": "Aspetto",
    "Theme:": "Tema:",
    "Dark (Mocha)": "Scuro (Mocha)",
    "Light (Latte)": "Chiaro (Latte)",
    "Terminal font:": "Carattere del terminale:",
    "Terminal": "Terminale",
    "Default shell:": "Shell predefinita:",
    "default ({shell})": "predefinita ({shell})",
    "General": "Generale",
    "Check for updates at startup:": "Cerca aggiornamenti all'avvio:",
    "Save": "Salva",
    "Working tree clean": "Albero di lavoro pulito",
    "Hide sidebar (Ctrl+B)": "Nascondi barra laterale (Ctrl+B)",
    "Show sidebar (Ctrl+B)": "Mostra barra laterale (Ctrl+B)",
    "Toggle light/dark theme": "Cambia tema chiaro/scuro",
    "Show Git panel (Ctrl+G)": "Mostra pannello Git (Ctrl+G)",
    "Memory": "Memoria",
    "Files": "File",
    "Skills": "Skills",
    "{app} Sessions": "Sessioni {app}",
    "Language": "Lingua",
    "Interface language:": "Lingua dell'interfaccia:",
    "Auto-detect": "Rileva automaticamente",
    "Restart BTerminal to apply language change.":
        "Riavvia BTerminal per applicare il cambio di lingua.",
    "Tell the AI agent which language I speak":
        "Comunica all'agente IA quale lingua parlo",
    PLURALS_KEY: {
        ("{n} file", "{n} files"): ("{n} file", "{n} file"),
    },
}


PT = {
    "BTerminal — License Agreement ({context})": "BTerminal — Contrato de licença ({context})",
    "Decline and exit": "Recusar e sair",
    "Accept": "Aceitar",
    "Please read the license agreement below.": "Por favor, leia o contrato de licença abaixo.",
    "You must accept these terms to use BTerminal.": "Você precisa aceitar estes termos para usar o BTerminal.",
    "I have read and accept the license terms.": "Li e aceito os termos da licença.",
    "First run": "Primeira execução",
    "Update": "Atualização",
    "No repository": "Sem repositório",
    "Cannot check for updates — repository directory not found.":
        "Não é possível verificar atualizações — diretório do repositório não encontrado.",
    "Checking for updates": "Verificando atualizações",
    "Connecting to server... ({seconds}s)": "Conectando ao servidor... ({seconds}s)",
    "Cancel": "Cancelar",
    "Cannot check for updates — timed out.": "Não foi possível verificar — tempo esgotado.",
    "Close": "Fechar",
    "BTerminal is up to date. No new updates.": "O BTerminal está atualizado. Sem novas atualizações.",
    "Cannot check for updates.": "Não foi possível verificar atualizações.",
    "New BTerminal version": "Nova versão do BTerminal",
    "A new version of BTerminal is available": "Uma nova versão do BTerminal está disponível",
    "Show errata": "Mostrar errata",
    "Not now": "Agora não",
    "Update and restart": "Atualizar e reiniciar",
    "BTerminal errata": "Errata do BTerminal",
    "No errata entries.": "Sem entradas de errata.",
    "BTerminal update": "Atualização do BTerminal",
    "Update in progress…": "Atualização em andamento…",
    (
        "The new version of BTerminal could not be installed.\n\n"
        "The previous version was restored automatically — "
        "BTerminal continues to work normally."
    ): (
        "A nova versão do BTerminal não pôde ser instalada.\n\n"
        "A versão anterior foi restaurada automaticamente — "
        "o BTerminal continua funcionando normalmente."
    ),
    (
        "Installation failed and no previous version is available "
        "to restore.\n\nDetails:\n{details}"
    ): (
        "A instalação falhou e não há versão anterior disponível "
        "para restaurar.\n\nDetalhes:\n{details}"
    ),
    "New local tab": "Nova aba local",
    "New SSH session…": "Nova sessão SSH…",
    "New Claude Code session…": "Nova sessão Claude Code…",
    "Options…": "Opções…",
    "Quit": "Sair",
    "File": "Arquivo",
    "Toggle sidebar (Ctrl+B)": "Alternar barra lateral (Ctrl+B)",
    "Toggle Git panel (Ctrl+G)": "Alternar painel Git (Ctrl+G)",
    "Toggle theme ☀/🌙": "Alternar tema ☀/🌙",
    "Sessions": "Sessões",
    "Ctx": "Ctx",
    "Consult": "Consult",
    "Tasks": "Tarefas",
    "Plugins": "Plugins",
    "View": "Exibir",
    "Check for updates": "Verificar atualizações",
    "Errata…": "Errata…",
    "Tools": "Ferramentas",
    (
        "The file ~/.config/bterminal/options.json was corrupted — "
        "default settings have been restored.\n\n"
        "Cause: {exc_type}: {exc}"
    ): (
        "O arquivo ~/.config/bterminal/options.json estava corrompido — "
        "as configurações padrão foram restauradas.\n\n"
        "Causa: {exc_type}: {exc}"
    ),
    (
        "Project context has not been collected yet. "
        "Gather context as you work and save important findings: "
        "ctx set <project> <key> <value>"
    ): (
        "O contexto do projeto ainda não foi coletado. "
        "Colete contexto conforme trabalha e salve descobertas importantes: "
        "ctx set <project> <key> <value>"
    ),
    "Appearance": "Aparência",
    "Theme:": "Tema:",
    "Dark (Mocha)": "Escuro (Mocha)",
    "Light (Latte)": "Claro (Latte)",
    "Terminal font:": "Fonte do terminal:",
    "Terminal": "Terminal",
    "Default shell:": "Shell padrão:",
    "default ({shell})": "padrão ({shell})",
    "General": "Geral",
    "Check for updates at startup:": "Verificar atualizações ao iniciar:",
    "Save": "Salvar",
    "Working tree clean": "Árvore de trabalho limpa",
    "Hide sidebar (Ctrl+B)": "Ocultar barra lateral (Ctrl+B)",
    "Show sidebar (Ctrl+B)": "Mostrar barra lateral (Ctrl+B)",
    "Toggle light/dark theme": "Alternar tema claro/escuro",
    "Show Git panel (Ctrl+G)": "Mostrar painel Git (Ctrl+G)",
    "Memory": "Memória",
    "Files": "Arquivos",
    "Skills": "Skills",
    "{app} Sessions": "Sessões do {app}",
    "Language": "Idioma",
    "Interface language:": "Idioma da interface:",
    "Auto-detect": "Detectar automaticamente",
    "Restart BTerminal to apply language change.":
        "Reinicie o BTerminal para aplicar a mudança de idioma.",
    "Tell the AI agent which language I speak":
        "Informar ao agente de IA qual idioma falo",
    PLURALS_KEY: {
        ("{n} file", "{n} files"): ("{n} arquivo", "{n} arquivos"),
    },
}


RU = {
    "BTerminal — License Agreement ({context})": "BTerminal — Лицензионное соглашение ({context})",
    "Decline and exit": "Отклонить и выйти",
    "Accept": "Принять",
    "Please read the license agreement below.": "Пожалуйста, прочитайте лицензионное соглашение ниже.",
    "You must accept these terms to use BTerminal.": "Вы должны принять эти условия, чтобы использовать BTerminal.",
    "I have read and accept the license terms.": "Я прочитал(а) и принимаю условия лицензии.",
    "First run": "Первый запуск",
    "Update": "Обновление",
    "No repository": "Нет репозитория",
    "Cannot check for updates — repository directory not found.":
        "Невозможно проверить обновления — каталог репозитория не найден.",
    "Checking for updates": "Проверка обновлений",
    "Connecting to server... ({seconds}s)": "Подключение к серверу... ({seconds} с)",
    "Cancel": "Отмена",
    "Cannot check for updates — timed out.": "Не удалось проверить — превышено время ожидания.",
    "Close": "Закрыть",
    "BTerminal is up to date. No new updates.": "BTerminal актуален. Новых обновлений нет.",
    "Cannot check for updates.": "Не удалось проверить обновления.",
    "New BTerminal version": "Новая версия BTerminal",
    "A new version of BTerminal is available": "Доступна новая версия BTerminal",
    "Show errata": "Показать список изменений",
    "Not now": "Не сейчас",
    "Update and restart": "Обновить и перезапустить",
    "BTerminal errata": "Список изменений BTerminal",
    "No errata entries.": "Нет записей в списке изменений.",
    "BTerminal update": "Обновление BTerminal",
    "Update in progress…": "Идёт обновление…",
    (
        "The new version of BTerminal could not be installed.\n\n"
        "The previous version was restored automatically — "
        "BTerminal continues to work normally."
    ): (
        "Не удалось установить новую версию BTerminal.\n\n"
        "Предыдущая версия восстановлена автоматически — "
        "BTerminal продолжает работать нормально."
    ),
    (
        "Installation failed and no previous version is available "
        "to restore.\n\nDetails:\n{details}"
    ): (
        "Установка не удалась, предыдущая версия для восстановления "
        "недоступна.\n\nПодробности:\n{details}"
    ),
    "New local tab": "Новая локальная вкладка",
    "New SSH session…": "Новый сеанс SSH…",
    "New Claude Code session…": "Новый сеанс Claude Code…",
    "Options…": "Параметры…",
    "Quit": "Выход",
    "File": "Файл",
    "Toggle sidebar (Ctrl+B)": "Боковая панель (Ctrl+B)",
    "Toggle Git panel (Ctrl+G)": "Панель Git (Ctrl+G)",
    "Toggle theme ☀/🌙": "Переключить тему ☀/🌙",
    "Sessions": "Сеансы",
    "Ctx": "Ctx",
    "Consult": "Consult",
    "Tasks": "Задачи",
    "Plugins": "Плагины",
    "View": "Вид",
    "Check for updates": "Проверить обновления",
    "Errata…": "Список изменений…",
    "Tools": "Инструменты",
    (
        "The file ~/.config/bterminal/options.json was corrupted — "
        "default settings have been restored.\n\n"
        "Cause: {exc_type}: {exc}"
    ): (
        "Файл ~/.config/bterminal/options.json был повреждён — "
        "восстановлены настройки по умолчанию.\n\n"
        "Причина: {exc_type}: {exc}"
    ),
    (
        "Project context has not been collected yet. "
        "Gather context as you work and save important findings: "
        "ctx set <project> <key> <value>"
    ): (
        "Контекст проекта ещё не собран. "
        "Собирайте контекст по ходу работы и сохраняйте важные находки: "
        "ctx set <project> <key> <value>"
    ),
    "Appearance": "Внешний вид",
    "Theme:": "Тема:",
    "Dark (Mocha)": "Тёмная (Mocha)",
    "Light (Latte)": "Светлая (Latte)",
    "Terminal font:": "Шрифт терминала:",
    "Terminal": "Терминал",
    "Default shell:": "Оболочка по умолчанию:",
    "default ({shell})": "по умолчанию ({shell})",
    "General": "Общие",
    "Check for updates at startup:": "Проверять обновления при запуске:",
    "Save": "Сохранить",
    "Working tree clean": "Рабочее дерево чистое",
    "Hide sidebar (Ctrl+B)": "Скрыть боковую панель (Ctrl+B)",
    "Show sidebar (Ctrl+B)": "Показать боковую панель (Ctrl+B)",
    "Toggle light/dark theme": "Светлая/тёмная тема",
    "Show Git panel (Ctrl+G)": "Показать панель Git (Ctrl+G)",
    "Memory": "Память",
    "Files": "Файлы",
    "Skills": "Skills",
    "{app} Sessions": "Сеансы {app}",
    "Language": "Язык",
    "Interface language:": "Язык интерфейса:",
    "Auto-detect": "Определить автоматически",
    "Restart BTerminal to apply language change.":
        "Перезапустите BTerminal, чтобы применить изменение языка.",
    "Tell the AI agent which language I speak":
        "Сообщить ИИ-агенту, на каком языке я говорю",
    PLURALS_KEY: {
        ("{n} file", "{n} files"): ("{n} файл", "{n} файла", "{n} файлов"),
    },
}


UK = {
    "BTerminal — License Agreement ({context})": "BTerminal — Ліцензійна угода ({context})",
    "Decline and exit": "Відхилити та вийти",
    "Accept": "Прийняти",
    "Please read the license agreement below.": "Будь ласка, прочитайте ліцензійну угоду нижче.",
    "You must accept these terms to use BTerminal.": "Ви маєте прийняти ці умови, щоб користуватися BTerminal.",
    "I have read and accept the license terms.": "Я прочитав(ла) і приймаю умови ліцензії.",
    "First run": "Перший запуск",
    "Update": "Оновлення",
    "No repository": "Немає репозиторію",
    "Cannot check for updates — repository directory not found.":
        "Неможливо перевірити оновлення — каталог репозиторію не знайдено.",
    "Checking for updates": "Перевірка оновлень",
    "Connecting to server... ({seconds}s)": "З'єднання з сервером... ({seconds} с)",
    "Cancel": "Скасувати",
    "Cannot check for updates — timed out.": "Не вдалося перевірити — час очікування вичерпано.",
    "Close": "Закрити",
    "BTerminal is up to date. No new updates.": "BTerminal актуальний. Нових оновлень немає.",
    "Cannot check for updates.": "Не вдалося перевірити оновлення.",
    "New BTerminal version": "Нова версія BTerminal",
    "A new version of BTerminal is available": "Доступна нова версія BTerminal",
    "Show errata": "Показати список змін",
    "Not now": "Не зараз",
    "Update and restart": "Оновити та перезапустити",
    "BTerminal errata": "Список змін BTerminal",
    "No errata entries.": "Немає записів у списку змін.",
    "BTerminal update": "Оновлення BTerminal",
    "Update in progress…": "Триває оновлення…",
    (
        "The new version of BTerminal could not be installed.\n\n"
        "The previous version was restored automatically — "
        "BTerminal continues to work normally."
    ): (
        "Не вдалося встановити нову версію BTerminal.\n\n"
        "Попередню версію автоматично відновлено — "
        "BTerminal продовжує працювати у звичайному режимі."
    ),
    (
        "Installation failed and no previous version is available "
        "to restore.\n\nDetails:\n{details}"
    ): (
        "Встановлення не вдалося, і немає попередньої версії "
        "для відновлення.\n\nДеталі:\n{details}"
    ),
    "New local tab": "Нова локальна вкладка",
    "New SSH session…": "Нова сесія SSH…",
    "New Claude Code session…": "Нова сесія Claude Code…",
    "Options…": "Параметри…",
    "Quit": "Вийти",
    "File": "Файл",
    "Toggle sidebar (Ctrl+B)": "Бічна панель (Ctrl+B)",
    "Toggle Git panel (Ctrl+G)": "Панель Git (Ctrl+G)",
    "Toggle theme ☀/🌙": "Змінити тему ☀/🌙",
    "Sessions": "Сесії",
    "Ctx": "Ctx",
    "Consult": "Consult",
    "Tasks": "Завдання",
    "Plugins": "Плагіни",
    "View": "Перегляд",
    "Check for updates": "Перевірити оновлення",
    "Errata…": "Список змін…",
    "Tools": "Інструменти",
    (
        "The file ~/.config/bterminal/options.json was corrupted — "
        "default settings have been restored.\n\n"
        "Cause: {exc_type}: {exc}"
    ): (
        "Файл ~/.config/bterminal/options.json було пошкоджено — "
        "відновлено типові налаштування.\n\n"
        "Причина: {exc_type}: {exc}"
    ),
    (
        "Project context has not been collected yet. "
        "Gather context as you work and save important findings: "
        "ctx set <project> <key> <value>"
    ): (
        "Контекст проєкту ще не зібрано. "
        "Збирайте контекст під час роботи й зберігайте важливі знахідки: "
        "ctx set <project> <key> <value>"
    ),
    "Appearance": "Вигляд",
    "Theme:": "Тема:",
    "Dark (Mocha)": "Темна (Mocha)",
    "Light (Latte)": "Світла (Latte)",
    "Terminal font:": "Шрифт термінала:",
    "Terminal": "Термінал",
    "Default shell:": "Типова оболонка:",
    "default ({shell})": "типова ({shell})",
    "General": "Загальні",
    "Check for updates at startup:": "Перевіряти оновлення під час запуску:",
    "Save": "Зберегти",
    "Working tree clean": "Робоче дерево чисте",
    "Hide sidebar (Ctrl+B)": "Сховати бічну панель (Ctrl+B)",
    "Show sidebar (Ctrl+B)": "Показати бічну панель (Ctrl+B)",
    "Toggle light/dark theme": "Світла/темна тема",
    "Show Git panel (Ctrl+G)": "Показати панель Git (Ctrl+G)",
    "Memory": "Пам'ять",
    "Files": "Файли",
    "Skills": "Skills",
    "{app} Sessions": "Сесії {app}",
    "Language": "Мова",
    "Interface language:": "Мова інтерфейсу:",
    "Auto-detect": "Визначити автоматично",
    "Restart BTerminal to apply language change.":
        "Перезапустіть BTerminal, щоб застосувати зміну мови.",
    "Tell the AI agent which language I speak":
        "Повідомити ШІ-агенту, якою мовою я говорю",
    PLURALS_KEY: {
        ("{n} file", "{n} files"): ("{n} файл", "{n} файли", "{n} файлів"),
    },
}


CS = {
    "BTerminal — License Agreement ({context})": "BTerminal — Licenční smlouva ({context})",
    "Decline and exit": "Odmítnout a ukončit",
    "Accept": "Přijmout",
    "Please read the license agreement below.": "Prosím, přečtěte si licenční smlouvu níže.",
    "You must accept these terms to use BTerminal.": "Pro používání BTerminalu musíte přijmout tyto podmínky.",
    "I have read and accept the license terms.": "Přečetl(a) jsem si a přijímám podmínky licence.",
    "First run": "První spuštění",
    "Update": "Aktualizace",
    "No repository": "Bez repozitáře",
    "Cannot check for updates — repository directory not found.":
        "Nelze zkontrolovat aktualizace — adresář repozitáře nenalezen.",
    "Checking for updates": "Kontrola aktualizací",
    "Connecting to server... ({seconds}s)": "Připojování k serveru... ({seconds} s)",
    "Cancel": "Zrušit",
    "Cannot check for updates — timed out.": "Nelze zkontrolovat — vypršel časový limit.",
    "Close": "Zavřít",
    "BTerminal is up to date. No new updates.": "BTerminal je aktuální. Žádné nové aktualizace.",
    "Cannot check for updates.": "Nelze zkontrolovat aktualizace.",
    "New BTerminal version": "Nová verze BTerminalu",
    "A new version of BTerminal is available": "Je k dispozici nová verze BTerminalu",
    "Show errata": "Zobrazit errata",
    "Not now": "Teď ne",
    "Update and restart": "Aktualizovat a restartovat",
    "BTerminal errata": "Errata BTerminalu",
    "No errata entries.": "Žádné záznamy errata.",
    "BTerminal update": "Aktualizace BTerminalu",
    "Update in progress…": "Probíhá aktualizace…",
    (
        "The new version of BTerminal could not be installed.\n\n"
        "The previous version was restored automatically — "
        "BTerminal continues to work normally."
    ): (
        "Novou verzi BTerminalu se nepodařilo nainstalovat.\n\n"
        "Předchozí verze byla automaticky obnovena — "
        "BTerminal nadále funguje normálně."
    ),
    (
        "Installation failed and no previous version is available "
        "to restore.\n\nDetails:\n{details}"
    ): (
        "Instalace selhala a není k dispozici žádná předchozí verze "
        "k obnovení.\n\nPodrobnosti:\n{details}"
    ),
    "New local tab": "Nová místní karta",
    "New SSH session…": "Nová SSH relace…",
    "New Claude Code session…": "Nová relace Claude Code…",
    "Options…": "Možnosti…",
    "Quit": "Ukončit",
    "File": "Soubor",
    "Toggle sidebar (Ctrl+B)": "Přepnout boční panel (Ctrl+B)",
    "Toggle Git panel (Ctrl+G)": "Přepnout panel Git (Ctrl+G)",
    "Toggle theme ☀/🌙": "Přepnout motiv ☀/🌙",
    "Sessions": "Relace",
    "Ctx": "Ctx",
    "Consult": "Consult",
    "Tasks": "Úkoly",
    "Plugins": "Pluginy",
    "View": "Zobrazení",
    "Check for updates": "Zkontrolovat aktualizace",
    "Errata…": "Errata…",
    "Tools": "Nástroje",
    (
        "The file ~/.config/bterminal/options.json was corrupted — "
        "default settings have been restored.\n\n"
        "Cause: {exc_type}: {exc}"
    ): (
        "Soubor ~/.config/bterminal/options.json byl poškozen — "
        "byly obnoveny výchozí nastavení.\n\n"
        "Příčina: {exc_type}: {exc}"
    ),
    (
        "Project context has not been collected yet. "
        "Gather context as you work and save important findings: "
        "ctx set <project> <key> <value>"
    ): (
        "Kontext projektu ještě nebyl shromážděn. "
        "Sbírejte kontext při práci a ukládejte důležité poznatky: "
        "ctx set <project> <key> <value>"
    ),
    "Appearance": "Vzhled",
    "Theme:": "Motiv:",
    "Dark (Mocha)": "Tmavý (Mocha)",
    "Light (Latte)": "Světlý (Latte)",
    "Terminal font:": "Písmo terminálu:",
    "Terminal": "Terminál",
    "Default shell:": "Výchozí shell:",
    "default ({shell})": "výchozí ({shell})",
    "General": "Obecné",
    "Check for updates at startup:": "Zkontrolovat aktualizace při spuštění:",
    "Save": "Uložit",
    "Working tree clean": "Pracovní strom čistý",
    "Hide sidebar (Ctrl+B)": "Skrýt boční panel (Ctrl+B)",
    "Show sidebar (Ctrl+B)": "Zobrazit boční panel (Ctrl+B)",
    "Toggle light/dark theme": "Přepnout světlý/tmavý motiv",
    "Show Git panel (Ctrl+G)": "Zobrazit panel Git (Ctrl+G)",
    "Memory": "Paměť",
    "Files": "Soubory",
    "Skills": "Skills",
    "{app} Sessions": "Relace {app}",
    "Language": "Jazyk",
    "Interface language:": "Jazyk rozhraní:",
    "Auto-detect": "Automaticky detekovat",
    "Restart BTerminal to apply language change.":
        "Pro použití změny jazyka restartujte BTerminal.",
    "Tell the AI agent which language I speak":
        "Sdělit AI agentovi, jakým jazykem mluvím",
    PLURALS_KEY: {
        ("{n} file", "{n} files"): ("{n} soubor", "{n} soubory", "{n} souborů"),
    },
}


ZH = {
    "BTerminal — License Agreement ({context})": "BTerminal — 许可协议 ({context})",
    "Decline and exit": "拒绝并退出",
    "Accept": "接受",
    "Please read the license agreement below.": "请阅读下方的许可协议。",
    "You must accept these terms to use BTerminal.": "您必须接受这些条款才能使用 BTerminal。",
    "I have read and accept the license terms.": "我已阅读并接受许可条款。",
    "First run": "首次运行",
    "Update": "更新",
    "No repository": "无仓库",
    "Cannot check for updates — repository directory not found.":
        "无法检查更新 — 未找到仓库目录。",
    "Checking for updates": "正在检查更新",
    "Connecting to server... ({seconds}s)": "正在连接到服务器…({seconds}秒)",
    "Cancel": "取消",
    "Cannot check for updates — timed out.": "无法检查 — 超时。",
    "Close": "关闭",
    "BTerminal is up to date. No new updates.": "BTerminal 已是最新。没有新的更新。",
    "Cannot check for updates.": "无法检查更新。",
    "New BTerminal version": "新版 BTerminal",
    "A new version of BTerminal is available": "BTerminal 有新版本可用",
    "Show errata": "显示更新日志",
    "Not now": "暂不更新",
    "Update and restart": "更新并重启",
    "BTerminal errata": "BTerminal 更新日志",
    "No errata entries.": "无更新日志条目。",
    "BTerminal update": "BTerminal 更新",
    "Update in progress…": "更新中…",
    (
        "The new version of BTerminal could not be installed.\n\n"
        "The previous version was restored automatically — "
        "BTerminal continues to work normally."
    ): (
        "无法安装 BTerminal 新版本。\n\n"
        "已自动恢复至之前的版本 — "
        "BTerminal 仍可正常运行。"
    ),
    (
        "Installation failed and no previous version is available "
        "to restore.\n\nDetails:\n{details}"
    ): (
        "安装失败且没有可恢复的旧版本。\n\n"
        "详细信息:\n{details}"
    ),
    "New local tab": "新建本地标签页",
    "New SSH session…": "新建 SSH 会话…",
    "New Claude Code session…": "新建 Claude Code 会话…",
    "Options…": "选项…",
    "Quit": "退出",
    "File": "文件",
    "Toggle sidebar (Ctrl+B)": "切换侧边栏 (Ctrl+B)",
    "Toggle Git panel (Ctrl+G)": "切换 Git 面板 (Ctrl+G)",
    "Toggle theme ☀/🌙": "切换主题 ☀/🌙",
    "Sessions": "会话",
    "Ctx": "Ctx",
    "Consult": "Consult",
    "Tasks": "任务",
    "Plugins": "插件",
    "View": "视图",
    "Check for updates": "检查更新",
    "Errata…": "更新日志…",
    "Tools": "工具",
    (
        "The file ~/.config/bterminal/options.json was corrupted — "
        "default settings have been restored.\n\n"
        "Cause: {exc_type}: {exc}"
    ): (
        "文件 ~/.config/bterminal/options.json 已损坏 — "
        "已恢复默认设置。\n\n"
        "原因: {exc_type}: {exc}"
    ),
    (
        "Project context has not been collected yet. "
        "Gather context as you work and save important findings: "
        "ctx set <project> <key> <value>"
    ): (
        "尚未收集项目上下文。"
        "在工作时收集上下文并保存重要发现:"
        "ctx set <project> <key> <value>"
    ),
    "Appearance": "外观",
    "Theme:": "主题:",
    "Dark (Mocha)": "深色 (Mocha)",
    "Light (Latte)": "浅色 (Latte)",
    "Terminal font:": "终端字体:",
    "Terminal": "终端",
    "Default shell:": "默认 shell:",
    "default ({shell})": "默认 ({shell})",
    "General": "通用",
    "Check for updates at startup:": "启动时检查更新:",
    "Save": "保存",
    "Working tree clean": "工作树干净",
    "Hide sidebar (Ctrl+B)": "隐藏侧边栏 (Ctrl+B)",
    "Show sidebar (Ctrl+B)": "显示侧边栏 (Ctrl+B)",
    "Toggle light/dark theme": "切换浅色/深色主题",
    "Show Git panel (Ctrl+G)": "显示 Git 面板 (Ctrl+G)",
    "Memory": "记忆",
    "Files": "文件",
    "Skills": "Skills",
    "{app} Sessions": "{app} 会话",
    "Language": "语言",
    "Interface language:": "界面语言:",
    "Auto-detect": "自动检测",
    "Restart BTerminal to apply language change.":
        "重启 BTerminal 以应用语言更改。",
    "Tell the AI agent which language I speak":
        "告知 AI 代理我使用的语言",
    PLURALS_KEY: {
        ("{n} file", "{n} files"): ("{n} 个文件",),
    },
}


JA = {
    "BTerminal — License Agreement ({context})": "BTerminal — ライセンス契約 ({context})",
    "Decline and exit": "拒否して終了",
    "Accept": "同意する",
    "Please read the license agreement below.": "下記のライセンス契約をお読みください。",
    "You must accept these terms to use BTerminal.": "BTerminal を使用するにはこれらの条項に同意する必要があります。",
    "I have read and accept the license terms.": "ライセンス条項を読み、同意します。",
    "First run": "初回起動",
    "Update": "アップデート",
    "No repository": "リポジトリなし",
    "Cannot check for updates — repository directory not found.":
        "アップデートを確認できません — リポジトリディレクトリが見つかりません。",
    "Checking for updates": "アップデートを確認中",
    "Connecting to server... ({seconds}s)": "サーバーに接続中…({seconds}秒)",
    "Cancel": "キャンセル",
    "Cannot check for updates — timed out.": "確認できません — タイムアウトしました。",
    "Close": "閉じる",
    "BTerminal is up to date. No new updates.": "BTerminal は最新です。新しいアップデートはありません。",
    "Cannot check for updates.": "アップデートを確認できません。",
    "New BTerminal version": "新しい BTerminal バージョン",
    "A new version of BTerminal is available": "BTerminal の新バージョンが利用可能です",
    "Show errata": "更新履歴を表示",
    "Not now": "後で",
    "Update and restart": "アップデートして再起動",
    "BTerminal errata": "BTerminal 更新履歴",
    "No errata entries.": "更新履歴はありません。",
    "BTerminal update": "BTerminal アップデート",
    "Update in progress…": "アップデート中…",
    (
        "The new version of BTerminal could not be installed.\n\n"
        "The previous version was restored automatically — "
        "BTerminal continues to work normally."
    ): (
        "BTerminal の新しいバージョンをインストールできませんでした。\n\n"
        "以前のバージョンが自動的に復元されました — "
        "BTerminal は通常通り動作します。"
    ),
    (
        "Installation failed and no previous version is available "
        "to restore.\n\nDetails:\n{details}"
    ): (
        "インストールに失敗し、復元可能な以前のバージョンがありません。\n\n"
        "詳細:\n{details}"
    ),
    "New local tab": "新しいローカルタブ",
    "New SSH session…": "新しい SSH セッション…",
    "New Claude Code session…": "新しい Claude Code セッション…",
    "Options…": "オプション…",
    "Quit": "終了",
    "File": "ファイル",
    "Toggle sidebar (Ctrl+B)": "サイドバーを切替 (Ctrl+B)",
    "Toggle Git panel (Ctrl+G)": "Git パネルを切替 (Ctrl+G)",
    "Toggle theme ☀/🌙": "テーマを切替 ☀/🌙",
    "Sessions": "セッション",
    "Ctx": "Ctx",
    "Consult": "Consult",
    "Tasks": "タスク",
    "Plugins": "プラグイン",
    "View": "表示",
    "Check for updates": "アップデートを確認",
    "Errata…": "更新履歴…",
    "Tools": "ツール",
    (
        "The file ~/.config/bterminal/options.json was corrupted — "
        "default settings have been restored.\n\n"
        "Cause: {exc_type}: {exc}"
    ): (
        "ファイル ~/.config/bterminal/options.json が破損していました — "
        "デフォルト設定が復元されました。\n\n"
        "原因: {exc_type}: {exc}"
    ),
    (
        "Project context has not been collected yet. "
        "Gather context as you work and save important findings: "
        "ctx set <project> <key> <value>"
    ): (
        "プロジェクトコンテキストはまだ収集されていません。"
        "作業しながらコンテキストを収集し、重要な発見を保存してください: "
        "ctx set <project> <key> <value>"
    ),
    "Appearance": "外観",
    "Theme:": "テーマ:",
    "Dark (Mocha)": "ダーク (Mocha)",
    "Light (Latte)": "ライト (Latte)",
    "Terminal font:": "ターミナルフォント:",
    "Terminal": "ターミナル",
    "Default shell:": "デフォルトシェル:",
    "default ({shell})": "デフォルト ({shell})",
    "General": "一般",
    "Check for updates at startup:": "起動時にアップデートを確認:",
    "Save": "保存",
    "Working tree clean": "ワーキングツリーは綺麗",
    "Hide sidebar (Ctrl+B)": "サイドバーを隠す (Ctrl+B)",
    "Show sidebar (Ctrl+B)": "サイドバーを表示 (Ctrl+B)",
    "Toggle light/dark theme": "ライト/ダークテーマを切替",
    "Show Git panel (Ctrl+G)": "Git パネルを表示 (Ctrl+G)",
    "Memory": "メモリ",
    "Files": "ファイル",
    "Skills": "Skills",
    "{app} Sessions": "{app} セッション",
    "Language": "言語",
    "Interface language:": "インターフェース言語:",
    "Auto-detect": "自動検出",
    "Restart BTerminal to apply language change.":
        "言語の変更を適用するには BTerminal を再起動してください。",
    "Tell the AI agent which language I speak":
        "AI エージェントに使用言語を伝える",
    PLURALS_KEY: {
        ("{n} file", "{n} files"): ("{n} ファイル",),
    },
}


KO = {
    "BTerminal — License Agreement ({context})": "BTerminal — 라이선스 계약 ({context})",
    "Decline and exit": "거부하고 종료",
    "Accept": "동의함",
    "Please read the license agreement below.": "아래 라이선스 계약을 읽어 주십시오.",
    "You must accept these terms to use BTerminal.": "BTerminal을 사용하려면 이 약관에 동의해야 합니다.",
    "I have read and accept the license terms.": "라이선스 약관을 읽었으며 동의합니다.",
    "First run": "첫 실행",
    "Update": "업데이트",
    "No repository": "저장소 없음",
    "Cannot check for updates — repository directory not found.":
        "업데이트를 확인할 수 없습니다 — 저장소 디렉터리를 찾을 수 없습니다.",
    "Checking for updates": "업데이트 확인 중",
    "Connecting to server... ({seconds}s)": "서버에 연결 중… ({seconds}초)",
    "Cancel": "취소",
    "Cannot check for updates — timed out.": "확인할 수 없습니다 — 시간 초과.",
    "Close": "닫기",
    "BTerminal is up to date. No new updates.": "BTerminal은 최신 상태입니다. 새 업데이트가 없습니다.",
    "Cannot check for updates.": "업데이트를 확인할 수 없습니다.",
    "New BTerminal version": "새 BTerminal 버전",
    "A new version of BTerminal is available": "새 버전의 BTerminal이 사용 가능합니다",
    "Show errata": "업데이트 내역 보기",
    "Not now": "나중에",
    "Update and restart": "업데이트 후 재시작",
    "BTerminal errata": "BTerminal 업데이트 내역",
    "No errata entries.": "업데이트 내역이 없습니다.",
    "BTerminal update": "BTerminal 업데이트",
    "Update in progress…": "업데이트 진행 중…",
    (
        "The new version of BTerminal could not be installed.\n\n"
        "The previous version was restored automatically — "
        "BTerminal continues to work normally."
    ): (
        "BTerminal 새 버전을 설치할 수 없습니다.\n\n"
        "이전 버전이 자동으로 복원되었습니다 — "
        "BTerminal은 정상적으로 작동합니다."
    ),
    (
        "Installation failed and no previous version is available "
        "to restore.\n\nDetails:\n{details}"
    ): (
        "설치에 실패했으며 복원할 수 있는 이전 버전이 없습니다.\n\n"
        "세부 정보:\n{details}"
    ),
    "New local tab": "새 로컬 탭",
    "New SSH session…": "새 SSH 세션…",
    "New Claude Code session…": "새 Claude Code 세션…",
    "Options…": "옵션…",
    "Quit": "종료",
    "File": "파일",
    "Toggle sidebar (Ctrl+B)": "사이드바 전환 (Ctrl+B)",
    "Toggle Git panel (Ctrl+G)": "Git 패널 전환 (Ctrl+G)",
    "Toggle theme ☀/🌙": "테마 전환 ☀/🌙",
    "Sessions": "세션",
    "Ctx": "Ctx",
    "Consult": "Consult",
    "Tasks": "작업",
    "Plugins": "플러그인",
    "View": "보기",
    "Check for updates": "업데이트 확인",
    "Errata…": "업데이트 내역…",
    "Tools": "도구",
    (
        "The file ~/.config/bterminal/options.json was corrupted — "
        "default settings have been restored.\n\n"
        "Cause: {exc_type}: {exc}"
    ): (
        "~/.config/bterminal/options.json 파일이 손상되었습니다 — "
        "기본 설정이 복원되었습니다.\n\n"
        "원인: {exc_type}: {exc}"
    ),
    (
        "Project context has not been collected yet. "
        "Gather context as you work and save important findings: "
        "ctx set <project> <key> <value>"
    ): (
        "프로젝트 컨텍스트가 아직 수집되지 않았습니다. "
        "작업하면서 컨텍스트를 수집하고 중요한 발견을 저장하세요: "
        "ctx set <project> <key> <value>"
    ),
    "Appearance": "모양",
    "Theme:": "테마:",
    "Dark (Mocha)": "어두움 (Mocha)",
    "Light (Latte)": "밝음 (Latte)",
    "Terminal font:": "터미널 글꼴:",
    "Terminal": "터미널",
    "Default shell:": "기본 셸:",
    "default ({shell})": "기본 ({shell})",
    "General": "일반",
    "Check for updates at startup:": "시작 시 업데이트 확인:",
    "Save": "저장",
    "Working tree clean": "작업 트리 깨끗함",
    "Hide sidebar (Ctrl+B)": "사이드바 숨기기 (Ctrl+B)",
    "Show sidebar (Ctrl+B)": "사이드바 표시 (Ctrl+B)",
    "Toggle light/dark theme": "밝은/어두운 테마 전환",
    "Show Git panel (Ctrl+G)": "Git 패널 표시 (Ctrl+G)",
    "Memory": "메모리",
    "Files": "파일",
    "Skills": "Skills",
    "{app} Sessions": "{app} 세션",
    "Language": "언어",
    "Interface language:": "인터페이스 언어:",
    "Auto-detect": "자동 감지",
    "Restart BTerminal to apply language change.":
        "언어 변경을 적용하려면 BTerminal을 재시작하세요.",
    "Tell the AI agent which language I speak":
        "AI 에이전트에게 사용 언어 알리기",
    PLURALS_KEY: {
        ("{n} file", "{n} files"): ("{n}개 파일",),
    },
}


# Map: locale short code -> translation dict.
# Order matters only for human readability of the script.
TRANSLATIONS = {
    "pl": PL,
    "de": DE,
    "es": ES,
    "fr": FR,
    "it": IT,
    "pt": PT,
    "ru": RU,
    "uk": UK,
    "cs": CS,
    "zh": ZH,
    "ja": JA,
    "ko": KO,
}


# ─── Filling logic ──────────────────────────────────────────────────────────


def fill_one(short_code: str, table: dict) -> tuple[int, list, list]:
    po_path = f"{LOCALE_DIR}/{short_code}/LC_MESSAGES/bterminal.po"
    with open(po_path, "rb") as fh:
        catalog = read_po(fh)

    plurals = table.get(PLURALS_KEY, {})
    misses_singular: list = []
    misses_plural: list = []

    for message in catalog:
        if not message.id:
            continue  # header
        if isinstance(message.id, tuple):
            tr = plurals.get(message.id)
            if tr is None:
                misses_plural.append(message.id)
                continue
            message.string = tr
        else:
            tr = table.get(message.id)
            if tr is None:
                misses_singular.append(message.id)
                continue
            message.string = tr

    with open(po_path, "wb") as fh:
        write_po(fh, catalog, width=0)

    total = sum(1 for m in catalog if m.id)
    filled = total - len(misses_singular) - len(misses_plural)
    return filled, misses_singular, misses_plural


def main() -> int:
    overall_ok = True
    for short, table in TRANSLATIONS.items():
        try:
            filled, miss_s, miss_p = fill_one(short, table)
        except FileNotFoundError as exc:
            print(f"[{short}] SKIP — {exc}")
            continue

        total = filled + len(miss_s) + len(miss_p)
        status = "OK" if not (miss_s or miss_p) else "INCOMPLETE"
        print(f"[{short}] {status}: {filled}/{total} filled")
        if miss_s:
            print(f"  untranslated singular ({len(miss_s)}):")
            for m in miss_s[:5]:
                print(f"    - {m!r}")
            overall_ok = False
        if miss_p:
            print(f"  untranslated plural ({len(miss_p)}):")
            for m in miss_p:
                print(f"    - {m!r}")
            overall_ok = False
    return 0 if overall_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
