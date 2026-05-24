import requests
from core.config import load_config
from core.logger import setup_logger

logger = setup_logger("api_client")


def _headers():
    return {"apikey": load_config()["api"]["key"]}


def _base():
    return load_config()["api"]["url"]


def get_group_participants(instance: str, group_jid: str) -> list:
    """Fetch all participants of a group."""
    url = f"{_base()}/group/participants/{instance}"
    try:
        resp = requests.get(url, headers=_headers(), params={"groupJid": group_jid}, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        return data.get("participants", [])
    except Exception as e:
        logger.error(f"Failed to fetch participants for {group_jid}: {e}")
        return []


def get_all_groups(instance: str) -> list:
    """Fetch all groups the instance is part of."""
    url = f"{_base()}/group/fetchAllGroups/{instance}"
    try:
        resp = requests.get(url, headers=_headers(), params={"getParticipants": "false"}, timeout=30)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        logger.error(f"Failed to fetch groups for instance {instance}: {e}")
        return []


def add_participant(instance: str, group_jid: str, phone: str) -> bool:
    """Add one participant to a group. Returns True on success."""
    url = f"{_base()}/group/updateParticipant/{instance}"
    payload = {
        "groupJid": group_jid,
        "action": "add",
        "participants": [f"{phone}@s.whatsapp.net"]
    }
    try:
        resp = requests.post(url, headers=_headers(), json=payload, timeout=30)
        if resp.status_code == 200:
            return True
        logger.warning(f"Add failed for {phone} via {instance}: {resp.status_code} {resp.text}")
        return False
    except Exception as e:
        logger.error(f"Exception adding {phone} via {instance}: {e}")
        return False


def set_webhook(instance: str, webhook_url: str) -> bool:
    """Configure webhook URL for an instance."""
    url = f"{_base()}/webhook/set/{instance}"
    payload = {
        "url": webhook_url,
        "webhook_by_events": False,
        "webhook_base64": False,
        "events": [
            "GROUP_PARTICIPANTS_UPDATE",
            "CONNECTION_UPDATE"
        ]
    }
    try:
        resp = requests.post(url, headers=_headers(), json=payload, timeout=15)
        return resp.status_code == 200
    except Exception as e:
        logger.error(f"Failed to set webhook for {instance}: {e}")
        return False
