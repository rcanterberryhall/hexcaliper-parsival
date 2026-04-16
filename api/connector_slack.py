"""
connector_slack.py — Slack data connector.

Fetches @mentions, direct messages, and active channel threads for all
connected workspaces using per-user OAuth tokens.  Falls back to a legacy
bot token if no user tokens are configured.

When ``config.PROJECTS`` or ``config.FOCUS_TOPICS`` are configured, channel
messages are pre-filtered by ``_relevance()`` before being turned into
``RawItem`` objects.  ``_relevance()`` checks (in priority order):

1. Slack ``<@uid>`` mention or user name/email text patterns → ``"user"``
2. Project keywords (manual + learned) → ``"project"``
3. Watch-topic keywords → ``"topic"``
4. Noise keywords (only reached if no positive match) → skip

The resulting ``hierarchy`` and ``project_tag`` values are stored in
``RawItem.metadata`` so the LLM prompt can use them as hints.

Each call to ``fetch()`` returns a deduplicated list of ``RawItem`` objects
covering the lookback window defined in ``config.LOOKBACK_HOURS``.
"""
import logging
import requests
from datetime import datetime, timedelta, timezone
from models import RawItem
import config
import db

log = logging.getLogger(__name__)

# Slack Web API base URL.
BASE = "https://slack.com/api"


def _get(token: str, endpoint: str, params: dict = None) -> dict:
    """
    Make an authenticated GET request to the Slack Web API.

    :param token: A Slack user or bot OAuth token (``xoxp-`` or ``xoxb-``).
    :type token: str
    :param endpoint: Slack API method name, e.g. ``"conversations.history"``.
    :type endpoint: str
    :param params: Optional query parameters to include in the request.
    :type params: dict
    :return: Parsed JSON response from the Slack API.
    :rtype: dict
    :raises RuntimeError: If the Slack API returns ``ok: false``.
    :raises requests.HTTPError: If the HTTP request fails.
    """
    r = requests.get(
        f"{BASE}/{endpoint}",
        headers={"Authorization": f"Bearer {token}"},
        params=params or {},
        timeout=15,
    )
    r.raise_for_status()
    data = r.json()
    if not data.get("ok"):
        raise RuntimeError(f"Slack error on {endpoint}: {data.get('error')}")
    return data


def _username(token: str, uid: str, cache: dict) -> str:
    """
    Resolve a Slack user ID to a human-readable display name.

    Results are stored in ``cache`` to avoid redundant API calls within a
    single fetch pass.

    :param token: A Slack OAuth token with ``users:read`` scope.
    :type token: str
    :param uid: Slack user ID to resolve, e.g. ``"U012AB3CD"``.
    :type uid: str
    :param cache: Mutable dict used as a local lookup cache.
    :type cache: dict
    :return: The user's real name, or the raw ``uid`` if resolution fails.
    :rtype: str
    """
    if uid in cache:
        return cache[uid]
    try:
        name = _get(token, "users.info", {"user": uid})["user"].get("real_name") or uid
    except Exception:
        name = uid
    cache[uid] = name
    return name


def _user_identifiers() -> list[str]:
    """
    Build a list of text patterns that identify the configured user in message bodies.

    Covers: Slack @uid (added separately), full name, full email, and the
    @username prefix form extracted from the email (e.g. "@john.smith" from
    "john.smith@company.com").
    """
    ids = []
    if config.USER_NAME:
        ids.append(config.USER_NAME.lower())
    if config.USER_EMAIL:
        email = config.USER_EMAIL.lower()
        ids.append(email)
        username = email.split("@")[0]
        if username:
            ids.append("@" + username)
    return ids


def _relevance(text: str, my_uid: str) -> tuple[bool, str, str | None]:
    """
    Determine whether a message is relevant to the configured user context.

    Checks in priority order:
    1. Slack @uid mention or text-form name/email → user
    2. Project keywords (manual + learned) → project
    3. Topic keywords → topic
    4. Noise keywords (learned irrelevant) → explicitly skip

    :return: ``(relevant, hierarchy, project_tag)``
    """
    lower = text.lower()

    # ── User-level: Slack mention or name/email in text ───────────────────────
    if f"<@{my_uid}>" in text:
        return True, "user", None
    for ident in _user_identifiers():
        if ident in lower:
            return True, "user", None

    # ── Project keywords ──────────────────────────────────────────────────────
    for p in config.PROJECTS:
        all_kw = list(p.get("keywords", [])) + list(p.get("learned_keywords", []))
        for kw in all_kw:
            if kw.lower() in lower:
                return True, "project", p["name"]

    # ── Topic keywords ────────────────────────────────────────────────────────
    for t in config.FOCUS_TOPICS:
        if t.lower() in lower:
            return True, "topic", None

    # ── Noise: explicitly irrelevant (only reached if no positive match) ──────
    for kw in config.NOISE_KEYWORDS:
        if kw.lower() in lower:
            return False, "noise", None

    return False, "general", None


def _fetch_for_token(token: str, cutoff_ts: float) -> list[RawItem]:
    """
    Fetch @mentions, DMs, and active channel threads for one user token.

    Three passes are made against the Slack API:

    1. ``search.messages`` to surface messages that mention the authenticated user.
    2. ``conversations.list`` (IM/MPIM types) to capture recent DM threads.
    3. ``conversations.list`` (channels) to capture channel activity since cutoff.
       When projects or topics are configured, up to 100 messages per channel are
       fetched and filtered through ``_relevance()``; only matching messages are
       included.  The resulting ``hierarchy`` and ``project_tag`` are stored in
       the item's metadata for use by the LLM prompt.

    :param token: Slack user OAuth token (``xoxp-``).
    :type token: str
    :param cutoff_ts: Unix timestamp representing the earliest message to include.
    :type cutoff_ts: float
    :return: Deduplicated list of raw items from this workspace.
    :rtype: list[RawItem]
    """
    items: list[RawItem] = []
    seen:  set[str]      = set()
    cache: dict          = {}

    # Identify whose token this is.
    try:
        auth   = _get(token, "auth.test")
        my_uid = auth.get("user_id", "")
        team   = auth.get("team", "")
    except Exception as e:
        log.error("auth.test failed: %s", e)
        return []

    log.info("%s: my_uid=%s, cutoff=%s", team, my_uid, datetime.fromtimestamp(cutoff_ts, tz=timezone.utc).isoformat())

    # ── 1. @mentions via search API ──────────────────────────────────────────
    # Mentions use a per-message item_id (stable across scans) so todo dedup
    # via ``todo_exists(item_id, description)`` works naturally.  We still
    # consult ``slack_seen_messages`` to skip LLM work on mentions surfaced
    # in an earlier scan.
    try:
        search_result = _get(token, "search.messages", {
            "query":    f"<@{my_uid}>",
            "count":    20,
            "sort":     "timestamp",
            "sort_dir": "desc",
        })
        matches = search_result.get("messages", {}).get("matches", [])
        log.info("%s: mentions search: %d total matches", team, len(matches))

        # Group mentions by channel so we can run one unseen-filter query per
        # channel instead of one per message.
        by_channel: dict[str, list[dict]] = {}
        for m in matches:
            ch_id = m.get("channel", {}).get("id", "")
            by_channel.setdefault(ch_id, []).append(m)

        for ch_id, msgs in by_channel.items():
            ts_candidates = [m["ts"] for m in msgs
                             if float(m.get("ts", 0)) >= cutoff_ts]
            unseen = db.slack_unseen_message_ts(team, ch_id, ts_candidates)
            if not unseen:
                continue
            new_ts_for_channel = []
            for m in msgs:
                if m["ts"] not in unseen:
                    continue
                mid = f"mention_{my_uid}_{m['ts']}"
                if mid in seen:
                    continue
                seen.add(mid)
                ts = float(m["ts"])
                ch = m.get("channel", {})
                items.append(RawItem(
                    source    = "slack",
                    item_id   = mid,
                    title     = f"[@mention] #{ch.get('name','?')} ({team}): {m.get('text','')[:80]}",
                    body      = m.get("text", "")[:3000],
                    url       = m.get("permalink", f"https://slack.com/app_redirect?channel={ch.get('id','')}"),
                    author    = _username(token, m.get("user", "?"), cache),
                    timestamp = datetime.fromtimestamp(ts, tz=timezone.utc).isoformat(),
                    metadata  = {"channel": ch.get("name", ""), "workspace": team, "type": "mention"},
                ))
                new_ts_for_channel.append(m["ts"])
            if new_ts_for_channel:
                db.slack_mark_messages_seen(team, ch_id, new_ts_for_channel)
    except Exception as e:
        log.error("%s: mentions: %s", team, e)

    # ── 2. Direct messages and group DMs ─────────────────────────────────────
    # One aggregate item per DM conversation.  item_id is the (team, channel)
    # pair so todo dedup keyed on ``(item_id, description)`` works across
    # scans; the body is rebuilt from ONLY the messages the user hasn't seen
    # yet, so the LLM isn't re-analysing the same thread after the user has
    # already acted on it (parsival#69).
    try:
        channels = _get(token, "conversations.list", {
            "types":            "im,mpim",
            "exclude_archived": True,
            "limit":            50,
        }).get("channels", [])
        log.info("%s: DM conversations found: %d", team, len(channels))

        for ch in channels:
            ch_id = ch["id"]
            try:
                msgs = _get(token, "conversations.history", {
                    "channel": ch_id,
                    "limit":   10,
                }).get("messages", [])
            except Exception:
                continue

            if not msgs:
                continue

            unseen = db.slack_unseen_message_ts(
                team, ch_id, [m["ts"] for m in msgs]
            )
            new_msgs = [m for m in msgs if m["ts"] in unseen]
            if not new_msgs:
                continue

            # Build a single RawItem per DM conversation from the unseen msgs.
            lines = []
            for msg in reversed(new_msgs):
                sender = _username(token, msg.get("user", "?"), cache)
                lines.append(f"[{sender}]: {msg.get('text', '')}")

            mid = f"dm_{team}_{ch_id}"
            if mid in seen:
                continue
            seen.add(mid)
            first_ts = float(new_msgs[0]["ts"])
            items.append(RawItem(
                source    = "slack",
                item_id   = mid,
                title     = f"[DM] ({team}): {new_msgs[0].get('text','')[:70]}",
                body      = "\n".join(lines)[:3000],
                url       = f"https://slack.com/app_redirect?channel={ch_id}",
                author    = _username(token, new_msgs[0].get("user", "?"), cache),
                timestamp = datetime.fromtimestamp(first_ts, tz=timezone.utc).isoformat(),
                metadata  = {"workspace": team, "type": "dm"},
            ))
            db.slack_mark_messages_seen(team, ch_id, [m["ts"] for m in new_msgs])
    except Exception as e:
        log.error("%s: DMs: %s", team, e)

    # ── 3. Channels where I participated or was mentioned ────────────────────
    try:
        channels = _get(token, "conversations.list", {
            "types":            "public_channel,private_channel",
            "exclude_archived": True,
            "limit":            200,
        }).get("channels", [])
        log.info("%s: channel memberships: %d", team, len(channels))

        filtering = bool(config.PROJECTS or config.FOCUS_TOPICS)

        for ch in channels:
            ch_id   = ch["id"]
            ch_name = ch.get("name", ch_id)

            try:
                msgs = _get(token, "conversations.history", {
                    "channel": ch_id,
                    "oldest":  str(cutoff_ts),
                    "limit":   100 if filtering else 40,
                }).get("messages", [])
            except Exception:
                continue

            if not msgs:
                continue

            # When context is configured, filter to messages the user sent,
            # was mentioned in, or that match a project/topic keyword.
            ch_hierarchy = "general"
            ch_project   = None
            if filtering:
                relevant = []
                for msg in msgs:
                    text = msg.get("text", "")
                    if msg.get("user") == my_uid:
                        relevant.append(msg)
                        ch_hierarchy = "user"
                        continue
                    ok, h, pt = _relevance(text, my_uid)
                    if ok:
                        relevant.append(msg)
                        if h == "user" or ch_hierarchy == "general":
                            ch_hierarchy = h
                        if pt and not ch_project:
                            ch_project = pt
                msgs = relevant
                if not msgs:
                    continue

            # Filter out messages we've already surfaced in a previous scan so
            # the LLM only re-analyses when there's genuinely new content in
            # the channel (parsival#69).
            unseen = db.slack_unseen_message_ts(
                team, ch_id, [m["ts"] for m in msgs]
            )
            new_msgs = [m for m in msgs if m["ts"] in unseen]
            if not new_msgs:
                continue

            log.info("%s: #%s: %d new msgs — including", team, ch_name, len(new_msgs))

            lines = []
            for msg in reversed(new_msgs):
                sender = _username(token, msg.get("user", "?"), cache)
                lines.append(f"[{sender}]: {msg.get('text', '')}")

            # Stable item_id per (team, channel) so upserts land on the same
            # items row and todo_exists() dedup kicks in.
            mid = f"ch_{team}_{ch_id}"
            if mid in seen:
                continue
            seen.add(mid)
            first_ts = float(new_msgs[0]["ts"])
            items.append(RawItem(
                source    = "slack",
                item_id   = mid,
                title     = f"[#{ch_name}] ({team}): recent activity",
                body      = "\n".join(lines)[:3000],
                url       = f"https://slack.com/app_redirect?channel={ch_id}",
                author    = f"#{ch_name}",
                timestamp = datetime.fromtimestamp(first_ts, tz=timezone.utc).isoformat(),
                metadata  = {
                    "channel":     ch_name,
                    "workspace":   team,
                    "type":        "channel",
                    "hierarchy":   ch_hierarchy,
                    "project_tag": ch_project,
                },
            ))
            db.slack_mark_messages_seen(team, ch_id, [m["ts"] for m in new_msgs])
    except Exception as e:
        log.error("%s: channels: %s", team, e)

    log.info("%s: %d items", team, len(items))
    return items


def fetch() -> list[RawItem]:
    """
    Fetch Slack items across all configured workspaces.

    Prefers per-user OAuth tokens stored in ``config.SLACK_USER_TOKENS``.
    Falls back to the legacy bot token path if no user tokens are present.

    :return: Combined list of raw items from all workspaces, deduplicated
             within each workspace by item ID.
    :rtype: list[RawItem]
    """
    cutoff_ts = (
        datetime.now(timezone.utc) - timedelta(hours=config.LOOKBACK_HOURS)
    ).timestamp()

    # ── User token path (one per connected workspace) ─────────────────────────
    if config.SLACK_USER_TOKENS:
        all_items: list[RawItem] = []
        for ws in config.SLACK_USER_TOKENS:
            token = ws.get("token", "")
            if not token:
                continue
            try:
                all_items.extend(_fetch_for_token(token, cutoff_ts))
            except Exception as e:
                log.error("workspace %s: %s", ws.get('team', '?'), e)
        return all_items

    # ── Legacy bot token fallback ─────────────────────────────────────────────
    if not config.SLACK_BOT_TOKEN or config.SLACK_BOT_TOKEN.startswith("xoxb-your"):
        log.info("not configured — skipping")
        return []

    log.info("using legacy bot token")
    token      = config.SLACK_BOT_TOKEN
    cutoff_str = str(cutoff_ts)
    cache: dict          = {}
    items: list[RawItem] = []

    try:
        bot_uid  = _get(token, "auth.test").get("user_id", "")
        channels = _get(token, "conversations.list", {
            "types":            "public_channel,private_channel,im,mpim",
            "exclude_archived": True,
            "limit":            100,
        }).get("channels", [])

        if config.SLACK_CHANNELS:
            name_map = {c["name"]: c for c in channels}
            channels = [name_map[n] for n in config.SLACK_CHANNELS if n in name_map]

        for ch in channels:
            ch_id   = ch["id"]
            ch_name = ch.get("name", ch_id)
            is_im   = ch.get("is_im", False)
            try:
                msgs = _get(token, "conversations.history", {
                    "channel": ch_id,
                    "oldest":  cutoff_str,
                    "limit":   50,
                }).get("messages", [])
            except Exception as e:
                log.error("#%s: %s", ch_name, e)
                continue

            # Skip messages already surfaced in a previous scan.  The legacy
            # path uses a per-message item_id so todo dedup is already stable;
            # the seen filter just avoids redundant LLM work (parsival#69).
            candidates = [
                m for m in msgs
                if is_im or f"<@{bot_uid}>" in m.get("text", "")
            ]
            unseen = db.slack_unseen_message_ts(
                "", ch_id, [m["ts"] for m in candidates]
            )
            candidates = [m for m in candidates if m["ts"] in unseen]
            for msg in candidates:
                text = msg.get("text", "")
                body = text
                if msg.get("reply_count", 0) > 0:
                    try:
                        replies = _get(token, "conversations.replies", {"channel": ch_id, "ts": msg["ts"]})
                        for rp in replies.get("messages", [])[1:5]:
                            rn   = _username(token, rp.get("user", "?"), cache)
                            body += f"\n[{rn}]: {rp.get('text', '')}"
                    except Exception:
                        pass
                ts = float(msg["ts"])
                items.append(RawItem(
                    source    = "slack",
                    item_id   = f"{ch_id}_{msg['ts']}",
                    title     = f"{'DM' if is_im else f'#{ch_name}'}: {text[:80]}",
                    body      = body[:3000],
                    url       = f"https://slack.com/app_redirect?channel={ch_id}&message_ts={msg['ts']}",
                    author    = _username(token, msg.get("user", "unknown"), cache),
                    timestamp = datetime.fromtimestamp(ts, tz=timezone.utc).isoformat(),
                    metadata  = {"channel": ch_name, "is_dm": is_im},
                ))
            if candidates:
                db.slack_mark_messages_seen(
                    "", ch_id, [m["ts"] for m in candidates]
                )
    except Exception as e:
        log.error("legacy error: %s", e)

    log.info("%d items (legacy)", len(items))
    return items
