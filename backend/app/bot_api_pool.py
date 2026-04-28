from __future__ import annotations

from typing import Iterable

from .bot_api_client import BotApiUploadError, LocalBotApiClient
from .models import BotApiAccount, BotDispatchMode, ChannelConfig


class BotApiClientPool:
    def __init__(self) -> None:
        self._clients: dict[str, LocalBotApiClient] = {}
        self._accounts: dict[str, BotApiAccount] = {}
        self._round_robin_index = 0

    def configure(
        self, accounts: Iterable[BotApiAccount], proxy_settings: dict | None = None
    ) -> None:
        next_accounts: dict[str, BotApiAccount] = {}
        for account in accounts:
            next_accounts[account.id] = account.model_copy()
            client = self._clients.get(account.id) or LocalBotApiClient()
            client.configure(
                account.server_url,
                account.bot_token,
                account.send_rate_limit_per_minute,
                account.send_rate_limit_per_channel_per_minute,
                account.send_jitter_min_ms,
                account.send_jitter_max_ms,
                account.auto_slowdown_enabled,
                account.auto_slowdown_factor_percent,
                account.auto_slowdown_duration_seconds,
                proxy_settings,
            )
            self._clients[account.id] = client
        stale_ids = set(self._clients) - set(next_accounts)
        for account_id in stale_ids:
            self._clients.pop(account_id, None)
        self._accounts = next_accounts
        enabled_count = len([item for item in next_accounts.values() if item.enabled])
        if enabled_count <= 0:
            self._round_robin_index = 0
        elif self._round_robin_index >= enabled_count:
            self._round_robin_index = 0

    def status(self) -> dict:
        items = []
        for account_id, account in self._accounts.items():
            client = self._clients.get(account_id)
            client_status = client.status() if client else {}
            items.append(
                {
                    "id": account.id,
                    "name": account.name,
                    "enabled": account.enabled,
                    "stage": "authorized"
                    if account.enabled and client and account.bot_token
                    else "logged_out",
                    "last_error": client.last_error if client else "",
                    "wait_seconds": int(client.preview_wait_seconds()) if client else 0,
                    "send_rate_limit_per_minute": account.send_rate_limit_per_minute,
                    "send_rate_limit_per_channel_per_minute": account.send_rate_limit_per_channel_per_minute,
                    "recent_send_count": client.recent_send_count() if client else 0,
                    "remaining_quota": max(
                        0,
                        int(
                            client_status.get(
                                "effective_send_rate_limit_per_minute",
                                account.send_rate_limit_per_minute,
                            )
                        )
                        - (client.recent_send_count() if client else 0),
                    ),
                    "last_wait_reason": client.last_wait_reason() if client else "",
                    "effective_send_rate_limit_per_minute": int(
                        client_status.get(
                            "effective_send_rate_limit_per_minute",
                            account.send_rate_limit_per_minute,
                        )
                    )
                    if client
                    else account.send_rate_limit_per_minute,
                    "effective_send_rate_limit_per_channel_per_minute": int(
                        client_status.get(
                            "effective_send_rate_limit_per_channel_per_minute",
                            account.send_rate_limit_per_channel_per_minute,
                        )
                    )
                    if client
                    else account.send_rate_limit_per_channel_per_minute,
                    "slowdown_active": client_status.get("slowdown_active", "false") == "true" if client else False,
                    "slowdown_wait_seconds": int(client_status.get("slowdown_wait_seconds", 0)) if client else 0,
                    "slowdown_reason": client_status.get("slowdown_reason", "") if client else "",
                }
            )
        return {
            "total": len(self._accounts),
            "enabled": len([item for item in self._accounts.values() if item.enabled]),
            "items": items,
        }

    def account_map(self) -> dict[str, BotApiAccount]:
        return {key: value.model_copy() for key, value in self._accounts.items()}

    def get_client(self, account_id: str) -> LocalBotApiClient:
        account = self._accounts.get(account_id)
        if not account:
            raise BotApiUploadError("bot api account is not configured")
        if not account.enabled:
            raise BotApiUploadError("bot api account is disabled")
        client = self._clients.get(account_id)
        if not client:
            raise BotApiUploadError("bot api account client is unavailable")
        return client

    def enabled_accounts(self) -> list[BotApiAccount]:
        return [item.model_copy() for item in self._accounts.values() if item.enabled]

    def pick_account_id(
        self,
        *,
        dispatch_mode: BotDispatchMode,
        default_account_id: str,
        channel: ChannelConfig | None = None,
    ) -> str:
        if dispatch_mode == BotDispatchMode.CHANNEL_BOUND:
            account_id = channel.bot_api_account_id if channel else ""
            if account_id and self._accounts.get(account_id) and self._accounts[account_id].enabled:
                return account_id
            raise BotApiUploadError("no enabled bot api account is bound to this channel")
        if dispatch_mode == BotDispatchMode.ROUND_ROBIN:
            return self._pick_round_robin_account_id()
        account_id = default_account_id
        if account_id and self._accounts.get(account_id) and self._accounts[account_id].enabled:
            return account_id
        enabled = self.enabled_accounts()
        if enabled:
            return enabled[0].id
        raise BotApiUploadError("no enabled bot api account is available")

    async def test_connection(self, account_id: str) -> dict:
        client = self.get_client(account_id)
        return await client.test_connection()

    async def shutdown(self) -> None:
        for client in self._clients.values():
            await client.shutdown()

    def _pick_round_robin_account_id(self) -> str:
        enabled = self.enabled_accounts()
        if not enabled:
            raise BotApiUploadError("no enabled bot api account is available")
        ready_accounts = [
            account
            for account in enabled
            if (
                (self._clients.get(account.id).preview_wait_seconds() if self._clients.get(account.id) else 0)
                <= 0
            )
        ]
        candidates = ready_accounts or enabled
        index = self._round_robin_index % len(candidates)
        self._round_robin_index = (index + 1) % len(candidates)
        return candidates[index].id
