# WhatsApp Group Manager

Automated WhatsApp group member scraper and adder using Evolution API.

## System Structure

```
whatsapp-manager/
├── main.py              ← Entry point, CLI commands
├── scraper.py           ← Scrapes members from groups
├── adder.py             ← Adds members to your group
├── webhook.py           ← Listens for real-time events (join/leave/remove)
├── scheduler.py         ← Runs scraper daily + adder every 20 min
├── config.json          ← All configuration (edit this)
├── requirements.txt     ← Python dependencies
│
├── core/
│   ├── __init__.py
│   ├── config.py        ← Config loader
│   ├── database.py      ← DB connection manager
│   ├── logger.py        ← Logging setup
│   └── api_client.py    ← Evolution API wrapper
│
├── db/
│   └── schema.sql       ← All 8 database tables
│
└── logs/
    └── system.log       ← Auto-created on first run
```

## Setup Steps

### 1. Install dependencies
```bash
pip install -r requirements.txt
```

### 2. Configure
Edit `config.json`:
- Update `my_groups` with your actual group JID
- Update `scrape_groups` with groups to scrape from
- Update `database.password` with your postgres password
- Set instance names to match what you created in Evolution API

### 3. Create database tables
```bash
python main.py --setup-db
```

### 4. Register webhooks with Evolution API
```bash
python main.py --setup-webhooks
```

### 5. Run a manual scrape first
```bash
python main.py --scrape
```

### 6. Check status
```bash
python main.py --status
```

### 7. Start the full system (webhook + scheduler)
```bash
python main.py
```

## CLI Commands

| Command | What it does |
|---|---|
| `python main.py` | Start everything (scheduler + webhook server) |
| `python main.py --setup-db` | Create all database tables |
| `python main.py --setup-webhooks` | Register webhook URL with all instances |
| `python main.py --scrape` | Run scraper once manually |
| `python main.py --add` | Run one add cycle manually |
| `python main.py --status` | Print system status |

## How Instance Rotation Works

- 8 adder instances, each adds 30 members then enters 12-hour cooldown
- System automatically picks the instance with fewest adds done
- After cooldown expires, instance resets to 0 and becomes available again
- Total capacity: 8 × 30 = 240 members per 12-hour window

## Important Notes

- Use dedicated/secondary numbers for adder instances — not your personal number
- The webhook server needs a public URL (use ngrok for local testing)
- Safe hours: system only adds members between 9am and 10pm
- Never add more than 3 members per hour per group to avoid bans
