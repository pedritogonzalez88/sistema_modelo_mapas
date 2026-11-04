"""Valida ``Settings`` con la configuración de producción, sin mostrar secretos."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def main() -> int:
    try:
        from pydantic import ValidationError

        from app.core.config import Settings

        Settings()
    except ValidationError as exc:
        for error in exc.errors():
            print(f"CONFIG_ERROR: {error.get('msg', 'configuración inválida')}")
        return 1
    except Exception as exc:  # noqa: BLE001 - no imprimir valores sensibles
        print(f"CONFIG_ERROR: {type(exc).__name__}")
        return 1
    print("CONFIG_OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
