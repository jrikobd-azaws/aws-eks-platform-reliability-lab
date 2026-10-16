"""Small internal platform endpoints; do not expose metrics through public ingress."""
from fastapi import FastAPI, Response
from sqlalchemy import text
from app.core.db import engine


def install_platform_health(app: FastAPI) -> None:
    @app.get('/health/live', include_in_schema=False)
    def live() -> dict[str, str]:
        return {'status': 'alive'}

    @app.get('/health/ready', include_in_schema=False)
    def ready(response: Response) -> dict[str, str]:
        try:
            with engine.connect() as connection:
                connection.execute(text('SELECT 1'))
        except Exception:
            response.status_code = 503
            return {'status': 'unready'}
        return {'status': 'ready'}

    @app.get('/metrics', include_in_schema=False)
    def metrics() -> Response:
        # Minimal Prometheus-compatible platform metric; no user/credential labels.
        return Response('# HELP eks_lab_application_up Application process running\n'
                        '# TYPE eks_lab_application_up gauge\n'
                        'eks_lab_application_up 1\n',
                        media_type='text/plain; version=0.0.4; charset=utf-8')
