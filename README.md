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
| `domain_parser.py` | OSINT domain & subdomain reconnaissance |
| `messenger.py` | Alert dispatcher service (+ `dispatch_alert`) |
| `telegram_notifier.py` | Telegram notification handler |
| `remote_orchestrator.py` | **Hybrid Outbound TLS 1.3 + Challenge-Authenticated orchestrator** |
| `bip39_validator.py` | Cryptographic BIP-39 seed phrase validator |
| `bip39_english.txt` | Official BIP-39 English wordlist |

---

## Configuration Files

- `runtime.json` (host / port for remote_orchestrator – default port now 8443 for TLS)
- `strategy.json`
- `tracker.json`
- `transport.json`

---

## remote_orchestrator (Hybrid Secure Version)

Standalone unit that maintains a persistent **outbound TLS 1.3** connection to a controller defined in `runtime.json`.

### Security features
- TLS 1.3 only + mutual HMAC challenge-response with 30s anti-replay window
- COMMAND_MAP whitelist → executes only `harvester.py`, `domain_parser.py`, `db_manager.py`
- `shell=False` + `safe_args` filtering (alnum + `-` `_`)
- Length-prefixed framing
- Side-effects (`db_manager.log_session_event`, `messenger.dispatch_alert`) run **after** the response is sent

### Setup
1. Place CA certificate:
   ```bash
   mkdir -p certs
   openssl req -x509 -newkey rsa:4096 -keyout certs/ca.key -out certs/ca.crt -days 365 -nodes -subj "/CN=DEMON-CORE-Orchestrator"
   ```
2. Set challenge secret (never hard-code):
   ```bash
   export DEMON_CORE_CHALLENGE_SECRET="$(openssl rand -hex 32)"
   ```
3. Edit `runtime.json` with the controller host/port (default 8443).
4. Run:
   ```bash
   python remote_orchestrator.py
   ```

### Allowed commands (from controller)
- `START_HARVESTER`
- `RUN_DOMAIN_RECON`
- `GET_DB_STATUS`

---

## Project Status

Most core modules have been uploaded and the project structure is now largely complete.

Created and maintained via connected GitHub tools.
