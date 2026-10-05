from fastapi import Request

from app.services.runtime_state import RuntimeState


def get_runtime(request: Request) -> RuntimeState:
    return request.app.state.runtime

