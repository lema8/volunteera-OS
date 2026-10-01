# Runtime configuration

The application writes non-secret settings to `settings.json` and, if the UI is used to store an API key, writes it to `secrets.json` with user-only permissions where the operating system permits. Both files are ignored by Git. Environment variables in `.env` take precedence.
