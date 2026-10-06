from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Coroutine, Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response, status
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from pydantic import BaseModel, ConfigDict, Field

from app.artifacts import AssistRequest, DeliveryResponse, RelationshipRule, WorkflowName
from app.integrations.telegram import TelegramInitData, TelegramInitDataError, TelegramMiniAppAuth
from app.repositories import RelationshipCreate, RelationshipRecord, RelationshipUpdate, UserRecord


class ConsentRequiredError(BaseModel):
    """403 body: the Telegram user has no current personal-data consent. Consent is given in the bot via /start."""

    model_config = ConfigDict(extra="forbid")

    error: Literal["consent_required"] = "consent_required"
    message: str
    bot_command: Literal["/start"] = "/start"


CONSENT_REQUIRED_MESSAGE = "Personal data processing consent is required. Open the Telegram bot and send /start."


class ConsentRequired(Exception):
    """Raised by the Mini App auth dependency; rendered as a flat ConsentRequiredError 403 body."""


class MiniAppRoute(APIRoute):
    def get_route_handler(self) -> Callable[[Request], Coroutine[Any, Any, Response]]:
        handler = super().get_route_handler()

        async def route_handler(request: Request) -> Response:
            try:
                return await handler(request)
            except ConsentRequired:
                body = ConsentRequiredError(message=CONSENT_REQUIRED_MESSAGE)
                return JSONResponse(status_code=status.HTTP_403_FORBIDDEN, content=body.model_dump())

        return route_handler


router = APIRouter(
    prefix="/v1/miniapp",
    tags=["miniapp"],
    route_class=MiniAppRoute,
    responses={status.HTTP_403_FORBIDDEN: {"model": ConsentRequiredError, "description": "Consent required"}},
)


class UserView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    user_id: str
    telegram_user_id: int
    first_name: str
    last_name: str | None = None
    username: str | None = None
    language_code: str | None = None
    default_relationship_id: str | None = None


class RelationshipView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    relationship_id: str
    relation_type: str | None = None
    aliases: list[str] = Field(default_factory=list)
    communication_style: dict[str, Any] = Field(default_factory=dict)
    ruleset_version: int
    rules: list[RelationshipRule] = Field(default_factory=list)


class AuthResponse(BaseModel):
    user: UserView
    auth_date: int


class BootstrapResponse(BaseModel):
    user: UserView
    relationships: list[RelationshipView]


class RelationshipCreateBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    relation_type: str | None = Field(default=None, max_length=64)
    aliases: list[str] = Field(default_factory=list, max_length=20)
    communication_style: dict[str, Any] = Field(default_factory=dict)
    set_as_default: bool = False


class RelationshipUpdateBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    relation_type: str | None = Field(default=None, max_length=64)
    aliases: list[str] | None = Field(default=None, max_length=20)
    communication_style: dict[str, Any] | None = None


class RuleBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: str = Field(min_length=1, max_length=64)
    value: str = Field(min_length=1, max_length=2000)
    priority: int = Field(default=0, ge=-1000, le=1000)


class MiniAppAssistBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str = Field(min_length=1, max_length=10_000)
    relationship_id: str | None = None
    language: str | None = Field(default=None, max_length=16)


@dataclass(slots=True)
class AuthenticatedMiniAppUser:
    user: UserRecord
    init_data: TelegramInitData


def _user_view(record: UserRecord) -> UserView:
    return UserView(
        user_id=record.user_id,
        telegram_user_id=record.telegram_user_id,
        first_name=record.first_name,
        last_name=record.last_name,
        username=record.username,
        language_code=record.language_code,
        default_relationship_id=record.default_relationship_id,
    )


def _relationship_view(record: RelationshipRecord) -> RelationshipView:
    return RelationshipView(
        relationship_id=record.relationship_id,
        relation_type=record.relation_type,
        aliases=record.aliases,
        communication_style=record.communication_style,
        ruleset_version=record.ruleset_version,
        rules=record.rules,
    )


async def authenticate_miniapp_user(
    request: Request,
    x_telegram_init_data: str | None = Header(default=None, alias="X-Telegram-Init-Data"),
) -> AuthenticatedMiniAppUser:
    settings = request.app.state.settings
    container = request.app.state.container
    if settings.telegram_bot_token is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Telegram Mini App authentication is not configured",
        )
    if not x_telegram_init_data:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing Telegram initData")

    verifier = TelegramMiniAppAuth(
        settings.telegram_bot_token.get_secret_value(),
        max_age_seconds=settings.telegram_init_data_max_age_seconds,
    )
    try:
        init_data = verifier.validate(x_telegram_init_data)
    except TelegramInitDataError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)) from exc

    account = getattr(container, "account", None)
    if account is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Account service is not configured",
        )
    # Consent is checked before any user record is created or read.
    if not await account.is_active(init_data.user.id):
        raise ConsentRequired()
    user = await container.users.get_or_create_from_telegram(init_data.user)
    return AuthenticatedMiniAppUser(user=user, init_data=init_data)


@router.post("/auth", response_model=AuthResponse)
async def miniapp_auth(auth: AuthenticatedMiniAppUser = Depends(authenticate_miniapp_user)) -> AuthResponse:
    return AuthResponse(user=_user_view(auth.user), auth_date=auth.init_data.auth_date)


@router.get("/bootstrap", response_model=BootstrapResponse)
async def bootstrap(
    request: Request,
    auth: AuthenticatedMiniAppUser = Depends(authenticate_miniapp_user),
) -> BootstrapResponse:
    relationships = await request.app.state.container.relationships.list_for_user(auth.user.user_id)
    return BootstrapResponse(
        user=_user_view(auth.user),
        relationships=[_relationship_view(item) for item in relationships],
    )


@router.get("/relationships", response_model=list[RelationshipView])
async def list_relationships(
    request: Request,
    auth: AuthenticatedMiniAppUser = Depends(authenticate_miniapp_user),
) -> list[RelationshipView]:
    values = await request.app.state.container.relationships.list_for_user(auth.user.user_id)
    return [_relationship_view(item) for item in values]


@router.post("/relationships", response_model=RelationshipView, status_code=status.HTTP_201_CREATED)
async def create_relationship(
    request: Request,
    body: RelationshipCreateBody,
    auth: AuthenticatedMiniAppUser = Depends(authenticate_miniapp_user),
) -> RelationshipView:
    container = request.app.state.container
    record = await container.relationships.create(
        auth.user.user_id,
        RelationshipCreate(
            relation_type=body.relation_type,
            aliases=body.aliases,
            communication_style=body.communication_style,
        ),
    )
    if body.set_as_default or auth.user.default_relationship_id is None:
        auth.user = await container.users.set_default_relationship(auth.user.user_id, record.relationship_id)
    return _relationship_view(record)


@router.patch("/relationships/{relationship_id}", response_model=RelationshipView)
async def update_relationship(
    request: Request,
    relationship_id: str,
    body: RelationshipUpdateBody,
    auth: AuthenticatedMiniAppUser = Depends(authenticate_miniapp_user),
) -> RelationshipView:
    try:
        record = await request.app.state.container.relationships.update(
            auth.user.user_id,
            relationship_id,
            RelationshipUpdate(
                relation_type=body.relation_type,
                aliases=body.aliases,
                communication_style=body.communication_style,
            ),
        )
    except KeyError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Relationship not found") from exc
    return _relationship_view(record)


@router.delete("/relationships/{relationship_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_relationship(
    request: Request,
    relationship_id: str,
    auth: AuthenticatedMiniAppUser = Depends(authenticate_miniapp_user),
) -> Response:
    container = request.app.state.container
    deleted = await container.relationships.delete(auth.user.user_id, relationship_id)
    if not deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Relationship not found")
    if auth.user.default_relationship_id == relationship_id:
        await container.users.set_default_relationship(auth.user.user_id, None)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.put("/me/default-relationship/{relationship_id}", response_model=UserView)
async def set_default_relationship(
    request: Request,
    relationship_id: str,
    auth: AuthenticatedMiniAppUser = Depends(authenticate_miniapp_user),
) -> UserView:
    container = request.app.state.container
    relationship = await container.relationships.get(auth.user.user_id, relationship_id)
    if relationship is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Relationship not found")
    user = await container.users.set_default_relationship(auth.user.user_id, relationship_id)
    return _user_view(user)


@router.post("/relationships/{relationship_id}/rules", response_model=RelationshipRule, status_code=status.HTTP_201_CREATED)
async def add_rule(
    request: Request,
    relationship_id: str,
    body: RuleBody,
    auth: AuthenticatedMiniAppUser = Depends(authenticate_miniapp_user),
) -> RelationshipRule:
    try:
        return await request.app.state.container.relationships.add_rule(
            auth.user.user_id,
            relationship_id,
            RelationshipRule(type=body.type, value=body.value, priority=body.priority),
        )
    except KeyError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Relationship not found") from exc


@router.put("/relationships/{relationship_id}/rules/{rule_id}", response_model=RelationshipRule)
async def update_rule(
    request: Request,
    relationship_id: str,
    rule_id: int,
    body: RuleBody,
    auth: AuthenticatedMiniAppUser = Depends(authenticate_miniapp_user),
) -> RelationshipRule:
    try:
        return await request.app.state.container.relationships.update_rule(
            auth.user.user_id,
            relationship_id,
            rule_id,
            RelationshipRule(type=body.type, value=body.value, priority=body.priority),
        )
    except KeyError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Rule not found") from exc


@router.delete("/relationships/{relationship_id}/rules/{rule_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_rule(
    request: Request,
    relationship_id: str,
    rule_id: int,
    auth: AuthenticatedMiniAppUser = Depends(authenticate_miniapp_user),
) -> Response:
    deleted = await request.app.state.container.relationships.delete_rule(
        auth.user.user_id,
        relationship_id,
        rule_id,
    )
    if not deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Rule not found")
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/assist/{workflow}", response_model=DeliveryResponse)
async def miniapp_assist(
    request: Request,
    workflow: WorkflowName,
    body: MiniAppAssistBody,
    auth: AuthenticatedMiniAppUser = Depends(authenticate_miniapp_user),
) -> DeliveryResponse:
    if body.relationship_id is not None:
        relationship = await request.app.state.container.relationships.get(
            auth.user.user_id,
            body.relationship_id,
        )
        if relationship is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Relationship not found")

    language = body.language or auth.user.language_code or "ru"
    return await request.app.state.container.engine.execute(
        workflow,
        AssistRequest(
            user_id=auth.user.user_id,
            text=body.text,
            relationship_id=body.relationship_id,
            language=language,
        ),
    )
