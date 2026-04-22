## Contributing

Если хочется помочь/поменять что-то “под себя”:

### Локальный запуск

```bash
python -m venv .venv
source .venv/bin/activate
pip install -U pip
pip install -e .
resmon --no-history
```

### Стиль

- Пишите небольшими коммитами.
- Не добавляйте в репозиторий локальные артефакты (`.venv/`, `__pycache__/`, логи, дампы).
