# Frontend structure

The web UI uses Flask/Jinja, CSS, and native JavaScript modules. It runs directly from source and in Docker without a frontend build step or additional dependencies.

## HTML and styles

`templates/index.html` is the document shell. The files in `templates/partials/` contain the header, login/QR/loading screens, device list, scan form, device panel, and logout dialog. Data-dependent markup stays in the JavaScript renderer for its feature.

Styles live in `static/css/`. Load them in the order listed in the main template: base, header, auth, devices, panel, setup, network, then utilities. Base styles define the light/dark theme and shared controls. Utilities load last so `.hidden` keeps its precedence. Keep fixed styles in CSS classes. Only calculated popover coordinates are set from JavaScript.

## JavaScript ownership

`static/js/app.js` composes the application. It reads the inert JSON configuration, registers panel pages, connects feature callbacks, binds shared copy/key visibility/keyboard handling, and starts loading the current session. Initialization runs once.

| Module in `static/js/features/` | Owns |
| --- | --- |
| `shell.js` | Top-level views and the error banner |
| `auth.js` | QR login, login polling, and logout confirmation |
| `devices.js` | Device loading and refresh, search indexing, table rendering, sorting, gateway grouping, row-click routing, selection highlighting, CSV export, and cache-age updates |
| `filters.js` | Home filters, saved exclusions, selection state, and exclusion/restoration requests |
| `notices.js` | Cloud and LAN change notices, scan summaries, and dismissal preferences |
| `panel.js` | Panel navigation, history, focus, scroll, and disclosure state |
| `details.js` | Device fields, data points, and masked raw JSON |
| `setup.js` | Integration tabs, setup values, snippets, and inline guidance |
| `network.js` | LAN results, scan/check requests, scan polling, and the Local network page |
| `header.js` | App name/logo home action, responsive action placement, and the More actions menu |

Cross-feature data lives in `shared/state.js` as `appState`. Features keep their timers and private caches in their own modules. Modules in `shared/` provide the API helper, device/gateway/connection derivations, constants, HTML/field renderers, formatting, icons, and clipboard support.

Feature controllers import shared modules directly. Actions from other controllers are passed to their `init…` function by `app.js`, avoiding circular feature imports. Importing a feature or shared module does not bind DOM events or start requests. The application entrypoint starts the UI. The table routes selection interactions to the filters controller, which updates selection state and asks the table to synchronize checkboxes and row classes.

To add a panel page, provide its renderer and register its body element, title, back label, and optional initial focus selector with `registerPanelPage` in `app.js`. Existing panel navigation and Back handling then apply to it.

## URLs and configuration

Stylesheet and entrypoint URLs are relative to the document. JavaScript imports are relative to their module. The API helper keeps requests relative to the document by stripping leading slashes, preserving Home Assistant ingress prefixes. Avoid root-relative asset or API URLs.

Gateway categories come from Flask through the `app-config` JSON block. Static JavaScript is served unchanged and contains no Jinja expressions. Static assets use the same direct-access and ingress authentication rules as the rest of the app. Docker includes both the templates and static directories.

## Verification

Run the existing suite with `.venv/bin/python -m pytest`. Browser tests require Playwright and a supported browser. They exercise the real app with fake device data, including startup through a simulated ingress prefix. Tests that inspect internal state import the owning module instead of relying on browser globals. Screenshot generation remains available through the existing tools described in [Tools](../tools/README.md).
