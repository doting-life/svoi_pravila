from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from redis.asyncio import Redis
else:
    Redis = Any  # type: ignore[misc,assignment]
from sqlalchemy.ext.asyncio import AsyncEngine

from app.checkpoints import CheckpointStore, InMemoryCheckpointStore, RedisCheckpointStore
from app.integrations.telegram import TelegramBotClient
from app.observability import RequestTraceSink, TraceSink
from app.observability.postgres_sink import PostgresTraceSink
from app.persistence import build_async_engine, build_session_factory
from app.repositories import (
    InMemoryRelationshipRepository,
    InMemoryUserRepository,
    PostgresRelationshipRepository,
    PostgresUserRepository,
    RelationshipRepository,
    UserRecord,
    UserRepository,
)
from app.settings import AppSettings
from app.skills import SkillLoader
from app.stages import StageRegistry
from app.tools.llm import (
    DeepSeekStructuredLLMProvider,
    FakeStructuredLLMProvider,
    GigaChatStructuredLLMProvider,
    LLMGenerateTool,
    OpenAIStructuredLLMProvider,
    StructuredLLMProvider,
)
from app.tools.registry import ToolRegistry
from app.workflows.engine import WorkflowDependencies, WorkflowEngine


AsyncCloser = Callable[[], Awaitable[Any]]

WORKFLOW_SKILLS: tuple[str, ...] = ("soften", "decode", "help-say")


def preload_workflow_skills(skills: SkillLoader) -> None:
    """Warm the loader cache at startup; a missing or broken skill fails here, not on the first request."""
    for name in WORKFLOW_SKILLS:
        try:
            skills.load_bundle(name)
        except Exception as exc:
            raise RuntimeError(f"Failed to preload workflow skill {name!r}: {exc}") from exc


def build_llm_provider(settings: AppSettings) -> tuple[StructuredLLMProvider, AsyncCloser | None]:
    """Select the structured LLM provider from LLM_PROVIDER. Workflows are provider-agnostic."""
    if settings.llm_provider == "openai":
        openai_provider = OpenAIStructuredLLMProvider(
            api_key=settings.openai_api_key.get_secret_value(),  # type: ignore[union-attr]
            model=settings.openai_model or "",
            timeout_seconds=settings.openai_timeout_seconds,
            max_retries=settings.openai_max_retries,
        )
        return openai_provider, openai_provider.aclose
    if settings.llm_provider == "gigachat":
        gigachat_provider = GigaChatStructuredLLMProvider(
            credentials=settings.gigachat_credentials.get_secret_value(),  # type: ignore[union-attr]
            model=settings.gigachat_model or "",
            scope=settings.gigachat_scope,
            base_url=settings.gigachat_base_url,
            auth_url=settings.gigachat_auth_url,
            timeout_seconds=settings.gigachat_timeout_seconds,
            verify=settings.gigachat_ca_bundle or True,
        )
        return gigachat_provider, gigachat_provider.aclose
    if settings.llm_provider == "deepseek":
        deepseek_provider = DeepSeekStructuredLLMProvider(
            api_key=settings.deepseek_api_key.get_secret_value(),  # type: ignore[union-attr]
            model=settings.deepseek_model or "",
            base_url=settings.deepseek_base_url,
            timeout_seconds=settings.deepseek_timeout_seconds,
            max_retries=settings.deepseek_max_retries,
        )
        return deepseek_provider, deepseek_provider.aclose
    return FakeStructuredLLMProvider(), None


@dataclass(slots=True)
class ApplicationContainer:
    engine: WorkflowEngine
    trace: TraceSink
    checkpoints: CheckpointStore
    relationships: RelationshipRepository
    users: UserRepository
    telegram: TelegramBotClient | None = None
    redis: Redis | None = None
    database_engine: AsyncEngine | None = None
    _closers: list[AsyncCloser] = field(default_factory=list, repr=False)

    async def aclose(self) -> None:
        for closer in reversed(self._closers):
            await closer()


def build_container(settings: AppSettings | None = None) -> ApplicationContainer:
    settings = settings or AppSettings()
    # Before any client is created so a broken skill fails startup with nothing to close.
    skills = SkillLoader()
    preload_workflow_skills(skills)
    closers: list[AsyncCloser] = []

    tools = ToolRegistry()
    provider, provider_closer = build_llm_provider(settings)
    if provider_closer is not None:
        closers.append(provider_closer)
    tools.register(LLMGenerateTool(provider, self_check_mode=settings.llm_self_check_mode))

    redis: Redis | None = None
    if settings.checkpoint_backend == "redis":
        try:
            from redis.asyncio import Redis as RedisClient
        except ModuleNotFoundError as exc:
            raise RuntimeError("redis package is required when CHECKPOINT_BACKEND=redis") from exc
        redis = RedisClient.from_url(settings.redis_url or "", decode_responses=True)
        checkpoints: CheckpointStore = RedisCheckpointStore(
            redis,
            ttl_seconds=settings.redis_checkpoint_ttl_seconds,
        )
        closers.append(redis.aclose)
    else:
        checkpoints = InMemoryCheckpointStore()

    database_engine: AsyncEngine | None = None
    trace: TraceSink = RequestTraceSink()
    if settings.relationship_backend == "postgres":
        database_engine = build_async_engine(settings.database_url or "")
        sessions = build_session_factory(database_engine)
        relationships: RelationshipRepository = PostgresRelationshipRepository(sessions)
        users: UserRepository = PostgresUserRepository(sessions)
        closers.append(database_engine.dispose)
        postgres_trace = PostgresTraceSink(sessions)
        closers.append(postgres_trace.drain)
        trace = postgres_trace
    else:
        relationships = InMemoryRelationshipRepository.demo()
        memory_users = InMemoryUserRepository()
        memory_users.upsert(
            UserRecord(
                user_id="u-1",
                telegram_user_id=100001,
                first_name="Demo",
                language_code="ru",
                default_relationship_id="partner-1",
            )
        )
        users = memory_users

    telegram: TelegramBotClient | None = None
    if settings.telegram_enabled:
        telegram = TelegramBotClient(settings.telegram_bot_token.get_secret_value())  # type: ignore[union-attr]
        closers.append(telegram.aclose)

    engine = WorkflowEngine(
        WorkflowDependencies(
            skills=skills,
            tools=tools,
            relationships=relationships,
            users=users,
            checkpoints=checkpoints,
            trace=trace,
            stages=StageRegistry(),
        )
    )
    return ApplicationContainer(
        engine=engine,
        trace=trace,
        checkpoints=checkpoints,
        relationships=relationships,
        users=users,
        telegram=telegram,
        redis=redis,
        database_engine=database_engine,
        _closers=closers,
    )
