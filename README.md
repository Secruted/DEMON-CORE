# Satanic essence 🧠🕷️

**Modular Proxy-Powered Harvesting & Reconnaissance System**

---

## Core Modules

| File | Description |
|------|-------------|
| `orchestrator.py` | Main system coordinator & entry point |
| `harvester.py` | Mission execution engine |
| `router.py` | Target & path generator |
| `content_parser.py` | Hybrid JSON / HTML content analyzer |
| `db_manager.py` | Encrypted SQLite database manager (+ `log_session_event`) |
| `transport_manager.py` | Network layer with mobile mimicry & proxy support |
| `proxy_manager.py` | Advanced proxy health monitoring & rotation |
| `maggie.py` | Dead proxy collector |
| `shane.py` | Hospital (cooling) recovery system |
| `negan.py` | Proxy warehouse supplier |
| `negan_config.py` | Central configuration & paths |
| `statuses.py` | Unified status language across modules |
| `bip39_validator.py` | Cryptographic BIP-39 seed phrase validator |
| `bip39_english.txt` | Official BIP-39 English wordlist |
| `domain_parser.py` | OSINT domain & subdomain reconnaissance |
| `feeder.py` | Google Dork based target acquisition |
| `messenger.py` | Alert dispatcher service (+ `dispatch_alert`) |
| `telegram_notifier.py` | Telegram notification handler |
| `remote_orchestrator.py` | Persistent outbound TCP command client (new unit) |
| `elite_check.py` | Elite proxy quality checker |
| `check_sentinel.py` | Quick proxy health checker |
| `clear_cooling.py` | Hospital room cleaner |
| `final_bypass.py` | SSL verification bypass injector |
| `fix_ssl.py` | Alternative SSL patch utility |

---

## Configuration Files

- `runtime.json` (now includes `host` / `port` for remote_orchestrator)
- `strategy.json`
- `tracker.json`
- `transport.json`

---

## remote_orchestrator

Standalone unit that maintains a persistent non-blocking TCP connection to a controller defined in `runtime.json`.

- Reads `host` and `port` from `runtime.json`
- Executes newline-framed commands via `subprocess.run`
- Logs every session via `db_manager.log_session_event`
- Broadcasts alerts via `messenger.dispatch_alert`
- Auto-reconnects every 5 seconds on disconnect

Run:
```bash
python remote_orchestrator.py
```

---

## Project Status

Most core modules have been uploaded and the project structure is now largely complete.

Some additional data files (such as `config.json`, `targets.txt`, proxy lists, etc.) may still need to be added depending on usage.

---

Created and maintained via connected GitHub tools.
