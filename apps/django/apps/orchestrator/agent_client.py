"""Async HTTP client for the opencode server REST API.

Wraps the opencode ``serve`` process REST API for session management,
message sending, and health checks.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from typing import Any

import httpx


class AgentClientError(Exception):
    """Base exception for agent client errors.

    Raised when an HTTP request fails due to a non-2xx response, a
    connection error, or a timeout.
    """


@dataclass
class MessageResponse:
    """Represents the response returned after sending a message."""

    id: str
    parts: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class MessageWithParts:
    """Represents a message that contains parts."""

    id: str
    parts: list[dict[str, Any]] = field(default_factory=list)


# ── Part inspection helpers ──────────────────────────────────────────


def is_completed(response: MessageResponse) -> bool:
    """Return ``True`` when *response* indicates stage completion.

    A stage is completed when none of its parts carry a blocked or
    error marker.
    """
    return not is_blocked(response) and not has_error(response)


def is_blocked(response: MessageResponse) -> bool:
    """Return ``True`` when *response* indicates the stage is blocked.

    A blocked marker is a part with ``{"type": "input_required"}``,
    signalling that the agent is waiting for user input.
    """
    return any(part.get("type") == "input_required" for part in response.parts)


def has_error(response: MessageResponse) -> bool:
    """Return ``True`` when *response* contains an error part.

    An error marker is a part with ``{"type": "error"}``.
    """
    return any(part.get("type") == "error" for part in response.parts)


def part_to_log_entry(part: dict) -> dict:
    """Convert an opencode message part to a structured log entry.

    The returned dict has at least ``ts``, ``type``, and ``content`` keys
    so that session-derived entries have a uniform shape for the frontend.
    """
    from datetime import datetime, timezone

    entry: dict = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "type": part.get("type", "unknown"),
    }
    ptype = entry["type"]
    if ptype == "text":
        entry["content"] = part.get("text", "")
    elif ptype == "tool_use":
        entry["content"] = json.dumps(part.get("input", {}))
        if "name" in part:
            entry["name"] = part["name"]
    elif ptype == "tool_result":
        entry["content"] = part.get("content", part.get("text", ""))
    elif ptype == "input_required":
        entry["content"] = json.dumps(
            {k: v for k, v in part.items() if k != "type"}
        )
    else:
        entry["content"] = part.get("text") or part.get("content") or json.dumps(part)
    return entry


class AgentClient:
    """Async HTTP client for the opencode server REST API.

    Parameters
    ----------
    base_url:
        The base URL of the opencode server (e.g. ``http://localhost:4096``).
    password:
        Optional bearer token sent as an ``Authorization`` header.
    client:
        Optional pre-configured ``httpx.AsyncClient``. When omitted the
        client creates its own with ``base_url`` set.
    timeout:
        Optional timeout in seconds for HTTP requests.  Defaults to 300.0.
        LLM-backed operations (``send_message``) may take minutes to
        complete, so the default is set well above the 5 s httpx default.
    """

    def __init__(
        self,
        base_url: str,
        password: str | None = None,
        client: httpx.AsyncClient | None = None,
        timeout: float | None = None,
    ) -> None:
        self.base_url = base_url
        self.password = password
        self._client = client or httpx.AsyncClient(
            base_url=base_url,
            timeout=timeout or 300.0,
        )

    async def create_session(
        self, title: str, agent: str | None = None
    ) -> str:
        """Create a new session on the opencode server.

        Parameters
        ----------
        title:
            The session title (typically the stage name).
        agent:
            Optional agent name to use for this session.

        Returns
        -------
        str:
            The session ID returned by the server.
        """
        body: dict[str, Any] = {"title": title}
        if agent is not None:
            body["agent"] = agent
        resp = await self._request("POST", "/session", json=body)
        data = await self._response_json(resp)
        return str(data["id"])

    async def send_message(
        self,
        session_id: str,
        parts: list[dict[str, Any]],
        model: str | None = None,
        agent: str | None = None,
    ) -> MessageResponse:
        """Send a message to an existing session.

        Parameters
        ----------
        session_id:
            The session to send the message to.
        parts:
            The message parts (e.g. ``[{"type": "text", "text": "..."}]``).
        model:
            Optional model override sent as a query parameter.
        agent:
            Optional agent override sent as a query parameter.

        Returns
        -------
        MessageResponse:
            The parsed response from the server.
        """
        params: dict[str, str] = {}
        if model is not None:
            params["model"] = model
        if agent is not None:
            params["agent"] = agent
        body = {"parts": parts}
        resp = await self._request(
            "POST",
            f"/session/{session_id}/message",
            json=body,
            params=params or None,
        )
        data = await self._response_json(resp)
        # Real opencode server nests message id under ``info.id``;
        # unit-test mocks may provide it at the top level.
        message_id = data.get("id") or data.get("info", {}).get("id")
        return MessageResponse(
            id=message_id,
            parts=data.get("parts", []),
        )

    async def get_session_messages(
        self, session_id: str, limit: int | None = None
    ) -> list[MessageWithParts]:
        """Retrieve messages from a session.

        Parameters
        ----------
        session_id:
            The session to fetch messages for.
        limit:
            Optional maximum number of messages to return.

        Returns
        -------
        list[MessageWithParts]:
            The messages returned by the server.
        """
        params: dict[str, int] = {}
        if limit is not None:
            params["limit"] = limit
        resp = await self._request(
            "GET",
            f"/session/{session_id}/message",
            params=params or None,
        )
        data = await self._response_json(resp)
        messages: list[MessageWithParts] = []
        for entry in data:
            # Real opencode server: each entry has ``info.id`` and ``parts``
            info = entry.get("info", {})
            msg_id = info.get("id")
            if msg_id:
                messages.append(
                    MessageWithParts(
                        id=msg_id, parts=entry.get("parts", []),
                    )
                )
            else:
                # Compat: some test mocks nest messages under ``messages``
                for msg in entry.get("messages", []):
                    messages.append(
                        MessageWithParts(
                            id=msg["id"], parts=msg.get("parts", []),
                        )
                    )
        return messages

    async def get_session_diff(
        self, session_id: str
    ) -> list[dict[str, Any]]:
        """Retrieve the diff for a session.

        Parameters
        ----------
        session_id:
            The session to fetch the diff for.

        Returns
        -------
        list[dict[str, Any]]:
            The parsed diff list from the server.
        """
        resp = await self._request("GET", f"/session/{session_id}/diff")
        data = await self._response_json(resp)
        return data

    async def abort_session(self, session_id: str) -> bool:
        """Abort a running session.

        Parameters
        ----------
        session_id:
            The session to abort.

        Returns
        -------
        bool:
            ``True`` on success.
        """
        await self._request("POST", f"/session/{session_id}/abort")
        return True

    async def health_check(self) -> bool:
        """Check whether the server is healthy.

        Returns
        -------
        bool:
            ``True`` if the server responds with 200, ``False`` otherwise.
        """
        try:
            resp = await self._request("GET", "/global/health")
            return resp.status_code == 200
        except AgentClientError:
            return False

    async def _response_json(self, resp: Any) -> Any:
        """Extract JSON body from a response.

        Works with both real ``httpx.Response`` (where ``.json()`` is
        synchronous) and mock responses (where ``.json()`` may return a
        coroutine).
        """
        result = resp.json()
        if asyncio.iscoroutine(result):
            return await result
        return result

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
    ) -> httpx.Response:
        """Internal helper to perform an HTTP request.

        Handles authentication header injection, transport error
        wrapping, and non-2xx response detection.

        Parameters
        ----------
        method:
            HTTP method (``"GET"``, ``"POST"``, etc.).
        path:
            Request path (e.g. ``"/session"``).
        json:
            Optional JSON body.
        params:
            Optional query parameters.

        Returns
        -------
        httpx.Response:
            The server response.

        Raises
        ------
        AgentClientError:
            On transport errors or non-2xx responses.
        """
        headers: dict[str, str] = {}
        if self.password:
            headers["Authorization"] = f"Bearer {self.password}"

        kwargs: dict[str, Any] = {}
        if headers:
            kwargs["headers"] = headers
        if json is not None:
            kwargs["json"] = json
        if params is not None:
            kwargs["params"] = params

        http_method = getattr(self._client, method.lower())
        try:
            resp = await http_method(path, **kwargs)
        except (
            ConnectionError,
            httpx.TimeoutException,
            httpx.ConnectError,
        ) as exc:
            raise AgentClientError(str(exc)) from exc

        if resp.status_code >= 400:
            raise AgentClientError(
                f"HTTP {resp.status_code}: {resp.text}"
            )
        return resp
