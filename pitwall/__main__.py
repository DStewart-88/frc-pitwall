from .cli import main

# The guard matters: worker processes (pitwall -j) import this module again on Windows/macOS.
if __name__ == "__main__":
    raise SystemExit(main())
