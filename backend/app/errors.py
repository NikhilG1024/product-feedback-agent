"""Safe, machine-readable service failures."""

import re


class ServiceError(Exception):
    def __init__(self, code: str, status_code: int, detail: str | None = None) -> None:
        if not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", code):
            raise ValueError("Invalid public error code")
        self.code = code
        self.status_code = status_code
        self.detail = detail
        super().__init__(code)


SAFE_PROCESSING_ERRORS = frozenset({'provider_failed', 'model_input_too_large',
    'model_rate_limited', 'analysis_checkpoint_unavailable', 'analysis_output_limit_exceeded', 'guidance_snapshot_unavailable'})

def safe_processing_error(code):
    return code if code in SAFE_PROCESSING_ERRORS else None
