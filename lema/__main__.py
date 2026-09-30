"""Allow `python -m lema`."""

from lema.main import main

if __name__ == "__main__":
    raise SystemExit(main())
