## resmon

Локальный GUI-монитор ресурсов для Linux “для себя”.

### Возможности

- **Метрики**: CPU, RAM, диски, сеть, температуры (если доступны).
- **Скорости**: сеть (↑/↓ bytes/s) и общий Disk I/O (R/W bytes/s).
- **NVIDIA GPU (через `nvidia-smi`)**: util GPU/mem, температура, VRAM, power, clocks, pstate, fan.
- **Процессы**: вкладка “Процессы” (топ по CPU и затем по RSS).
- **Алерты**: простые пороги (например, RAM > 90%).
- **История без БД**: запись в **JSONL** (одна строка = один снимок).

### Требования

- **Python**: 3.10+
- **NVIDIA метрики** (опционально): установлен драйвер NVIDIA и доступна команда `nvidia-smi`
- **Температуры** (опционально): зависит от датчиков в системе; часто помогает пакет `lm-sensors`

### Установка

```bash
python -m venv .venv
source .venv/bin/activate
pip install -U pip
pip install -e .
```

### Запуск

```bash
resmon
```

### Полезные флаги

```bash
# Быстрее обновлять UI (метрики собираются в фоне)
resmon --interval-ms 500

# Не писать историю
resmon --no-history

# Указать путь к history.jsonl
resmon --history-path /path/to/history.jsonl

# Мониторить несколько точек монтирования
resmon --disk-mountpoint / --disk-mountpoint /home
```

### История

По умолчанию пишется в `~/.local/state/resmon/history.jsonl`.
