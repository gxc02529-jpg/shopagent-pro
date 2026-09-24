from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="SHOPAGENT_", env_file=".env", extra="ignore")

    env: str = "development"
    host: str = "0.0.0.0"
    port: int = 8080
    memory_backend: str = "memory"
    operations_backend: str = "memory"
    session_ttl_seconds: int = 1800
    low_confidence_threshold: float = 0.55
    cors_origins: str = "http://localhost:8080"
    admin_token: str = "dev-admin-token"
    service_token: str = "dev-service-token"
    user_token_secret: str = "dev-user-token-secret"
    redis_url: str = "redis://localhost:6379/0"
    redis_key_prefix: str = "shopagent"
    database_url: str = "mysql+pymysql://shopagent:shopagent@localhost:3306/shopagent"
    llm_backend: str = "rule"
    llm_api_url: str = ""
    llm_api_key: str = ""
    llm_model: str = "mock"
    rag_backend: str = "character"
    guardrails_enabled: bool = False
    redact_generated_answers: bool = False
    agent_transport: str = "local"
    tool_transport: str = "local"
    a2a_base_url: str = "http://127.0.0.1:8080/a2a"
    mcp_url: str = "http://127.0.0.1:8080/mcp"
    public_base_url: str = ""
    a2a_task_ttl_seconds: int = 3600

    @property
    def cors_origin_list(self) -> list[str]:
        return [item.strip() for item in self.cors_origins.split(",") if item.strip()]

    def validate_for_startup(self) -> None:
        if self.memory_backend not in {"memory", "redis"}:
            raise ValueError("SHOPAGENT_MEMORY_BACKEND must be memory or redis")
        if self.operations_backend not in {"memory", "mysql"}:
            raise ValueError("SHOPAGENT_OPERATIONS_BACKEND must be memory or mysql")
        if self.llm_backend not in {"rule", "mock", "http"}:
            raise ValueError("SHOPAGENT_LLM_BACKEND must be rule, mock or http")
        if self.llm_backend == "http" and not self.llm_api_url:
            raise ValueError("llm_backend=http requires SHOPAGENT_LLM_API_URL")
        if self.rag_backend not in {"character", "vector"}:
            raise ValueError("SHOPAGENT_RAG_BACKEND must be character or vector")
        if self.agent_transport not in {"local", "a2a"}:
            raise ValueError("SHOPAGENT_AGENT_TRANSPORT must be local or a2a")
        if self.tool_transport not in {"local", "mcp"}:
            raise ValueError("SHOPAGENT_TOOL_TRANSPORT must be local or mcp")
        if self.a2a_task_ttl_seconds < 60:
            raise ValueError("SHOPAGENT_A2A_TASK_TTL_SECONDS must be at least 60")
        if self.env.lower() in {"production", "prod"}:
            if self._weak_secret(self.admin_token):
                raise ValueError("production requires a strong SHOPAGENT_ADMIN_TOKEN")
            if self._weak_secret(self.service_token):
                raise ValueError("production requires a strong SHOPAGENT_SERVICE_TOKEN")
            if self._weak_secret(self.user_token_secret):
                raise ValueError("production requires a strong SHOPAGENT_USER_TOKEN_SECRET")
            if any(origin == "*" for origin in self.cors_origin_list):
                raise ValueError("production CORS cannot allow wildcard origins")
            if not self.cors_origin_list:
                raise ValueError("production requires at least one CORS origin")
            if self.memory_backend != "redis" or self.operations_backend != "mysql":
                raise ValueError("production requires Redis memory and MySQL operations backends")
            if self.agent_transport != "a2a" or self.tool_transport != "mcp":
                raise ValueError("production requires A2A agent and MCP tool transports")
            if not self.public_base_url.startswith("https://"):
                raise ValueError("production SHOPAGENT_PUBLIC_BASE_URL must use https://")

    @staticmethod
    def _weak_secret(value: str) -> bool:
        lowered = value.casefold()
        placeholders = (
            "dev-",
            "replace",
            "placeholder",
            "change-me",
            "change_me",
            "changeme",
            "example",
        )
        return (
            len(value) < 32
            or len(set(value)) < 8
            or any(marker in lowered for marker in placeholders)
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()
