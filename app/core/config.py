from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    DATABASE_URL: str
    PINECONE_API_KEY: str
    PINECONE_INDEX_NAME: str = "teacheros-notebooks"
    GEMINI_API_KEY: str
    SECRET_KEY: str
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 10080
    GOOGLE_CLIENT_ID: str
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )
    TRAINING_FILE_MAX_MB: int = 50    # per file
    PUBLISH_FILE_MAX_MB: int = 10     # per file
    TRAINING_FILES_PER_SYLLABUS: int = 20
    PUBLISH_FILES_PER_SYLLABUS: int = 50

settings = Settings()