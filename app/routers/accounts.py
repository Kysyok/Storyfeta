"""Optional host accounts: sign up / in / out, profile, saved defaults, archive (full CRUD)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request, Response

from app.core import security
from app.game.errors import ForbiddenError, NotFoundError
from app.models.domain import Settings
from app.models.orm import Account, ArchivedGame
from app.routers.deps import (
    ACCOUNT_COOKIE,
    Container,
    get_container,
    require_account_id,
    set_account_cookie,
)
from app.schemas.http import (
    AccountView,
    ArchiveDetail,
    ArchiveItem,
    ArchivedStory,
    Credentials,
    Register,
    UpdateArchivedGame,
    UpdateDefaults,
    UpdateProfile,
)
from app.services.accounts import AccountService

router = APIRouter(prefix="/api", tags=["accounts"])


def _service(container: Container) -> AccountService:
    if container.accounts is None:
        raise NotFoundError("accounts_disabled", "Accounts are not enabled on this server.")
    return container.accounts


def _account_view(account: Account) -> AccountView:
    return AccountView(
        id=str(account.id),
        email=account.email,
        nickname=account.nickname,
        default_rounds=account.default_rounds,
        default_turn_seconds=account.default_turn_seconds,
    )


def _archive_item(game: ArchivedGame) -> ArchiveItem:
    return ArchiveItem(
        id=str(game.id),
        room_code=game.room_code,
        title=game.title,
        rounds=game.rounds,
        players=game.payload.get("players", []),
        finished_at=game.finished_at,
    )


def _sign_in(container: Container, response: Response, account: Account) -> AccountView:
    settings = container.settings
    token = security.create_account_token(
        settings.secret_key.get_secret_value(), str(account.id), settings.account_token_ttl_seconds
    )
    set_account_cookie(response, settings, token)
    return _account_view(account)


@router.post("/auth/register", response_model=AccountView, status_code=201)
async def register(
    body: Register, response: Response, container: Container = Depends(get_container)
) -> AccountView:
    """Create an account and sign in."""
    account = await _service(container).register(body.email, body.password, body.nickname)
    return _sign_in(container, response, account)


@router.post("/auth/login", response_model=AccountView)
async def login(
    body: Credentials, response: Response, container: Container = Depends(get_container)
) -> AccountView:
    """Sign in with email and password."""
    account = await _service(container).authenticate(body.email, body.password)
    return _sign_in(container, response, account)


@router.post("/auth/logout", status_code=204)
async def logout(response: Response) -> None:
    """Sign out. Any room you are playing in keeps working."""
    response.delete_cookie(ACCOUNT_COOKIE, path="/")


@router.get("/me", response_model=AccountView)
async def me(request: Request, container: Container = Depends(get_container)) -> AccountView:
    """The signed-in host."""
    account_id = require_account_id(request, container.settings)
    account = await _service(container).get(account_id)
    if account is None:
        raise ForbiddenError("sign_in_required", "Sign in to do that.")
    return _account_view(account)


@router.put("/me/profile", response_model=AccountView)
async def update_profile(
    body: UpdateProfile, request: Request, container: Container = Depends(get_container)
) -> AccountView:
    """Change the nickname used automatically when you host or join a room."""
    account_id = require_account_id(request, container.settings)
    account = await _service(container).update_nickname(account_id, body.nickname)
    return _account_view(account)


@router.put("/me/defaults", response_model=AccountView)
async def update_defaults(
    body: UpdateDefaults, request: Request, container: Container = Depends(get_container)
) -> AccountView:
    """Save default rounds/timer for future rooms."""
    account_id = require_account_id(request, container.settings)
    account = await _service(container).update_defaults(
        account_id, Settings(rounds=body.default_rounds, turn_seconds=body.default_turn_seconds)
    )
    return _account_view(account)


@router.delete("/me", status_code=204)
async def delete_me(
    request: Request, response: Response, container: Container = Depends(get_container)
) -> None:
    """Delete the account and its whole game history, then sign out."""
    account_id = require_account_id(request, container.settings)
    await _service(container).delete_account(account_id)
    response.delete_cookie(ACCOUNT_COOKIE, path="/")


@router.get("/archive", response_model=list[ArchiveItem])
async def archive_list(
    request: Request, container: Container = Depends(get_container)
) -> list[ArchiveItem]:
    """Finished games, newest first."""
    account_id = require_account_id(request, container.settings)
    return [_archive_item(g) for g in await _service(container).list_games(account_id)]


@router.get("/archive/{game_id}", response_model=ArchiveDetail)
async def archive_detail(
    game_id: str, request: Request, container: Container = Depends(get_container)
) -> ArchiveDetail:
    """One finished game with all its stories."""
    account_id = require_account_id(request, container.settings)
    game = await _service(container).get_game(account_id, game_id)
    return ArchiveDetail(
        **_archive_item(game).model_dump(),
        stories=[ArchivedStory(**s) for s in game.payload.get("stories", [])],
    )


@router.patch("/archive/{game_id}", response_model=ArchiveItem)
async def archive_rename(
    game_id: str,
    body: UpdateArchivedGame,
    request: Request,
    container: Container = Depends(get_container),
) -> ArchiveItem:
    """Give a saved game a title (null or empty resets it to the room code)."""
    account_id = require_account_id(request, container.settings)
    game = await _service(container).rename_game(account_id, game_id, body.title)
    return _archive_item(game)


@router.delete("/archive/{game_id}", status_code=204)
async def archive_delete(
    game_id: str, request: Request, container: Container = Depends(get_container)
) -> None:
    """Remove a game from your history. Other players keep their own copies."""
    account_id = require_account_id(request, container.settings)
    await _service(container).delete_game(account_id, game_id)
