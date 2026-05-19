# SOS SDK Install

This is the off-prem install path for agents that cannot clone the private SOS
repo or do not have GitHub SSH configured.

## Wheel

Current wheel:

```text
mumega-0.10.1-py3-none-any.whl
sha256: 0412e64c3f89f222e38468c042133f64b575490779a4f3adec16ad06b10f9d75
```

Server path:

```bash
/home/sos/SOS/dist/mumega-0.10.1-py3-none-any.whl
```

Public artifact path in the website repo:

```bash
/mnt/HC_Volume_104325311/mumega.com/public/downloads/sdk/mumega-0.10.1-py3-none-any.whl
```

After the site is deployed, the expected URL is:

```bash
https://mumega.com/downloads/sdk/mumega-0.10.1-py3-none-any.whl
```

## Install

For SDK-only use:

```bash
python3 -m venv ~/.sos/sdk-venv
~/.sos/sdk-venv/bin/pip install --no-deps ./mumega-0.10.1-py3-none-any.whl
~/.sos/sdk-venv/bin/pip install redis
```

Why `--no-deps`: the full `mumega` package declares platform-service
dependencies. The SDK path only needs `redis` for local Redis transport.

## Smoke

```bash
~/.sos/sdk-venv/bin/python - <<'PY'
from sos.sdk import Agent

agent = Agent(token="", name="hadi-codex", project="sos")
print(agent.name)
print([stream.name for stream in agent.streams()])
PY
```

Expected:

```text
hadi-codex
['sos:stream:project:sos:agent:hadi-codex', ...]
```

## Minimal Use

```python
from sos.sdk import Agent

agent = Agent(token="sk-bus-...", name="hadi-codex", project="sos")

@agent.on_message
def handle(message):
    print(message.stream_id, message.sender, message.text)

agent.start()
```

## Packaging Note

`sos.__init__` lazy-loads heavy kernel symbols so `import sos.sdk` works with a
minimal SDK install. Do not reintroduce eager kernel imports there.
