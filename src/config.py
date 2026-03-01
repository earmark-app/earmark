from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    tz: str = "UTC"
    log_level: str = "info"
    data_dir: str = "/data"
    gui_password: str = ""
    dry_run: bool = False
    port: int = 8780

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}

    @property
    def db_path(self) -> str:
        return f"{self.data_dir}/earmark.db"

    @property
    def lock_path(self) -> str:
        return f"{self.data_dir}/earmark.lock"


settings = Settings()
