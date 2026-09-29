"""Saved chats for the web app: kept in the data folder, per user, and nowhere else.

This is the user's own history, separate from the question log that feeds tuning and training
exports (``questions`` table). Saving a chat here never means consent to train: exports read only
the question log, and that log obeys the consent rules. Private chats are never sent here.
"""

from __future__ import annotations

import json
import re
import time
from typing import Any

from tempo.store import Store

ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{8,64}$")
MAX_CHAT_BYTES = 12 * 1024 * 1024  # a chat with a few images still fits
MAX_TITLE = 120
MAX_SEARCH_TEXT = 200_000


class ChatError(ValueError):
    """The chat was refused; the message says why and is safe to show."""


def _text_of(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(
            str(part.get("text", ""))
            for part in content
            if isinstance(part, dict) and "text" in part
        )
    return ""


def _search_text(title: str, messages: list[dict[str, Any]]) -> str:
    parts = [title]
    for message in messages:
        parts.append(_text_of(message.get("content")))
        for attachment in message.get("attachments") or []:
            if isinstance(attachment, dict):
                parts.append(str(attachment.get("name", "")))
    return "\n".join(parts)[:MAX_SEARCH_TEXT].lower()


def clean_title(title: Any, fallback: str = "New chat") -> str:
    text = " ".join(str(title or "").split())[:MAX_TITLE]
    return text or fallback


def save(store: Store, user_id: str, chat_id: str, chat: dict[str, Any]) -> dict[str, Any]:
    """Create or replace a chat's messages. The title is only taken from the client until the
    user renames the chat; pinned state is never changed here."""
    if not ID_PATTERN.match(chat_id):
        raise ChatError("A chat id is 8 to 64 letters, digits, dashes or underscores.")
    messages = chat.get("messages")
    if not isinstance(messages, list) or not all(isinstance(m, dict) for m in messages):
        raise ChatError("A chat needs a list of messages.")
    body = json.dumps({**chat, "id": chat_id}, ensure_ascii=False)
    if len(body.encode("utf-8")) > MAX_CHAT_BYTES:
        raise ChatError(
            "This chat is too large to save (over 12 MB). Remove large images or start a new chat."
        )
    now = time.time()
    title = clean_title(chat.get("title"))
    row = store.query(
        "SELECT title_edited, created_at FROM chats WHERE user_id=? AND id=?", (user_id, chat_id)
    )
    if row:
        if row[0]["title_edited"]:
            title = store.query(
                "SELECT title FROM chats WHERE user_id=? AND id=?", (user_id, chat_id)
            )[0]["title"]
        store.execute(
            "UPDATE chats SET title=?, updated_at=?, body=?, search_text=? "
            "WHERE user_id=? AND id=?",
            (title, now, body, _search_text(title, messages), user_id, chat_id),
        )
        created = row[0]["created_at"]
    else:
        created = now
        store.execute(
            "INSERT INTO chats (id, user_id, title, created_at, updated_at, body, search_text) "
            "VALUES (?,?,?,?,?,?,?)",
            (chat_id, user_id, title, now, now, body, _search_text(title, messages)),
        )
    return {"id": chat_id, "title": title, "created_at": created, "updated_at": now}


def get(store: Store, user_id: str, chat_id: str) -> dict[str, Any] | None:
    rows = store.query(
        "SELECT id, title, pinned, created_at, updated_at, body FROM chats "
        "WHERE user_id=? AND id=?",
        (user_id, chat_id),
    )
    if not rows:
        return None
    row = rows[0]
    chat = json.loads(row["body"])
    chat.update(
        title=row["title"],
        pinned=bool(row["pinned"]),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )
    return chat


def _preview(messages: list[dict[str, Any]]) -> str:
    for message in reversed(messages):
        if message.get("role") == "assistant" and _text_of(message.get("content")).strip():
            return " ".join(_text_of(message["content"]).split())[:140]
    for message in messages:
        text = _text_of(message.get("content")).strip()
        if text:
            return " ".join(text.split())[:140]
    return ""


def listing(store: Store, user_id: str, query: str = "", limit: int = 500) -> list[dict[str, Any]]:
    """Chat summaries, pinned first, then newest. A query matches titles and message text."""
    sql = "SELECT id, title, pinned, created_at, updated_at, search_text FROM chats WHERE user_id=?"
    params: list[Any] = [user_id]
    needle = " ".join(query.lower().split())
    if needle:
        sql += " AND search_text LIKE ? ESCAPE '\\'"
        escaped = needle.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        params.append(f"%{escaped}%")
    sql += " ORDER BY pinned DESC, updated_at DESC LIMIT ?"
    params.append(max(1, min(limit, 2000)))
    out = []
    for row in store.query(sql, params):
        item = {
            "id": row["id"],
            "title": row["title"],
            "pinned": bool(row["pinned"]),
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }
        if needle:
            text = row["search_text"]
            at = text.find(needle)
            item["snippet"] = " ".join(text[max(0, at - 40) : at + 100].split())
        out.append(item)
    return out


def rename(store: Store, user_id: str, chat_id: str, title: str) -> bool:
    cursor = store.execute(
        "UPDATE chats SET title=?, title_edited=1 WHERE user_id=? AND id=?",
        (clean_title(title), user_id, chat_id),
    )
    return cursor.rowcount > 0


def pin(store: Store, user_id: str, chat_id: str, pinned: bool) -> bool:
    cursor = store.execute(
        "UPDATE chats SET pinned=? WHERE user_id=? AND id=?", (int(pinned), user_id, chat_id)
    )
    return cursor.rowcount > 0


def delete(store: Store, user_id: str, chat_id: str | None = None) -> int:
    """Delete one chat, or every chat of the user when no id is given."""
    if chat_id is None:
        cursor = store.execute("DELETE FROM chats WHERE user_id=?", (user_id,))
    else:
        cursor = store.execute("DELETE FROM chats WHERE user_id=? AND id=?", (user_id, chat_id))
    return cursor.rowcount


# --- export ---------------------------------------------------------------------------


def _stamp(t: float | None) -> str:
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(t)) if t else ""


def to_markdown(chat: dict[str, Any]) -> str:
    lines = [f"# {chat.get('title') or 'Chat'}", ""]
    lines.append(f"_Exported from Tempo-server · {_stamp(chat.get('updated_at'))}_")
    for message in chat.get("messages", []):
        lines.append("")
        if message.get("role") == "user":
            lines += ["## You", ""]
        else:
            who = f"Tempo · {message['model']}" if message.get("model") else "Tempo"
            lines += [f"## {who}", ""]
        for attachment in message.get("attachments") or []:
            lines.append(f"_Attached: {attachment.get('name', 'file')}_")
        text = _text_of(message.get("content")).strip()
        if message.get("error") and not text:
            text = f"_No answer: {message['error']}_"
        lines.append(text)
        if message.get("role") != "user" and message.get("stages"):
            n = message["stages"]
            lines += ["", f"_{n} stage{'' if n == 1 else 's'}_"]
    return "\n".join(lines).rstrip() + "\n"


def to_json(chat: dict[str, Any]) -> str:
    return json.dumps(chat, ensure_ascii=False, indent=2)


def file_stem(chat: dict[str, Any]) -> str:
    stem = re.sub(r"[^\w\- ]+", "", chat.get("title") or "chat", flags=re.UNICODE).strip()
    return re.sub(r"\s+", "-", stem)[:60] or "chat"
